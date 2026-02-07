#!/usr/bin/env python3
"""
Quick E2E test of AI matching pipeline with SQLite persistence.

Tests: FB listing → TitleIdentifier (llava:13b) → eBay search → AIItemMatcher verification
Saves: All results to SQLite database
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from utils.title_identifier import TitleIdentifier
from utils.ai_matcher import AIItemMatcher
from scrapers.ebay_scraper import EbayScraper
import database as db


async def test_pipeline():
    """Test the full AI matching pipeline with a sample listing"""
    
    # Simulate a FB listing (working item)
    fb_listing = {
        "title": "1 oz American Silver Eagle 2024 BU in capsule",
        "price": 28.00,
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/1/1e/2006_AESilver_Proof_Obv.jpg/440px-2006_AESilver_Proof_Obv.jpg",
        "description": "Selling my silver eagle, great condition"
    }
    
    print("=" * 60)
    print("🧪 E2E TEST: AI MATCHING PIPELINE")
    print("=" * 60)
    print(f"\n📦 Test FB Listing:")
    print(f"   Title: {fb_listing['title']}")
    print(f"   Price: ${fb_listing['price']}")
    print(f"   Image: {fb_listing['image_url'][:50]}...")
    
    # Save FB listing to database
    fb_id = db.insert_fb_listing(
        title=fb_listing['title'],
        price=fb_listing['price'],
        description=fb_listing.get('description'),
        image_url=fb_listing.get('image_url'),
        raw_data=fb_listing
    )
    print(f"   💾 Saved to DB: fb_listing #{fb_id}")
    
    # Step 1: Identify product with vision model
    print("\n" + "-" * 40)
    print("STEP 1: Product Identification (llava:13b)")
    print("-" * 40)
    
    identifier = TitleIdentifier()
    product = await identifier.identify_product(
        original_title=fb_listing["title"],
        image_url=fb_listing["image_url"]
    )
    
    print(f"\n✅ Identified:")
    print(f"   Brand: {product.brand}")
    print(f"   Model: {product.model}")
    print(f"   Category: {product.category}")
    print(f"   Condition: {product.condition}")
    print(f"   Defective: {product.is_defective} - {product.defect_reason}")
    print(f"   Search title: {product.identified_title}")
    print(f"   Variations: {product.search_variations}")
    print(f"   Confidence: {product.confidence:.0%}")
    
    # Save AI identification to database
    ai_id = db.insert_ai_identification(
        fb_listing_id=fb_id,
        identified_title=product.identified_title,
        brand=product.brand,
        model=product.model,
        category=product.category,
        condition=product.condition,
        is_defective=product.is_defective,
        defect_reason=product.defect_reason,
        search_queries=product.get_search_queries(),
        confidence=product.confidence,
        vision_model="llava:13b",
        text_model="qwen2.5",
        raw_vision_response=product.raw_vision_response,
        raw_text_response=product.raw_text_response
    )
    print(f"   💾 Saved to DB: ai_identification #{ai_id}")
    
    # Check if defective - still process but flag it
    if product.is_defective:
        print(f"\n⚠️  DEFECTIVE ITEM DETECTED: {product.defect_reason}")
        print("   Will search for comparable defective items on eBay...")
    
    # Step 2: Search eBay with generated queries
    print("\n" + "-" * 40)
    print("STEP 2: eBay Sold Search")
    print("-" * 40)
    
    queries = product.get_search_queries(max_queries=2)
    print(f"🔍 Searching eBay for: {queries}")
    
    scraper = EbayScraper(headless=True)
    ebay_results = []
    best_ebay_id = None
    
    for i, query in enumerate(queries):
        print(f"\n   Searching: '{query}'...")
        result = await scraper.search_sold_items(query, limit=10)
        if result and result.recent_sales:
            print(f"   Found: avg ${result.avg_sold_price:.2f} ({result.num_sold} sold)")
            
            # Save each eBay result to database
            for sale in result.recent_sales[:5]:  # Save top 5
                ebay_id = db.insert_ebay_listing(
                    title=sale.title,
                    price=sale.price,
                    search_query=query,
                    sold_date=sale.sold_date,
                    condition=sale.condition,
                    image_url=sale.image_url,
                    listing_url=sale.url,
                    raw_data={'sale': sale.__dict__}
                )
                if best_ebay_id is None:
                    best_ebay_id = ebay_id
                
                ebay_results.append({
                    "id": ebay_id,
                    "title": sale.title,
                    "price": sale.price,
                    "image_url": sale.image_url,
                    "url": sale.url
                })
            
            print(f"   💾 Saved {len(result.recent_sales[:5])} eBay listings to DB")
            break  # Found results, no need to try more queries
        
        # Rate limit: wait between queries to avoid eBay blocking
        if i < len(queries) - 1:
            print("   ⏳ Waiting before next search...")
            await asyncio.sleep(10)
    
    if not ebay_results:
        print("❌ No eBay results found")
        # Save opportunity as no-match
        db.insert_opportunity(
            fb_listing_id=fb_id,
            fb_price=fb_listing['price'],
            ebay_price=0,
            is_defective=product.is_defective,
            status='no_match',
            notes='No eBay results found'
        )
        await identifier.close()
        return
    
    # Step 3: AI verification of matches
    print("\n" + "-" * 40)
    print("STEP 3: AI Match Verification (llava:13b)")
    print("-" * 40)
    
    matcher = AIItemMatcher()
    best_match = None
    best_confidence = 0
    
    for ebay in ebay_results[:3]:  # Check top 3
        print(f"\n🔍 Comparing to: {ebay['title'][:50]}...")
        
        result = await matcher.compare_listings(
            fb_title=fb_listing["title"],
            fb_description=fb_listing.get("description", ""),
            fb_image_url=fb_listing["image_url"],
            ebay_title=ebay["title"],
            ebay_image_url=ebay.get("image_url")
        )
        
        print(f"   {result}")
        
        # Save match result to database
        db.insert_ai_match(
            fb_listing_id=fb_id,
            ebay_listing_id=ebay['id'],
            is_match=result.is_match,
            confidence=result.confidence,
            title_similarity=result.title_similarity,
            image_match=result.image_match,
            image_confidence=result.image_confidence,
            reasoning=result.reasoning
        )
        
        if result.is_match and result.confidence > best_confidence:
            best_match = ebay
            best_confidence = result.confidence
    
    # Calculate and save opportunity
    if best_match:
        profit = best_match["price"] - fb_listing["price"]
        margin = (profit / fb_listing["price"]) * 100 if fb_listing["price"] > 0 else 0
        
        opp_id = db.insert_opportunity(
            fb_listing_id=fb_id,
            ebay_listing_id=best_match['id'],
            fb_price=fb_listing['price'],
            ebay_price=best_match['price'],
            is_defective=product.is_defective,
            status='new'
        )
        
        print(f"\n💰 {'⚠️ DEFECTIVE ' if product.is_defective else ''}ARBITRAGE OPPORTUNITY!")
        print(f"   Buy on FB: ${fb_listing['price']:.2f}")
        print(f"   Sell on eBay: ${best_match['price']:.2f}")
        print(f"   Gross profit: ${profit:.2f} ({margin:.0f}%)")
        print(f"   💾 Saved to DB: opportunity #{opp_id}")
    else:
        db.insert_opportunity(
            fb_listing_id=fb_id,
            fb_price=fb_listing['price'],
            ebay_price=ebay_results[0]['price'] if ebay_results else 0,
            is_defective=product.is_defective,
            status='low_confidence',
            notes='No confident AI match'
        )
        print("\n❌ No confident match found")
    
    # Cleanup
    await identifier.close()
    await matcher.close()
    
    print("\n" + "=" * 60)
    print("✅ E2E TEST COMPLETE")
    print("=" * 60)


async def test_defective():
    """Test with a defective listing"""
    print("\n" + "=" * 60)
    print("🧪 TEST: DEFECTIVE ITEM DETECTION")
    print("=" * 60)
    
    # The GPU from the screenshot
    fb_listing = {
        "title": "Zotac Rtx 4090 Amp Extreme Airo 24gb Gddr6x Graphics Card For Parts- No Core",
        "price": 140.00,
        "description": "This unit arrives with no core or memory, but no core for sure."
    }
    
    print(f"\n📦 Test FB Listing:")
    print(f"   Title: {fb_listing['title']}")
    print(f"   Price: ${fb_listing['price']}")
    
    identifier = TitleIdentifier()
    product = await identifier.identify_product(
        original_title=fb_listing["title"],
        image_url=None
    )
    
    print(f"\n✅ Identified:")
    print(f"   Defective: {'⚠️ YES' if product.is_defective else '❌ NO'}")
    print(f"   Reason: {product.defect_reason}")
    print(f"   Search queries: {product.get_search_queries()}")
    
    # The search queries should include "for parts" so we compare against 
    # other cooler-only listings, NOT working $1500 GPUs!
    
    await identifier.close()


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "defective":
        asyncio.run(test_defective())
    else:
        asyncio.run(test_pipeline())
