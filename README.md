# ScrapedFace

**Facebook Marketplace Arbitrage Scanner**

Find underpriced items on FB Marketplace and compare against eBay sold prices.

## Quick Setup

```bash
cd ~/scrapedface
./setup.sh
```

Or manually:

```bash
cd ~/scrapedface

# 1. Create virtual environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Run interactive setup
python config.py

# 3. Run scanner
python scanner.py
```

## Features

- **Stealth browser scraping** — no API keys needed
- **AI-powered product identification** — llava:13b vision + qwen2.5 text
- **Multi-source identification** — title → description → image
- **Defective/for-parts detection** — flags "no core", "for parts", etc.
- **Vague listing filtering** — skips generic "lot", "bundle" items
- **Pickup cost calculation** — fuel cost based on MPG + distance
- **SQLite persistence** — full history tracking
- **Twice-daily opportunity monitoring** — automated rechecks via cron

## Requirements

- Python 3.10+
- [stealth-browser-mcp](https://github.com/anthropics/anthropic-cookbook) — for browser automation
- [Ollama](https://ollama.ai) with models:
  - `llava:13b` — vision model for image analysis
  - `qwen2.5` — text model for search query generation

## Usage

### Run Scanner
```bash
source .venv/bin/activate
python scanner.py
```

Options:
1. Single scan
2. Continuous scanning
3. Reconfigure
4. Test eBay scraper
5. Recheck tracked opportunities

### Opportunity Tracking

Positive hits are automatically rechecked twice daily to:
- Verify FB listing is still available
- Update eBay sold prices
- Recalculate profit margins
- Track price history

**Cron jobs are set up automatically during setup**, or manually:

```bash
python setup_cron.py install   # Install cron jobs
python setup_cron.py status    # Check what's installed
python setup_cron.py uninstall # Remove cron jobs
```

Logs: `/tmp/scrapedface-recheck.log`

## Configuration

Config file: `config.json`

Key settings:
- `categories` — search terms (e.g., ["iphone", "nintendo switch"])
- `zip_code` — your location for FB search
- `radius_miles` — search radius
- `min_profit_dollars` — minimum profit threshold
- `vehicle_mpg` — for pickup cost calculation
- `max_listing_age_days` — skip old listings (default: 30)
- `exclude_pending` — skip "pending" listings
- `sort_by_price` — sort FB results lowest first
- `use_lowest_sold_price` — use eBay min price (not avg) for profit calc

## Project Structure

```
scrapedface/
├── setup.sh              # One-command setup
├── scanner.py            # Main entry point
├── config.py             # Configuration + setup wizard
├── database.py           # SQLite schema + queries
├── reports.py            # Report generation
├── setup_cron.py         # Cron job installer
├── scrapers/
│   ├── marketplace_scraper.py  # FB Marketplace
│   └── ebay_scraper.py         # eBay sold listings
├── services/
│   ├── arbitrage.py      # Profit calculation
│   ├── price_lookup.py   # Multi-source price lookup
│   └── recheck.py        # Opportunity monitoring
└── utils/
    ├── paths.py          # Path auto-detection
    ├── title_identifier.py   # AI product identification
    ├── ai_matcher.py     # AI match verification
    ├── pickup_cost.py    # Fuel cost calculator
    └── listing_parser.py # FB listing parser
```

## License

MIT
