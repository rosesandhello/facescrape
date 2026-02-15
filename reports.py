#!/usr/bin/env python3
"""
Scan Report Generator

Generates detailed reports after each scan:
- Text summary (console)
- Markdown file (saveable)
- HTML dashboard (optional)
- SQLite persistence
"""
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import database as db


@dataclass
class ScanItem:
    """Individual item from a scan"""
    fb_id: int  # DB id
    title: str
    fb_price: float
    location: str = ""
    image_url: str = ""
    listing_url: str = ""
    
    # AI identification
    identified_title: str = ""
    brand: str = ""
    model: str = ""
    category: str = ""
    condition: str = ""
    is_defective: bool = False
    defect_reason: str = ""
    is_vague: bool = False  # Too generic to search
    vague_reason: str = ""
    ai_confidence: float = 0.0
    
    # eBay comparison
    ebay_price: float = 0.0
    ebay_title: str = ""
    ebay_matches: int = 0
    match_confidence: float = 0.0
    
    # Arbitrage
    profit_dollars: float = 0.0
    profit_percent: float = 0.0
    is_opportunity: bool = False
    
    # Pickup cost
    pickup_cost: float = 0.0
    pickup_distance: float = 0.0  # Round-trip miles
    
    # Status
    status: str = "scanned"  # scanned, matched, opportunity, defective, vague, no_match


@dataclass
class ScanReport:
    """Complete scan report"""
    timestamp: datetime = field(default_factory=datetime.now)
    search_terms: list[str] = field(default_factory=list)
    location: str = ""
    radius_miles: int = 0
    
    # Counts
    total_fb_listings: int = 0
    ai_identified: int = 0
    ebay_matched: int = 0
    opportunities_found: int = 0
    defective_skipped: int = 0
    vague_skipped: int = 0  # Too generic to search
    no_match: int = 0
    
    # Items
    items: list[ScanItem] = field(default_factory=list)
    
    # Timing
    scan_duration_seconds: float = 0.0
    
    def add_item(self, item: ScanItem):
        """Add item and update counts"""
        self.items.append(item)
        self.total_fb_listings += 1
        
        if item.identified_title:
            self.ai_identified += 1
        if item.ebay_matches > 0:
            self.ebay_matched += 1
        if item.is_opportunity:
            self.opportunities_found += 1
        if item.is_defective:
            self.defective_skipped += 1
        if item.is_vague:
            self.vague_skipped += 1
        if item.status == "no_match":
            self.no_match += 1
    
    @property
    def opportunities(self) -> list[ScanItem]:
        """Get opportunities sorted by profit"""
        return sorted(
            [i for i in self.items if i.is_opportunity],
            key=lambda x: x.profit_dollars,
            reverse=True
        )
    
    @property
    def defective_items(self) -> list[ScanItem]:
        """Get defective/for-parts items"""
        return [i for i in self.items if i.is_defective]
    
    @property
    def vague_items(self) -> list[ScanItem]:
        """Get vague/generic items that couldn't be specifically identified"""
        return [i for i in self.items if i.is_vague]
    
    @property
    def total_potential_profit(self) -> float:
        """Sum of all opportunity profits"""
        return sum(i.profit_dollars for i in self.opportunities)


class ReportGenerator:
    """Generates and saves scan reports"""
    
    def __init__(self, output_dir: str = None):
        from utils.paths import get_reports_dir
        self.output_dir = Path(output_dir) if output_dir else get_reports_dir()
        self.output_dir.mkdir(exist_ok=True)
    
    def generate_text(self, report: ScanReport) -> str:
        """Generate console-friendly text report"""
        lines = []
        
        # Header
        lines.append("")
        lines.append("=" * 70)
        lines.append("📊 ARBITRAGE SCAN REPORT")
        lines.append("=" * 70)
        lines.append(f"🕐 {report.timestamp.strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"🔍 Search: {', '.join(report.search_terms)}")
        lines.append(f"📍 Location: {report.location}, {report.radius_miles} miles")
        if report.scan_duration_seconds > 0:
            lines.append(f"⏱️  Duration: {report.scan_duration_seconds:.1f}s")
        lines.append("")
        
        # Summary stats
        lines.append("-" * 70)
        lines.append("SUMMARY")
        lines.append("-" * 70)
        lines.append(f"  FB Listings Found:     {report.total_fb_listings:>5}")
        lines.append(f"  AI Identified:         {report.ai_identified:>5}")
        lines.append(f"  eBay Matches:          {report.ebay_matched:>5}")
        lines.append(f"  ⚠️  Defective/Parts:    {report.defective_skipped:>5}")
        lines.append(f"  🔸 Vague/Generic:      {report.vague_skipped:>5}")
        lines.append(f"  ❌ No Match:            {report.no_match:>5}")
        lines.append(f"  💰 Opportunities:       {report.opportunities_found:>5}")
        if report.opportunities_found > 0:
            lines.append(f"  💵 Total Profit:       ${report.total_potential_profit:>7.2f}")
        lines.append("")
        
        # Top opportunities
        if report.opportunities:
            lines.append("-" * 70)
            lines.append("💰 TOP OPPORTUNITIES")
            lines.append("-" * 70)
            for i, item in enumerate(report.opportunities[:10], 1):
                lines.append(f"\n{i}. {item.title}")
                if item.identified_title and item.identified_title != item.title:
                    lines.append(f"   🏷️  Identified as: {item.identified_title}")
                lines.append(f"   💰 FB: ${item.fb_price:.2f}  →  eBay: ${item.ebay_price:.2f}")
                lines.append(f"   💵 Profit: ${item.profit_dollars:.2f} ({item.profit_percent:.0f}%)")
                if item.pickup_cost > 0:
                    lines.append(f"   🚗 Pickup: ${item.pickup_cost:.2f} ({item.pickup_distance:.0f} mi round trip)")
                if item.listing_url:
                    lines.append(f"   🔗 {item.listing_url}")
            lines.append("")
        
        # Defective items (important to show these!)
        if report.defective_items:
            lines.append("-" * 70)
            lines.append("⚠️  DEFECTIVE / FOR PARTS (SKIPPED)")
            lines.append("-" * 70)
            for item in report.defective_items[:5]:
                lines.append(f"  • ${item.fb_price:.2f} - {item.title[:45]}...")
                lines.append(f"    Reason: {item.defect_reason}")
            if len(report.defective_items) > 5:
                lines.append(f"  ... and {len(report.defective_items) - 5} more")
            lines.append("")
        
        # Vague items (can't be meaningfully searched)
        if report.vague_items:
            lines.append("-" * 70)
            lines.append("🔸 VAGUE / GENERIC (SKIPPED)")
            lines.append("-" * 70)
            for item in report.vague_items[:5]:
                lines.append(f"  • ${item.fb_price:.2f} - {item.title[:45]}...")
                lines.append(f"    Reason: {item.vague_reason}")
            if len(report.vague_items) > 5:
                lines.append(f"  ... and {len(report.vague_items) - 5} more")
            lines.append("")
        
        # All items detailed list
        lines.append("-" * 70)
        lines.append("ALL ITEMS")
        lines.append("-" * 70)
        
        status_emoji = {
            "opportunity": "💰",
            "matched": "✅",
            "defective": "⚠️",
            "vague": "🔸",
            "no_match": "❌",
            "scanned": "📦"
        }
        
        for i, item in enumerate(report.items, 1):
            emoji = status_emoji.get(item.status, "•")
            lines.append(f"\n{emoji} {i}. {item.title}")
            if item.identified_title and item.identified_title != item.title:
                lines.append(f"   🏷️  Identified as: {item.identified_title}")
            lines.append(f"   📊 Status: {item.status}")
            lines.append(f"   💰 FB: ${item.fb_price:.2f}  →  eBay: ${item.ebay_price:.2f}" if item.ebay_price > 0 else f"   💰 FB: ${item.fb_price:.2f}  →  eBay: -")
            if item.profit_dollars > 0:
                lines.append(f"   💵 Profit: ${item.profit_dollars:.2f} ({item.profit_percent:.0f}%)")
            if item.pickup_cost > 0:
                lines.append(f"   🚗 Pickup: ${item.pickup_cost:.2f} ({item.pickup_distance:.0f} mi round trip)")
            if item.is_defective:
                lines.append(f"   ⚠️  Defective: {item.defect_reason}")
            if item.is_vague:
                lines.append(f"   🔸 Vague: {item.vague_reason}")
            if item.listing_url:
                lines.append(f"   🔗 {item.listing_url}")
        
        if len(report.items) > 50:
            lines.append(f"... and {len(report.items) - 20} more items")
        
        lines.append("")
        lines.append("=" * 70)
        lines.append("")
        
        return "\n".join(lines)
    
    def generate_markdown(self, report: ScanReport) -> str:
        """Generate Markdown report for saving"""
        lines = []
        
        lines.append(f"# Arbitrage Scan Report")
        lines.append(f"**{report.timestamp.strftime('%Y-%m-%d %H:%M:%S')}**\n")
        
        lines.append("## Summary\n")
        lines.append(f"| Metric | Value |")
        lines.append(f"|--------|-------|")
        lines.append(f"| Search Terms | {', '.join(report.search_terms)} |")
        lines.append(f"| Location | {report.location}, {report.radius_miles} mi |")
        lines.append(f"| FB Listings | {report.total_fb_listings} |")
        lines.append(f"| AI Identified | {report.ai_identified} |")
        lines.append(f"| eBay Matched | {report.ebay_matched} |")
        lines.append(f"| Defective | {report.defective_skipped} |")
        lines.append(f"| **Opportunities** | **{report.opportunities_found}** |")
        if report.opportunities_found > 0:
            lines.append(f"| **Total Profit** | **${report.total_potential_profit:.2f}** |")
        lines.append("")
        
        if report.opportunities:
            lines.append("## 💰 Opportunities\n")
            for i, item in enumerate(report.opportunities, 1):
                lines.append(f"### {i}. {item.title}\n")
                if item.identified_title and item.identified_title != item.title:
                    lines.append(f"**Identified as:** {item.identified_title}\n")
                lines.append(f"- **FB Price:** ${item.fb_price:.2f}")
                lines.append(f"- **eBay Price:** ${item.ebay_price:.2f}")
                lines.append(f"- **Profit:** ${item.profit_dollars:.2f} ({item.profit_percent:.0f}%)")
                if item.pickup_cost > 0:
                    lines.append(f"- **Pickup Cost:** ${item.pickup_cost:.2f} ({item.pickup_distance:.0f} mi)")
                if item.brand and item.brand != "unknown":
                    lines.append(f"- **Brand:** {item.brand}")
                if item.model and item.model != "unknown":
                    lines.append(f"- **Model:** {item.model}")
                if item.listing_url:
                    lines.append(f"- **[View on FB Marketplace]({item.listing_url})**")
                if item.image_url:
                    lines.append(f"\n![{item.title[:30]}]({item.image_url})")
                lines.append("")
        
        if report.defective_items:
            lines.append("## ⚠️ Defective Items (Skipped)\n")
            lines.append("| Price | Title | Reason |")
            lines.append("|-------|-------|--------|")
            for item in report.defective_items:
                title = item.title[:40] + ("..." if len(item.title) > 40 else "")
                lines.append(f"| ${item.fb_price:.2f} | {title} | {item.defect_reason} |")
            lines.append("")
        
        lines.append("## All Items\n")
        
        status_emoji = {
            "opportunity": "💰",
            "matched": "✅", 
            "defective": "⚠️",
            "vague": "🔸",
            "no_match": "❌",
            "scanned": "📦"
        }
        
        for i, item in enumerate(report.items, 1):
            emoji = status_emoji.get(item.status, "•")
            lines.append(f"### {emoji} {i}. {item.title}\n")
            if item.identified_title and item.identified_title != item.title:
                lines.append(f"**Identified as:** {item.identified_title}\n")
            lines.append(f"- **Status:** {item.status}")
            lines.append(f"- **FB Price:** ${item.fb_price:.2f}")
            if item.ebay_price > 0:
                lines.append(f"- **eBay Price:** ${item.ebay_price:.2f}")
            if item.profit_dollars > 0:
                lines.append(f"- **Profit:** ${item.profit_dollars:.2f} ({item.profit_percent:.0f}%)")
            if item.pickup_cost > 0:
                lines.append(f"- **Pickup Cost:** ${item.pickup_cost:.2f} ({item.pickup_distance:.0f} mi)")
            if item.is_defective:
                lines.append(f"- **⚠️ Defective:** {item.defect_reason}")
            if item.is_vague:
                lines.append(f"- **🔸 Vague:** {item.vague_reason}")
            if item.listing_url:
                lines.append(f"- **[View on FB Marketplace]({item.listing_url})**")
            if item.image_url:
                lines.append(f"\n![{item.title[:30]}]({item.image_url})")
            lines.append("")
        
        return "\n".join(lines)
    
    def generate_html(self, report: ScanReport) -> str:
        """Generate a self-contained static HTML report"""
        import html as html_lib
        
        def esc(text):
            return html_lib.escape(str(text)) if text else ""
        
        status_colors = {
            "opportunity": "#22c55e",  # green
            "matched": "#3b82f6",      # blue
            "defective": "#f59e0b",    # amber
            "vague": "#a855f7",        # purple
            "no_match": "#6b7280",     # gray
            "scanned": "#6b7280"       # gray
        }
        
        status_labels = {
            "opportunity": "💰 Opportunity",
            "matched": "✅ Matched",
            "defective": "⚠️ Defective",
            "vague": "🔸 Vague",
            "no_match": "❌ No Match",
            "scanned": "📦 Scanned"
        }
        
        # Build item cards HTML
        items_html = []
        for i, item in enumerate(report.items, 1):
            status_color = status_colors.get(item.status, "#6b7280")
            status_label = status_labels.get(item.status, item.status)
            
            profit_html = ""
            if item.profit_dollars > 0:
                profit_class = "profit-high" if item.profit_percent >= 50 else "profit-med" if item.profit_percent >= 20 else "profit-low"
                profit_html = f'<div class="profit {profit_class}">💵 ${item.profit_dollars:.2f} ({item.profit_percent:.0f}%)</div>'
            
            image_html = ""
            if item.image_url:
                image_html = f'<img src="{esc(item.image_url)}" alt="{esc(item.title[:30])}" loading="lazy" onerror="this.style.display=\'none\'">'
            
            identified_html = ""
            if item.identified_title and item.identified_title != item.title:
                identified_html = f'<div class="identified">🏷️ {esc(item.identified_title)}</div>'
            
            defect_html = ""
            if item.is_defective:
                defect_html = f'<div class="defect-reason">⚠️ {esc(item.defect_reason)}</div>'
            if item.is_vague:
                defect_html = f'<div class="defect-reason">🔸 {esc(item.vague_reason)}</div>'
            
            link_html = ""
            if item.listing_url:
                link_html = f'<a href="{esc(item.listing_url)}" target="_blank" class="view-link">View on FB →</a>'
            
            items_html.append(f'''
            <div class="item-card" data-status="{item.status}" data-profit="{item.profit_dollars}">
                <div class="item-header">
                    <span class="item-num">#{i}</span>
                    <span class="status-badge" style="background: {status_color}">{status_label}</span>
                </div>
                {image_html}
                <div class="item-content">
                    <h3>{esc(item.title[:80])}</h3>
                    {identified_html}
                    <div class="prices">
                        <div class="price fb-price">FB: ${item.fb_price:.2f}</div>
                        <div class="price ebay-price">eBay: {f"${item.ebay_price:.2f}" if item.ebay_price > 0 else "—"}</div>
                    </div>
                    {profit_html}
                    {defect_html}
                    {link_html}
                </div>
            </div>
            ''')
        
        html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Arbitrage Scan - {report.timestamp.strftime("%Y-%m-%d %H:%M")}</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #0f172a;
            color: #e2e8f0;
            line-height: 1.5;
            padding: 20px;
        }}
        .container {{ max-width: 1400px; margin: 0 auto; }}
        
        /* Header */
        header {{
            text-align: center;
            margin-bottom: 30px;
            padding: 30px;
            background: linear-gradient(135deg, #1e293b 0%, #334155 100%);
            border-radius: 16px;
            border: 1px solid #475569;
        }}
        h1 {{ font-size: 2rem; margin-bottom: 8px; }}
        .subtitle {{ color: #94a3b8; font-size: 0.9rem; }}
        
        /* Stats Grid */
        .stats {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 16px;
            margin-bottom: 30px;
        }}
        .stat-card {{
            background: #1e293b;
            padding: 20px;
            border-radius: 12px;
            text-align: center;
            border: 1px solid #334155;
        }}
        .stat-value {{
            font-size: 2rem;
            font-weight: bold;
            color: #22c55e;
        }}
        .stat-value.blue {{ color: #3b82f6; }}
        .stat-value.amber {{ color: #f59e0b; }}
        .stat-value.purple {{ color: #a855f7; }}
        .stat-label {{ color: #94a3b8; font-size: 0.85rem; margin-top: 4px; }}
        
        /* Filters */
        .filters {{
            display: flex;
            gap: 12px;
            margin-bottom: 24px;
            flex-wrap: wrap;
        }}
        .filter-btn {{
            padding: 8px 16px;
            border: 1px solid #475569;
            background: #1e293b;
            color: #e2e8f0;
            border-radius: 8px;
            cursor: pointer;
            transition: all 0.2s;
        }}
        .filter-btn:hover {{ background: #334155; }}
        .filter-btn.active {{ background: #3b82f6; border-color: #3b82f6; }}
        
        /* Items Grid */
        .items-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
            gap: 20px;
        }}
        .item-card {{
            background: #1e293b;
            border-radius: 12px;
            overflow: hidden;
            border: 1px solid #334155;
            transition: transform 0.2s, box-shadow 0.2s;
        }}
        .item-card:hover {{
            transform: translateY(-2px);
            box-shadow: 0 8px 24px rgba(0,0,0,0.3);
        }}
        .item-card.hidden {{ display: none; }}
        .item-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 12px 16px;
            background: #0f172a;
        }}
        .item-num {{ color: #64748b; font-size: 0.85rem; }}
        .status-badge {{
            padding: 4px 10px;
            border-radius: 20px;
            font-size: 0.75rem;
            font-weight: 500;
        }}
        .item-card img {{
            width: 100%;
            height: 200px;
            object-fit: cover;
            background: #334155;
        }}
        .item-content {{ padding: 16px; }}
        .item-content h3 {{
            font-size: 1rem;
            margin-bottom: 8px;
            line-height: 1.4;
        }}
        .identified {{
            color: #a855f7;
            font-size: 0.85rem;
            margin-bottom: 12px;
        }}
        .prices {{
            display: flex;
            gap: 16px;
            margin-bottom: 12px;
        }}
        .price {{
            font-size: 1.1rem;
            font-weight: 600;
        }}
        .fb-price {{ color: #3b82f6; }}
        .ebay-price {{ color: #22c55e; }}
        .profit {{
            padding: 8px 12px;
            border-radius: 8px;
            font-weight: 600;
            margin-bottom: 12px;
        }}
        .profit-high {{ background: rgba(34, 197, 94, 0.2); color: #22c55e; }}
        .profit-med {{ background: rgba(59, 130, 246, 0.2); color: #3b82f6; }}
        .profit-low {{ background: rgba(148, 163, 184, 0.2); color: #94a3b8; }}
        .defect-reason {{
            color: #f59e0b;
            font-size: 0.85rem;
            margin-bottom: 12px;
        }}
        .view-link {{
            display: inline-block;
            color: #3b82f6;
            text-decoration: none;
            font-size: 0.9rem;
        }}
        .view-link:hover {{ text-decoration: underline; }}
        
        /* Sort dropdown */
        select {{
            padding: 8px 16px;
            border: 1px solid #475569;
            background: #1e293b;
            color: #e2e8f0;
            border-radius: 8px;
            cursor: pointer;
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>📊 Arbitrage Scan Report</h1>
            <div class="subtitle">
                {report.timestamp.strftime("%B %d, %Y at %I:%M %p")} · 
                {", ".join(report.search_terms)} · 
                {report.location}, {report.radius_miles} mi
            </div>
        </header>
        
        <div class="stats">
            <div class="stat-card">
                <div class="stat-value">{report.total_fb_listings}</div>
                <div class="stat-label">FB Listings</div>
            </div>
            <div class="stat-card">
                <div class="stat-value blue">{report.ai_identified}</div>
                <div class="stat-label">AI Identified</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{report.opportunities_found}</div>
                <div class="stat-label">Opportunities</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">${report.total_potential_profit:.2f}</div>
                <div class="stat-label">Total Profit</div>
            </div>
            <div class="stat-card">
                <div class="stat-value amber">{report.defective_skipped}</div>
                <div class="stat-label">Defective</div>
            </div>
            <div class="stat-card">
                <div class="stat-value purple">{report.vague_skipped}</div>
                <div class="stat-label">Vague</div>
            </div>
        </div>
        
        <div class="filters">
            <button class="filter-btn active" data-filter="all">All ({len(report.items)})</button>
            <button class="filter-btn" data-filter="opportunity">💰 Opportunities ({report.opportunities_found})</button>
            <button class="filter-btn" data-filter="matched">✅ Matched ({report.ebay_matched - report.opportunities_found})</button>
            <button class="filter-btn" data-filter="no_match">❌ No Match ({report.no_match})</button>
            <button class="filter-btn" data-filter="defective">⚠️ Defective ({report.defective_skipped})</button>
            <select id="sort-select">
                <option value="default">Sort: Default</option>
                <option value="profit-desc">Profit: High → Low</option>
                <option value="profit-asc">Profit: Low → High</option>
                <option value="price-asc">FB Price: Low → High</option>
                <option value="price-desc">FB Price: High → Low</option>
            </select>
        </div>
        
        <div class="items-grid" id="items-grid">
            {"".join(items_html)}
        </div>
    </div>
    
    <script>
        // Filter functionality
        document.querySelectorAll('.filter-btn').forEach(btn => {{
            btn.addEventListener('click', () => {{
                document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                
                const filter = btn.dataset.filter;
                document.querySelectorAll('.item-card').forEach(card => {{
                    if (filter === 'all' || card.dataset.status === filter) {{
                        card.classList.remove('hidden');
                    }} else {{
                        card.classList.add('hidden');
                    }}
                }});
            }});
        }});
        
        // Sort functionality
        document.getElementById('sort-select').addEventListener('change', (e) => {{
            const grid = document.getElementById('items-grid');
            const cards = Array.from(grid.querySelectorAll('.item-card'));
            
            cards.sort((a, b) => {{
                const profitA = parseFloat(a.dataset.profit) || 0;
                const profitB = parseFloat(b.dataset.profit) || 0;
                
                switch(e.target.value) {{
                    case 'profit-desc': return profitB - profitA;
                    case 'profit-asc': return profitA - profitB;
                    default: return 0;
                }}
            }});
            
            cards.forEach(card => grid.appendChild(card));
        }});
    </script>
</body>
</html>'''
        
        return html
    
    def save_report(self, report: ScanReport, formats: list[str] = ["txt", "md"]) -> dict[str, Path]:
        """Save report to files"""
        timestamp = report.timestamp.strftime("%Y%m%d_%H%M%S")
        saved = {}
        
        if "txt" in formats:
            txt_path = self.output_dir / f"scan_{timestamp}.txt"
            txt_path.write_text(self.generate_text(report))
            saved["txt"] = txt_path
        
        if "md" in formats:
            md_path = self.output_dir / f"scan_{timestamp}.md"
            md_path.write_text(self.generate_markdown(report))
            saved["md"] = md_path
        
        if "html" in formats:
            html_path = self.output_dir / f"scan_{timestamp}.html"
            html_path.write_text(self.generate_html(report))
            saved["html"] = html_path
        
        if "json" in formats:
            json_path = self.output_dir / f"scan_{timestamp}.json"
            json_data = {
                "timestamp": report.timestamp.isoformat(),
                "search_terms": report.search_terms,
                "location": report.location,
                "radius_miles": report.radius_miles,
                "total_fb_listings": report.total_fb_listings,
                "opportunities_found": report.opportunities_found,
                "defective_skipped": report.defective_skipped,
                "total_potential_profit": report.total_potential_profit,
                "items": [
                    {
                        "title": i.title,
                        "identified_title": i.identified_title,
                        "fb_price": i.fb_price,
                        "ebay_price": i.ebay_price,
                        "profit_dollars": i.profit_dollars,
                        "profit_percent": i.profit_percent,
                        "listing_url": i.listing_url,
                        "image_url": i.image_url,
                        "brand": i.brand,
                        "model": i.model,
                        "is_defective": i.is_defective,
                        "defect_reason": i.defect_reason,
                        "is_opportunity": i.is_opportunity,
                        "status": i.status
                    }
                    for i in report.items
                ]
            }
            json_path.write_text(json.dumps(json_data, indent=2))
            saved["json"] = json_path
        
        return saved
    
    def print_report(self, report: ScanReport):
        """Print report to console"""
        print(self.generate_text(report))


def save_scan_to_db(report: ScanReport) -> int:
    """Save all scan data to SQLite, return number of items saved"""
    saved = 0
    
    for item in report.items:
        try:
            # Save FB listing
            fb_id = db.insert_fb_listing(
                title=item.title,
                price=item.fb_price,
                location=item.location,
                image_url=item.image_url,
                listing_url=item.listing_url
            )
            
            # Save AI identification
            if item.identified_title:
                db.insert_ai_identification(
                    fb_listing_id=fb_id,
                    identified_title=item.identified_title,
                    brand=item.brand,
                    model=item.model,
                    category=item.category,
                    condition=item.condition,
                    is_defective=item.is_defective,
                    defect_reason=item.defect_reason,
                    confidence=item.ai_confidence
                )
            
            # Save opportunity if applicable
            if item.is_opportunity or item.ebay_price > 0:
                db.insert_opportunity(
                    fb_listing_id=fb_id,
                    fb_price=item.fb_price,
                    ebay_price=item.ebay_price,
                    is_defective=item.is_defective,
                    status=item.status
                )
            
            saved += 1
        except Exception as e:
            print(f"⚠️ DB save error for {item.title[:30]}: {e}")
    
    return saved


# Quick test
if __name__ == "__main__":
    # Create sample report
    report = ScanReport(
        search_terms=["silver coins", "bullion"],
        location="Pittsburgh, PA",
        radius_miles=25
    )
    
    # Add some test items
    report.add_item(ScanItem(
        fb_id=1,
        title="1 oz Silver Eagle 2024 BU",
        fb_price=28.00,
        ebay_price=38.50,
        profit_dollars=7.50,
        profit_percent=26.8,
        is_opportunity=True,
        status="opportunity",
        match_confidence=0.85
    ))
    
    report.add_item(ScanItem(
        fb_id=2,
        title="RTX 4090 For Parts No Core",
        fb_price=140.00,
        is_defective=True,
        defect_reason="Title contains 'for parts'",
        status="defective"
    ))
    
    report.add_item(ScanItem(
        fb_id=3,
        title="Nintendo Switch OLED",
        fb_price=280.00,
        ebay_price=295.00,
        profit_dollars=-5.00,
        status="matched"
    ))
    
    # Generate and print
    gen = ReportGenerator()
    gen.print_report(report)
    
    # Save
    saved = gen.save_report(report, formats=["txt", "md", "json", "html"])
    print(f"\n📁 Saved reports:")
    for fmt, path in saved.items():
        print(f"   {fmt}: {path}")
