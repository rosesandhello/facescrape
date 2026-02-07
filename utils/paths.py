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
    
    Checks common locations:
    1. ~/stealth-browser-mcp/src/server.py
    2. Sibling directory ../stealth-browser-mcp/src/server.py
    3. /home/bosh/stealth-browser-mcp/src/server.py (legacy)
    
    Returns path or empty string if not found.
    """
    candidates = [
        Path.home() / "stealth-browser-mcp" / "src" / "server.py",
        get_project_root().parent / "stealth-browser-mcp" / "src" / "server.py",
        Path("/home/bosh/stealth-browser-mcp/src/server.py"),
    ]
    
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
