"""
Unified Price Lookup Service

Abstracts eBay scraping and PriceCharting lookups into a single interface.
Uses stealth browser for eBay (no API needed).
Uses AI vision model to verify product matches.
"""
import asyncio
from dataclasses import dataclass
from typing import Optional, Literal
import sys
sys.path.insert(0, str(__file__).rsplit('/', 2)[0])

from scrapers.ebay_scraper import EbayScraper, EbayPriceResult, EbaySoldItem, get_ebay_price
from services.pricecharting_lookup import get_pricecharting_price, PriceChartingResult
from utils.ai_matcher import AIItemMatcher, MatchResult
from utils.title_identifier import TitleIdentifier, IdentifiedProduct
from utils.search_term_generator import SearchTermGenerator, SearchTermResult, MultiItemResult


@dataclass
class PriceLookupResult:
    """Unified price lookup result"""
    query: str
    source: str
    avg_price: float
    median_price: float
    min_price: float
    max_price: float
    sample_size: int
    details: Optional[dict] = None
    
    def __str__(self):
        return f"{self.source}: ${self.avg_price:.2f} avg (n={self.sample_size})"


class PriceLookupService:
    """
    Unified price lookup service.
    
    Supports:
    - eBay sold listings (via stealth browser)
    - PriceCharting API (for games/collectibles)
    - AI-verified matching (uses vision model to confirm product matches)
    """
    
    def __init__(
        self,
        stealth_browser_path: str = None,
        pricecharting_api_key: str = "",
        headless: bool = True,
        ebay_condition: str = "used",
        use_ai_matching: bool = True,
        ai_min_confidence: float = 0.6
    ):
        # Auto-detect stealth browser if not provided
        if stealth_browser_path is None:
            from utils.paths import find_stealth_browser
            stealth_browser_path = find_stealth_browser()
        self.stealth_browser_path = stealth_browser_path
        self.pricecharting_api_key = pricecharting_api_key
        self.headless = headless
        self.ebay_condition = ebay_condition
        self.use_ai_matching = use_ai_matching
        self.ai_min_confidence = ai_min_confidence
        
        # Reuse scraper instance for batch lookups
        self._ebay_scraper = None
        self._ai_matcher = None
        self._title_identifier = None
        self._search_term_generator = None
    
    async def _get_search_term_generator(self) -> SearchTermGenerator:
        """Get or create search term generator instance (uses Gemini for vision)"""
        if self._search_term_generator is None:
            self._search_term_generator = SearchTermGenerator()
        return self._search_term_generator
    
    async def _get_title_identifier(self) -> TitleIdentifier:
        """Get or create title identifier instance"""
        if self._title_identifier is None:
            self._title_identifier = TitleIdentifier()
        return self._title_identifier
    
    async def _get_ai_matcher(self) -> AIItemMatcher:
        """Get or create AI matcher instance"""
        if self._ai_matcher is None:
            self._ai_matcher = AIItemMatcher(
                match_threshold=self.ai_min_confidence
            )
        return self._ai_matcher
    
    async def close(self):
        """Close any open connections"""
        if self._ai_matcher:
            await self._ai_matcher.close()
            self._ai_matcher = None
        if self._title_identifier:
            await self._title_identifier.close()
            self._title_identifier = None
        if self._search_term_generator:
            await self._search_term_generator.close()
            self._search_term_generator = None
    
    async def lookup_ebay_smart(
        self,
        fb_title: str,
        fb_description: str = "",
        fb_image_url: Optional[str] = None,
        condition: Optional[str] = None,
        max_search_queries: int = 3,
        max_candidates_per_query: int = 5,
        skip_ai_verification: bool = False  # Skip AI match verification (trust search term)
    ) -> Optional[PriceLookupResult]:
        """
        Smart eBay lookup with LLM-powered title identification.
        
        Full pipeline:
        1. Use vision model to identify product from image
        2. Use text LLM to generate optimal search title + variations
        3. Search eBay with multiple query variations
        4. Use AI matcher to verify results
        5. Return prices from verified matches only
        
        Args:
            fb_title: Original FB Marketplace title
            fb_description: FB listing description
            fb_image_url: FB listing image URL
            condition: eBay condition filter
            max_search_queries: How many query variations to try
            max_candidates_per_query: Max eBay results to check per query
            
        Returns:
            PriceLookupResult with verified matches, or None
        """
        cond = condition or self.ebay_condition
        
        # Step 1: Generate search terms (handles multi-item listings)
        generator = await self._get_search_term_generator()
        multi_result = await generator.generate_search_terms_multi(
            title=fb_title,
            description=fb_description,
            image_url=fb_image_url
        )
        
        # Get valid items (ones that weren't dropped)
        valid_items = multi_result.valid_items
        
        if not valid_items:
            print(f"   🚫 All items dropped - no searchable products identified")
            return None
        
        if multi_result.is_multi_item:
            print(f"   📦 Multi-item listing: {len(valid_items)} searchable items found")
        
        # Search for each valid item and combine results
        all_ebay_results = []
        search_queries = []
        best_term_result = valid_items[0]  # Track for source info
        
        for item_result in valid_items:
            search_queries.append(item_result.search_term)
        
        print(f"   🔎 Searching eBay for {len(search_queries)} term(s)...")
        
        # Step 2: Search eBay with each query variation
        scraper = EbayScraper(
            stealth_browser_path=self.stealth_browser_path,
            headless=self.headless
        )
        
        all_results: list[EbaySoldItem] = []
        seen_urls = set()
        
        for i, query in enumerate(search_queries):
            print(f"      [{i+1}/{len(search_queries)}] Searching: {query}")
            result = await scraper.search_sold_items(query, condition=cond)
            
            if result and result.recent_sales:
                for item in result.recent_sales[:max_candidates_per_query]:
                    if item.url not in seen_urls:
                        all_results.append(item)
                        seen_urls.add(item.url)
        
        if not all_results:
            print(f"   ⚠️ No eBay results found for any query")
            return None
        
        print(f"   📦 Found {len(all_results)} unique eBay results")
        
        # Step 3: Verify each result with AI matcher (optional)
        # Skip verification if search term came from image (Gemini already verified)
        # or if explicitly disabled
        should_skip_verification = skip_ai_verification or best_term_result.source == "image"
        
        if should_skip_verification:
            print(f"   ⏭️ Skipping AI verification (search term from {best_term_result.source})")
            verified_items = all_results[:max_candidates_per_query]
        else:
            matcher = await self._get_ai_matcher()
            verified_items: list[EbaySoldItem] = []
            
            print(f"   🤖 AI-verifying results...")
            
            for ebay_item in all_results[:max_candidates_per_query * max_search_queries]:
                match_result = await matcher.compare_listings(
                    fb_title=fb_title,
                    fb_description=fb_description,
                    fb_image_url=fb_image_url,
                    ebay_title=ebay_item.title,
                    ebay_description="",
                    ebay_image_url=ebay_item.image_url
                )
                
                if match_result.is_match:
                    print(f"      ✅ {match_result.confidence:.0%}: {ebay_item.title[:40]}...")
                    verified_items.append(ebay_item)
                # Only print rejections at verbose level
        
        if not verified_items:
            print(f"   ⚠️ No verified matches found")
            return None
        
        # Step 4: Calculate stats from verified items
        prices = [item.total_price for item in verified_items]
        prices.sort()
        median_idx = len(prices) // 2
        
        return PriceLookupResult(
            query=best_term_result.search_term,
            source=f"eBay Smart (n={len(verified_items)})",
            avg_price=sum(prices) / len(prices),
            median_price=prices[median_idx],
            min_price=min(prices),
            max_price=max(prices),
            sample_size=len(verified_items),
            details={
                "original_title": fb_title,
                "search_term": best_term_result.search_term,
                "search_source": best_term_result.source,
                "search_queries": search_queries,
                "verified_sales": [
                    {
                        "title": s.title,
                        "price": s.total_price,
                        "condition": s.condition,
                        "url": s.url
                    }
                    for s in verified_items[:5]
                ],
                "total_candidates": len(all_results),
                "verification_method": "LLM title + AI vision"
            }
        )
    
    async def lookup_ebay_with_ai(
        self,
        fb_title: str,
        fb_description: str = "",
        fb_image_url: Optional[str] = None,
        condition: Optional[str] = None,
        max_candidates: int = 10
    ) -> Optional[PriceLookupResult]:
        """
        Look up eBay sold prices with AI verification (legacy method).
        
        Uses the FB listing's raw title and verifies each eBay result.
        For better results, use lookup_ebay_smart() which generates
        optimal search queries first.
        
        Args:
            fb_title: Original FB Marketplace title
            fb_description: FB listing description (optional)
            fb_image_url: FB listing image URL (for vision matching)
            condition: eBay condition filter
            max_candidates: Max eBay results to check with AI
            
        Returns:
            PriceLookupResult with only verified matches, or None
        """
        cond = condition or self.ebay_condition
        
        # Search eBay with raw title (no cleaning!)
        scraper = EbayScraper(
            stealth_browser_path=self.stealth_browser_path,
            headless=self.headless
        )
        
        result = await scraper.search_sold_items(fb_title, condition=cond)
        
        if not result or not result.recent_sales:
            return None
        
        # Get AI matcher
        matcher = await self._get_ai_matcher()
        
        # Verify each eBay result is actually a match
        verified_items: list[EbaySoldItem] = []
        
        print(f"   🤖 AI-verifying up to {min(max_candidates, len(result.recent_sales))} eBay results...")
        
        for i, ebay_item in enumerate(result.recent_sales[:max_candidates]):
            match_result = await matcher.compare_listings(
                fb_title=fb_title,
                fb_description=fb_description,
                fb_image_url=fb_image_url,
                ebay_title=ebay_item.title,
                ebay_description="",
                ebay_image_url=ebay_item.image_url
            )
            
            if match_result.is_match:
                print(f"      ✅ {match_result.confidence:.0%} match: {ebay_item.title[:40]}...")
                verified_items.append(ebay_item)
            else:
                print(f"      ❌ {match_result.confidence:.0%}: {match_result.reasoning[:50]}")
        
        if not verified_items:
            print(f"   ⚠️ No verified matches found")
            return None
        
        # Calculate stats from verified items only
        prices = [item.total_price for item in verified_items]
        prices.sort()
        median_idx = len(prices) // 2
        
        return PriceLookupResult(
            query=fb_title,
            source=f"eBay AI-Verified (n={len(verified_items)})",
            avg_price=sum(prices) / len(prices),
            median_price=prices[median_idx],
            min_price=min(prices),
            max_price=max(prices),
            sample_size=len(verified_items),
            details={
                "verified_sales": [
                    {
                        "title": s.title,
                        "price": s.total_price,
                        "condition": s.condition,
                        "url": s.url,
                        "image_url": s.image_url
                    }
                    for s in verified_items[:5]
                ],
                "total_candidates": len(result.recent_sales),
                "verification_method": "AI vision + text"
            }
        )
    
    async def lookup_ebay(
        self,
        query: str,
        condition: Optional[str] = None,
        fb_image_url: Optional[str] = None
    ) -> Optional[PriceLookupResult]:
        """
        Look up sold prices on eBay.
        
        If AI matching is enabled, uses the smart lookup which:
        1. Identifies the product using LLM
        2. Generates optimal search queries
        3. Verifies results with vision model
        
        Otherwise falls back to simple lookup.
        
        Args:
            query: Search query (raw title from FB)
            condition: Override default condition ("used", "new", "any")
            fb_image_url: FB listing image for AI matching
            
        Returns:
            PriceLookupResult or None if not found
        """
        # Use smart AI matching if enabled
        if self.use_ai_matching:
            return await self.lookup_ebay_smart(
                fb_title=query,
                fb_image_url=fb_image_url,
                condition=condition
            )
        
        # Fallback: simple eBay lookup (no AI verification)
        cond = condition or self.ebay_condition
        
        scraper = EbayScraper(
            stealth_browser_path=self.stealth_browser_path,
            headless=self.headless
        )
        
        result = await scraper.search_sold_items(query, condition=cond)
        
        if not result:
            return None
        
        return PriceLookupResult(
            query=query,
            source=f"eBay Sold (n={result.num_sold})",
            avg_price=result.avg_sold_price,
            median_price=result.median_sold_price,
            min_price=result.min_price,
            max_price=result.max_price,
            sample_size=result.num_sold,
            details={
                "recent_sales": [
                    {
                        "title": s.title,
                        "price": s.total_price,
                        "condition": s.condition,
                        "url": s.url
                    }
                    for s in result.recent_sales[:5]
                ]
            }
        )
    
    async def lookup_pricecharting(
        self,
        query: str,
        price_type: str = "cib"  # "loose", "cib", "new"
    ) -> Optional[PriceLookupResult]:
        """
        Look up prices on PriceCharting.
        
        Args:
            query: Game/item name
            price_type: Which price to use as avg
            
        Returns:
            PriceLookupResult or None if not found
        """
        if not self.pricecharting_api_key:
            return None
        
        result = await get_pricecharting_price(query, self.pricecharting_api_key)
        
        if not result:
            return None
        
        # Pick price based on type
        if price_type == "loose":
            avg_price = result.loose_price
        elif price_type == "new":
            avg_price = result.new_price
        else:
            avg_price = result.cib_price if result.cib_price > 0 else result.loose_price
        
        if avg_price <= 0:
            return None
        
        return PriceLookupResult(
            query=query,
            source=f"PriceCharting ({result.console})",
            avg_price=avg_price,
            median_price=avg_price,  # PC doesn't give median
            min_price=result.loose_price,
            max_price=result.new_price if result.new_price > 0 else result.cib_price,
            sample_size=1,  # PC doesn't give sample size
            details={
                "console": result.console,
                "loose_price": result.loose_price,
                "cib_price": result.cib_price,
                "new_price": result.new_price,
                "url": result.url
            }
        )
    
    async def lookup(
        self,
        query: str,
        sources: list[str] = ["ebay"],
        stop_on_first: bool = True,
        fb_image_url: Optional[str] = None
    ) -> Optional[PriceLookupResult]:
        """
        Look up price using specified sources.
        
        Args:
            query: Search query (raw title for AI matching)
            sources: List of sources to try ("ebay", "pricecharting")
            stop_on_first: Stop after first successful lookup
            fb_image_url: FB listing image URL for AI verification
            
        Returns:
            First successful PriceLookupResult or None
        """
        for source in sources:
            result = None
            
            if source == "ebay":
                result = await self.lookup_ebay(query, fb_image_url=fb_image_url)
            elif source == "pricecharting":
                result = await self.lookup_pricecharting(query)
            
            if result and stop_on_first:
                return result
        
        return None


async def quick_ebay_lookup(query: str, headless: bool = True) -> Optional[PriceLookupResult]:
    """
    Quick eBay lookup function for one-off queries.
    """
    service = PriceLookupService(headless=headless)
    return await service.lookup_ebay(query)


if __name__ == "__main__":
    async def test():
        service = PriceLookupService(headless=False)
        
        query = input("🔍 Search query: ").strip() or "nintendo switch oled"
        
        print(f"\n📡 Looking up: {query}")
        result = await service.lookup_ebay(query)
        
        if result:
            print(f"\n✅ {result}")
            print(f"   Median: ${result.median_price:.2f}")
            print(f"   Range: ${result.min_price:.2f} - ${result.max_price:.2f}")
        else:
            print("❌ No results found")
    
    asyncio.run(test())
