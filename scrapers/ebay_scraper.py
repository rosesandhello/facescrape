"""
eBay Sold Listings Scraper

Uses stealth browser to scrape eBay sold/completed listings.
Replaces API approach - no credentials needed.
"""
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", message=".*pydantic.*")

import logging
logging.getLogger("fastmcp").setLevel(logging.ERROR)
logging.getLogger("mcp").setLevel(logging.ERROR)
logging.getLogger("websockets").setLevel(logging.ERROR)

import asyncio
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

sys.path.append('..')
from utils.stealth_helpers import (
    human_delay, page_load_delay, scroll_delay, get_stealth_spawn_options,
    _random_mouse_move, simulate_human_browsing, get_random_typing_delay_ms,
    type_like_human
)


@dataclass
class EbaySoldItem:
    """Individual sold item from eBay"""
    title: str
    price: float
    shipping: float = 0.0
    total_price: float = 0.0
    condition: str = ""
    sold_date: str = ""
    url: str = ""
    image_url: str = ""  # For AI image matching
    
    def __post_init__(self):
        if self.total_price == 0.0:
            self.total_price = self.price + self.shipping


@dataclass
class EbayPriceResult:
    """Aggregated price data from eBay sold listings"""
    query: str
    avg_sold_price: float
    median_sold_price: float
    min_price: float
    max_price: float
    num_sold: int
    recent_sales: list[EbaySoldItem]
    lookup_time: str
    
    def __str__(self):
        return f"eBay: ${self.avg_sold_price:.2f} avg (n={self.num_sold})"


class EbayScraper:
    """Stealth eBay scraper using MCP browser automation"""
    
    def __init__(
        self,
        stealth_browser_path: str = None,
        user_data_dir: str = None,
        headless: bool = True
    ):
        # Auto-detect paths if not provided
        if stealth_browser_path is None:
            from utils.paths import find_stealth_browser
            stealth_browser_path = find_stealth_browser()
        if user_data_dir is None:
            user_data_dir = "/tmp/scrapedface-ebay-profile"
        self.stealth_browser_path = stealth_browser_path
        # Use unique profile each time to avoid fingerprint tracking
        import time
        self.user_data_dir = f"{user_data_dir}-{int(time.time())}"
        self.headless = headless
        self.session = None
        self.instance_id = None
        self._browser_ready = False
    
    def build_sold_url(
        self,
        query: str,
        condition: str = "used",  # "new", "used", "any"
        min_price: Optional[float] = None,
        max_price: Optional[float] = None
    ) -> str:
        """Build eBay sold listings search URL"""
        encoded_query = quote_plus(query)
        url = f"https://www.ebay.com/sch/i.html?_nkw={encoded_query}"
        
        # Sold/Completed items
        url += "&LH_Complete=1&LH_Sold=1"
        
        # Sort by newest
        url += "&_sop=13"
        
        # Condition filter
        if condition == "used":
            url += "&LH_ItemCondition=3000"  # Used
        elif condition == "new":
            url += "&LH_ItemCondition=1000"  # New
        # "any" = no filter
        
        # Price range
        if min_price is not None:
            url += f"&_udlo={min_price:.0f}"
        if max_price is not None:
            url += f"&_udhi={max_price:.0f}"
        
        return url
    
    async def _spawn_browser(self) -> bool:
        """Spawn a browser instance with stealth options"""
        if self._browser_ready and self.instance_id:
            return True
        
        try:
            # Use stealth spawn options (random viewport, etc.)
            stealth_opts = get_stealth_spawn_options(self.user_data_dir, self.headless)
            
            spawn_result = await asyncio.wait_for(
                self.session.call_tool(
                    "spawn_browser",
                    arguments=stealth_opts
                ),
                timeout=30
            )
            
            if hasattr(spawn_result, 'isError') and spawn_result.isError:
                print(f"❌ Browser spawn failed: {spawn_result.content}")
                return False
            
            self.instance_id = self._extract_instance_id(spawn_result)
            if not self.instance_id:
                print("❌ Could not get browser instance_id")
                return False
            
            # Set up stealth hooks
            await self._setup_stealth_hooks()
            
            self._browser_ready = True
            return True
            
        except asyncio.TimeoutError:
            print("❌ Browser spawn timed out")
            return False
        except Exception as e:
            print(f"❌ Browser spawn error: {e}")
            return False
    
    async def _setup_stealth_hooks(self):
        """Set up hooks to block known tracking/fingerprinting scripts"""
        try:
            # Block known eBay tracking endpoints
            tracking_patterns = [
                "*://beacon.walmart.com/*",
                "*://tags.tiqcdn.com/*",
                "*://rover.ebay.com/*",
                "*://www.ebay.com/sch/ajax/*",
            ]
            
            for pattern in tracking_patterns:
                try:
                    await asyncio.wait_for(
                        self.session.call_tool(
                            "create_simple_dynamic_hook",
                            arguments={
                                "name": f"block_{pattern[:20]}",
                                "url_pattern": pattern,
                                "action": "block",
                                "instance_ids": [self.instance_id]
                            }
                        ),
                        timeout=5
                    )
                except:
                    pass  # Hook creation is optional
            
            print("   🛡️ Stealth hooks configured")
        except Exception as e:
            print(f"   ⚠️ Could not set up stealth hooks: {e}")
    
    def _extract_instance_id(self, spawn_result) -> str:
        """Extract instance_id from spawn result"""
        if hasattr(spawn_result, 'structuredContent') and spawn_result.structuredContent:
            return spawn_result.structuredContent.get('instance_id', '')
        
        for item in spawn_result.content:
            if hasattr(item, 'text'):
                try:
                    data = json.loads(item.text)
                    return data.get('instance_id', '')
                except:
                    pass
        
        return ''
    
    async def _get_page_content(self) -> str:
        """Get current page text content. Handles both inline and file-saved responses."""
        try:
            result = await asyncio.wait_for(
                self.session.call_tool(
                    "get_page_content",
                    arguments={"instance_id": self.instance_id}
                ),
                timeout=15
            )
            
            content_str = str(result.content)
            
            # Check if content was saved to file (common for large pages)
            if 'file_path' in content_str:
                # Extract file path from the response
                import re
                match = re.search(r'file_path["\':]+([^"\',\}]+)', content_str)
                if match:
                    file_path = match.group(1).strip()
                    try:
                        with open(file_path, 'r') as f:
                            data = json.load(f)
                            # Return the text content for parsing
                            text = data.get('data', {}).get('text', '')
                            if text:
                                return text
                            # Fallback to HTML
                            html = data.get('data', {}).get('html', '')
                            return html
                    except Exception as e:
                        print(f"   ⚠️ Could not read saved content file: {e}")
            
            return content_str
        except Exception as e:
            print(f"   ⚠️ _get_page_content error: {e}")
            return ""
    
    async def _execute_js(self, script: str) -> str:
        """Execute JavaScript in the page"""
        try:
            result = await asyncio.wait_for(
                self.session.call_tool(
                    "execute_script",
                    arguments={
                        "instance_id": self.instance_id,
                        "script": script
                    }
                ),
                timeout=10
            )
            return str(result.content)
        except:
            return ""
    
    async def _navigate(self, url: str, timeout: int = 30, referrer: str = None) -> bool:
        """Navigate to URL with optional referrer"""
        try:
            args = {
                "instance_id": self.instance_id,
                "url": url,
                "timeout": timeout * 1000,
                "wait_until": "domcontentloaded"  # Don't wait for all resources
            }
            
            # Add referrer for more natural navigation
            if referrer:
                args["referrer"] = referrer
            elif "ebay.com" in url:
                args["referrer"] = "https://www.ebay.com/"
            
            await asyncio.wait_for(
                self.session.call_tool("navigate", arguments=args),
                timeout=timeout + 5
            )
            return True
        except asyncio.TimeoutError:
            # Navigation timeout is okay, page might still be usable
            return True
        except Exception as e:
            print(f"❌ Navigation error: {e}")
            return False
    
    async def _close_browser(self):
        """Close browser instance"""
        if self.instance_id and self.session:
            try:
                await self.session.call_tool(
                    "close_instance",
                    arguments={"instance_id": self.instance_id}
                )
            except:
                pass
        self._browser_ready = False
        self.instance_id = None
    
    async def _scroll_down(self, pixels: int = None):
        """Scroll down the page with smooth behavior"""
        import random
        if pixels is None:
            pixels = int(random.gauss(200, 80))
            pixels = max(80, min(pixels, 400))
        await self._execute_js(f"window.scrollBy({{top: {pixels}, behavior: 'smooth'}});")
        await asyncio.sleep(random.uniform(0.3, 0.8))
    
    async def _human_search_ebay(self, query: str, condition: str = "used") -> bool:
        """Type search query into eBay's search box like a human."""
        import random
        try:
            # eBay search box selectors
            search_selectors = [
                'input#gh-ac',  # eBay's main search input
                'input[type="text"][name="_nkw"]',
                'input[aria-label*="Search"]',
                'input[placeholder*="Search"]',
            ]
            
            # Click the search input
            clicked = False
            for selector in search_selectors:
                try:
                    result = await asyncio.wait_for(
                        self.session.call_tool(
                            "click_element",
                            arguments={
                                "instance_id": self.instance_id,
                                "selector": selector,
                                "timeout": 3000
                            }
                        ),
                        timeout=5
                    )
                    if result and not (hasattr(result, 'isError') and result.isError):
                        clicked = True
                        break
                except:
                    continue
            
            if not clicked:
                print("      ✗ Could not find eBay search input")
                return False
            
            await asyncio.sleep(random.uniform(0.3, 0.7))
            
            # Type the query character by character
            for selector in search_selectors:
                try:
                    success = await type_like_human(
                        self.session, self.instance_id, selector, query, clear_first=True
                    )
                    if success:
                        print(f"      ✓ Typed: {query}")
                        break
                except:
                    continue
            
            await asyncio.sleep(random.uniform(0.2, 0.5))
            
            # Press Enter to search
            await self._execute_js("""
                const input = document.querySelector('#gh-ac') || 
                              document.querySelector('input[name="_nkw"]');
                if (input) {
                    input.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true}));
                    input.dispatchEvent(new KeyboardEvent('keyup', {key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true}));
                }
                // Also submit the form
                const form = input ? input.closest('form') : null;
                if (form) form.submit();
            """)
            
            print("      ✓ Submitted search")
            await asyncio.sleep(random.uniform(2.0, 4.0))
            
            # Now we need to filter to "Sold" items — click the sold filter
            # eBay has a "Sold Items" checkbox in the left sidebar
            await self._click_sold_filter()
            
            return True
        except Exception as e:
            print(f"      ✗ eBay search error: {e}")
            return False
    
    async def _click_sold_filter(self):
        """Click the 'Sold Items' filter on eBay search results."""
        import random
        try:
            # Try clicking "Sold Items" checkbox/link
            sold_js = """
                (() => {
                    // Look for "Sold Items" or "Completed Items" link/checkbox
                    const links = document.querySelectorAll('a, span, label');
                    for (const el of links) {
                        const text = el.textContent.trim().toLowerCase();
                        if (text === 'sold items' || text === 'sold') {
                            el.click();
                            return 'clicked_sold';
                        }
                    }
                    // Try checkbox approach
                    const checkboxes = document.querySelectorAll('input[type="checkbox"]');
                    for (const cb of checkboxes) {
                        const label = cb.closest('label') || cb.parentElement;
                        if (label && label.textContent.toLowerCase().includes('sold')) {
                            cb.click();
                            return 'clicked_checkbox';
                        }
                    }
                    return 'not_found';
                })();
            """
            result = await self._execute_js(sold_js)
            if 'clicked' in str(result):
                print("      ✓ Applied 'Sold Items' filter")
                await asyncio.sleep(random.uniform(2.0, 3.5))
            else:
                print("      ⚠️ Could not find Sold filter — results may include active listings")
        except:
            pass
    
    async def _scroll_results(self):
        """Scroll through eBay results like a human browsing."""
        import random
        spurts = random.randint(2, 4)
        for i in range(spurts):
            amount = int(random.gauss(200, 80))
            amount = max(80, min(amount, 400))
            await self._execute_js(f"window.scrollBy({{top: {amount}, behavior: 'smooth'}});")
            
            if random.random() < 0.3:
                await asyncio.sleep(random.uniform(1.5, 3.5))
            else:
                await asyncio.sleep(random.uniform(0.4, 1.0))
            
            # 10% scroll back
            if random.random() < 0.10:
                back = random.randint(60, 180)
                await self._execute_js(f"window.scrollBy({{top: -{back}, behavior: 'smooth'}});")
                await asyncio.sleep(random.uniform(0.4, 1.0))
            
            # Mouse movement
            if self.session and self.instance_id:
                await _random_mouse_move(self.session, self.instance_id)
    
    def _parse_listings_from_text(self, text: str) -> list[EbaySoldItem]:
        """Parse sold listings from eBay page text content.
        Works with the extracted text when DOM elements aren't available."""
        items = []
        
        # Split by "Sold [date]" pattern - each listing starts with "Sold"
        pattern = r'Sold\s+(?:Feb|Jan|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},?\s*\d{4}'
        blocks = re.split(f'({pattern})', text)
        
        # Process pairs: sold_date + listing content
        i = 1
        while i < len(blocks) - 1:
            sold_date = blocks[i].strip()
            content = blocks[i + 1] if i + 1 < len(blocks) else ""
            
            # Get content until next listing or section break
            content = content.split('Sold Feb')[0].split('Sold Jan')[0].split('Sold Mar')[0]
            
            if len(content) < 20:
                i += 2
                continue
            
            # Extract title (first line after date marker)
            lines = [l.strip() for l in content.split('\n') if l.strip()]
            if not lines:
                i += 2
                continue
            
            # Title is usually first substantial line
            title = ""
            for line in lines[:5]:
                if len(line) > 10 and not line.startswith('Opens in'):
                    title = line
                    break
            
            if not title or len(title) < 5:
                i += 2
                continue
            
            # Find price - look for $ pattern
            price_match = re.search(r'\$([0-9,]+\.[0-9]{2})', content)
            if not price_match:
                i += 2
                continue
            
            try:
                price = float(price_match.group(1).replace(',', ''))
            except ValueError:
                i += 2
                continue
            
            # Sanity check on price
            if price < 1 or price > 50000:
                i += 2
                continue
            
            # Find shipping
            shipping = 0.0
            if 'Free delivery' in content or 'Free shipping' in content.lower():
                shipping = 0.0
            else:
                ship_match = re.search(r'\+\s*\$([0-9,.]+)\s*(?:delivery|shipping)', content)
                if ship_match:
                    try:
                        shipping = float(ship_match.group(1).replace(',', ''))
                    except:
                        pass
            
            items.append(EbaySoldItem(
                title=title,
                price=price,
                shipping=shipping,
                total_price=price + shipping,
                condition="",
                sold_date=sold_date,
                url=""
            ))
            
            i += 2
        
        return items
    
    def _parse_listings_from_html(self, html: str) -> list[EbaySoldItem]:
        """Parse sold listings from eBay HTML"""
        items = []
        
        # First try the new text-based parser on the extracted text
        # This works better with modern eBay page structure
        if 'Sold Feb' in html or 'Sold Jan' in html:
            text_items = self._parse_listings_from_text(html)
            if text_items:
                return text_items
        
        # Fallback to original HTML parsing
        # Extract listing blocks using regex
        # eBay sold listings have specific patterns
        
        # Pattern 1: s-item containers
        # Look for price + title combinations
        
        # Price patterns: $XX.XX or $X,XXX.XX
        price_pattern = r'\$([0-9,]+\.[0-9]{2})'
        
        # Try to find item blocks
        # Each sold item typically has: title, price, shipping, condition
        
        # Split by common item boundaries
        item_blocks = re.split(r'(?=s-item__wrapper|s-item__info|li class="s-item)', html)
        
        for block in item_blocks:
            if len(block) < 100:  # Skip tiny fragments
                continue
            
            # Find price
            prices = re.findall(price_pattern, block)
            if not prices:
                continue
            
            # First price is usually the item price
            try:
                price = float(prices[0].replace(',', ''))
            except ValueError:
                continue
            
            # Sanity check
            if price < 1 or price > 50000:
                continue
            
            # Find shipping (often second price or "+$X.XX shipping")
            shipping = 0.0
            shipping_match = re.search(r'\+\s*\$([0-9,.]+)\s*shipping', block, re.I)
            if shipping_match:
                try:
                    shipping = float(shipping_match.group(1).replace(',', ''))
                except:
                    pass
            
            # Free shipping check
            if 'free shipping' in block.lower():
                shipping = 0.0
            
            # Find title - look for text between quotes or in specific patterns
            title = ""
            # Try to find item title from link text or aria-label
            title_match = re.search(r'role="heading"[^>]*>([^<]+)<', block)
            if title_match:
                title = title_match.group(1).strip()
            
            if not title:
                # Try alt text from images
                alt_match = re.search(r'alt="([^"]+)"', block)
                if alt_match:
                    title = alt_match.group(1).strip()
            
            if not title:
                # Try any reasonable looking title
                title_match = re.search(r'>([A-Z][^<]{20,100})</[^>]+>', block)
                if title_match:
                    title = title_match.group(1).strip()
            
            if not title or len(title) < 5:
                continue
            
            # Find condition
            condition = ""
            cond_match = re.search(r'(Pre-[Oo]wned|Used|New|For parts|Refurbished)', block, re.I)
            if cond_match:
                condition = cond_match.group(1)
            
            # Find URL
            url = ""
            url_match = re.search(r'href="(https://www\.ebay\.com/itm/[^"]+)"', block)
            if url_match:
                url = url_match.group(1)
            
            # Find sold date
            sold_date = ""
            date_match = re.search(r'Sold\s+([A-Z][a-z]{2}\s+\d{1,2},?\s*\d{4})', block)
            if date_match:
                sold_date = date_match.group(1)
            
            items.append(EbaySoldItem(
                title=title,
                price=price,
                shipping=shipping,
                total_price=price + shipping,
                condition=condition,
                sold_date=sold_date,
                url=url
            ))
        
        return items
    
    async def _extract_via_js(self) -> list[EbaySoldItem]:
        """Extract listings using JavaScript DOM queries"""
        items = []
        
        js_script = """
        (() => {
            const items = [];
            // Find all listing items
            const listings = document.querySelectorAll('.s-item');
            
            listings.forEach(item => {
                try {
                    // Get price
                    const priceEl = item.querySelector('.s-item__price');
                    if (!priceEl) return;
                    
                    const priceText = priceEl.textContent;
                    const priceMatch = priceText.match(/\\$([\\d,]+\\.\\d{2})/);
                    if (!priceMatch) return;
                    
                    const price = parseFloat(priceMatch[1].replace(',', ''));
                    if (price < 1 || price > 50000) return;
                    
                    // Get title
                    const titleEl = item.querySelector('.s-item__title');
                    const title = titleEl ? titleEl.textContent.trim() : '';
                    if (!title || title.length < 5 || title.includes('Shop on eBay')) return;
                    
                    // Get shipping
                    let shipping = 0;
                    const shipEl = item.querySelector('.s-item__shipping, .s-item__freeXDays');
                    if (shipEl) {
                        const shipText = shipEl.textContent;
                        if (shipText.toLowerCase().includes('free')) {
                            shipping = 0;
                        } else {
                            const shipMatch = shipText.match(/\\$([\\d,.]+)/);
                            if (shipMatch) {
                                shipping = parseFloat(shipMatch[1].replace(',', ''));
                            }
                        }
                    }
                    
                    // Get condition
                    const condEl = item.querySelector('.SECONDARY_INFO');
                    const condition = condEl ? condEl.textContent.trim() : '';
                    
                    // Get URL
                    const linkEl = item.querySelector('.s-item__link');
                    const url = linkEl ? linkEl.href : '';
                    
                    // Get image URL
                    const imgEl = item.querySelector('.s-item__image-wrapper img');
                    let imageUrl = '';
                    if (imgEl) {
                        // Prefer data-src (lazy load) over src
                        imageUrl = imgEl.dataset.src || imgEl.src || '';
                        // Clean up eBay's thumbnail URL to get larger image
                        if (imageUrl.includes('/thumbs/')) {
                            imageUrl = imageUrl.replace('/thumbs/', '/images/');
                        }
                    }
                    
                    items.push({
                        title: title,
                        price: price,
                        shipping: shipping,
                        total_price: price + shipping,
                        condition: condition,
                        url: url,
                        image_url: imageUrl
                    });
                } catch(e) {}
            });
            
            return JSON.stringify(items);
        })()
        """
        
        result = await self._execute_js(js_script)
        
        # Parse JSON from result
        try:
            json_match = re.search(r'\[.*\]', result, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                for item in data:
                    items.append(EbaySoldItem(
                        title=item.get('title', ''),
                        price=item.get('price', 0),
                        shipping=item.get('shipping', 0),
                        total_price=item.get('total_price', 0),
                        condition=item.get('condition', ''),
                        url=item.get('url', ''),
                        image_url=item.get('image_url', '')
                    ))
        except Exception as e:
            pass
        
        return items
    
    async def search_sold_items(
        self,
        query: str,
        condition: str = "used",
        min_price: Optional[float] = None,
        max_price: Optional[float] = None,
        limit: int = 50,
        max_retries: int = 2
    ) -> Optional[EbayPriceResult]:
        """
        Search for sold/completed items on eBay.
        
        Args:
            query: Search query
            condition: "used", "new", or "any"
            min_price: Minimum price filter
            max_price: Maximum price filter
            limit: Max items to return
            max_retries: Number of retries if first attempt fails
            
        Returns:
            EbayPriceResult with price statistics
        """
        for attempt in range(max_retries + 1):
            result = await self._search_sold_items_impl(
                query, condition, min_price, max_price, limit
            )
            if result:
                return result
            
            if attempt < max_retries:
                print(f"   🔄 Retry {attempt + 1}/{max_retries} after delay...")
                await asyncio.sleep(8)  # Wait before retry
                # Use fresh profile for retry
                import time
                self.user_data_dir = f"/tmp/ebay-retry-{int(time.time())}"
        
        return None
    
    async def _search_sold_items_impl(
        self,
        query: str,
        condition: str = "used",
        min_price: Optional[float] = None,
        max_price: Optional[float] = None,
        limit: int = 50
    ) -> Optional[EbayPriceResult]:
        """Internal implementation of search_sold_items."""
        # Use the stealth browser's virtualenv Python
        stealth_venv_python = self.stealth_browser_path.replace("/src/server.py", "/venv/bin/python")
        
        env = os.environ.copy()
        env["PYTHONWARNINGS"] = "ignore::DeprecationWarning"
        
        server_params = StdioServerParameters(
            command=stealth_venv_python,
            args=["-W", "ignore", self.stealth_browser_path],
            env=env
        )
        
        try:
            async with stdio_client(server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self.session = session
                    
                    # Spawn browser with stealth settings
                    print("🌐 Spawning browser with stealth settings...")
                    if not await self._spawn_browser():
                        return None
                    
                    print(f"✅ Browser ready: {self.instance_id[:8]}...")
                    
                    # Build URL
                    url = self.build_sold_url(query, condition, min_price, max_price)
                    
                    # Visit eBay homepage first — look around like a human
                    print(f"🔍 Searching eBay: {query}")
                    try:
                        await self._navigate("https://www.ebay.com", timeout=15)
                        await page_load_delay()
                        await _random_mouse_move(session, self.instance_id)
                    except:
                        pass
                    
                    # Type the search query into eBay's search box
                    print(f"   🔍 Typing search query...")
                    searched = await self._human_search_ebay(query, condition)
                    
                    if not searched:
                        # Fall back to direct URL if search box fails
                        print(f"   ⚠️ Search box failed, using direct URL...")
                        if not await self._navigate(url, timeout=25):
                            return None
                    
                    # Browse results like a human
                    await page_load_delay()
                    await _random_mouse_move(session, self.instance_id)
                    await self._scroll_results()
                    await simulate_human_browsing(session, self.instance_id)
                    
                    # Get page content (do this before browser connection becomes unstable)
                    print("   📄 Extracting page content...")
                    items = []
                    
                    try:
                        html = await self._get_page_content()
                        content_len = len(html) if html else 0
                        print(f"   📊 Got content: {content_len} chars")
                        
                        # Verify we have search results, not homepage
                        if html and 'Sold' in html and content_len > 10000:
                            items = self._parse_listings_from_html(html)
                            print(f"   📊 Parser returned: {len(items)} items")
                            if items:
                                print(f"   ✅ Text parser found {len(items)} items")
                        elif html and 'Sold' not in html:
                            print("   ⚠️ Page doesn't contain sold listings (wrong page?)")
                        elif content_len < 10000:
                            print("   ⚠️ Content too short, page may not have loaded")
                    except Exception as e:
                        import traceback
                        print(f"   ⚠️ Content extraction failed: {e}")
                        traceback.print_exc()
                    
                    # If text parsing didn't work, try JS extraction
                    if not items:
                        print("   📜 Trying JS extraction...")
                        try:
                            # Light scroll to trigger lazy load
                            await self._scroll_down(300)
                            await asyncio.sleep(1)
                            items = await self._extract_via_js()
                        except Exception as e:
                            print(f"   ⚠️ JS extraction failed: {e}")
                    
                    # Close browser
                    await self._close_browser()
                    
                    if not items:
                        print(f"❌ No sold items found for: {query}")
                        return None
                    
                    # Limit results
                    items = items[:limit]
                    
                    # Calculate stats
                    prices = [item.total_price for item in items]
                    prices.sort()
                    median_idx = len(prices) // 2
                    
                    result = EbayPriceResult(
                        query=query,
                        avg_sold_price=sum(prices) / len(prices),
                        median_sold_price=prices[median_idx],
                        min_price=min(prices),
                        max_price=max(prices),
                        num_sold=len(prices),
                        recent_sales=items[:10],
                        lookup_time=datetime.now().isoformat()
                    )
                    
                    print(f"✅ Found {len(items)} sold items")
                    print(f"   Avg: ${result.avg_sold_price:.2f}")
                    print(f"   Median: ${result.median_sold_price:.2f}")
                    print(f"   Range: ${result.min_price:.2f} - ${result.max_price:.2f}")
                    
                    return result
                    
        except Exception as e:
            print(f"❌ eBay scraper error: {e}")
            import traceback
            traceback.print_exc()
            return None


async def get_ebay_price(
    query: str,
    condition: str = "used",
    headless: bool = True
) -> Optional[EbayPriceResult]:
    """
    Convenience function to get eBay sold price for an item.
    
    Args:
        query: Item search query
        condition: "used", "new", or "any"
        headless: Run browser in headless mode
        
    Returns:
        EbayPriceResult with price statistics
    """
    scraper = EbayScraper(headless=headless)
    return await scraper.search_sold_items(query, condition=condition)


async def main():
    """Test the eBay scraper"""
    query = input("🔍 Search query: ").strip() or "nintendo switch oled"
    
    scraper = EbayScraper(headless=False)  # Non-headless for testing
    result = await scraper.search_sold_items(query, condition="used")
    
    if result:
        print(f"\n📊 Results for: {result.query}")
        print(f"   Average: ${result.avg_sold_price:.2f}")
        print(f"   Median: ${result.median_sold_price:.2f}")
        print(f"   Range: ${result.min_price:.2f} - ${result.max_price:.2f}")
        print(f"   Sample size: {result.num_sold}")
        
        if result.recent_sales:
            print(f"\n📦 Recent sales:")
            for item in result.recent_sales[:5]:
                print(f"   ${item.total_price:.2f} - {item.title[:50]}...")
    else:
        print("❌ No results found")


if __name__ == "__main__":
    asyncio.run(main())
