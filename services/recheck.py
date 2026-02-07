"""
Opportunity Recheck Service

Monitors positive hits (opportunities) and rechecks them twice daily to:
1. Verify FB listing is still available (not sold/removed/pending)
2. Update eBay sold prices
3. Update profit calculations
"""
import asyncio
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from database import DB_PATH, get_connection
from scrapers.ebay_scraper import EbayScraper
from utils.title_identifier import TitleIdentifier


@dataclass
class RecheckResult:
    """Result of rechecking an opportunity"""
    opportunity_id: int
    fb_listing_id: int
    fb_status: str  # "available", "sold", "pending", "removed", "unknown"
    old_fb_price: float
    new_fb_price: float
    old_ebay_price: float
    new_ebay_price: float
    old_profit: float
    new_profit: float
    price_changed: bool
    still_opportunity: bool
    checked_at: datetime


def migrate_db():
    """Add recheck tracking columns to database"""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Check if columns exist
    cursor.execute("PRAGMA table_info(opportunities)")
    columns = {row[1] for row in cursor.fetchall()}
    
    migrations = []
    
    if 'last_checked_at' not in columns:
        migrations.append("ALTER TABLE opportunities ADD COLUMN last_checked_at TIMESTAMP")
    if 'fb_status' not in columns:
        migrations.append("ALTER TABLE opportunities ADD COLUMN fb_status TEXT DEFAULT 'available'")
    if 'check_count' not in columns:
        migrations.append("ALTER TABLE opportunities ADD COLUMN check_count INTEGER DEFAULT 0")
    if 'price_history' not in columns:
        migrations.append("ALTER TABLE opportunities ADD COLUMN price_history TEXT")  # JSON array
    if 'ebay_min_price' not in columns:
        migrations.append("ALTER TABLE opportunities ADD COLUMN ebay_min_price REAL")
    if 'ebay_avg_price' not in columns:
        migrations.append("ALTER TABLE opportunities ADD COLUMN ebay_avg_price REAL")
    if 'ebay_sample_size' not in columns:
        migrations.append("ALTER TABLE opportunities ADD COLUMN ebay_sample_size INTEGER")
    
    for sql in migrations:
        try:
            cursor.execute(sql)
            print(f"   ✅ {sql}")
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                print(f"   ⚠️ Migration error: {e}")
    
    conn.commit()
    conn.close()
    
    if migrations:
        print(f"✅ Applied {len(migrations)} database migrations")


def get_opportunities_to_check(
    min_hours_since_check: float = 12.0,  # Twice daily = every 12 hours
    limit: int = 50
) -> list[dict]:
    """
    Get opportunities that need rechecking.
    
    Returns opportunities that:
    - Haven't been checked in the last N hours
    - Are still in active status (not purchased/skipped)
    - FB listing is still available
    """
    conn = get_connection()
    cursor = conn.cursor()
    
    cutoff_time = datetime.now() - timedelta(hours=min_hours_since_check)
    
    cursor.execute("""
        SELECT 
            o.id, o.fb_listing_id, o.ebay_listing_id,
            o.fb_price, o.ebay_price, o.profit_dollars, o.profit_margin,
            o.status, o.fb_status, o.last_checked_at, o.check_count,
            o.price_history,
            f.title, f.listing_url, f.image_url, f.location
        FROM opportunities o
        JOIN fb_listings f ON o.fb_listing_id = f.id
        WHERE o.status IN ('new', 'reviewed')
          AND (o.fb_status IS NULL OR o.fb_status = 'available')
          AND o.is_defective = FALSE
          AND (o.last_checked_at IS NULL OR o.last_checked_at < ?)
        ORDER BY o.profit_dollars DESC
        LIMIT ?
    """, (cutoff_time.isoformat(), limit))
    
    rows = cursor.fetchall()
    conn.close()
    
    return [dict(row) for row in rows]


def update_opportunity_check(
    opportunity_id: int,
    fb_status: str = None,
    fb_price: float = None,
    ebay_price: float = None,
    ebay_min_price: float = None,
    ebay_avg_price: float = None,
    ebay_sample_size: int = None,
    profit_dollars: float = None,
    profit_margin: float = None,
    status: str = None,
    notes: str = None
):
    """Update opportunity after recheck"""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Get current price history
    cursor.execute("SELECT price_history, check_count FROM opportunities WHERE id = ?", (opportunity_id,))
    row = cursor.fetchone()
    
    price_history = json.loads(row['price_history']) if row and row['price_history'] else []
    check_count = (row['check_count'] or 0) + 1 if row else 1
    
    # Add new price point to history
    price_history.append({
        'timestamp': datetime.now().isoformat(),
        'fb_price': fb_price,
        'ebay_price': ebay_price,
        'ebay_min': ebay_min_price,
        'profit': profit_dollars
    })
    
    # Keep last 30 days of history
    if len(price_history) > 60:  # ~30 days at 2x/day
        price_history = price_history[-60:]
    
    # Build update query
    updates = ["last_checked_at = CURRENT_TIMESTAMP", "check_count = ?"]
    params = [check_count]
    
    if fb_status:
        updates.append("fb_status = ?")
        params.append(fb_status)
    if fb_price is not None:
        updates.append("fb_price = ?")
        params.append(fb_price)
    if ebay_price is not None:
        updates.append("ebay_price = ?")
        params.append(ebay_price)
    if ebay_min_price is not None:
        updates.append("ebay_min_price = ?")
        params.append(ebay_min_price)
    if ebay_avg_price is not None:
        updates.append("ebay_avg_price = ?")
        params.append(ebay_avg_price)
    if ebay_sample_size is not None:
        updates.append("ebay_sample_size = ?")
        params.append(ebay_sample_size)
    if profit_dollars is not None:
        updates.append("profit_dollars = ?")
        params.append(profit_dollars)
    if profit_margin is not None:
        updates.append("profit_margin = ?")
        params.append(profit_margin)
    if status:
        updates.append("status = ?")
        params.append(status)
    if notes:
        updates.append("notes = ?")
        params.append(notes)
    
    updates.append("price_history = ?")
    params.append(json.dumps(price_history))
    
    params.append(opportunity_id)
    
    sql = f"UPDATE opportunities SET {', '.join(updates)} WHERE id = ?"
    cursor.execute(sql, params)
    
    conn.commit()
    conn.close()


async def check_fb_listing_status(listing_url: str) -> tuple[str, Optional[float]]:
    """
    Check if FB listing is still available.
    
    Returns: (status, current_price)
    - status: "available", "sold", "pending", "removed"
    - current_price: Price if available, None otherwise
    """
    # TODO: Implement FB listing status check via browser
    # For now, return available
    # This would need to:
    # 1. Navigate to listing_url
    # 2. Check for "This listing is no longer available" → removed
    # 3. Check for "Pending" badge → pending
    # 4. Check for "Sold" → sold
    # 5. Extract current price if available
    
    return "available", None  # Placeholder


async def recheck_opportunity(
    opportunity: dict,
    ebay_scraper: EbayScraper = None,
    identifier: TitleIdentifier = None
) -> RecheckResult:
    """
    Recheck a single opportunity.
    
    1. Check FB listing status
    2. Get fresh eBay prices
    3. Recalculate profit
    """
    opp_id = opportunity['id']
    title = opportunity['title']
    old_fb_price = opportunity['fb_price']
    old_ebay_price = opportunity['ebay_price']
    old_profit = opportunity['profit_dollars']
    
    print(f"   🔄 Rechecking: {title[:40]}...")
    
    # Check FB status
    fb_status = "available"
    new_fb_price = old_fb_price
    
    if opportunity.get('listing_url'):
        fb_status, price = await check_fb_listing_status(opportunity['listing_url'])
        if price is not None:
            new_fb_price = price
    
    # Get fresh eBay prices
    new_ebay_price = old_ebay_price
    ebay_min = None
    ebay_avg = None
    ebay_count = 0
    
    if ebay_scraper and fb_status == "available":
        # Generate search query
        if identifier:
            product = await identifier.identify_product(title, image_url=opportunity.get('image_url'))
            queries = product.get_search_queries(max_queries=1)
            search_query = queries[0] if queries else title
        else:
            search_query = title
        
        result = await ebay_scraper.search_sold_items(search_query, limit=10)
        if result and result.num_sold > 0:
            new_ebay_price = result.min_price  # Use min for conservative estimate
            ebay_min = result.min_price
            ebay_avg = result.avg_sold_price
            ebay_count = result.num_sold
    
    # Calculate new profit
    ebay_fee_percent = 13.25
    shipping_estimate = 15.0
    
    gross_sale = new_ebay_price
    ebay_fees = gross_sale * (ebay_fee_percent / 100)
    net_after_fees = gross_sale - ebay_fees - shipping_estimate
    new_profit = net_after_fees - new_fb_price
    new_margin = (new_profit / new_fb_price * 100) if new_fb_price > 0 else 0
    
    # Determine if still an opportunity
    still_opportunity = (
        fb_status == "available" and
        new_profit >= 30.0  # Min profit threshold
    )
    
    # Update status based on FB status
    new_status = None
    if fb_status == "sold":
        new_status = "fb_sold"
    elif fb_status == "removed":
        new_status = "fb_removed"
    elif fb_status == "pending":
        new_status = "fb_pending"
    elif not still_opportunity:
        new_status = "no_longer_profitable"
    
    # Update database
    update_opportunity_check(
        opportunity_id=opp_id,
        fb_status=fb_status,
        fb_price=new_fb_price,
        ebay_price=new_ebay_price,
        ebay_min_price=ebay_min,
        ebay_avg_price=ebay_avg,
        ebay_sample_size=ebay_count,
        profit_dollars=new_profit,
        profit_margin=new_margin,
        status=new_status
    )
    
    price_changed = (new_fb_price != old_fb_price or new_ebay_price != old_ebay_price)
    
    return RecheckResult(
        opportunity_id=opp_id,
        fb_listing_id=opportunity['fb_listing_id'],
        fb_status=fb_status,
        old_fb_price=old_fb_price,
        new_fb_price=new_fb_price,
        old_ebay_price=old_ebay_price,
        new_ebay_price=new_ebay_price,
        old_profit=old_profit,
        new_profit=new_profit,
        price_changed=price_changed,
        still_opportunity=still_opportunity,
        checked_at=datetime.now()
    )


async def run_recheck(
    min_hours: float = 12.0,
    limit: int = 20
) -> list[RecheckResult]:
    """
    Run recheck on opportunities that need updating.
    
    Call this twice daily (e.g., via cron at 9am and 9pm).
    """
    print("\n" + "=" * 60)
    print("🔄 OPPORTUNITY RECHECK")
    print("=" * 60)
    
    # Ensure DB has recheck columns
    migrate_db()
    
    # Get opportunities to check
    opportunities = get_opportunities_to_check(min_hours_since_check=min_hours, limit=limit)
    
    if not opportunities:
        print("✅ No opportunities need rechecking")
        return []
    
    print(f"📋 Found {len(opportunities)} opportunities to recheck")
    
    # Initialize services
    scraper = EbayScraper(headless=True)
    identifier = TitleIdentifier()
    
    results = []
    
    for opp in opportunities:
        try:
            result = await recheck_opportunity(opp, scraper, identifier)
            results.append(result)
            
            # Log changes
            if result.fb_status != "available":
                print(f"      ❌ FB Status: {result.fb_status}")
            elif result.price_changed:
                profit_change = result.new_profit - result.old_profit
                direction = "📈" if profit_change > 0 else "📉"
                print(f"      {direction} Profit: ${result.old_profit:.2f} → ${result.new_profit:.2f}")
            else:
                print(f"      ✅ No changes")
            
            await asyncio.sleep(2)  # Rate limit
            
        except Exception as e:
            print(f"      ⚠️ Error: {e}")
    
    await identifier.close()
    
    # Summary
    still_good = sum(1 for r in results if r.still_opportunity)
    gone = sum(1 for r in results if r.fb_status != "available")
    price_drops = sum(1 for r in results if r.new_profit < r.old_profit)
    
    print("\n" + "-" * 40)
    print("RECHECK SUMMARY")
    print("-" * 40)
    print(f"  Checked:        {len(results)}")
    print(f"  Still good:     {still_good}")
    print(f"  FB gone/sold:   {gone}")
    print(f"  Price drops:    {price_drops}")
    print("=" * 60)
    
    return results


def get_recheck_status() -> dict:
    """Get summary of opportunity tracking status"""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN fb_status = 'available' OR fb_status IS NULL THEN 1 ELSE 0 END) as available,
            SUM(CASE WHEN fb_status = 'sold' THEN 1 ELSE 0 END) as sold,
            SUM(CASE WHEN fb_status = 'pending' THEN 1 ELSE 0 END) as pending,
            SUM(CASE WHEN fb_status = 'removed' THEN 1 ELSE 0 END) as removed,
            SUM(CASE WHEN last_checked_at > datetime('now', '-12 hours') THEN 1 ELSE 0 END) as checked_recently,
            AVG(check_count) as avg_checks
        FROM opportunities
        WHERE status IN ('new', 'reviewed')
    """)
    
    row = cursor.fetchone()
    conn.close()
    
    return dict(row) if row else {}


if __name__ == "__main__":
    # Run recheck
    asyncio.run(run_recheck(min_hours=0.1, limit=5))  # Test with short interval
