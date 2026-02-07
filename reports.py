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
    saved = gen.save_report(report, formats=["txt", "md", "json"])
    print(f"\n📁 Saved reports:")
    for fmt, path in saved.items():
        print(f"   {fmt}: {path}")
