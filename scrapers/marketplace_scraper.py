"""
Facebook Marketplace Scraper
Enhanced version using stealth browser's full capabilities
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
import re
import os
import random
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import sys
sys.path.append('..')
from utils.listing_parser import Listing, extract_listings_from_html, parse_price
from utils.stealth_helpers import (
    human_delay, typing_delay, page_load_delay, scroll_delay, between_search_delay,
    get_stealth_spawn_options, get_random_typing_delay_ms, type_like_human,
    warm_profile, save_cookies, simulate_human_browsing, ensure_profile_dir,
    _random_mouse_move, _random_scroll,
    DEFAULT_PROFILE_DIR
)


class MarketplaceScraper:
    """Facebook Marketplace scraper using stealth browser's full capabilities"""
    
    def __init__(
        self,
        stealth_browser_path: str = None,
        user_data_dir: str = None,
        headless: bool = False
    ):
        # Auto-detect stealth browser if not provided
        if stealth_browser_path is None:
            from utils.paths import find_stealth_browser
            stealth_browser_path = find_stealth_browser()
        self.stealth_browser_path = stealth_browser_path
        # Use persistent profile by default (survives across runs)
        self.user_data_dir = ensure_profile_dir(user_data_dir or DEFAULT_PROFILE_DIR)
        self.headless = headless
        self.session = None
        self.instance_id = None
    
    def build_search_url(
        self,
        query: str,
        zip_code: str = "",
        radius_miles: int = 25,
        min_price: int = None,
        max_price: int = None,
        condition: str = None,
        sort_by_price: bool = False,  # Sort by lowest price first
        days_listed: int = None  # Filter by listing age (7, 14, 30)
    ) -> str:
        """Build Facebook Marketplace search URL with filters"""
        base = "https://www.facebook.com/marketplace"
        
        if zip_code:
            base += f"/{zip_code}"
        
        base += f"/search?query={query.replace(' ', '%20')}"
        
        radius_km = int(radius_miles * 1.60934)
        base += f"&radius={radius_km}"
        
        if min_price:
            base += f"&minPrice={min_price}"
        if max_price:
            base += f"&maxPrice={max_price}"
        if condition:
            base += f"&itemCondition={condition}"
        
        # Filter by days listed (FB supports 1, 7, 30)
        if days_listed:
            if days_listed <= 1:
                base += "&daysSinceListed=1"
            elif days_listed <= 7:
                base += "&daysSinceListed=7"
            else:
                base += "&daysSinceListed=30"
        
        # Sort: price_ascend (lowest first), creation_time_descend (newest first)
        if sort_by_price:
            base += "&sortBy=price_ascend"
        else:
            base += "&sortBy=creation_time_descend"
        
        return base
    
    async def scrape(
        self,
        query: str,
        zip_code: str = "",
        radius_miles: int = 25,
        scroll_pages: int = 3,
        sort_by_price: bool = True
    ) -> list[Listing]:
        """Scrape Facebook Marketplace for listings."""
        
        stealth_venv_python = self.stealth_browser_path.replace("/src/server.py", "/venv/bin/python")
        
        env = os.environ.copy()
        env["PYTHONWARNINGS"] = "ignore::DeprecationWarning"
        
        server_params = StdioServerParameters(
            command=stealth_venv_python,
            args=["-W", "ignore", self.stealth_browser_path],
            env=env
        )
        
        listings = []
        
        try:
            async with stdio_client(server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self.session = session
                    
                    # Spawn browser with stealth options
                    print("🌐 Spawning browser with stealth settings...")
                    stealth_opts = get_stealth_spawn_options(self.user_data_dir, self.headless)
                    spawn_result = await asyncio.wait_for(
                        session.call_tool(
                            "spawn_browser",
                            arguments=stealth_opts
                        ),
                        timeout=30
                    )
                    
                    if hasattr(spawn_result, 'isError') and spawn_result.isError:
                        raise Exception(f"Browser spawn failed: {spawn_result.content}")
                    
                    self.instance_id = self._extract_instance_id(spawn_result)
                    if not self.instance_id:
                        raise Exception("Could not get browser instance_id")
                    
                    print(f"✅ Browser ready: {self.instance_id[:8]}...")
                    
                    # Set up stealth hooks
                    await self._setup_stealth_hooks()
                    
                    # Warm profile (builds browsing history, skips if recent)
                    await warm_profile(session, self.instance_id)
                    
                    print(f"🔍 Searching: {query}")
                    print(f"📍 Location: {zip_code or 'default'}, {radius_miles} miles")
                    
                    # Navigate to marketplace homepage (with location if provided)
                    mp_url = f"https://www.facebook.com/marketplace/{zip_code}" if zip_code else "https://www.facebook.com/marketplace/"
                    print(f"   📍 Loading marketplace: {mp_url}")
                    await self._navigate(mp_url)
                    await page_load_delay()  # Human-like random delay
                    
                    # Check for login FIRST before trying to interact
                    login_needed = await self._check_login_state()
                    if login_needed:
                        print("\n" + "="*50)
                        print("⚠️  FACEBOOK LOGIN REQUIRED")
                        print("="*50)
                        print("📌 Please log in to Facebook in the browser window")
                        print("⏳ Waiting up to 3 minutes for you to complete login...")
                        print("="*50 + "\n")
                        
                        # Wait up to 3 minutes for login
                        for i in range(36):  # 36 * 5 = 180 seconds
                            await asyncio.sleep(5)
                            still_login = await self._check_login_state()
                            if not still_login:
                                print("\n✅ Login successful! Continuing...")
                                await asyncio.sleep(random.uniform(1.5, 3.0))  # Brief pause after login
                                # Re-navigate to marketplace after login
                                await self._navigate(mp_url)
                                await page_load_delay()
                                break
                            if i % 6 == 0 and i > 0:
                                print(f"   ⏳ Still waiting for login... ({(i)*5}s)")
                        else:
                            print("\n❌ Login timeout (3 min) - please restart and try again")
                            return []
                    
                    # Always search like a human — type into the search box
                    print(f"   🔍 Searching via search box...")
                    await _random_mouse_move(session, self.instance_id)
                    await human_delay(0.5, 1.5)
                    searched = await self._human_search(query)
                    if not searched:
                        print(f"   ⚠️ Search box not found, retrying after page reload...")
                        await self._navigate(mp_url)
                        await page_load_delay()
                        await self._human_search(query)
                    
                    await page_load_delay()
                    
                    # Browse results like a human — mouse movements, scrolling, pausing
                    print("   📜 Browsing results...")
                    await self._humanize_results_browsing()
                    
                    # Wait for listings using proper wait_for_element
                    print("   ⏳ Waiting for listings to appear...")
                    await self._wait_for_element('a[href*="/marketplace/item/"]', timeout=15000)
                    
                    # Try to extract listings from network requests (GraphQL)
                    print("   🔌 Checking network for GraphQL data...")
                    network_listings = await self._extract_from_network()
                    if network_listings:
                        print(f"   ✅ Got {len(network_listings)} listings from network!")
                        listings.extend(network_listings)
                    
                    # Scroll and collect listings
                    print(f"\n📜 Scraping {scroll_pages} pages...")
                    
                    for page in range(scroll_pages):
                        print(f"   Page {page + 1}/{scroll_pages}...")
                        
                        # Simulate human browsing on every page
                        await simulate_human_browsing(session, self.instance_id)
                        await _random_mouse_move(session, self.instance_id)
                        
                        # Try JS extraction first (has image support), fall back to query
                        page_listings = await self._extract_listings_js()
                        if not page_listings:
                            page_listings = await self._extract_listings_query()
                        
                        # Dedupe and add
                        existing_titles = {l.title.lower()[:30] for l in listings}
                        for listing in page_listings:
                            if listing.title.lower()[:30] not in existing_titles:
                                listings.append(listing)
                                existing_titles.add(listing.title.lower()[:30])
                        
                        # Scroll down for more with human-like behavior
                        if page < scroll_pages - 1:
                            await self._humanize_results_browsing()
                    
                    print(f"\n✅ Found {len(listings)} listings")
                    
                    if len(listings) == 0:
                        # Take a screenshot for debugging
                        print("\n📸 Taking debug screenshot...")
                        await self._take_screenshot("/tmp/fb-debug.png")
                        print("   Saved to /tmp/fb-debug.png")
                        print("\n💡 No listings found. Check the screenshot to see what's on screen.")
                    
                    # Save cookies/session before closing
                    await save_cookies(session, self.instance_id)
                    
                    # Cleanup
                    try:
                        await session.call_tool(
                            "close_instance",
                            arguments={"instance_id": self.instance_id}
                        )
                    except:
                        pass
                    
        except Exception as e:
            print(f"❌ Scraper error: {e}")
            import traceback
            traceback.print_exc()
        
        return listings
    
    async def scrape_multiple(
        self,
        queries: list[str],
        zip_code: str = "",
        radius_miles: int = 25,
        scroll_pages: int = 2,
        sort_by_price: bool = True
    ) -> list[Listing]:
        """
        Scrape Facebook Marketplace for multiple search terms.
        Keeps browser open between searches for efficiency and stealth.
        """
        all_listings = []
        seen_titles = set()
        
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
                    
                    # Spawn browser with stealth options
                    print("🌐 Spawning browser with stealth settings...")
                    stealth_opts = get_stealth_spawn_options(self.user_data_dir, self.headless)
                    spawn_result = await asyncio.wait_for(
                        session.call_tool("spawn_browser", arguments=stealth_opts),
                        timeout=30
                    )
                    
                    self.instance_id = self._extract_instance_id(spawn_result)
                    if not self.instance_id:
                        raise Exception("Could not get browser instance_id")
                    
                    print(f"✅ Browser ready: {self.instance_id[:8]}...")
                    
                    # Set up stealth hooks
                    await self._setup_stealth_hooks()
                    
                    # Warm profile
                    await warm_profile(session, self.instance_id)
                    
                    # Navigate to marketplace first
                    mp_url = f"https://www.facebook.com/marketplace/{zip_code}" if zip_code else "https://www.facebook.com/marketplace/"
                    print(f"📍 Loading marketplace: {mp_url}")
                    await self._navigate(mp_url)
                    await page_load_delay()
                    
                    # Check for login FIRST before trying to interact
                    login_needed = await self._check_login_state()
                    if login_needed:
                        print("\n" + "="*50)
                        print("⚠️  FACEBOOK LOGIN REQUIRED")
                        print("="*50)
                        print("📌 Please log in to Facebook in the browser window")
                        print("⏳ Waiting up to 3 minutes for you to complete login...")
                        print("="*50 + "\n")
                        
                        # Wait up to 3 minutes for login
                        for i in range(36):  # 36 * 5 = 180 seconds
                            await asyncio.sleep(5)
                            still_login = await self._check_login_state()
                            if not still_login:
                                print("\n✅ Login successful! Continuing...")
                                await asyncio.sleep(2)
                                await self._navigate(mp_url)
                                await page_load_delay()
                                break
                            if i % 6 == 0 and i > 0:
                                print(f"   ⏳ Still waiting for login... ({(i)*5}s)")
                        else:
                            print("\n❌ Login timeout (3 min) - please restart and try again")
                            return []
                    
                    # Process each query
                    for i, query in enumerate(queries):
                        print(f"\n{'='*50}")
                        print(f"🔍 [{i+1}/{len(queries)}] Searching: {query}")
                        print(f"{'='*50}")
                        
                        # Random delay between searches (human-like variance)
                        if i > 0:
                            delay = between_search_delay()
                            print(f"   ⏳ Waiting {delay:.0f}s before next search...")
                            await asyncio.sleep(delay)
                            # Simulate some human fidgeting
                            await simulate_human_browsing(session, self.instance_id)
                        
                        # Always search like a human — type into the search box
                        await _random_mouse_move(session, self.instance_id)
                        await human_delay(0.5, 1.5)
                        searched = await self._human_search(query)
                        if not searched:
                            print(f"   ⚠️ Search box not found, retrying after page reload...")
                            await self._navigate(mp_url)
                            await page_load_delay()
                            await self._human_search(query)
                        
                        await page_load_delay()
                        
                        # Browse results like a human
                        await self._humanize_results_browsing()
                        
                        # Scroll to load content with human behavior
                        for p in range(scroll_pages):
                            await simulate_human_browsing(session, self.instance_id)
                            await self._scroll_down()
                            await scroll_delay()
                            if p > 0:
                                await _random_mouse_move(session, self.instance_id)
                        
                        # Wait for listings
                        await self._wait_for_element('a[href*="/marketplace/item/"]', timeout=10000)
                        
                        # Extract listings (JS first for image support)
                        query_listings = await self._extract_listings_js()
                        if not query_listings:
                            query_listings = await self._extract_listings_query()
                        
                        # Also try network extraction
                        network_listings = await self._extract_from_network()
                        if network_listings:
                            query_listings.extend(network_listings)
                        
                        # Dedupe and add to results
                        new_count = 0
                        for listing in query_listings:
                            title_key = listing.title.lower()[:30]
                            if title_key not in seen_titles:
                                seen_titles.add(title_key)
                                all_listings.append(listing)
                                new_count += 1
                        
                        print(f"   ✅ Found {new_count} new listings (total: {len(all_listings)})")
                        
                        # Navigate back to marketplace home for next search
                        if i < len(queries) - 1:
                            await self._navigate(mp_url)
                            await human_delay(1, 2)
                    
                    # Save cookies before closing
                    await save_cookies(session, self.instance_id)
                    
                    # Cleanup
                    print(f"\n🏁 Scraping complete: {len(all_listings)} total listings")
                    try:
                        await session.call_tool("close_instance", arguments={"instance_id": self.instance_id})
                    except:
                        pass
                        
        except Exception as e:
            print(f"❌ Scraper error: {e}")
            import traceback
            traceback.print_exc()
        
        return all_listings
    
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
    
    async def _navigate(self, url: str, timeout: int = 30, referrer: str = None):
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
            elif "facebook.com" in url:
                args["referrer"] = "https://www.facebook.com/"
            
            await asyncio.wait_for(
                self.session.call_tool("navigate", arguments=args),
                timeout=timeout + 5
            )
        except asyncio.TimeoutError:
            pass  # Navigation timeout is often okay
        except Exception as e:
            print(f"      Navigation error: {e}")
    
    async def _setup_stealth_hooks(self):
        """Set up hooks to block known tracking/fingerprinting scripts"""
        try:
            # Block known FB bot detection endpoints
            tracking_patterns = [
                "*://pixel.facebook.com/*",
                "*://www.facebook.com/tr/*",
                "*://connect.facebook.net/signals/*",
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
    
    async def _check_login_state(self) -> bool:
        """Check if we're on a login/2FA page"""
        js_script = """
        (() => {
            const url = window.location.href;
            const html = document.documentElement.innerHTML.toLowerCase();
            
            // Check URL patterns
            if (url.includes('/login') || url.includes('/checkpoint')) {
                return true;
            }
            
            // Check for login form elements
            if (document.querySelector('input[name="email"]') && 
                document.querySelector('input[name="pass"]')) {
                return true;
            }
            
            // Check for 2FA/checkpoint text
            if (html.includes('two-factor') || 
                html.includes('enter the code') ||
                html.includes('check your notifications') ||
                html.includes('approve this login')) {
                return true;
            }
            
            return false;
        })()
        """
        
        result = await self._execute_js(js_script)
        return result == "true" or result == True
    
    async def _wait_for_listings(self, timeout: int = 15):
        """Wait for marketplace listings to load"""
        js_check = """
        (() => {
            // Check for listing links or price elements
            const links = document.querySelectorAll('a[href*="/marketplace/item/"]');
            const allLinks = document.querySelectorAll('a[href*="/marketplace/"]');
            const prices = document.querySelectorAll('span');
            let priceCount = 0;
            let priceExamples = [];
            prices.forEach(s => {
                if (s.innerText && s.innerText.match(/^\\$\\d/)) {
                    priceCount++;
                    if (priceExamples.length < 3) priceExamples.push(s.innerText.slice(0,20));
                }
            });
            
            // Check for any loading indicators or empty states
            const loading = document.querySelector('[role="progressbar"]') ? true : false;
            const noResults = document.body.innerText.includes('No listings found') || 
                             document.body.innerText.includes('No results');
            
            // Get main content area text sample
            const mainContent = document.querySelector('[role="main"]');
            const contentSample = mainContent ? mainContent.innerText.slice(0, 200) : 'no main found';
            
            return {
                itemLinks: links.length, 
                allMpLinks: allLinks.length,
                prices: priceCount,
                priceExamples: priceExamples,
                loading: loading,
                noResults: noResults,
                url: window.location.href,
                contentSample: contentSample
            };
        })()
        """
        
        for i in range(timeout):
            result = await self._execute_js(js_check)
            try:
                # Parse result
                result_str = str(result)
                if '{' in result_str:
                    # Find JSON object
                    start = result_str.find('{')
                    depth = 0
                    end = start
                    for j, c in enumerate(result_str[start:]):
                        if c == '{': depth += 1
                        elif c == '}':
                            depth -= 1
                            if depth == 0:
                                end = start + j + 1
                                break
                    
                    data = json.loads(result_str[start:end])
                    links = data.get('itemLinks', 0)
                    prices = data.get('prices', 0)
                    
                    if i == 0 or i == timeout - 1:  # First and last check, print details
                        print(f"      URL: {data.get('url', '?')[:70]}...")
                        print(f"      Item links: {links}, All MP links: {data.get('allMpLinks', 0)}, Prices: {prices}")
                        if data.get('priceExamples'):
                            print(f"      Price examples: {data.get('priceExamples')}")
                        if data.get('loading'):
                            print(f"      ⏳ Page still loading...")
                        if data.get('noResults'):
                            print(f"      ❌ 'No results' message detected")
                        print(f"      Content: {data.get('contentSample', '')[:100]}...")
                    
                    if links > 0 or prices > 3:
                        print(f"   ✅ Found {links} listing links, {prices} prices")
                        return
            except Exception as e:
                if i == 0:
                    print(f"      Parse error: {e}")
            await asyncio.sleep(random.uniform(0.8, 1.3))
        
        print(f"   ⚠️ Timeout waiting for listings")
    
    async def _execute_js(self, script: str) -> str:
        """Execute JavaScript in the page"""
        try:
            result = await asyncio.wait_for(
                self.session.call_tool(
                    "execute_script",  # Changed from execute_js
                    arguments={
                        "instance_id": self.instance_id,
                        "script": script
                    }
                ),
                timeout=10
            )
            
            # Extract result from response
            if hasattr(result, 'content'):
                for item in result.content:
                    if hasattr(item, 'text'):
                        try:
                            data = json.loads(item.text)
                            if 'result' in data:
                                return data['result']
                            return item.text
                        except:
                            return item.text
            return str(result)
        except Exception as e:
            return f"Error: {e}"
    
    async def _scroll_down(self):
        """Scroll down to load more content using native smooth scroll"""
        try:
            # Use native scroll_page tool for more human-like behavior
            await asyncio.wait_for(
                self.session.call_tool(
                    "scroll_page",
                    arguments={
                        "instance_id": self.instance_id,
                        "direction": "down",
                        "amount": random.randint(600, 1200),  # Random scroll amount
                        "smooth": True
                    }
                ),
                timeout=5
            )
        except:
            # Fallback to JS
            await self._execute_js("window.scrollBy(0, window.innerHeight * 2);")
    
    async def _humanize_results_browsing(self):
        """
        Simulate a human browsing through search results.
        Combines scrolling, mouse movements, and pauses like someone
        scanning listings and deciding what to click.
        """
        try:
            # Initial pause — human looks at results before scrolling
            await human_delay(1.0, 3.0)
            
            # 2-4 rounds of scroll + look + mouse
            rounds = random.randint(2, 4)
            for i in range(rounds):
                # Scroll down a variable amount
                await self._scroll_down()
                
                # Pause to "read" listings — longer pauses sometimes (found something interesting)
                if random.random() < 0.3:
                    await human_delay(2.0, 5.0)  # Lingering on something
                else:
                    await scroll_delay()
                
                # Move mouse around like scanning listings
                await _random_mouse_move(self.session, self.instance_id)
                
                # Occasionally scroll back up a bit (re-checking something)
                if random.random() < 0.2:
                    try:
                        await self.session.call_tool(
                            "scroll_page",
                            arguments={
                                "instance_id": self.instance_id,
                                "direction": "up",
                                "amount": random.randint(150, 400),
                                "smooth": True
                            }
                        )
                        await human_delay(0.5, 1.5)
                    except:
                        pass
                
                # Small pause between rounds
                await human_delay(0.3, 1.0)
            
        except Exception as e:
            pass  # Humanization is best-effort, never break the scrape
    
    async def _human_search(self, query: str) -> bool:
        """Use the search box like a human would"""
        try:
            # Find and click the search input
            # FB marketplace search box selectors
            search_selectors = [
                'input[placeholder*="Search Marketplace"]',
                'input[aria-label*="Search Marketplace"]',
                'input[type="search"]',
                '[role="search"] input',
            ]
            
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
                        print(f"      ✓ Clicked: {selector}")
                        clicked = True
                        break
                except:
                    continue
            
            if not clicked:
                print("      ✗ Could not find search input")
                return False
            
            await asyncio.sleep(random.uniform(0.3, 0.7))
            
            # Type the search query with human-like randomized timing
            typed = False
            for selector in search_selectors:
                try:
                    # Use human-like typing with randomized delays
                    success = await type_like_human(
                        self.session,
                        self.instance_id,
                        selector,
                        query,
                        clear_first=True
                    )
                    if success:
                        print(f"      ✓ Typed query: {query}")
                        typed = True
                        break
                except Exception as e:
                    # Fallback to regular type_text with variable delay
                    try:
                        delay = get_random_typing_delay_ms()
                        result = await asyncio.wait_for(
                            self.session.call_tool(
                                "type_text",
                                arguments={
                                    "instance_id": self.instance_id,
                                    "selector": selector,
                                    "text": query,
                                    "clear_first": True,
                                    "delay_ms": delay
                                }
                            ),
                            timeout=10
                        )
                        if result and not (hasattr(result, 'isError') and result.isError):
                            print(f"      ✓ Typed query: {query}")
                            typed = True
                            break
                    except:
                        continue
            
            await asyncio.sleep(random.uniform(0.2, 0.5))
            
            # Press Enter to search
            await self._execute_js("""
                const input = document.querySelector('input[placeholder*="Search"], input[type="search"], [role="search"] input');
                if (input) {
                    input.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true}));
                    input.dispatchEvent(new KeyboardEvent('keyup', {key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true}));
                }
            """)
            
            # Also try form submission
            await self._execute_js("""
                const form = document.querySelector('[role="search"] form, form:has(input[type="search"])');
                if (form) form.submit();
            """)
            
            print("      ✓ Submitted search")
            await asyncio.sleep(random.uniform(2.5, 4.5))  # Wait for search results to load
            
            return True
            
        except Exception as e:
            print(f"      ✗ Search error: {e}")
            return False
    
    async def _wait_for_element(self, selector: str, timeout: int = 30000) -> bool:
        """Wait for an element to appear using stealth browser's wait"""
        try:
            result = await asyncio.wait_for(
                self.session.call_tool(
                    "wait_for_element",
                    arguments={
                        "instance_id": self.instance_id,
                        "selector": selector,
                        "timeout": timeout,
                        "visible": True
                    }
                ),
                timeout=(timeout / 1000) + 5
            )
            return True
        except Exception as e:
            print(f"      ⏳ Element not found: {selector[:40]}...")
            return False
    
    async def _query_elements(self, selector: str, limit: int = 50) -> list:
        """Query elements using stealth browser's query_elements"""
        try:
            result = await asyncio.wait_for(
                self.session.call_tool(
                    "query_elements",
                    arguments={
                        "instance_id": self.instance_id,
                        "selector": selector,
                        "visible_only": True,
                        "limit": limit
                    }
                ),
                timeout=15
            )
            
            # Extract from response
            if hasattr(result, 'content'):
                for item in result.content:
                    if hasattr(item, 'text'):
                        try:
                            data = json.loads(item.text)
                            if isinstance(data, list):
                                return data
                            elif isinstance(data, dict) and 'elements' in data:
                                return data['elements']
                        except:
                            pass
            return []
        except Exception as e:
            print(f"      Query error: {e}")
            return []
    
    async def _extract_from_network(self) -> list[Listing]:
        """Extract listings from intercepted GraphQL network requests"""
        listings = []
        try:
            # Get all XHR requests
            result = await asyncio.wait_for(
                self.session.call_tool(
                    "list_network_requests",
                    arguments={
                        "instance_id": self.instance_id,
                        "filter_type": "xhr"
                    }
                ),
                timeout=10
            )
            
            requests = []
            if hasattr(result, 'content'):
                for item in result.content:
                    if hasattr(item, 'text'):
                        try:
                            data = json.loads(item.text)
                            if isinstance(data, list):
                                requests = data
                            break
                        except:
                            pass
            
            # Look for GraphQL marketplace requests
            for req in requests:
                url = req.get('url', '')
                if 'graphql' in url.lower() or 'marketplace' in url.lower():
                    request_id = req.get('request_id')
                    if not request_id:
                        continue
                    
                    # Get response content
                    try:
                        body_result = await asyncio.wait_for(
                            self.session.call_tool(
                                "get_response_content",
                                arguments={
                                    "instance_id": self.instance_id,
                                    "request_id": request_id
                                }
                            ),
                            timeout=5
                        )
                        
                        body_text = ""
                        if hasattr(body_result, 'content'):
                            for item in body_result.content:
                                if hasattr(item, 'text'):
                                    body_text = item.text
                                    break
                        
                        # Parse GraphQL response for listings
                        if body_text and 'marketplace' in body_text.lower():
                            parsed = self._parse_graphql_listings(body_text)
                            listings.extend(parsed)
                    except:
                        pass
            
        except Exception as e:
            print(f"      Network extraction error: {e}")
        
        return listings
    
    def _parse_graphql_listings(self, body: str) -> list[Listing]:
        """Parse Facebook GraphQL response for marketplace listings"""
        listings = []
        try:
            # FB returns JSON (sometimes multiple concatenated)
            data = json.loads(body)
            
            # Recursively search for listing data
            def find_listings(obj, depth=0):
                if depth > 20:
                    return
                if isinstance(obj, dict):
                    # Check for marketplace listing patterns
                    if 'listing' in obj or 'marketplace_listing' in obj:
                        listing_data = obj.get('listing') or obj.get('marketplace_listing') or obj
                        if isinstance(listing_data, dict):
                            title = listing_data.get('marketplace_listing_title', '') or \
                                   listing_data.get('title', '') or \
                                   listing_data.get('name', '')
                            price_obj = listing_data.get('listing_price', {})
                            price = 0
                            if isinstance(price_obj, dict):
                                price = float(price_obj.get('amount', 0)) / 100  # Usually in cents
                            elif isinstance(price_obj, (int, float)):
                                price = float(price_obj)
                            
                            url = listing_data.get('listing_url', '') or \
                                  listing_data.get('url', '') or \
                                  f"https://www.facebook.com/marketplace/item/{listing_data.get('id', '')}"
                            
                            location = listing_data.get('location', {})
                            if isinstance(location, dict):
                                location = location.get('name', '')
                            
                            if title and price > 0:
                                listings.append(Listing(
                                    title=str(title)[:200],
                                    price=price,
                                    price_raw=f"${price:.2f}",
                                    location=str(location),
                                    listing_url=str(url)
                                ))
                    
                    # Recurse into dict values
                    for v in obj.values():
                        find_listings(v, depth + 1)
                
                elif isinstance(obj, list):
                    for item in obj:
                        find_listings(item, depth + 1)
            
            find_listings(data)
            
        except json.JSONDecodeError:
            # Try to find JSON in the response
            pass
        except Exception as e:
            pass
        
        return listings
    
    async def _take_screenshot(self, path: str):
        """Take a screenshot for debugging"""
        try:
            await self.session.call_tool(
                "take_screenshot",
                arguments={
                    "instance_id": self.instance_id,
                    "file_path": path,
                    "full_page": False
                }
            )
        except Exception as e:
            print(f"   Screenshot error: {e}")
    
    async def _extract_listings_query(self) -> list[Listing]:
        """Extract listings using query_elements tool"""
        listings = []
        
        try:
            # Query for marketplace item links
            elements = await self._query_elements('a[href*="/marketplace/item/"]', limit=100)
            
            print(f"      query_elements found: {len(elements)} item links")
            
            for el in elements:
                try:
                    href = el.get('href', '') or el.get('attributes', {}).get('href', '')
                    text = el.get('text', '') or el.get('innerText', '')
                    
                    if not href or '/marketplace/item/' not in href:
                        continue
                    
                    # Parse price from text
                    price_match = re.search(r'\$[\d,]+(?:\.\d{2})?', text)
                    if not price_match:
                        continue
                    
                    price = float(price_match.group().replace('$', '').replace(',', ''))
                    
                    # Get title (first line that's not price/location)
                    lines = [l.strip() for l in text.split('\n') if l.strip()]
                    title = ''
                    for line in lines:
                        if not line.startswith('$') and not re.match(r'^\d+ miles?', line, re.I):
                            if len(line) > 5:
                                title = line
                                break
                    
                    if title and price > 0:
                        # Try to get image from element's container
                        img_url = ''
                        # query_elements might include image info
                        if 'image' in el:
                            img_url = el.get('image', '')
                        elif 'img' in str(el):
                            # Try to extract from element data
                            pass
                        
                        listings.append(Listing(
                            title=title[:200],
                            price=price,
                            price_raw=price_match.group(),
                            location='',
                            listing_url=href if href.startswith('http') else f"https://www.facebook.com{href}",
                            image_url=img_url
                        ))
                except:
                    continue
                    
        except Exception as e:
            print(f"      query extraction error: {e}")
        
        return listings
    
    async def _extract_listings_js(self) -> list[Listing]:
        """Extract listings using JavaScript DOM queries"""
        
        # This script extracts listing data directly from FB's DOM
        js_script = """
        (() => {
            const debug = {
                url: window.location.href,
                selectors_tried: [],
                cards_found: 0,
                links_found: 0
            };
            
            const listings = [];
            
            // Strategy 1: Find listing cards by common patterns
            // FB Marketplace uses various selectors, try multiple
            const selectors = [
                'div[data-testid="marketplace_feed_item"]',
                'div[class*="x9f619"][class*="x1n2onr6"] a[href*="/marketplace/item/"]',
                'a[href*="/marketplace/item/"]'
            ];
            
            let cards = [];
            for (const selector of selectors) {
                const found = document.querySelectorAll(selector);
                debug.selectors_tried.push({selector, count: found.length});
                if (found.length > 0 && cards.length === 0) {
                    cards = found;
                }
            }
            
            // Also count all marketplace links
            debug.links_found = document.querySelectorAll('a[href*="/marketplace/"]').length;
            debug.cards_found = cards.length;
            
            // If we found links, get their parent containers
            if (cards.length > 0 && cards[0].tagName === 'A') {
                const containers = new Set();
                cards.forEach(link => {
                    // Walk up to find a reasonable container
                    let el = link;
                    for (let i = 0; i < 5; i++) {
                        if (el.parentElement) el = el.parentElement;
                    }
                    containers.add(el);
                });
                cards = Array.from(containers);
            }
            
            cards.forEach((card, idx) => {
                try {
                    // Find price - look for $ pattern
                    const allText = card.innerText || '';
                    const priceMatch = allText.match(/\\$[\\d,]+(?:\\.\\d{2})?/);
                    if (!priceMatch) return;
                    
                    const price = priceMatch[0];
                    const priceNum = parseFloat(price.replace(/[$,]/g, ''));
                    if (priceNum < 1 || priceNum > 50000) return;
                    
                    // Find link
                    const link = card.querySelector('a[href*="/marketplace/item/"]');
                    const url = link ? link.href : '';
                    
                    // Extract title - usually first substantial text after or before price
                    const lines = allText.split('\\n').map(l => l.trim()).filter(l => l.length > 3);
                    let title = '';
                    
                    for (const line of lines) {
                        // Skip if it's the price or location-like
                        if (line.startsWith('$') || 
                            line.match(/^\\d+ miles?/i) ||
                            line.match(/^(Listed|Free|Local)/i)) continue;
                        
                        // Take first reasonable title
                        if (line.length > 5 && line.length < 200) {
                            title = line;
                            break;
                        }
                    }
                    
                    if (!title) return;
                    
                    // Find location
                    let location = '';
                    const locMatch = allText.match(/(\\d+\\s*miles?\\s*away|Listed\\s+[^\\n]+)/i);
                    if (locMatch) location = locMatch[0];
                    
                    // Find image - FB uses img tags or background-image styles
                    let imageUrl = '';
                    const img = card.querySelector('img[src*="fbcdn"]') || 
                                card.querySelector('img[src*="fb"]') ||
                                card.querySelector('img');
                    if (img && img.src && !img.src.includes('data:')) {
                        imageUrl = img.src;
                    }
                    // Also check for background-image style
                    if (!imageUrl) {
                        const bgEl = card.querySelector('[style*="background-image"]');
                        if (bgEl) {
                            const bgMatch = bgEl.style.backgroundImage.match(/url\\(['"]?([^'"\\)]+)/);
                            if (bgMatch) imageUrl = bgMatch[1];
                        }
                    }
                    
                    listings.push({
                        title: title,
                        price: priceNum,
                        price_raw: price,
                        location: location,
                        url: url,
                        image: imageUrl
                    });
                } catch(e) {}
            });
            
            return JSON.stringify({debug, listings});
        })()
        """
        
        result = await self._execute_js(js_script)
        listings = []
        
        try:
            # Parse JSON from result - look for outermost braces
            # The result may be wrapped in quotes or have extra text
            result_str = str(result)
            
            # Find the JSON object
            start = result_str.find('{"debug"')
            if start == -1:
                start = result_str.find('{')
            
            if start != -1:
                # Find matching closing brace
                depth = 0
                end = start
                for i, c in enumerate(result_str[start:]):
                    if c == '{':
                        depth += 1
                    elif c == '}':
                        depth -= 1
                        if depth == 0:
                            end = start + i + 1
                            break
                
                json_str = result_str[start:end]
                full_data = json.loads(json_str)
                
                # Print debug info
                debug = full_data.get('debug', {})
                print(f"   🔍 URL: {debug.get('url', '?')[:80]}...")
                print(f"   🔍 Links found: {debug.get('links_found', 0)}, Cards: {debug.get('cards_found', 0)}")
                for sel in debug.get('selectors_tried', []):
                    print(f"      - {sel['selector'][:50]}: {sel['count']}")
                
                data = full_data.get('listings', [])
                for item in data:
                    listings.append(Listing(
                        title=item.get('title', ''),
                        price=item.get('price', 0),
                        price_raw=item.get('price_raw', ''),
                        location=item.get('location', ''),
                        listing_url=item.get('url', ''),
                        image_url=item.get('image', '')
                    ))
            else:
                print(f"   ⚠️ No JSON found in result: {result_str[:200]}")
        except Exception as e:
            print(f"   ⚠️ JS parse error: {e}")
            print(f"   Raw result: {str(result)[:300]}")
        
        # Fallback: also try HTML parsing
        if len(listings) < 3:
            try:
                content_result = await asyncio.wait_for(
                    self.session.call_tool(
                        "get_page_content",
                        arguments={"instance_id": self.instance_id}
                    ),
                    timeout=15
                )
                content = str(content_result.content)
                html_listings = extract_listings_from_html(content)
                
                existing_titles = {l.title.lower()[:30] for l in listings}
                for hl in html_listings:
                    if hl.title.lower()[:30] not in existing_titles:
                        listings.append(hl)
            except:
                pass
        
        return listings


async def main():
    """Test the scraper"""
    scraper = MarketplaceScraper(
        headless=False
    )
    
    query = input("🔍 Search query: ").strip() or "iphone"
    zip_code = input("📍 ZIP code (or Enter for default): ").strip()
    
    listings = await scraper.scrape(
        query=query,
        zip_code=zip_code,
        radius_miles=25,
        scroll_pages=2
    )
    
    print(f"\n📦 Found {len(listings)} listings:\n")
    for listing in listings[:10]:
        print(f"  ${listing.price:.2f} - {listing.title[:50]}...")
        if listing.location:
            print(f"         📍 {listing.location}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
