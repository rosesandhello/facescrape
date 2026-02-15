"""
SQLite Database for ScrapedFace - FB Arbitrage Scanner

Stores:
- FB listings (raw scrapes)
- AI identifications 
- eBay search results and matches
- Arbitrage opportunities
"""
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from utils.paths import get_database_path

DB_PATH = get_database_path()


def get_connection() -> sqlite3.Connection:
    """Get database connection with row factory"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initialize database schema"""
    conn = get_connection()
    cursor = conn.cursor()
    
    # FB Marketplace listings
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS fb_listings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fb_id TEXT UNIQUE,
            title TEXT NOT NULL,
            description TEXT,
            price REAL,
            currency TEXT DEFAULT 'USD',
            location TEXT,
            image_url TEXT,
            image_urls TEXT,  -- JSON array of all image URLs
            listing_url TEXT,
            seller_name TEXT,
            scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            raw_data TEXT  -- Full JSON of scraped data
        )
    """)
    
    # AI product identifications
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ai_identifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fb_listing_id INTEGER NOT NULL,
            identified_title TEXT,
            brand TEXT,
            model TEXT,
            category TEXT,
            condition TEXT,
            is_defective BOOLEAN DEFAULT FALSE,  -- "for parts", "no core", "broken", etc.
            defect_reason TEXT,  -- Why it's flagged as defective
            search_queries TEXT,  -- JSON array of generated queries
            confidence REAL,
            vision_model TEXT,
            text_model TEXT,
            raw_vision_response TEXT,
            raw_text_response TEXT,
            identified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (fb_listing_id) REFERENCES fb_listings(id)
        )
    """)
    
    # eBay sold listings (search results)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ebay_listings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ebay_id TEXT,
            title TEXT NOT NULL,
            description TEXT,
            price REAL,
            currency TEXT DEFAULT 'USD',
            sold_date TEXT,
            condition TEXT,
            image_url TEXT,
            listing_url TEXT,
            search_query TEXT,  -- The query that found this
            scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            raw_data TEXT,
            UNIQUE(ebay_id, search_query)
        )
    """)
    
    # AI matches between FB and eBay listings
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ai_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fb_listing_id INTEGER NOT NULL,
            ebay_listing_id INTEGER NOT NULL,
            is_match BOOLEAN,
            confidence REAL,
            title_similarity REAL,
            image_match BOOLEAN,
            image_confidence REAL,
            fb_synthesis TEXT,
            ebay_synthesis TEXT,
            reasoning TEXT,
            matched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (fb_listing_id) REFERENCES fb_listings(id),
            FOREIGN KEY (ebay_listing_id) REFERENCES ebay_listings(id)
        )
    """)
    
    # Arbitrage opportunities (final results)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS opportunities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fb_listing_id INTEGER NOT NULL,
            ebay_listing_id INTEGER,  -- Best match
            fb_price REAL,
            ebay_price REAL,  -- Median or matched price
            profit_margin REAL,  -- (ebay - fb) / fb * 100
            profit_dollars REAL,
            status TEXT DEFAULT 'new',  -- new, reviewed, purchased, sold, skipped
            notes TEXT,
            is_defective BOOLEAN DEFAULT FALSE,  -- Inherited from AI identification
            found_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            reviewed_at TIMESTAMP,
            FOREIGN KEY (fb_listing_id) REFERENCES fb_listings(id),
            FOREIGN KEY (ebay_listing_id) REFERENCES ebay_listings(id)
        )
    """)
    
    # Create indexes for common queries
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_fb_scraped ON fb_listings(scraped_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_fb_price ON fb_listings(price)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ebay_query ON ebay_listings(search_query)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_opportunities_margin ON opportunities(profit_margin)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_opportunities_status ON opportunities(status)")
    
    conn.commit()
    conn.close()
    print(f"✅ Database initialized at {DB_PATH}")


# ============ Insert Functions ============

def insert_fb_listing(
    title: str,
    price: float,
    fb_id: str = None,
    description: str = None,
    location: str = None,
    image_url: str = None,
    image_urls: list[str] = None,
    listing_url: str = None,
    seller_name: str = None,
    raw_data: dict = None
) -> int:
    """Insert or update FB listing, return ID"""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Check if exists by fb_id
    if fb_id:
        cursor.execute("SELECT id FROM fb_listings WHERE fb_id = ?", (fb_id,))
        row = cursor.fetchone()
        if row:
            conn.close()
            return row['id']
    
    cursor.execute("""
        INSERT INTO fb_listings 
        (fb_id, title, description, price, location, image_url, image_urls, 
         listing_url, seller_name, raw_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        fb_id, title, description, price, location, image_url,
        json.dumps(image_urls) if image_urls else None,
        listing_url, seller_name,
        json.dumps(raw_data) if raw_data else None
    ))
    
    listing_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return listing_id


def insert_ai_identification(
    fb_listing_id: int,
    identified_title: str,
    brand: str = None,
    model: str = None,
    category: str = None,
    condition: str = None,
    is_defective: bool = False,
    defect_reason: str = None,
    search_queries: list[str] = None,
    confidence: float = None,
    vision_model: str = None,
    text_model: str = None,
    raw_vision_response: str = None,
    raw_text_response: str = None
) -> int:
    """Insert AI identification result"""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO ai_identifications
        (fb_listing_id, identified_title, brand, model, category, condition,
         is_defective, defect_reason, search_queries, confidence,
         vision_model, text_model, raw_vision_response, raw_text_response)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        fb_listing_id, identified_title, brand, model, category, condition,
        is_defective, defect_reason,
        json.dumps(search_queries) if search_queries else None,
        confidence, vision_model, text_model,
        raw_vision_response, raw_text_response
    ))
    
    ident_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return ident_id


def insert_ebay_listing(
    title: str,
    price: float,
    search_query: str,
    ebay_id: str = None,
    description: str = None,
    sold_date: str = None,
    condition: str = None,
    image_url: str = None,
    listing_url: str = None,
    raw_data: dict = None
) -> int:
    """Insert eBay listing, return ID (or existing ID if duplicate)"""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Check for duplicate
    if ebay_id:
        cursor.execute(
            "SELECT id FROM ebay_listings WHERE ebay_id = ? AND search_query = ?",
            (ebay_id, search_query)
        )
        row = cursor.fetchone()
        if row:
            conn.close()
            return row['id']
    
    cursor.execute("""
        INSERT INTO ebay_listings
        (ebay_id, title, description, price, sold_date, condition,
         image_url, listing_url, search_query, raw_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        ebay_id, title, description, price, sold_date, condition,
        image_url, listing_url, search_query,
        json.dumps(raw_data) if raw_data else None
    ))
    
    listing_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return listing_id


def insert_ai_match(
    fb_listing_id: int,
    ebay_listing_id: int,
    is_match: bool,
    confidence: float,
    title_similarity: float = None,
    image_match: bool = None,
    image_confidence: float = None,
    reasoning: str = None,
    fb_synthesis: str = None,
    ebay_synthesis: str = None
) -> int:
    """Insert AI match result"""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO ai_matches
        (fb_listing_id, ebay_listing_id, is_match, confidence,
         title_similarity, image_match, image_confidence, fb_synthesis, ebay_synthesis, reasoning)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        fb_listing_id, ebay_listing_id, is_match, confidence,
        title_similarity, image_match, image_confidence, fb_synthesis, ebay_synthesis, reasoning
    ))
    
    match_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return match_id


def insert_opportunity(
    fb_listing_id: int,
    fb_price: float,
    ebay_price: float,
    ebay_listing_id: int = None,
    is_defective: bool = False,
    status: str = 'new',
    notes: str = None
) -> int:
    """Insert arbitrage opportunity"""
    conn = get_connection()
    cursor = conn.cursor()
    
    profit_dollars = ebay_price - fb_price
    profit_margin = (profit_dollars / fb_price * 100) if fb_price > 0 else 0
    
    cursor.execute("""
        INSERT INTO opportunities
        (fb_listing_id, ebay_listing_id, fb_price, ebay_price,
         profit_margin, profit_dollars, is_defective, status, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        fb_listing_id, ebay_listing_id, fb_price, ebay_price,
        profit_margin, profit_dollars, is_defective, status, notes
    ))
    
    opp_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return opp_id


# ============ Query Functions ============

def get_recent_opportunities(limit: int = 20, min_margin: float = 20.0) -> list[dict]:
    """Get recent opportunities with good margins"""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT 
            o.*,
            fb.title as fb_title,
            fb.image_url as fb_image,
            fb.listing_url as fb_url,
            eb.title as ebay_title,
            eb.image_url as ebay_image
        FROM opportunities o
        JOIN fb_listings fb ON o.fb_listing_id = fb.id
        LEFT JOIN ebay_listings eb ON o.ebay_listing_id = eb.id
        WHERE o.profit_margin >= ? AND o.is_defective = FALSE
        ORDER BY o.found_at DESC
        LIMIT ?
    """, (min_margin, limit))
    
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_defective_listings(limit: int = 50) -> list[dict]:
    """Get listings flagged as defective/for-parts"""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT 
            fb.title, fb.price, fb.image_url,
            ai.condition, ai.defect_reason, ai.identified_title
        FROM fb_listings fb
        JOIN ai_identifications ai ON fb.id = ai.fb_listing_id
        WHERE ai.is_defective = TRUE
        ORDER BY fb.scraped_at DESC
        LIMIT ?
    """, (limit,))
    
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_fb_listing(listing_id: int) -> Optional[dict]:
    """Get single FB listing by ID"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM fb_listings WHERE id = ?", (listing_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def update_opportunity_status(opp_id: int, status: str, notes: str = None):
    """Update opportunity status"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE opportunities 
        SET status = ?, notes = ?, reviewed_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (status, notes, opp_id))
    conn.commit()
    conn.close()


# Initialize on import
if __name__ == "__main__":
    init_db()
else:
    # Ensure DB exists
    if not DB_PATH.exists():
        init_db()
