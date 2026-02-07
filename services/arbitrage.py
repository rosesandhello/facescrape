"""
Arbitrage calculation and analysis

Uses stealth browser for eBay scraping (no API needed).
"""
import asyncio
from typing import Optional
import sys
sys.path.insert(0, str(__file__).rsplit('/', 2)[0])

from utils.listing_parser import Listing, clean_title_for_search
from utils.pickup_cost import PickupCostCalculator, PickupCost
from services.price_lookup import PriceLookupService, PriceLookupResult


async def analyze_listing(
    listing: Listing,
    price_service: PriceLookupService,
    price_sources: list[str] = ["ebay"],
    ebay_fee_percent: float = 13.25,
    shipping_estimate: float = 15.0,
    min_profit_dollars: float = 30.0,
    min_profit_percent: float = 20.0,
    use_ai_matching: bool = True,
    use_lowest_sold_price: bool = True,  # Use min instead of avg
    pickup_calculator: Optional[PickupCostCalculator] = None  # For fuel cost
) -> Listing:
    """
    Analyze a listing for arbitrage opportunity.
    
    Updates the listing with:
    - reference_price
    - reference_source
    - potential_profit
    - profit_percent
    - is_arbitrage_opportunity
    
    When use_ai_matching=True (default), uses the raw title and
    AI vision model to verify eBay matches. This is more accurate
    than keyword-cleaning approaches.
    
    Returns the updated listing.
    """
    # Use raw title for AI matching (no cleaning!)
    # The AI matcher will verify each eBay result is the same product
    search_title = listing.title
    
    # Get FB image URL for AI matching (if available)
    fb_image_url = getattr(listing, 'image_url', None)
    
    # Get reference price (with AI verification if enabled)
    result = await price_service.lookup(
        search_title, 
        sources=price_sources,
        fb_image_url=fb_image_url
    )
    
    if result:
        # Use lowest sold price for conservative estimate, or average
        if use_lowest_sold_price and result.min_price > 0:
            listing.reference_price = result.min_price
            listing.reference_source = f"{result.source} (min)"
        else:
            listing.reference_price = result.avg_price
            listing.reference_source = result.source
        
        # Store the identified search term (from Gemini/Qwen)
        listing.identified_title = result.query
        
        # Store additional price data for reporting
        listing.ebay_avg_price = result.avg_price
        listing.ebay_min_price = result.min_price
        listing.ebay_max_price = result.max_price
        listing.ebay_sample_size = result.sample_size
    else:
        listing.reference_price = None
        listing.reference_source = None
        listing.identified_title = None
    
    # Calculate pickup cost if calculator provided
    pickup_cost = 0.0
    if pickup_calculator and hasattr(listing, 'location'):
        pickup_result = await pickup_calculator.calculate(
            location_string=listing.location
        )
        if pickup_result:
            pickup_cost = pickup_result.fuel_cost
            listing.pickup_cost = pickup_cost
            listing.pickup_distance = pickup_result.round_trip_miles
    
    # Calculate potential profit
    if listing.reference_price and listing.reference_price > listing.price:
        # Calculate sell price after fees
        gross_sale = listing.reference_price
        ebay_fees = gross_sale * (ebay_fee_percent / 100)
        net_after_fees = gross_sale - ebay_fees - shipping_estimate
        
        # Total cost = purchase price + pickup fuel cost
        total_cost = listing.price + pickup_cost
        
        # Profit = what you'd get - total cost
        profit = net_after_fees - total_cost
        profit_percent = (profit / total_cost) * 100 if total_cost > 0 else 0
        
        listing.potential_profit = round(profit, 2)
        listing.profit_percent = round(profit_percent, 1)
        
        # Check if meets thresholds
        listing.is_arbitrage_opportunity = (
            profit >= min_profit_dollars or 
            profit_percent >= min_profit_percent
        )
    else:
        listing.potential_profit = 0
        listing.profit_percent = 0
        listing.is_arbitrage_opportunity = False
    
    return listing


async def analyze_batch(
    listings: list[Listing],
    stealth_browser_path: str = None,  # Auto-detected if not provided
    pricecharting_api_key: str = "",
    price_sources: list[str] = ["ebay"],
    headless: bool = True,
    ebay_fee_percent: float = 13.25,
    shipping_estimate: float = 15.0,
    min_profit_dollars: float = 30.0,
    min_profit_percent: float = 20.0,
    max_concurrent: int = 1,  # Browser scraping is slow, keep at 1
    use_ai_matching: bool = True,
    use_lowest_sold_price: bool = True,  # Use min price for profit calc
    vehicle_mpg: float = 0.0,  # 0 = don't calculate pickup cost
    zip_code: str = "",  # For gas price lookup
    ai_min_confidence: float = 0.6
) -> list[Listing]:
    """
    Analyze multiple listings with rate limiting.
    
    When use_ai_matching=True, uses AI vision model to verify that
    eBay sold items actually match the FB listing. More accurate
    than keyword-based matching.
    
    Note: Browser scraping is sequential (max_concurrent=1 recommended)
    because we spawn/close browser for each lookup.
    """
    # Create price service with AI matching settings
    price_service = PriceLookupService(
        stealth_browser_path=stealth_browser_path,
        pricecharting_api_key=pricecharting_api_key,
        headless=headless,
        use_ai_matching=use_ai_matching,
        ai_min_confidence=ai_min_confidence
    )
    
    # Create pickup cost calculator if MPG specified
    pickup_calculator = None
    if vehicle_mpg > 0:
        pickup_calculator = PickupCostCalculator(
            vehicle_mpg=vehicle_mpg,
            zip_code=zip_code
        )
        print(f"🚗 Pickup cost enabled: {vehicle_mpg} MPG from {zip_code}")
    
    semaphore = asyncio.Semaphore(max_concurrent)
    
    async def analyze_with_limit(listing: Listing) -> Listing:
        async with semaphore:
            print(f"📊 Analyzing: {listing.title[:40]}...")
            return await analyze_listing(
                listing,
                price_service=price_service,
                price_sources=price_sources,
                ebay_fee_percent=ebay_fee_percent,
                shipping_estimate=shipping_estimate,
                min_profit_dollars=min_profit_dollars,
                min_profit_percent=min_profit_percent,
                use_lowest_sold_price=use_lowest_sold_price,
                pickup_calculator=pickup_calculator
            )
    
    # Process sequentially for browser scraping
    if max_concurrent == 1:
        analyzed = []
        for listing in listings:
            try:
                result = await analyze_with_limit(listing)
                analyzed.append(result)
                # Small delay between lookups
                await asyncio.sleep(1)
            except Exception as e:
                print(f"⚠️ Analysis error for {listing.title[:30]}: {e}")
                analyzed.append(listing)
        return analyzed
    
    # Otherwise use gather
    tasks = [analyze_with_limit(listing) for listing in listings]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Filter out exceptions
    analyzed = []
    for i, result in enumerate(results):
        if isinstance(result, Listing):
            analyzed.append(result)
        elif isinstance(result, Exception):
            print(f"⚠️ Analysis error: {result}")
            analyzed.append(listings[i])  # Return original listing
    
    return analyzed


def filter_opportunities(listings: list[Listing]) -> list[Listing]:
    """Filter to only arbitrage opportunities, sorted by profit"""
    opportunities = [l for l in listings if l.is_arbitrage_opportunity]
    return sorted(opportunities, key=lambda l: l.potential_profit or 0, reverse=True)


def print_analysis_report(listings: list[Listing]):
    """Print a formatted analysis report"""
    opportunities = filter_opportunities(listings)
    
    print("\n" + "=" * 70)
    print("ARBITRAGE ANALYSIS REPORT")
    print("=" * 70)
    print(f"\nTotal listings analyzed: {len(listings)}")
    print(f"Opportunities found: {len(opportunities)}")
    
    if opportunities:
        print("\n🎯 TOP OPPORTUNITIES:\n")
        for i, listing in enumerate(opportunities[:10], 1):
            print(f"{i}. {listing.title[:50]}...")
            print(f"   FB Price: ${listing.price:.2f}")
            print(f"   Reference: ${listing.reference_price:.2f} ({listing.reference_source})")
            # Show pickup cost if calculated
            pickup_cost = getattr(listing, 'pickup_cost', None) or 0
            if pickup_cost > 0:
                pickup_dist = getattr(listing, 'pickup_distance', None) or 0
                print(f"   🚗 Pickup: ${pickup_cost:.2f} ({pickup_dist:.0f}mi round trip)")
            print(f"   💰 Profit: ${listing.potential_profit:.2f} ({listing.profit_percent:.1f}%)")
            print(f"   📍 {listing.location}")
            if listing.listing_url:
                print(f"   🔗 {listing.listing_url}")
            print()
    else:
        print("\n❌ No arbitrage opportunities found meeting thresholds.")
    
    # Also show items with reference prices but no profit
    analyzed_but_no_profit = [
        l for l in listings 
        if l.reference_price and not l.is_arbitrage_opportunity
    ]
    
    if analyzed_but_no_profit:
        print("\n📋 OTHER ANALYZED (no profit opportunity):\n")
        for listing in analyzed_but_no_profit[:5]:
            profit_str = f"${listing.potential_profit:.2f}" if listing.potential_profit != 0 else "N/A"
            print(f"   ${listing.price:.2f} - {listing.title[:40]}...")
            print(f"   Ref: ${listing.reference_price:.2f} | Profit: {profit_str}")
    
    print("\n" + "=" * 70)


if __name__ == "__main__":
    async def test():
        # Test with sample listings
        test_listings = [
            Listing(
                title="Nintendo Switch OLED White Console",
                price=250.0,
                price_raw="$250",
                location="5 miles away",
                condition="Like New"
            ),
            Listing(
                title="PS5 Disc Edition Console Bundle",
                price=400.0,
                price_raw="$400",
                location="10 miles away",
                condition="Used"
            ),
        ]
        
        print("Testing arbitrage analysis (stealth browser)...")
        results = await analyze_batch(
            test_listings, 
            price_sources=["ebay"],
            headless=False  # Visible for testing
        )
        print_analysis_report(results)
    
    asyncio.run(test())
