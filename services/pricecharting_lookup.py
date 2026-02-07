"""
PriceCharting API for video games and collectibles pricing.

API Docs: https://www.pricecharting.com/api-documentation
Free tier: 500 requests/day
"""
import asyncio
import httpx
from typing import Optional
from dataclasses import dataclass


@dataclass
class PriceChartingResult:
    """Result from PriceCharting lookup"""
    query: str
    product_name: str
    console: str
    loose_price: float  # Game only, no case/manual
    cib_price: float    # Complete in box
    new_price: float    # Factory sealed
    graded_price: Optional[float]
    product_id: str
    
    def __str__(self):
        return f"PC: {self.product_name} ({self.console}) - Loose: ${self.loose_price:.2f}, CIB: ${self.cib_price:.2f}"


class PriceChartingClient:
    """PriceCharting API client"""
    
    BASE_URL = "https://www.pricecharting.com/api"
    
    def __init__(self, api_key: str):
        self.api_key = api_key
    
    async def search(self, query: str) -> list[dict]:
        """Search for products matching query"""
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{self.BASE_URL}/products",
                params={
                    "t": self.api_key,
                    "q": query,
                    "type": "json"
                }
            )
            
            if resp.status_code != 200:
                print(f"PriceCharting error: {resp.status_code}")
                return []
            
            data = resp.json()
            return data.get("products", [])
    
    async def get_product(self, product_id: str) -> Optional[dict]:
        """Get detailed pricing for a specific product"""
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{self.BASE_URL}/product",
                params={
                    "t": self.api_key,
                    "id": product_id,
                    "type": "json"
                }
            )
            
            if resp.status_code != 200:
                return None
            
            return resp.json()
    
    async def lookup_price(self, query: str) -> Optional[PriceChartingResult]:
        """Search and get price for best matching product"""
        products = await self.search(query)
        
        if not products:
            return None
        
        # Get the first (best) match
        product = products[0]
        
        # Parse prices (returned in cents)
        def parse_price(val) -> float:
            if not val:
                return 0.0
            try:
                return float(val) / 100.0
            except (ValueError, TypeError):
                return 0.0
        
        return PriceChartingResult(
            query=query,
            product_name=product.get("product-name", ""),
            console=product.get("console-name", ""),
            loose_price=parse_price(product.get("loose-price")),
            cib_price=parse_price(product.get("cib-price")),
            new_price=parse_price(product.get("new-price")),
            graded_price=parse_price(product.get("graded-price")) if product.get("graded-price") else None,
            product_id=str(product.get("id", ""))
        )


# Fallback scraper if no API key
async def scrape_pricecharting(query: str) -> Optional[PriceChartingResult]:
    """
    Scrape PriceCharting search results.
    Use as fallback if no API key.
    """
    import urllib.parse
    import re
    
    encoded_query = urllib.parse.quote(query)
    url = f"https://www.pricecharting.com/search-products?q={encoded_query}"
    
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                }
            )
            
            if resp.status_code != 200:
                return None
            
            html = resp.text
        
        # Extract first product row
        # Looking for price cells with class like "price js-price"
        # Prices are in format $XX.XX
        
        # Find product name
        name_match = re.search(r'<a[^>]*class="[^"]*product_name[^"]*"[^>]*>([^<]+)</a>', html)
        product_name = name_match.group(1).strip() if name_match else query
        
        # Find console/platform
        console_match = re.search(r'<td[^>]*class="[^"]*console[^"]*"[^>]*>([^<]+)</td>', html)
        console = console_match.group(1).strip() if console_match else ""
        
        # Find prices
        price_pattern = r'\$(\d+\.?\d*)'
        prices = re.findall(price_pattern, html)[:5]  # Get first few prices
        
        if len(prices) >= 2:
            loose = float(prices[0]) if prices[0] else 0
            cib = float(prices[1]) if len(prices) > 1 else loose
            new = float(prices[2]) if len(prices) > 2 else cib
            
            return PriceChartingResult(
                query=query,
                product_name=product_name,
                console=console,
                loose_price=loose,
                cib_price=cib,
                new_price=new,
                graded_price=None,
                product_id=""
            )
        
        return None
        
    except Exception as e:
        print(f"PriceCharting scrape error: {e}")
        return None


async def get_pricecharting_price(query: str, api_key: str = "") -> Optional[PriceChartingResult]:
    """
    Get PriceCharting price for a game/collectible.
    Uses API if key provided, falls back to scraping.
    """
    if api_key:
        client = PriceChartingClient(api_key)
        result = await client.lookup_price(query)
        if result:
            return result
    
    # Fallback to scraping
    return await scrape_pricecharting(query)


if __name__ == "__main__":
    async def test():
        # Test scraping fallback
        result = await scrape_pricecharting("zelda breath of the wild switch")
        if result:
            print(f"Product: {result.product_name}")
            print(f"Console: {result.console}")
            print(f"Loose: ${result.loose_price:.2f}")
            print(f"CIB: ${result.cib_price:.2f}")
            print(f"New: ${result.new_price:.2f}")
        else:
            print("No results")
    
    asyncio.run(test())
