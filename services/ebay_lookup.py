"""
eBay Price Lookup Service

Uses eBay Browse API to find sold/completed listings for price reference.
"""
import asyncio
import httpx
import base64
import re
from typing import Optional
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class EbayPriceResult:
    """Result from eBay price lookup"""
    query: str
    avg_sold_price: float
    median_sold_price: float
    min_price: float
    max_price: float
    num_sold: int
    recent_sales: list[dict]
    lookup_time: str
    
    def __str__(self):
        return f"eBay: ${self.avg_sold_price:.2f} avg (n={self.num_sold})"


class EbayClient:
    """eBay API client for price lookups"""
    
    def __init__(self, app_id: str, cert_id: str = "", dev_id: str = ""):
        self.app_id = app_id
        self.cert_id = cert_id
        self.dev_id = dev_id
        self.access_token = None
        self.token_expires = None
        
        # API endpoints
        self.auth_url = "https://api.ebay.com/identity/v1/oauth2/token"
        self.browse_url = "https://api.ebay.com/buy/browse/v1"
        self.finding_url = "https://svcs.ebay.com/services/search/FindingService/v1"
    
    async def get_oauth_token(self) -> str:
        """Get OAuth token for Browse API"""
        if self.access_token and self.token_expires and datetime.now() < self.token_expires:
            return self.access_token
        
        # Client credentials flow
        credentials = base64.b64encode(f"{self.app_id}:{self.cert_id}".encode()).decode()
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self.auth_url,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Authorization": f"Basic {credentials}"
                },
                data={
                    "grant_type": "client_credentials",
                    "scope": "https://api.ebay.com/oauth/api_scope"
                }
            )
            
            if resp.status_code != 200:
                raise Exception(f"OAuth failed: {resp.status_code} {resp.text}")
            
            data = resp.json()
            self.access_token = data["access_token"]
            expires_in = data.get("expires_in", 7200)
            self.token_expires = datetime.now() + timedelta(seconds=expires_in - 60)
            
            return self.access_token
    
    async def search_sold_items(self, query: str, limit: int = 50) -> Optional[EbayPriceResult]:
        """
        Search for sold/completed items to get market price.
        Uses the Finding API's findCompletedItems.
        """
        # Clean up query
        query = re.sub(r'[^\w\s-]', '', query)
        query = ' '.join(query.split()[:10])  # Max 10 words
        
        params = {
            "OPERATION-NAME": "findCompletedItems",
            "SERVICE-VERSION": "1.0.0",
            "SECURITY-APPNAME": self.app_id,
            "RESPONSE-DATA-FORMAT": "JSON",
            "REST-PAYLOAD": "",
            "keywords": query,
            "itemFilter(0).name": "SoldItemsOnly",
            "itemFilter(0).value": "true",
            "itemFilter(1).name": "Condition",
            "itemFilter(1).value": "Used",  # Focus on used items
            "sortOrder": "EndTimeSoonest",
            "paginationInput.entriesPerPage": str(limit),
        }
        
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(self.finding_url, params=params)
                
                if resp.status_code != 200:
                    print(f"eBay API error: {resp.status_code}")
                    return None
                
                data = resp.json()
                
            # Parse response
            result = data.get("findCompletedItemsResponse", [{}])[0]
            search_result = result.get("searchResult", [{}])[0]
            items = search_result.get("item", [])
            
            if not items:
                print(f"No sold items found for: {query}")
                return None
            
            # Extract prices
            prices = []
            recent_sales = []
            
            for item in items:
                try:
                    selling_status = item.get("sellingStatus", [{}])[0]
                    price_info = selling_status.get("currentPrice", [{}])[0]
                    price = float(price_info.get("__value__", 0))
                    
                    if price > 0:
                        prices.append(price)
                        
                        recent_sales.append({
                            "title": item.get("title", [""])[0],
                            "price": price,
                            "end_time": item.get("listingInfo", [{}])[0].get("endTime", [""])[0],
                            "condition": item.get("condition", [{}])[0].get("conditionDisplayName", [""])[0] if item.get("condition") else "",
                            "url": item.get("viewItemURL", [""])[0],
                        })
                except (KeyError, IndexError, ValueError):
                    continue
            
            if not prices:
                return None
            
            prices.sort()
            median_idx = len(prices) // 2
            
            return EbayPriceResult(
                query=query,
                avg_sold_price=sum(prices) / len(prices),
                median_sold_price=prices[median_idx],
                min_price=min(prices),
                max_price=max(prices),
                num_sold=len(prices),
                recent_sales=recent_sales[:10],
                lookup_time=datetime.now().isoformat()
            )
            
        except Exception as e:
            print(f"eBay lookup error: {e}")
            return None
    
    async def search_active_listings(self, query: str, limit: int = 20) -> list[dict]:
        """Search active listings (for comparison)"""
        token = await self.get_oauth_token()
        
        query = re.sub(r'[^\w\s-]', '', query)
        
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{self.browse_url}/item_summary/search",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-EBAY-C-MARKETPLACE-ID": "EBAY_US"
                },
                params={
                    "q": query,
                    "limit": limit,
                    "filter": "buyingOptions:{FIXED_PRICE}"
                }
            )
            
            if resp.status_code != 200:
                print(f"eBay search error: {resp.status_code}")
                return []
            
            data = resp.json()
            return data.get("itemSummaries", [])


# Simple fallback using web scraping if API not available
async def scrape_ebay_sold_prices(query: str, limit: int = 20) -> Optional[EbayPriceResult]:
    """
    Fallback: Scrape eBay sold listings page.
    Use this if you don't have API access.
    """
    import urllib.parse
    
    encoded_query = urllib.parse.quote(query)
    url = f"https://www.ebay.com/sch/i.html?_nkw={encoded_query}&LH_Complete=1&LH_Sold=1&_sop=13"
    
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
            
        # Extract prices from sold listings
        # Pattern: $XXX.XX format
        price_pattern = r'\$([\d,]+\.\d{2})'
        matches = re.findall(price_pattern, html)
        
        prices = []
        for match in matches[:limit]:
            try:
                price = float(match.replace(',', ''))
                if 0 < price < 50000:  # Sanity check
                    prices.append(price)
            except ValueError:
                continue
        
        if not prices:
            return None
        
        prices.sort()
        median_idx = len(prices) // 2
        
        return EbayPriceResult(
            query=query,
            avg_sold_price=sum(prices) / len(prices),
            median_sold_price=prices[median_idx],
            min_price=min(prices),
            max_price=max(prices),
            num_sold=len(prices),
            recent_sales=[],  # Can't get details from scrape
            lookup_time=datetime.now().isoformat()
        )
        
    except Exception as e:
        print(f"eBay scrape error: {e}")
        return None


async def get_ebay_price(query: str, app_id: str = "", cert_id: str = "") -> Optional[EbayPriceResult]:
    """
    Get eBay sold price for an item.
    Uses API if credentials provided, falls back to scraping.
    """
    if app_id:
        client = EbayClient(app_id, cert_id)
        result = await client.search_sold_items(query)
        if result:
            return result
    
    # Fallback to scraping
    return await scrape_ebay_sold_prices(query)


if __name__ == "__main__":
    async def test():
        # Test without API (scraping fallback)
        result = await scrape_ebay_sold_prices("nintendo switch oled")
        if result:
            print(f"Query: {result.query}")
            print(f"Average sold: ${result.avg_sold_price:.2f}")
            print(f"Median: ${result.median_sold_price:.2f}")
            print(f"Range: ${result.min_price:.2f} - ${result.max_price:.2f}")
            print(f"Sample size: {result.num_sold}")
        else:
            print("No results")
    
    asyncio.run(test())
