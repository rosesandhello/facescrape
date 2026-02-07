#!/bin/bash
#
# ScrapedFace - FB Marketplace Arbitrage Scanner
# Setup Script
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "========================================"
echo "  ScrapedFace Setup"
echo "========================================"
echo ""
echo "Directory: $SCRIPT_DIR"
echo ""

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is required but not installed"
    exit 1
fi

PYTHON_VERSION=$(python3 --version | cut -d' ' -f2 | cut -d'.' -f1,2)
echo "✅ Python $PYTHON_VERSION found"

# Check for stealth-browser-mcp
STEALTH_BROWSER=""
if [ -f "$HOME/stealth-browser-mcp/src/server.py" ]; then
    STEALTH_BROWSER="$HOME/stealth-browser-mcp/src/server.py"
    echo "✅ stealth-browser-mcp found at $STEALTH_BROWSER"
elif [ -f "/home/bosh/stealth-browser-mcp/src/server.py" ]; then
    STEALTH_BROWSER="/home/bosh/stealth-browser-mcp/src/server.py"
    echo "✅ stealth-browser-mcp found at $STEALTH_BROWSER"
else
    echo "⚠️  stealth-browser-mcp not found"
    echo "   Clone it: git clone https://github.com/anthropics/anthropic-cookbook ~/stealth-browser-mcp"
    echo "   Or enter path manually during config"
fi

# Create virtual environment
if [ ! -d ".venv" ]; then
    echo ""
    echo "📦 Creating virtual environment..."
    python3 -m venv .venv
fi

# Activate and install dependencies
echo ""
echo "📦 Installing dependencies..."
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt

echo ""
echo "✅ Dependencies installed"

# Initialize database
echo ""
echo "🗄️  Initializing database..."
python3 -c "import database; database.init_db()"

# Run config setup
echo ""
echo "⚙️  Running configuration..."
python3 config.py

echo ""
echo "========================================"
echo "  Setup Complete!"
echo "========================================"
echo ""
echo "To run the scanner:"
echo "  cd $SCRIPT_DIR"
echo "  source .venv/bin/activate"
echo "  python scanner.py"
echo ""
echo "To check cron status:"
echo "  python setup_cron.py status"
echo ""
