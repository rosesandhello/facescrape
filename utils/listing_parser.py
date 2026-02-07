"""
Parse Facebook Marketplace listings from page content
"""
import re
import json
from dataclasses import dataclass, asdict
from typing import Optional
from datetime import datetime


@dataclass
class Listing:
    """Represents a single FB Marketplace listing"""
    title: str
    price: float
    price_raw: str  # Original price string
    location: str
    condition: Optional[str] = None
    listing_url: Optional[str] = None
    image_url: Optional[str] = None
    seller_name: Optional[str] = None
    posted_time: Optional[str] = None  # "2 days ago", "1 week ago", etc.
    listing_id: Optional[str] = None
    is_pending: bool = False  # Listing marked as "pending"
    listing_age_days: Optional[int] = None  # Calculated age in days
    
    # Arbitrage analysis (filled in later)
    reference_price: Optional[float] = None
    reference_source: Optional[str] = None
    potential_profit: Optional[float] = None
    profit_percent: Optional[float] = None
    is_arbitrage_opportunity: bool = False
    
    # Pickup cost (filled in by arbitrage service)
    pickup_cost: Optional[float] = None
    pickup_distance: Optional[float] = None
    
    def to_dict(self):
        return asdict(self)
    
    def __str__(self):
        return f"{self.title} - ${self.price:.2f} ({self.location})"


def clean_title_for_search(title: str) -> str:
    """
    Clean a FB Marketplace listing title for use in eBay search.
    Removes junk like prices, "Partner listing", etc.
    """
    if not title:
        return ""
    
    cleaned = title
    
    # Remove "Partner listing" prefix
    cleaned = re.sub(r'^Partner\s+listing\s*', '', cleaned, flags=re.IGNORECASE)
    
    # Remove price patterns like "$164.99" anywhere in title
    cleaned = re.sub(r'\$[\d,]+(?:\.\d{2})?\s*', '', cleaned)
    
    # Remove "Incl" prefix patterns
    cleaned = re.sub(r'^Incl\s+\d+\s+', '', cleaned, flags=re.IGNORECASE)
    
    # Remove location suffixes like "Youngstown, OH" or "Pittsburgh, PA"
    cleaned = re.sub(r'\s+[A-Z][a-z]+,\s*[A-Z]{2}\s*$', '', cleaned)
    
    # Remove "Listed X ago" or "Listed in..."
    cleaned = re.sub(r'\s*Listed\s+.*$', '', cleaned, flags=re.IGNORECASE)
    
    # Remove trailing dimensions/specs that are too specific
    # e.g., "0 Degree 35.0 X 35mm" - keep brand/model, remove exact specs
    cleaned = re.sub(r'\s+\d+(?:\.\d+)?\s*[xX]\s*\d+(?:\.\d+)?(?:mm|cm|in)?\s*$', '', cleaned)
    
    # Truncate to first 80 chars for more general search (keeps main product name)
    if len(cleaned) > 80:
        # Try to cut at a word boundary
        truncated = cleaned[:80]
        last_space = truncated.rfind(' ')
        if last_space > 50:
            cleaned = truncated[:last_space]
        else:
            cleaned = truncated
    
    # Clean up extra whitespace
    cleaned = ' '.join(cleaned.split())
    
    return cleaned.strip()


def extract_product_keywords(title: str) -> str:
    """
    Extract key product identifiers for eBay search.
    Focuses on brand names, model numbers, and key descriptors.
    """
    cleaned = clean_title_for_search(title)
    
    # Common brands to look for and preserve
    brands = [
        'apple', 'iphone', 'ipad', 'macbook', 'samsung', 'sony', 'nintendo',
        'playstation', 'xbox', 'rolex', 'omega', 'seiko', 'american eagle',
        'silver', 'gold', 'platinum', 'sterling', 'burgtec', 'shimano'
    ]
    
    words = cleaned.lower().split()
    
    # Keep brand words and model-like words (alphanumeric)
    keywords = []
    for word in words:
        # Keep if it's a known brand
        if any(brand in word for brand in brands):
            keywords.append(word)
        # Keep model numbers (mix of letters and numbers)
        elif re.match(r'^[a-z]+\d+|\d+[a-z]+', word, re.IGNORECASE):
            keywords.append(word)
        # Keep capitalized words (likely product names)
        elif word[0].isupper() if word else False:
            keywords.append(word)
        # Keep descriptive words
        elif word in ['new', 'sealed', 'vintage', 'rare', 'limited', 'edition']:
            keywords.append(word)
    
    # Limit to most important keywords
    return ' '.join(keywords[:8]) if keywords else cleaned[:50]


def parse_price(price_str: str) -> Optional[float]:
    """Extract numeric price from string like '$1,234' or 'Free'"""
    if not price_str:
        return None
    
    price_str = price_str.strip().lower()
    
    if price_str == "free":
        return 0.0
    
    # Remove currency symbols and commas
    cleaned = re.sub(r'[^\d.]', '', price_str)
    
    try:
        return float(cleaned)
    except ValueError:
        return None


def extract_listings_from_html(html_content: str) -> list[Listing]:
    """
    Extract listings from raw HTML/text content.
    
    FB Marketplace structure changes, so we use multiple strategies.
    """
    listings = []
    
    # Strategy 1: Look for JSON-LD structured data
    json_ld_listings = extract_from_json_ld(html_content)
    if json_ld_listings:
        listings.extend(json_ld_listings)
    
    # Strategy 2: Look for common patterns in the text
    pattern_listings = extract_from_patterns(html_content)
    if pattern_listings:
        # Dedupe by title
        existing_titles = {l.title.lower() for l in listings}
        for listing in pattern_listings:
            if listing.title.lower() not in existing_titles:
                listings.append(listing)
    
    return listings


def extract_from_json_ld(content: str) -> list[Listing]:
    """Try to find JSON-LD product data"""
    listings = []
    
    # Look for script tags with JSON-LD
    json_ld_pattern = r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>'
    matches = re.findall(json_ld_pattern, content, re.DOTALL | re.IGNORECASE)
    
    for match in matches:
        try:
            data = json.loads(match)
            if isinstance(data, list):
                for item in data:
                    listing = parse_json_ld_item(item)
                    if listing:
                        listings.append(listing)
            else:
                listing = parse_json_ld_item(data)
                if listing:
                    listings.append(listing)
        except json.JSONDecodeError:
            continue
    
    return listings


def parse_json_ld_item(data: dict) -> Optional[Listing]:
    """Parse a single JSON-LD item"""
    if data.get("@type") not in ("Product", "Offer", "ItemPage"):
        return None
    
    name = data.get("name", "")
    if not name:
        return None
    
    # Get price
    price = None
    offers = data.get("offers", {})
    if isinstance(offers, dict):
        price = parse_price(str(offers.get("price", "")))
    elif isinstance(offers, list) and offers:
        price = parse_price(str(offers[0].get("price", "")))
    
    if price is None:
        price = parse_price(str(data.get("price", "")))
    
    if price is None:
        return None
    
    return Listing(
        title=name,
        price=price,
        price_raw=str(offers.get("price", price)) if isinstance(offers, dict) else str(price),
        location=data.get("areaServed", ""),
        condition=data.get("itemCondition", ""),
        listing_url=data.get("url", ""),
        image_url=data.get("image", ""),
    )


def extract_from_patterns(content: str) -> list[Listing]:
    """
    Extract listings using regex patterns.
    FB Marketplace listings typically follow patterns like:
    - "$XXX" followed by title text
    - Location info nearby
    """
    listings = []
    
    # Pattern: Price followed by text (common in FB Marketplace)
    # Looking for: $XXX Title of item Location
    price_pattern = r'\$[\d,]+(?:\.\d{2})?'
    
    # Find all prices
    prices = list(re.finditer(price_pattern, content))
    
    for match in prices:
        price_str = match.group()
        price = parse_price(price_str)
        
        if price is None or price > 50000:  # Sanity check
            continue
        
        # Get surrounding context (500 chars after price)
        start = match.end()
        end = min(start + 500, len(content))
        context = content[start:end]
        
        # Try to extract title (first line of text after price)
        title_match = re.search(r'^[\s\n]*([^\n$]{5,100})', context)
        if not title_match:
            continue
        
        title = title_match.group(1).strip()
        
        # Clean up title
        title = re.sub(r'\s+', ' ', title)
        title = title.strip('.,;:!?')
        
        # Skip if title looks like navigation/UI text
        skip_patterns = [
            r'^(log in|sign up|marketplace|home|notifications)',
            r'^(see more|view all|filter|sort)',
            r'^(message seller|save|share|hide)',
        ]
        
        if any(re.match(p, title, re.IGNORECASE) for p in skip_patterns):
            continue
        
        # Try to find location
        location = ""
        loc_match = re.search(r'([\d\.]+ miles? away|in .{3,30})', context, re.IGNORECASE)
        if loc_match:
            location = loc_match.group(1)
        
        # Try to find condition
        condition = None
        cond_match = re.search(r'(new|used|like new|good|fair|refurbished)', context, re.IGNORECASE)
        if cond_match:
            condition = cond_match.group(1).title()
        
        # Check if pending
        is_pending = bool(re.search(r'\b(pending|sale pending)\b', context, re.IGNORECASE))
        
        # Try to find posted time and calculate age
        posted_time = None
        listing_age_days = None
        time_match = re.search(
            r'(listed\s+)?(\d+)\s*(minute|hour|day|week|month)s?\s*ago',
            context, re.IGNORECASE
        )
        if time_match:
            num = int(time_match.group(2))
            unit = time_match.group(3).lower()
            posted_time = f"{num} {unit}{'s' if num > 1 else ''} ago"
            
            # Calculate age in days
            if unit == 'minute':
                listing_age_days = 0
            elif unit == 'hour':
                listing_age_days = 0 if num < 12 else 1
            elif unit == 'day':
                listing_age_days = num
            elif unit == 'week':
                listing_age_days = num * 7
            elif unit == 'month':
                listing_age_days = num * 30
        
        listing = Listing(
            title=title,
            price=price,
            price_raw=price_str,
            location=location,
            condition=condition,
            is_pending=is_pending,
            posted_time=posted_time,
            listing_age_days=listing_age_days,
        )
        
        listings.append(listing)
    
    # Dedupe by similar titles
    seen_titles = set()
    unique_listings = []
    
    for listing in listings:
        # Normalize title for comparison
        normalized = re.sub(r'[^a-z0-9]', '', listing.title.lower())
        if normalized not in seen_titles and len(normalized) > 5:
            seen_titles.add(normalized)
            unique_listings.append(listing)
    
    return unique_listings


def extract_listing_urls(content: str) -> list[str]:
    """Extract marketplace listing URLs"""
    pattern = r'facebook\.com/marketplace/item/(\d+)'
    matches = re.findall(pattern, content)
    
    return [f"https://www.facebook.com/marketplace/item/{m}" for m in set(matches)]


def filter_listings(
    listings: list[Listing],
    max_age_days: int = None,
    exclude_pending: bool = True
) -> list[Listing]:
    """
    Filter listings by age and pending status.
    
    Args:
        listings: List of listings to filter
        max_age_days: Maximum listing age in days (None = no filter)
        exclude_pending: Remove listings marked as "pending"
    
    Returns:
        Filtered list of listings
    """
    filtered = []
    
    for listing in listings:
        # Skip pending listings
        if exclude_pending and listing.is_pending:
            continue
        
        # Skip old listings
        if max_age_days is not None and listing.listing_age_days is not None:
            if listing.listing_age_days > max_age_days:
                continue
        
        filtered.append(listing)
    
    return filtered


if __name__ == "__main__":
    # Test with sample content
    sample = """
    $450 Gaming Laptop RTX 3060
    Used - Like New
    5 miles away
    
    $25 Nintendo Switch Games Bundle
    Good condition
    Pittsburgh, PA
    
    $1,200 MacBook Pro 2021
    Like New
    3 miles away
    """
    
    listings = extract_from_patterns(sample)
    print(f"Found {len(listings)} listings:")
    for l in listings:
        print(f"  - {l}")
