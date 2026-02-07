"""
Discord notification service for arbitrage alerts
"""
import asyncio
import httpx
from datetime import datetime
from typing import Optional
import sys
sys.path.append('..')
from utils.listing_parser import Listing


async def send_discord_alert(
    webhook_url: str,
    listing: Listing,
    category: str = ""
) -> bool:
    """
    Send a Discord embed for an arbitrage opportunity.
    
    Returns True if sent successfully.
    """
    if not webhook_url:
        print("⚠️ No Discord webhook URL configured")
        return False
    
    # Calculate profit display
    profit_str = f"${listing.potential_profit:.2f}" if listing.potential_profit else "Unknown"
    profit_pct_str = f"{listing.profit_percent:.1f}%" if listing.profit_percent else "Unknown"
    
    # Build embed
    embed = {
        "title": f"💰 Arbitrage Alert: {listing.title[:100]}",
        "color": 0x00FF00 if listing.potential_profit and listing.potential_profit > 50 else 0xFFFF00,
        "fields": [
            {
                "name": "📦 FB Marketplace Price",
                "value": f"**${listing.price:.2f}**",
                "inline": True
            },
            {
                "name": "📊 Reference Price",
                "value": f"${listing.reference_price:.2f}" if listing.reference_price else "N/A",
                "inline": True
            },
            {
                "name": "💵 Potential Profit",
                "value": f"**{profit_str}** ({profit_pct_str})",
                "inline": True
            },
        ],
        "footer": {
            "text": f"FB Arbitrage Scanner | {category}" if category else "FB Arbitrage Scanner"
        },
        "timestamp": datetime.utcnow().isoformat()
    }
    
    # Add optional fields
    if listing.location:
        embed["fields"].append({
            "name": "📍 Location",
            "value": listing.location,
            "inline": True
        })
    
    if listing.condition:
        embed["fields"].append({
            "name": "📋 Condition",
            "value": listing.condition,
            "inline": True
        })
    
    if listing.reference_source:
        embed["fields"].append({
            "name": "🔍 Price Source",
            "value": listing.reference_source,
            "inline": True
        })
    
    # Add identified product name if different from original title
    identified = getattr(listing, 'identified_title', None)
    if identified and identified != listing.title:
        embed["fields"].append({
            "name": "🏷️ Identified As",
            "value": identified[:100],
            "inline": False
        })
    
    # Add listing URL if available
    if listing.listing_url:
        embed["url"] = listing.listing_url
        embed["fields"].append({
            "name": "🔗 Listing",
            "value": f"[View on Facebook]({listing.listing_url})",
            "inline": False
        })
    
    # Add image if available
    if listing.image_url:
        embed["thumbnail"] = {"url": listing.image_url}
    
    payload = {
        "embeds": [embed]
    }
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"}
            )
            
            if resp.status_code in (200, 204):
                print(f"✅ Discord alert sent: {listing.title[:50]}...")
                return True
            else:
                print(f"❌ Discord error: {resp.status_code} {resp.text}")
                return False
                
    except Exception as e:
        print(f"❌ Discord error: {e}")
        return False


async def send_scan_summary(
    webhook_url: str,
    category: str,
    total_listings: int,
    opportunities: int,
    best_deal: Optional[Listing] = None
) -> bool:
    """Send a summary of a scan run"""
    if not webhook_url:
        return False
    
    embed = {
        "title": "🔍 Scan Complete",
        "color": 0x0099FF,
        "fields": [
            {
                "name": "Category",
                "value": category,
                "inline": True
            },
            {
                "name": "Listings Scanned",
                "value": str(total_listings),
                "inline": True
            },
            {
                "name": "Opportunities Found",
                "value": str(opportunities),
                "inline": True
            }
        ],
        "timestamp": datetime.utcnow().isoformat()
    }
    
    if best_deal and best_deal.potential_profit:
        embed["fields"].append({
            "name": "🏆 Best Deal",
            "value": f"{best_deal.title[:50]}... - **${best_deal.potential_profit:.2f}** profit",
            "inline": False
        })
    
    payload = {"embeds": [embed]}
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(webhook_url, json=payload)
            return resp.status_code in (200, 204)
    except:
        return False


async def send_error_alert(webhook_url: str, error_msg: str) -> bool:
    """Send an error notification"""
    if not webhook_url:
        return False
    
    payload = {
        "embeds": [{
            "title": "⚠️ Scanner Error",
            "description": error_msg[:500],
            "color": 0xFF0000,
            "timestamp": datetime.utcnow().isoformat()
        }]
    }
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(webhook_url, json=payload)
            return resp.status_code in (200, 204)
    except:
        return False


if __name__ == "__main__":
    # Test
    async def test():
        # Create a test listing
        test_listing = Listing(
            title="Nintendo Switch OLED White",
            price=250.0,
            price_raw="$250",
            location="5 miles away",
            condition="Like New",
            reference_price=320.0,
            reference_source="eBay Sold",
            potential_profit=45.0,
            profit_percent=18.0,
            is_arbitrage_opportunity=True
        )
        
        # Replace with your webhook for testing
        webhook = input("Enter Discord webhook URL to test: ").strip()
        if webhook:
            await send_discord_alert(webhook, test_listing, "Test Category")
        else:
            print("Skipping test - no webhook provided")
    
    asyncio.run(test())
