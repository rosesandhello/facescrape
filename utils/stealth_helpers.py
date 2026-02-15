"""
Stealth helpers for more human-like browser behavior
"""
import asyncio
import json
import os
import random
import time


# --- Persistent profile directory ---
DEFAULT_PROFILE_DIR = os.path.expanduser("~/.scrapedface/chrome-profile")
COOKIE_CACHE_PATH = os.path.expanduser("~/.scrapedface/cookies.json")
WARM_STATE_PATH = os.path.expanduser("~/.scrapedface/warm-state.json")


def ensure_profile_dir(profile_dir: str = None) -> str:
    """Ensure persistent profile directory exists and return path."""
    d = profile_dir or DEFAULT_PROFILE_DIR
    os.makedirs(d, exist_ok=True)
    return d


def get_warm_state() -> dict:
    """Load warming state — tracks when we last warmed the profile."""
    try:
        with open(WARM_STATE_PATH) as f:
            return json.load(f)
    except:
        return {"last_warm": 0, "warm_count": 0}


def save_warm_state(state: dict):
    os.makedirs(os.path.dirname(WARM_STATE_PATH), exist_ok=True)
    with open(WARM_STATE_PATH, "w") as f:
        json.dump(state, f)


async def save_cookies(session, instance_id: str):
    """Save cookies from browser session to disk."""
    try:
        result = await asyncio.wait_for(
            session.call_tool(
                "execute_script",
                arguments={
                    "instance_id": instance_id,
                    "script": "JSON.stringify(document.cookie)"
                }
            ),
            timeout=10
        )
        # Also try CDP cookies if available
        cookie_js = """
        (() => {
            return JSON.stringify({
                url: window.location.href,
                cookies: document.cookie,
                localStorage: (() => {
                    try {
                        const items = {};
                        for (let i = 0; i < localStorage.length; i++) {
                            const key = localStorage.key(i);
                            items[key] = localStorage.getItem(key);
                        }
                        return items;
                    } catch(e) { return {}; }
                })()
            });
        })()
        """
        result = await asyncio.wait_for(
            session.call_tool(
                "execute_script",
                arguments={"instance_id": instance_id, "script": cookie_js}
            ),
            timeout=10
        )
        if hasattr(result, 'content'):
            for item in result.content:
                if hasattr(item, 'text'):
                    os.makedirs(os.path.dirname(COOKIE_CACHE_PATH), exist_ok=True)
                    with open(COOKIE_CACHE_PATH, 'w') as f:
                        f.write(item.text)
                    break
    except Exception as e:
        pass  # Cookie save is best-effort


async def warm_profile(session, instance_id: str, force: bool = False):
    """
    Warm up the browser profile by browsing naturally.
    
    This builds up cookies, localStorage, history, and behavioral fingerprint.
    Only runs if profile hasn't been warmed recently (unless force=True).
    """
    state = get_warm_state()
    hours_since_warm = (time.time() - state.get("last_warm", 0)) / 3600
    
    # Don't warm if we did it within the last 4 hours (unless forced)
    if not force and hours_since_warm < 4:
        print("   🔥 Profile warm (last warmed {:.1f}h ago)".format(hours_since_warm))
        return
    
    print("   🔥 Warming profile (building browsing history)...")
    
    # Top traffic sites — pick randomly to look like organic browsing
    # Always end with Facebook so cookies are fresh
    warmup_pool = [
        "https://www.google.com",
        "https://www.youtube.com",
        "https://www.amazon.com",
        "https://www.wikipedia.org",
        "https://www.reddit.com",
        "https://www.twitter.com",
        "https://www.instagram.com",
        "https://www.linkedin.com",
        "https://www.netflix.com",
        "https://www.bing.com",
        "https://www.yahoo.com",
        "https://www.tiktok.com",
        "https://www.ebay.com",
        "https://www.twitch.tv",
        "https://www.cnn.com",
        "https://www.espn.com",
        "https://www.nytimes.com",
        "https://www.weather.com",
        "https://www.target.com",
        "https://www.walmart.com",
        "https://www.pinterest.com",
        "https://www.craigslist.org",
        "https://www.msn.com",
        "https://www.bbc.com",
        "https://www.imdb.com",
        "https://www.github.com",
        "https://www.stackoverflow.com",
        "https://www.paypal.com",
        "https://www.etsy.com",
        "https://www.zillow.com",
        "https://www.bestbuy.com",
        "https://www.hulu.com",
        "https://www.spotify.com",
        "https://www.quora.com",
        "https://www.huffpost.com",
        "https://www.foxnews.com",
        "https://www.washingtonpost.com",
        "https://www.tumblr.com",
        "https://www.disneyplus.com",
        "https://www.office.com",
    ]
    
    # Pick 2-4 random sites, then always end with Facebook
    num_sites = random.randint(2, 4)
    sites = [(url, random.uniform(2, 6), random.uniform(5, 12)) for url in random.sample(warmup_pool, k=num_sites)]
    sites.append(("https://www.facebook.com", random.uniform(3, 7), random.uniform(6, 12)))
    
    for url, min_wait, max_wait in sites:
        try:
            await asyncio.wait_for(
                session.call_tool(
                    "navigate",
                    arguments={
                        "instance_id": instance_id,
                        "url": url,
                        "timeout": 15000,
                        "wait_until": "domcontentloaded"
                    }
                ),
                timeout=20
            )
            
            # Simulate looking at the page
            wait = random.uniform(min_wait, max_wait)
            await asyncio.sleep(wait)
            
            # Do a few scrolls like a human scanning a page
            num_scrolls = random.randint(1, 4)
            for _ in range(num_scrolls):
                # Humans scroll variable amounts — sometimes a little peek, sometimes big jumps
                scroll_amount = int(random.gauss(400, 200))
                scroll_amount = max(100, min(scroll_amount, 1200))
                
                await session.call_tool(
                    "execute_script",
                    arguments={
                        "instance_id": instance_id,
                        "script": f"window.scrollBy({{top: {scroll_amount}, behavior: 'smooth'}})"
                    }
                )
                # Pause between scrolls — reading/scanning
                await asyncio.sleep(random.uniform(0.8, 3.5))
            
            # Sometimes scroll back up a bit (re-reading something)
            if random.random() < 0.3:
                back_amount = random.randint(100, 400)
                await session.call_tool(
                    "execute_script",
                    arguments={
                        "instance_id": instance_id,
                        "script": f"window.scrollBy({{top: -{back_amount}, behavior: 'smooth'}})"
                    }
                )
                await asyncio.sleep(random.uniform(0.5, 2.0))
            
            # Mouse movements
            await _random_mouse_move(session, instance_id)
            
        except:
            pass  # Warming is best-effort
    
    # Update warm state
    state["last_warm"] = time.time()
    state["warm_count"] = state.get("warm_count", 0) + 1
    save_warm_state(state)
    print("   🔥 Profile warmed!")


async def _random_mouse_move(session, instance_id: str):
    """
    Simulate realistic mouse movements with Bézier-like curves.
    Real humans don't teleport the cursor — they move in smooth arcs
    with slight acceleration/deceleration.
    """
    try:
        # Start from a plausible position
        cx = random.randint(300, 900)
        cy = random.randint(200, 500)
        
        # Generate 2-5 movement targets
        moves = random.randint(2, 5)
        for _ in range(moves):
            # Target — biased toward center of viewport (where content is)
            tx = int(random.gauss(700, 300))
            ty = int(random.gauss(400, 200))
            tx = max(50, min(tx, 1400))
            ty = max(50, min(ty, 800))
            
            # Interpolate with 5-12 intermediate points (smooth arc)
            steps = random.randint(5, 12)
            
            # Add slight curve via a control point offset
            ctrl_x = (cx + tx) / 2 + random.gauss(0, 80)
            ctrl_y = (cy + ty) / 2 + random.gauss(0, 60)
            
            for s in range(steps):
                t = (s + 1) / steps
                # Quadratic Bézier interpolation
                ix = int((1 - t)**2 * cx + 2 * (1 - t) * t * ctrl_x + t**2 * tx)
                iy = int((1 - t)**2 * cy + 2 * (1 - t) * t * ctrl_y + t**2 * ty)
                
                await session.call_tool(
                    "execute_script",
                    arguments={
                        "instance_id": instance_id,
                        "script": f"""
                            document.dispatchEvent(new MouseEvent('mousemove', {{
                                clientX: {ix}, clientY: {iy},
                                bubbles: true
                            }}));
                        """
                    }
                )
                # Faster in the middle of the arc, slower at endpoints
                # (mimics human acceleration curve)
                speed_factor = 1.0 - 0.6 * abs(t - 0.5)  # slower near 0 and 1
                await asyncio.sleep(random.uniform(0.01, 0.05) * speed_factor + 0.005)
            
            cx, cy = tx, ty
            
            # Pause between movements (human hesitation/reading)
            await asyncio.sleep(random.uniform(0.1, 0.8))
    except:
        pass


async def simulate_human_browsing(session, instance_id: str):
    """
    Simulate natural human browsing behavior on the current page.
    Call this periodically during scraping to look more human.
    """
    actions = [
        _random_mouse_move,
        lambda s, i: _random_scroll(s, i),
        lambda s, i: _random_pause(),
    ]
    
    # Do 1-3 random human actions
    for action in random.sample(actions, k=random.randint(1, 2)):
        try:
            await action(session, instance_id)
        except:
            pass


async def _random_scroll(session, instance_id: str):
    """Small random scroll — humans fidget and re-read."""
    # Gaussian distribution centered on small downward scrolls
    amount = int(random.gauss(150, 200))
    amount = max(-300, min(amount, 500))
    try:
        await session.call_tool(
            "execute_script",
            arguments={
                "instance_id": instance_id,
                "script": f"window.scrollBy({{top: {amount}, behavior: 'smooth'}})"
            }
        )
        await asyncio.sleep(random.uniform(0.3, 1.5))
    except:
        pass


async def _random_pause():
    """Just... pause. Humans do that."""
    await asyncio.sleep(random.uniform(0.5, 2.0))


async def human_delay(min_sec: float = 0.5, max_sec: float = 2.0):
    """Random delay to simulate human behavior"""
    delay = random.uniform(min_sec, max_sec)
    await asyncio.sleep(delay)


async def typing_delay():
    """Short delay between actions like a human would have"""
    await asyncio.sleep(random.uniform(0.1, 0.3))


def get_random_typing_delay_ms() -> int:
    """
    Get a randomized typing delay that mimics human typing patterns.
    
    Humans type at varying speeds:
    - Average: ~50-100ms between keystrokes
    - Occasional pauses: ~150-300ms (thinking, finger repositioning)
    - Rare longer pauses: ~400-600ms (word boundaries, mistakes)
    """
    r = random.random()
    if r < 0.7:
        return random.randint(40, 120)
    elif r < 0.9:
        return random.randint(120, 250)
    else:
        return random.randint(250, 500)


async def type_like_human(session, instance_id: str, selector: str, text: str, clear_first: bool = True):
    """
    Type text character by character with human-like randomized timing.
    Includes occasional typo-correction simulation.
    """
    try:
        if clear_first:
            await session.call_tool(
                "evaluate_javascript",
                arguments={
                    "instance_id": instance_id,
                    "expression": f"""
                        const el = document.querySelector('{selector}');
                        if (el) {{
                            el.focus();
                            el.value = '';
                            el.dispatchEvent(new Event('input', {{bubbles: true}}));
                        }}
                    """
                }
            )
            await asyncio.sleep(random.uniform(0.1, 0.3))
        
        for i, char in enumerate(text):
            delay_ms = get_random_typing_delay_ms()
            
            await session.call_tool(
                "type_text",
                arguments={
                    "instance_id": instance_id,
                    "selector": selector,
                    "text": char,
                    "clear_first": False,
                    "delay_ms": 0
                }
            )
            
            await asyncio.sleep(delay_ms / 1000)
            
            # Occasional micro-pause after spaces or punctuation
            if char in ' .,!?':
                if random.random() < 0.3:
                    await asyncio.sleep(random.uniform(0.05, 0.15))
        
        return True
        
    except Exception as e:
        print(f"   ⚠️ Human typing failed: {e}")
        return False


async def page_load_delay():
    """
    Delay after page loads - humans look at the page before acting.
    """
    r = random.random()
    if r < 0.6:
        await asyncio.sleep(random.uniform(2.0, 4.0))
    elif r < 0.85:
        await asyncio.sleep(random.uniform(4.0, 8.0))
    else:
        await asyncio.sleep(random.uniform(8.0, 15.0))


async def scroll_delay():
    """
    Delay between scroll actions - humans scroll, pause, scroll.
    """
    r = random.random()
    if r < 0.5:
        await asyncio.sleep(random.uniform(0.5, 1.5))
    elif r < 0.8:
        await asyncio.sleep(random.uniform(1.5, 3.5))
    else:
        await asyncio.sleep(random.uniform(3.5, 7.0))


def between_search_delay():
    """
    Get a delay value between different search queries.
    Humans don't search at metronomic intervals.
    Returns seconds (float).
    """
    r = random.random()
    if r < 0.2:
        return random.uniform(3, 8)      # Quick (knew what to search)
    elif r < 0.6:
        return random.uniform(8, 20)     # Normal browsing
    elif r < 0.85:
        return random.uniform(20, 45)    # Reading results / distracted
    else:
        return random.uniform(45, 90)    # Got distracted, phone, etc.


def random_viewport():
    """Return random but common viewport dimensions"""
    viewports = [
        (1920, 1080),
        (1366, 768),
        (1536, 864),
        (1440, 900),
        (1280, 720),
    ]
    return random.choice(viewports)


def get_stealth_spawn_options(user_data_dir: str = None, headless: bool = False) -> dict:
    """Get spawn_browser options optimized for stealth with persistent profile."""
    profile_dir = ensure_profile_dir(user_data_dir)
    width, height = random_viewport()
    
    return {
        "headless": headless,
        "user_data_dir": profile_dir,
        "viewport_width": width,
        "viewport_height": height,
        "sandbox": False,
    }
