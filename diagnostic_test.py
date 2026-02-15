#!/usr/bin/env python3
"""
Quick diagnostic test - run each stage independently to find issues.
Target: <5 minutes total.

Run with: GEMINI_API_KEY="..." python3 diagnostic_test.py
"""
import asyncio
import os
import sys

# Ensure GEMINI_API_KEY is set
if not os.environ.get("GEMINI_API_KEY"):
    print("❌ GEMINI_API_KEY not set. Run with:")
    print('   GEMINI_API_KEY="..." python3 diagnostic_test.py')
    sys.exit(1)

async def test_1_gemini_basic():
    """Test 1: Basic Gemini API connectivity"""
    print("\n" + "="*60)
    print("TEST 1: Gemini API Connectivity")
    print("="*60)
    
    import httpx
    api_key = os.environ.get("GEMINI_API_KEY")
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}",
                headers={"Content-Type": "application/json"},
                json={"contents": [{"parts": [{"text": "Reply with just 'OK'"}]}]}
            )
            
            if response.status_code == 200:
                print("   ✅ Gemini API working")
                return True
            elif response.status_code == 429:
                print("   ⚠️ Gemini rate limited (429) - wait and retry")
                return False
            else:
                print(f"   ❌ Gemini error: {response.status_code}")
                print(f"      {response.text[:200]}")
                return False
    except Exception as e:
        print(f"   ❌ Connection error: {e}")
        return False

async def test_2_search_term_generation():
    """Test 2: Search term generation (Qwen via ollama)"""
    print("\n" + "="*60)
    print("TEST 2: Search Term Generation (Qwen + specificity check)")
    print("="*60)
    
    from utils.search_term_generator import SearchTermGenerator
    
    test_cases = [
        # (title, description, expected_result)
        ("GTX 1070 Ti GPU", "ASUS ROG Strix card", "should pass"),
        ("Hostel DVD", "Unrated widescreen edition", "should pass (media)"),
        ("Free stuff", "Random junk clearing out", "should fail (vague)"),
    ]
    
    gen = SearchTermGenerator()
    passed = 0
    
    try:
        for title, desc, expected in test_cases:
            print(f"\n   Testing: '{title}'")
            result = await gen.generate_search_term(title, desc, image_url=None)
            
            if result:
                print(f"      ✅ Generated: {result}")
                if "should pass" in expected:
                    passed += 1
            else:
                print(f"      ❌ Dropped (no specific term)")
                if "should fail" in expected:
                    passed += 1
        
        await gen.close()
        print(f"\n   Result: {passed}/{len(test_cases)} as expected")
        return passed >= 2  # Allow 1 failure
        
    except Exception as e:
        print(f"   ❌ Error: {e}")
        await gen.close()
        return False

async def test_3_ebay_scraper():
    """Test 3: eBay scraper (stealth browser)"""
    print("\n" + "="*60)
    print("TEST 3: eBay Scraper (browser-based)")
    print("="*60)
    
    from scrapers.ebay_scraper import EbayScraper
    
    scraper = EbayScraper(headless=True)
    
    try:
        print("   Searching eBay for: 'GTX 1070 Ti'...")
        results = await scraper.search_sold_items("GTX 1070 Ti")
        
        if results and results.num_sold > 0:
            print(f"   ✅ Found {results.num_sold} sold items")
            print(f"      First: {results.recent_sales[0].title[:50]}... ${results.recent_sales[0].total_price:.2f}")
            print(f"      Average: ${results.avg_sold_price:.2f}")
            await scraper._close_browser()
            return True
        else:
            print("   ❌ No results found")
            await scraper._close_browser()
            return False
            
    except Exception as e:
        print(f"   ❌ Error: {e}")
        import traceback
        traceback.print_exc()
        try:
            await scraper._close_browser()
        except:
            pass
        return False

async def test_4_ai_matcher():
    """Test 4: AI Matcher (the fixed component)"""
    print("\n" + "="*60)
    print("TEST 4: AI Item Matcher (holistic synthesis)")
    print("="*60)
    
    from utils.ai_matcher import AIItemMatcher
    
    matcher = AIItemMatcher(match_threshold=0.5)
    
    test_cases = [
        # (fb_title, fb_desc, ebay_title, should_match)
        (
            "ASUS GTX 1070 Ti GPU", 
            "ROG Strix gaming graphics card",
            "ASUS ROG Strix GTX 1070 Ti 8GB Gaming Graphics Card",
            True
        ),
        (
            "DDR4 RAM 16GB",
            "Desktop memory",
            "Dodge Ram 1500 Tailgate",
            False
        ),
    ]
    
    passed = 0
    
    try:
        for fb_title, fb_desc, ebay_title, should_match in test_cases:
            print(f"\n   FB: '{fb_title}' vs eBay: '{ebay_title[:30]}...'")
            
            result = await matcher.compare_listings(
                fb_title=fb_title,
                fb_description=fb_desc,
                fb_image_url=None,
                ebay_title=ebay_title,
                ebay_description="",
                ebay_image_url=None
            )
            
            match_str = "MATCH" if result.is_match else "NO MATCH"
            expected_str = "MATCH" if should_match else "NO MATCH"
            
            if result.is_match == should_match:
                print(f"      ✅ {match_str} ({result.confidence:.0%}) - correct!")
                passed += 1
            else:
                print(f"      ❌ Got {match_str} but expected {expected_str}")
            
            print(f"      FB synthesis: {result.fb_synthesis[:60]}...")
            print(f"      eBay synthesis: {result.ebay_synthesis[:60]}...")
        
        await matcher.close()
        print(f"\n   Result: {passed}/{len(test_cases)} correct")
        return passed == len(test_cases)
        
    except Exception as e:
        print(f"   ❌ Error: {e}")
        import traceback
        traceback.print_exc()
        await matcher.close()
        return False

async def main():
    print("🔍 SCRAPEDFACE DIAGNOSTIC TEST")
    print("=" * 60)
    print("Testing each component independently...")
    
    results = {}
    
    # Test 1: Gemini API
    results["Gemini API"] = await test_1_gemini_basic()
    
    if not results["Gemini API"]:
        print("\n⚠️ Gemini API failed - some tests may not work")
    
    # Test 2: Search term generation
    results["Search Terms"] = await test_2_search_term_generation()
    
    # Test 3: eBay scraper (requires browser)
    results["eBay Scraper"] = await test_3_ebay_scraper()
    
    # Test 4: AI Matcher
    if results["Gemini API"]:
        results["AI Matcher"] = await test_4_ai_matcher()
    else:
        print("\n⏭️ Skipping AI Matcher (Gemini not working)")
        results["AI Matcher"] = None
    
    # Summary
    print("\n" + "=" * 60)
    print("📊 SUMMARY")
    print("=" * 60)
    
    for name, passed in results.items():
        if passed is True:
            print(f"   ✅ {name}")
        elif passed is False:
            print(f"   ❌ {name}")
        else:
            print(f"   ⏭️ {name} (skipped)")
    
    failed = [k for k, v in results.items() if v is False]
    if failed:
        print(f"\n❌ Failed: {', '.join(failed)}")
        print("   Fix these before running full scan")
    else:
        print("\n✅ All tests passed! Ready for full scan.")

if __name__ == "__main__":
    asyncio.run(main())
