#!/usr/bin/env python3
"""
FB Marketplace Arbitrage Scanner

Main entry point - scans FB Marketplace for arbitrage opportunities
and sends Discord notifications.

Uses stealth browser scraping for both FB Marketplace and eBay.
No API credentials required!
"""
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", message=".*pydantic.*")
warnings.filterwarnings("ignore", module="pydantic.*")

import asyncio
import logging
import io

# Suppress noisy websocket/asyncio cleanup messages
logging.getLogger("websockets").setLevel(logging.ERROR)
logging.getLogger("asyncio").setLevel(logging.ERROR)


class TeeWriter:
    """Write to both stdout and a log file"""
    def __init__(self, log_path: str):
        self.terminal = sys.__stdout__
        self.log_file = open(log_path, 'w', encoding='utf-8')
    
    def write(self, message):
        self.terminal.write(message)
        self.log_file.write(message)
        self.log_file.flush()
    
    def flush(self):
        self.terminal.flush()
        self.log_file.flush()
    
    def close(self):
        self.log_file.close()

def _suppress_exception(loop, context):
    """Suppress Task exception was never retrieved messages"""
    if "exception" in context:
        exc = context["exception"]
        if "ConnectionClosedOK" in str(type(exc).__name__) or "ConnectionClosed" in str(type(exc)):
            return  # Suppress websocket cleanup noise
    # For other exceptions, use default handler
    loop.default_exception_handler(context)
import sys
from datetime import datetime
from pathlib import Path

# Ensure project root is on path for direct script execution
_project_root = str(Path(__file__).parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from config import Config, interactive_setup
from scrapers.marketplace_scraper import MarketplaceScraper
from services.arbitrage import analyze_batch, filter_opportunities, print_analysis_report
from services.discord_notifier import send_discord_alert, send_scan_summary, send_error_alert
from search_terms import get_all_search_variations, clarify_search_terms
from reports import ScanReport, ScanItem, ReportGenerator, save_scan_to_db
import database as db


class ArbitrageScanner:
    """Main scanner that orchestrates scraping, analysis, and notifications"""
    
    def __init__(self, config: Config):
        self.config = config
        self.scraper = MarketplaceScraper(
            stealth_browser_path=config.stealth_browser_path,
            user_data_dir=config.user_data_dir,
            headless=config.headless
        )
        self.seen_listings = set()  # Track seen listings to avoid duplicate alerts
    
    async def run_scan(self) -> dict:
        """
        Run a single scan cycle.
        
        Returns dict with scan results.
        """
        import time
        scan_start = time.time()
        
        # Get search terms (with optional expansion)
        base_terms = self.config.categories if self.config.categories else [self.config.category]
        expand = getattr(self.config, 'expand_search_terms', False)
        
        # Evaluate each search term - LLM checks if eBay results would be muddied
        # If muddied, asks user to clarify their intent and provides optimized terms
        print("\n📋 Evaluating search terms for eBay clarity...")
        clarified_terms = await clarify_search_terms(base_terms)
        
        if not clarified_terms:
            print("\n❌ No search terms after clarification!")
            print("   All terms were skipped. Please try different terms.")
            return {
                "timestamp": datetime.now().isoformat(),
                "categories": base_terms,
                "search_terms": [],
                "total_listings": 0,
                "analyzed": 0,
                "opportunities": 0,
                "alerts_sent": 0,
                "errors": ["All search terms were skipped during clarification"]
            }
        
        search_terms = get_all_search_variations(clarified_terms, expand=expand)
        
        # Always include a search for free items
        if "free" not in [t.lower() for t in search_terms]:
            search_terms.append("free")
        
        print("\n" + "=" * 60)
        print(f"🔍 SCAN STARTED: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"📦 Search terms: {', '.join(base_terms)}")
        if expand:
            print(f"   (expanded to {len(search_terms)} variations)")
        print(f"📍 Location: {self.config.zip_code}, {self.config.radius_miles} miles")
        print("=" * 60)
        
        # Initialize report
        report = ScanReport(
            search_terms=base_terms,
            location=self.config.zip_code,
            radius_miles=self.config.radius_miles
        )
        report_gen = ReportGenerator()
        
        results = {
            "timestamp": datetime.now().isoformat(),
            "categories": base_terms,
            "search_terms": search_terms,
            "total_listings": 0,
            "analyzed": 0,
            "opportunities": 0,
            "alerts_sent": 0,
            "errors": []
        }
        
        try:
            # Step 1: Scrape FB Marketplace listings (multiple terms)
            print("\n📡 Scraping Facebook Marketplace...")
            
            if len(search_terms) > 1:
                # Use multi-query scraper for multiple terms
                listings = await self.scraper.scrape_multiple(
                    queries=search_terms,
                    zip_code=self.config.zip_code,
                    radius_miles=self.config.radius_miles,
                    scroll_pages=2,  # Fewer pages per term since we're doing multiple
                    sort_by_price=self.config.sort_by_price
                )
            else:
                # Single term - use regular scraper
                listings = await self.scraper.scrape(
                    query=search_terms[0],
                    zip_code=self.config.zip_code,
                    radius_miles=self.config.radius_miles,
                    scroll_pages=3,
                    sort_by_price=self.config.sort_by_price
                )
            
            results["total_listings"] = len(listings)
            
            if not listings:
                print("❌ No listings found")
                return results
            
            # Note: ISO/WTB posts are filtered during search term generation
            # (checks title, description, AND image content)
            
            # Filter out "ships to you" items (non-local marketplace listings)
            local_listings = []
            shipped_count = 0
            for listing in listings:
                loc = getattr(listing, 'location', '').lower()
                title = listing.title.lower()
                # Check for shipping indicators
                if 'ship' in loc or 'ship' in title or 'ships to' in loc or 'delivery' in loc:
                    shipped_count += 1
                    continue
                local_listings.append(listing)
            
            if shipped_count > 0:
                print(f"   ⏭️ Skipped {shipped_count} 'ships to you' listings (non-local)")
            
            listings = local_listings
            
            # Sort by price: lowest first, but free items (price=0) go to the end
            listings.sort(key=lambda x: (x.price == 0, x.price))
            
            free_count = sum(1 for l in listings if l.price == 0)
            paid_count = len(listings) - free_count
            print(f"✅ Found {len(listings)} local listings ({paid_count} paid, {free_count} free)")
            
            # Limit to max listings
            all_listings = listings[:self.config.max_listings_per_scan]
            
            # Step 2: Analyze for arbitrage with adaptive batch sizing
            # Start with initial_batch_size, extend by 25 if no matches
            print("\n📊 Analyzing prices (scraping eBay sold listings)...")
            
            # Determine price sources
            price_sources = []
            if self.config.price_source == "ebay":
                price_sources = ["ebay"]
            elif self.config.price_source == "pricecharting":
                price_sources = ["pricecharting", "ebay"]  # Fall back to eBay
            else:  # "both"
                price_sources = ["pricecharting", "ebay"]
            
            # Get AI matching settings (default to True if not in config)
            use_ai = getattr(self.config, 'use_ai_matching', True)
            ai_conf = getattr(self.config, 'ai_min_confidence', 0.6)
            
            if use_ai:
                print("🤖 AI matching enabled - will verify eBay results with vision model")
            
            # Adaptive batch analysis: start small, extend if no matches
            initial_batch = getattr(self.config, 'initial_batch_size', 10)
            batch_extend = 25
            current_end = min(initial_batch, len(all_listings))
            analyzed = []
            opportunities = []
            
            while current_end <= len(all_listings):
                batch_start = len(analyzed)
                batch_listings = all_listings[batch_start:current_end]
                
                if not batch_listings:
                    break
                
                print(f"\n   📦 Analyzing listings {batch_start + 1}-{current_end} of {len(all_listings)}...")
                
                batch_analyzed = await analyze_batch(
                    batch_listings,
                    stealth_browser_path=self.config.stealth_browser_path,
                    pricecharting_api_key=self.config.pricecharting_api_key,
                    price_sources=price_sources,
                    headless=self.config.ebay_headless,
                    ebay_fee_percent=self.config.ebay_fee_percent,
                    shipping_estimate=self.config.shipping_estimate,
                    min_profit_dollars=self.config.min_profit_dollars,
                    min_profit_percent=self.config.min_profit_percent,
                    max_concurrent=1,  # Sequential for browser scraping
                    use_ai_matching=use_ai,
                    ai_min_confidence=ai_conf
                )
                
                analyzed.extend(batch_analyzed)
                
                # Check for opportunities in this batch
                batch_opportunities = filter_opportunities(batch_analyzed)
                opportunities.extend(batch_opportunities)
                
                if batch_opportunities:
                    print(f"   ✅ Found {len(batch_opportunities)} opportunity(ies) in this batch!")
                    break  # Got a match, stop extending
                else:
                    if current_end >= len(all_listings):
                        print(f"   ❌ No matches found in {len(analyzed)} listings (reached limit)")
                        break
                    # Extend by 25 and try more
                    print(f"   🔄 No matches yet, extending search by {batch_extend}...")
                    current_end = min(current_end + batch_extend, len(all_listings))
            
            results["analyzed"] = len(analyzed)
            results["opportunities"] = len(opportunities)
            
            # Print report
            print_analysis_report(analyzed)
            
            # Step 4: Send Discord alerts for new opportunities
            if opportunities and self.config.discord_webhook_url:
                print("\n🔔 Sending Discord alerts...")
                
                for listing in opportunities:
                    # Create unique ID for deduplication
                    listing_id = f"{listing.title[:30]}_{listing.price}"
                    
                    if listing_id in self.seen_listings:
                        print(f"  ⏭️ Skipping (already alerted): {listing.title[:40]}...")
                        continue
                    
                    # Send alert
                    success = await send_discord_alert(
                        self.config.discord_webhook_url,
                        listing,
                        self.config.category
                    )
                    
                    if success:
                        self.seen_listings.add(listing_id)
                        results["alerts_sent"] += 1
                        await asyncio.sleep(1)  # Rate limit Discord
                
                # Send scan summary
                best_deal = opportunities[0] if opportunities else None
                await send_scan_summary(
                    self.config.discord_webhook_url,
                    self.config.category,
                    results["total_listings"],
                    results["opportunities"],
                    best_deal
                )
            
            # Step 5: Build detailed report
            print("\n📝 Generating detailed report...")
            
            for listing in analyzed:
                # Get identified title from search term generator (already computed during price lookup)
                identified_title = getattr(listing, 'identified_title', '') or listing.title
                
                # Determine status based on listing state
                # Note: defective items are now dropped during search term generation
                if listing.is_arbitrage_opportunity:
                    status = "opportunity"
                elif listing.reference_price and listing.reference_price > 0:
                    status = "matched"
                else:
                    status = "no_match"
                
                # Build report item
                item = ScanItem(
                    fb_id=0,  # Will be assigned by DB
                    title=listing.title,
                    fb_price=listing.price,
                    location=getattr(listing, 'location', ''),
                    image_url=getattr(listing, 'image_url', ''),
                    listing_url=getattr(listing, 'listing_url', ''),
                    identified_title=identified_title,
                    brand="",  # Now handled by Gemini during search term gen
                    model="",
                    category="",
                    condition="",
                    is_defective=False,  # Defective items dropped earlier
                    defect_reason="",
                    ai_confidence=0.8 if listing.reference_price else 0.0,
                    ebay_price=listing.reference_price or 0,
                    ebay_matches=getattr(listing, 'ebay_sample_size', 1) if listing.reference_price else 0,
                    match_confidence=0.7 if listing.reference_price else 0,
                    profit_dollars=listing.potential_profit or 0,
                    profit_percent=listing.profit_percent or 0,
                    is_opportunity=listing.is_arbitrage_opportunity,
                    status=status
                )
                report.add_item(item)
            
            # Calculate scan duration
            report.scan_duration_seconds = time.time() - scan_start
            
            # Print detailed report
            report_gen.print_report(report)
            
            # Save report files
            saved_files = report_gen.save_report(report, formats=["txt", "md", "json", "html"])
            print(f"\n📁 Report saved:")
            for fmt, path in saved_files.items():
                print(f"   {fmt}: {path}")
            
            # Save to SQLite
            db_saved = save_scan_to_db(report)
            print(f"💾 Saved {db_saved} items to database")
            
        except Exception as e:
            error_msg = str(e)
            results["errors"].append(error_msg)
            print(f"\n❌ Scan error: {error_msg}")
            import traceback
            traceback.print_exc()
            
            # Send error alert to Discord
            if self.config.discord_webhook_url:
                await send_error_alert(self.config.discord_webhook_url, error_msg)
        
        print(f"\n✅ Scan complete: {results['opportunities']} opportunities, {results['alerts_sent']} alerts sent")
        return results
    
    async def run_continuous(self):
        """Run continuous scanning loop"""
        print("\n" + "=" * 60)
        print("🚀 STARTING CONTINUOUS SCAN MODE")
        print(f"⏱️  Interval: {self.config.scan_interval_minutes} minutes")
        print("Press Ctrl+C to stop")
        print("=" * 60)
        
        scan_count = 0
        
        try:
            while True:
                scan_count += 1
                print(f"\n📡 Starting scan #{scan_count}...")
                
                results = await self.run_scan()
                
                # Wait for next scan
                wait_seconds = self.config.scan_interval_minutes * 60
                print(f"\n⏳ Next scan in {self.config.scan_interval_minutes} minutes...")
                await asyncio.sleep(wait_seconds)
                
        except KeyboardInterrupt:
            print("\n\n🛑 Scanner stopped by user")
        except Exception as e:
            print(f"\n❌ Scanner error: {e}")
            raise


async def main():
    """Main entry point"""
    print("\n" + "=" * 60)
    print("🔍 FB MARKETPLACE ARBITRAGE SCANNER")
    print("=" * 60)
    print("\n💡 Uses stealth browser scraping - no API keys needed for eBay!")
    
    # Load or create config
    config = Config.load()
    
    # Check if config needs setup
    issues = config.validate()
    required_issues = [i for i in issues if "not set" in i and "won't work" not in i]
    
    if required_issues:
        print("\n⚠️ Configuration incomplete:")
        for issue in issues:
            print(f"  - {issue}")
        
        print("\n")
        setup = input("Run setup wizard? (y/n): ").strip().lower()
        if setup == 'y':
            config = interactive_setup()
        else:
            print("\n💡 Run 'python config.py' to configure settings")
            return
    
    # Display search terms
    terms = config.categories if config.categories else [config.category]
    print(f"\n📦 Search terms: {', '.join(terms)}")
    if getattr(config, 'expand_search_terms', False):
        expanded = get_all_search_variations(terms, expand=True)
        print(f"   (will expand to {len(expanded)} variations with synonyms/misspellings)")
    print(f"📍 Location: {config.zip_code}, {config.radius_miles} miles")
    print(f"💰 Min profit: ${config.min_profit_dollars} (dollars) or {config.min_profit_percent}% (percentage)")
    print(f"🔍 Price source: {config.price_source}")
    print(f"🌐 FB headless: {config.headless} | eBay headless: {config.ebay_headless}")
    
    # Choose mode
    print("\n" + "-" * 40)
    print("Options:")
    print("  1. Single scan")
    print("  2. Continuous scanning")
    print("  3. Reconfigure")
    print("  4. Test eBay scraper only")
    print("  5. Recheck tracked opportunities")
    
    choice = input("\nChoice (1/2/3/4/5): ").strip()
    
    if choice == "4":
        # Test eBay scraper
        from scrapers.ebay_scraper import EbayScraper
        query = input("🔍 Test query: ").strip() or "nintendo switch oled"
        scraper = EbayScraper(headless=False)
        result = await scraper.search_sold_items(query)
        if result:
            print(f"\n✅ {result}")
        return
    
    if choice == "5":
        # Recheck opportunities
        from services.recheck import run_recheck, get_recheck_status
        
        status = get_recheck_status()
        print(f"\n📊 Current tracking status:")
        print(f"   Total opportunities: {status.get('total', 0)}")
        print(f"   Still available: {status.get('available', 0)}")
        print(f"   Sold: {status.get('sold', 0)}")
        print(f"   Checked recently: {status.get('checked_recently', 0)}")
        
        confirm = input("\nRun recheck now? (y/n): ").strip().lower()
        if confirm == 'y':
            await run_recheck(min_hours=0, limit=50)
        return
    
    scanner = ArbitrageScanner(config)
    
    if choice == "1":
        await scanner.run_scan()
    elif choice == "2":
        await scanner.run_continuous()
    elif choice == "3":
        interactive_setup()
    else:
        print("Invalid choice")


if __name__ == "__main__":
    # Set up logging to file
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / f"scan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    
    tee = TeeWriter(str(log_path))
    sys.stdout = tee
    sys.stderr = tee
    
    print(f"📝 Logging to: {log_path}")
    
    # Set up event loop with suppressed websocket cleanup noise
    loop = asyncio.new_event_loop()
    loop.set_exception_handler(_suppress_exception)
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main())
    finally:
        loop.close()
        # Restore stdout and close log
        sys.stdout = sys.__stdout__
        sys.stderr = sys.__stderr__
        tee.close()
        print(f"\n📝 Log saved to: {log_path}")
