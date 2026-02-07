"""
Stealth helpers for more human-like browser behavior
"""
import asyncio
import random


async def human_delay(min_sec: float = 0.5, max_sec: float = 2.0):
    """Random delay to simulate human behavior"""
    delay = random.uniform(min_sec, max_sec)
    await asyncio.sleep(delay)


async def typing_delay():
    """Short delay between actions like a human would have"""
    await asyncio.sleep(random.uniform(0.1, 0.3))


async def page_load_delay():
    """Longer delay for page loads"""
    await asyncio.sleep(random.uniform(2.0, 4.0))


async def scroll_delay():
    """Delay between scroll actions"""
    await asyncio.sleep(random.uniform(0.8, 1.5))


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


def get_stealth_spawn_options(user_data_dir: str, headless: bool = False) -> dict:
    """Get spawn_browser options optimized for stealth"""
    width, height = random_viewport()
    
    return {
        "headless": headless,
        "user_data_dir": user_data_dir,
        "viewport_width": width,
        "viewport_height": height,
        "sandbox": False,  # Required when Chrome won't connect properly
        # Don't set custom user_agent - let nodriver use realistic defaults
        # Don't block resources - FB might detect that
    }
