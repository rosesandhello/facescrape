"""
Configuration for ScrapedFace - FB Marketplace Arbitrage Scanner

Uses stealth browser scraping - no API credentials required for eBay!
"""
import os
from dataclasses import dataclass, field
from search_terms import parse_search_terms, get_all_search_variations
from typing import Optional
import json
from pathlib import Path

from utils.paths import find_stealth_browser, get_default_user_data_dir, get_config_path

CONFIG_FILE = get_config_path()


@dataclass
class Config:
    # Search settings
    category: str = "iphone"  # e.g., "iphone", "nintendo switch", "ps5"
    categories: list = field(default_factory=list)  # Multiple search terms (comma-separated in input)
    zip_code: str = "15213"  # Pittsburgh default
    radius_miles: int = 25
    expand_search_terms: bool = True  # Include synonyms and misspellings
    
    # Arbitrage settings
    min_profit_dollars: float = 30.0  # Minimum dollar profit to alert
    min_profit_percent: float = 20.0  # Minimum percentage profit to alert (e.g., 20 = 20%)
    
    # Fee estimates for profit calculation
    ebay_fee_percent: float = 13.25  # eBay final value fee
    shipping_estimate: float = 15.0  # Estimated shipping cost
    
    # Discord
    discord_webhook_url: str = ""
    
    # PriceCharting API (optional, for games/collectibles)
    pricecharting_api_key: str = ""
    
    # Scan settings
    scan_interval_minutes: int = 5
    max_listings_per_scan: int = 50
    initial_batch_size: int = 10  # Start with this many, extend by 25 if no matches
    
    # Browser settings
    headless: bool = False  # Show browser for debugging/login (MUST be False for first run to login)
    stealth_browser_path: str = ""  # Auto-detected on load
    user_data_dir: str = ""  # Auto-detected on load
    
    def __post_init__(self):
        """Auto-detect paths if not set"""
        if not self.stealth_browser_path:
            self.stealth_browser_path = find_stealth_browser()
        if not self.user_data_dir:
            self.user_data_dir = get_default_user_data_dir()
    
    # Price lookup preference
    # "ebay" = eBay sold listings (scraped, no API needed)
    # "pricecharting" = PriceCharting API (for games/collectibles)
    # "both" = try PriceCharting first, fall back to eBay
    price_source: str = "ebay"
    
    # eBay scraping settings
    ebay_condition: str = "used"  # "used", "new", or "any"
    ebay_headless: bool = True  # eBay scraper can be headless (no login needed)
    
    # AI matching settings (uses local Ollama with Moondream vision model)
    use_ai_matching: bool = True  # Use AI to verify eBay results match FB listing
    ai_min_confidence: float = 0.6  # Minimum confidence to consider a match (0.0-1.0)
    
    # Pickup cost calculation
    vehicle_mpg: float = 25.0  # Fuel efficiency in miles per gallon
    gas_price_override: float = 0.0  # Override gas price (0 = auto-lookup by zip)
    
    # FB Marketplace filters
    max_listing_age_days: int = 30  # Skip listings older than this
    exclude_pending: bool = True  # Skip listings marked as "pending"
    sort_by_price: bool = True  # Sort by lowest price first
    
    # eBay profit calculation
    use_lowest_sold_price: bool = True  # Use lowest (not avg) sold price for profit calc
    include_shipping_in_profit: bool = True  # Factor in shipping costs
    
    def save(self):
        """Save config to JSON file"""
        with open(CONFIG_FILE, 'w') as f:
            json.dump(self.__dict__, f, indent=2)
        print(f"✅ Config saved to {CONFIG_FILE}")
    
    @classmethod
    def load(cls) -> 'Config':
        """Load config from JSON file or create default"""
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE) as f:
                data = json.load(f)
            # Handle old configs with removed fields
            for old_field in ['ebay_app_id', 'ebay_cert_id', 'ebay_dev_id']:
                data.pop(old_field, None)
            config = cls(**data)
            return config
        return cls()
    
    def validate(self) -> list[str]:
        """Check for missing required settings"""
        issues = []
        
        if not self.category:
            issues.append("category is not set")
        
        if not self.discord_webhook_url:
            issues.append("discord_webhook_url is not set (notifications won't work)")
        
        if self.price_source in ("pricecharting", "both") and not self.pricecharting_api_key:
            issues.append("PriceCharting API key not set (using eBay only)")
        
        return issues


def interactive_setup():
    """Interactive configuration wizard"""
    print("=" * 60)
    print("FB Marketplace Arbitrage Scanner - Setup")
    print("=" * 60)
    
    config = Config.load()
    
    print("\n📦 SEARCH SETTINGS\n")
    
    # Support multiple search terms
    current_categories = ', '.join(config.categories) if config.categories else config.category
    print("Enter search terms separated by commas (e.g., 'iphone, nintendo switch, ps5')")
    categories_input = input(f"Search terms [{current_categories}]: ").strip()
    if categories_input:
        config.categories = parse_search_terms(categories_input)
        config.category = config.categories[0] if config.categories else "iphone"
    elif not config.categories:
        config.categories = [config.category] if config.category else ["iphone"]
    
    # Synonym expansion
    expand_default = 'y' if config.expand_search_terms else 'n'
    expand_input = input(f"Expand with synonyms/misspellings? (y/n) [{expand_default}]: ").strip().lower()
    if expand_input:
        config.expand_search_terms = expand_input == 'y'
    
    config.zip_code = input(f"ZIP code [{config.zip_code}]: ").strip() or config.zip_code
    config.radius_miles = int(input(f"Search radius (miles) [{config.radius_miles}]: ").strip() or config.radius_miles)
    
    print("\n💰 ARBITRAGE SETTINGS\n")
    
    config.min_profit_dollars = float(input(f"Minimum profit in dollars (e.g., 30 = $30) [{config.min_profit_dollars}]: ").strip() or config.min_profit_dollars)
    config.min_profit_percent = float(input(f"Minimum profit percentage (e.g., 20 = 20%) [{config.min_profit_percent}]: ").strip() or config.min_profit_percent)
    config.ebay_fee_percent = float(input(f"eBay fee percentage (e.g., 13.25) [{config.ebay_fee_percent}]: ").strip() or config.ebay_fee_percent)
    config.shipping_estimate = float(input(f"Estimated shipping cost [$] [{config.shipping_estimate}]: ").strip() or config.shipping_estimate)
    
    print("\n🚗 PICKUP COST SETTINGS\n")
    print("Calculate fuel cost to pick up items based on distance.")
    
    config.vehicle_mpg = float(input(f"Your vehicle's MPG (0 to disable) [{config.vehicle_mpg}]: ").strip() or config.vehicle_mpg)
    if config.vehicle_mpg > 0:
        config.gas_price_override = float(input(f"Gas price override (0 = auto-lookup) [${config.gas_price_override}]: ").strip() or config.gas_price_override)
    
    print("\n📋 FB LISTING FILTERS\n")
    
    config.max_listing_age_days = int(input(f"Max listing age in days (0 = no limit) [{config.max_listing_age_days}]: ").strip() or config.max_listing_age_days)
    
    exclude_default = 'y' if config.exclude_pending else 'n'
    exclude_input = input(f"Exclude 'pending' listings? (y/n) [{exclude_default}]: ").strip().lower()
    if exclude_input:
        config.exclude_pending = exclude_input == 'y'
    
    sort_default = 'y' if config.sort_by_price else 'n'
    sort_input = input(f"Sort by lowest price first? (y/n) [{sort_default}]: ").strip().lower()
    if sort_input:
        config.sort_by_price = sort_input == 'y'
    
    print("\n🔔 DISCORD NOTIFICATION\n")
    
    webhook_display = config.discord_webhook_url[:30] + '...' if config.discord_webhook_url else 'not set'
    config.discord_webhook_url = input(f"Discord webhook URL [{webhook_display}]: ").strip() or config.discord_webhook_url
    
    print("\n📊 PRICE LOOKUP\n")
    print("Options:")
    print("  ebay - Scrape eBay sold listings (no API needed)")
    print("  pricecharting - Use PriceCharting API (games/collectibles)")
    print("  both - Try PriceCharting first, fall back to eBay")
    config.price_source = input(f"Price source [{config.price_source}]: ").strip() or config.price_source
    
    if config.price_source in ("pricecharting", "both"):
        print("\nPriceCharting API key (from pricecharting.com/api-documentation):")
        pc_display = config.pricecharting_api_key[:10] + '...' if config.pricecharting_api_key else 'not set'
        config.pricecharting_api_key = input(f"  API key [{pc_display}]: ").strip() or config.pricecharting_api_key
    
    # eBay price settings
    print("\neBay condition filter:")
    print("  used - Compare against used item sales")
    print("  new - Compare against new item sales")
    print("  any - Compare against all sales")
    config.ebay_condition = input(f"eBay condition [{config.ebay_condition}]: ").strip() or config.ebay_condition
    
    lowest_default = 'y' if config.use_lowest_sold_price else 'n'
    lowest_input = input(f"Use lowest eBay sold price (vs average)? (y/n) [{lowest_default}]: ").strip().lower()
    if lowest_input:
        config.use_lowest_sold_price = lowest_input == 'y'
    
    shipping_default = 'y' if config.include_shipping_in_profit else 'n'
    shipping_input = input(f"Include shipping in profit calculation? (y/n) [{shipping_default}]: ").strip().lower()
    if shipping_input:
        config.include_shipping_in_profit = shipping_input == 'y'
    
    print("\n🤖 AI MATCHING\n")
    print("Use local AI (Ollama) to verify eBay results match the FB listing.")
    
    ai_default = 'y' if config.use_ai_matching else 'n'
    ai_input = input(f"Enable AI matching? (y/n) [{ai_default}]: ").strip().lower()
    if ai_input:
        config.use_ai_matching = ai_input == 'y'
    
    if config.use_ai_matching:
        config.ai_min_confidence = float(input(f"Minimum match confidence (0.0-1.0) [{config.ai_min_confidence}]: ").strip() or config.ai_min_confidence)
    
    print("\n🌐 BROWSER SETTINGS\n")
    
    headless_input = input(f"Run FB scraper headless? (y/n) [{'y' if config.headless else 'n'}]: ").strip().lower()
    if headless_input:
        config.headless = headless_input == 'y'
    
    print("\n⏱️ SCAN SETTINGS\n")
    
    config.scan_interval_minutes = int(input(f"Scan interval for continuous mode (minutes) [{config.scan_interval_minutes}]: ").strip() or config.scan_interval_minutes)
    config.initial_batch_size = int(input(f"Initial batch size (extend by 25 if no match) [{config.initial_batch_size}]: ").strip() or config.initial_batch_size)
    config.max_listings_per_scan = int(input(f"Max listings to analyze per scan [{config.max_listings_per_scan}]: ").strip() or config.max_listings_per_scan)
    
    # Save
    config.save()
    
    # Validate
    issues = config.validate()
    if issues:
        print("\n⚠️ Notes:")
        for issue in issues:
            print(f"  - {issue}")
    
    print("\n✅ Configuration saved!")
    
    # Offer to set up cron jobs
    print("\n🕐 AUTOMATED MONITORING\n")
    print("The scanner can automatically recheck opportunities twice daily")
    print("to track price changes and listing availability.")
    
    setup_cron = input("\nSet up automated monitoring (cron jobs)? (y/n) [y]: ").strip().lower()
    if setup_cron != 'n':
        try:
            from setup_cron import install_cron_jobs
            install_cron_jobs()
        except Exception as e:
            print(f"⚠️ Cron setup failed: {e}")
            print("   You can run it manually later: python setup_cron.py install")
    
    print("\n✅ Setup complete!")
    print("\n💡 No eBay API needed - we scrape sold listings directly!")
    return config


if __name__ == "__main__":
    interactive_setup()
