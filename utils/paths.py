"""
Path utilities for ScrapedFace

Auto-detects paths for stealth-browser-mcp and other dependencies.
"""
import os
from pathlib import Path


def get_project_root() -> Path:
    """Get the project root directory"""
    return Path(__file__).parent.parent.resolve()


def find_stealth_browser() -> str:
    """
    Find stealth-browser-mcp installation.
    
    Resolution order:
    1. STEALTH_BROWSER_PATH env var (if set)
    2. ~/stealth-browser-mcp/src/server.py
    3. Sibling directory ../stealth-browser-mcp/src/server.py
    4. ./stealth-browser-mcp/src/server.py (inside project)
    
    Returns path or empty string if not found.
    """
    candidates = [
        get_project_root() / "stealth-browser-mcp" / "src" / "server.py",
        get_project_root().parent / "stealth-browser-mcp" / "src" / "server.py",
        Path.home() / "stealth-browser-mcp" / "src" / "server.py",
    ]
    
    # Also check STEALTH_BROWSER_PATH env var
    env_path = os.environ.get("STEALTH_BROWSER_PATH")
    if env_path:
        candidates.insert(0, Path(env_path))
    
    for path in candidates:
        if path.exists():
            return str(path)
    
    return ""


def get_default_user_data_dir() -> str:
    """Get default browser profile directory"""
    return "/tmp/scrapedface-profile"


def get_config_path() -> Path:
    """Get path to config.json"""
    return get_project_root() / "config.json"


def get_database_path() -> Path:
    """Get path to SQLite database"""
    return get_project_root() / "arbitrage.db"


def get_reports_dir() -> Path:
    """Get path to reports directory"""
    reports = get_project_root() / "reports"
    reports.mkdir(exist_ok=True)
    return reports
