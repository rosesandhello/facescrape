"""
Search term expansion with synonyms and common misspellings.
Supports both built-in expansions and user-defined custom terms.
Includes disambiguation check for overloaded terms.
"""
import json
import re
from pathlib import Path
from typing import Optional
import asyncio

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

# File for user-defined custom expansions
CUSTOM_TERMS_FILE = Path(__file__).parent / "custom_terms.json"

# Built-in expansions (can be overridden by custom_terms.json)
DEFAULT_EXPANSIONS = {
    # Precious metals
    "silver": ["sterling silver", "925 silver", ".999 silver", "fine silver", 
               "silver bullion", "silver coins", "silver bars"],
    "gold": ["14k gold", "18k gold", "24k gold", "gold bullion", "gold coins",
             "gold bars", "solid gold"],
    "bullion": ["gold bullion", "silver bullion", "platinum bullion",
                "bullion coins", "bullion bars", "precious metals"],
    "sterling": ["sterling silver", "925 sterling", ".925 silver",
                 "sterling flatware", "sterling jewelry"],
    
    # Electronics
    "iphone": ["iphone pro", "iphone plus", "iphone max", "apple iphone"],
    "ipad": ["ipad pro", "ipad air", "ipad mini", "apple ipad"],
    "macbook": ["macbook pro", "macbook air", "apple macbook"],
    "airpods": ["airpods pro", "airpods max", "apple airpods"],
    
    # Gaming
    "nintendo switch": ["switch oled", "switch lite", "nintendo switch oled"],
    "ps5": ["playstation 5", "playstation 5 digital", "ps5 disc", "ps5 digital"],
    "xbox": ["xbox series x", "xbox series s", "xbox one"],
    
    # Collectibles
    "pokemon cards": ["pokemon tcg", "pokemon booster", "pokemon box", "charizard"],
    "sports cards": ["baseball cards", "football cards", "basketball cards", "topps", "panini"],
}


def load_custom_terms() -> dict:
    """Load user-defined custom term expansions"""
    if CUSTOM_TERMS_FILE.exists():
        try:
            with open(CUSTOM_TERMS_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_custom_terms(terms: dict):
    """Save user-defined custom term expansions"""
    with open(CUSTOM_TERMS_FILE, 'w') as f:
        json.dump(terms, f, indent=2)


def get_all_expansions() -> dict:
    """Get combined built-in + custom expansions"""
    expansions = DEFAULT_EXPANSIONS.copy()
    custom = load_custom_terms()
    
    # Custom terms override/extend defaults
    for term, variations in custom.items():
        if term in expansions:
            # Extend existing, avoiding duplicates
            existing = set(v.lower() for v in expansions[term])
            for v in variations:
                if v.lower() not in existing:
                    expansions[term].append(v)
                    existing.add(v.lower())
        else:
            expansions[term] = variations
    
    return expansions


def generate_typos(word: str, max_typos: int = 3) -> list[str]:
    """
    Algorithmically generate common typo patterns for a word.
    """
    if len(word) < 4:
        return []
    
    typos = []
    
    # Adjacent letter swaps (teh -> the)
    for i in range(len(word) - 1):
        typo = word[:i] + word[i+1] + word[i] + word[i+2:]
        if typo != word:
            typos.append(typo)
    
    # Double letter (silve -> silver... wait that's adding)
    # Missing letter (silvr -> silver)
    for i in range(1, len(word) - 1):
        typo = word[:i] + word[i+1:]  # Skip a letter
        if len(typo) > 2:
            typos.append(typo)
    
    # Common keyboard adjacency mistakes
    keyboard_adjacent = {
        'a': 'sq', 's': 'awd', 'd': 'sfe', 'f': 'dgr', 'g': 'fht',
        'q': 'wa', 'w': 'qeas', 'e': 'wrd', 'r': 'etf', 't': 'ryg',
        'i': 'uok', 'o': 'ipl', 'l': 'okp', 'n': 'bm', 'm': 'n',
    }
    for i, char in enumerate(word.lower()):
        if char in keyboard_adjacent:
            for adj in keyboard_adjacent[char][:1]:  # Just one adjacent
                typo = word[:i] + adj + word[i+1:]
                if typo != word:
                    typos.append(typo)
    
    # Dedupe and limit
    seen = set()
    unique = []
    for t in typos:
        if t.lower() not in seen and t.lower() != word.lower():
            seen.add(t.lower())
            unique.append(t)
    
    return unique[:max_typos]


def expand_search_term(term: str, include_typos: bool = True) -> list[str]:
    """
    Expand a search term into variations including synonyms and optionally typos.
    Returns list of terms to search.
    """
    term_lower = term.lower().strip()
    variations = [term]  # Always include original
    
    # Get expansions (built-in + custom)
    expansions = get_all_expansions()
    
    # Check for matching expansions
    if term_lower in expansions:
        for v in expansions[term_lower]:
            if v.lower() not in [x.lower() for x in variations]:
                variations.append(v)
    
    # Add typos if enabled
    if include_typos:
        # Generate typos for the main term
        for word in term.split():
            if len(word) >= 4:
                typos = generate_typos(word)
                for typo in typos:
                    typo_term = term.replace(word, typo)
                    if typo_term.lower() not in [x.lower() for x in variations]:
                        variations.append(typo_term)
    
    return variations


def parse_search_terms(input_str: str) -> list[str]:
    """Parse comma-separated search terms from user input."""
    if not input_str:
        return []
    
    terms = [t.strip() for t in input_str.split(',')]
    return [t for t in terms if t]


def get_all_search_variations(terms: list[str], expand: bool = True, include_typos: bool = True) -> list[str]:
    """
    Get all search variations for a list of terms.
    
    Args:
        terms: List of base search terms
        expand: Include synonyms/related terms
        include_typos: Include common misspellings
    """
    if not expand:
        return terms
    
    all_variations = []
    seen = set()
    
    for term in terms:
        expansions = expand_search_term(term, include_typos=include_typos)
        for exp in expansions:
            if exp.lower() not in seen:
                seen.add(exp.lower())
                all_variations.append(exp)
    
    return all_variations


def add_custom_expansion(term: str, variations: list[str]):
    """Add a custom term expansion (persisted to custom_terms.json)"""
    custom = load_custom_terms()
    term_lower = term.lower()
    
    if term_lower in custom:
        existing = set(v.lower() for v in custom[term_lower])
        for v in variations:
            if v.lower() not in existing:
                custom[term_lower].append(v)
    else:
        custom[term_lower] = variations
    
    save_custom_terms(custom)
    print(f"✅ Added custom expansion for '{term}': {variations}")


def remove_custom_expansion(term: str):
    """Remove a custom term expansion"""
    custom = load_custom_terms()
    term_lower = term.lower()
    
    if term_lower in custom:
        del custom[term_lower]
        save_custom_terms(custom)
        print(f"✅ Removed custom expansion for '{term}'")
    else:
        print(f"⚠️ No custom expansion found for '{term}'")


def list_expansions():
    """List all term expansions (built-in + custom)"""
    expansions = get_all_expansions()
    custom = load_custom_terms()
    
    print("\n📚 Term Expansions")
    print("=" * 50)
    
    for term, variations in sorted(expansions.items()):
        is_custom = term in custom
        marker = "🔧" if is_custom else "📦"
        print(f"\n{marker} {term}:")
        for v in variations:
            print(f"   → {v}")
    
    print("\n📦 = built-in, 🔧 = custom")


def interactive_term_manager():
    """Interactive CLI for managing custom term expansions"""
    print("\n" + "=" * 50)
    print("🔧 Custom Term Manager")
    print("=" * 50)
    
    while True:
        print("\nOptions:")
        print("  1. List all expansions")
        print("  2. Add custom expansion")
        print("  3. Remove custom expansion")
        print("  4. Test expansion")
        print("  5. Exit")
        
        choice = input("\nChoice (1-5): ").strip()
        
        if choice == "1":
            list_expansions()
        
        elif choice == "2":
            term = input("Term to expand: ").strip()
            if term:
                variations_input = input("Variations (comma-separated): ").strip()
                variations = [v.strip() for v in variations_input.split(",") if v.strip()]
                if variations:
                    add_custom_expansion(term, variations)
        
        elif choice == "3":
            term = input("Term to remove: ").strip()
            if term:
                remove_custom_expansion(term)
        
        elif choice == "4":
            term = input("Term to test: ").strip()
            if term:
                variations = expand_search_term(term)
                print(f"\n'{term}' expands to {len(variations)} variations:")
                for v in variations:
                    print(f"   → {v}")
        
        elif choice == "5":
            break


# ============================================================================
# SEARCH TERM EVALUATION - LLM evaluates each term for eBay search quality
# ============================================================================

# Known ambiguous terms - bypass LLM check, always prompt for clarification
# These are terms where the LLM might be inconsistent
ALWAYS_AMBIGUOUS = {
    "ram": {
        "computer memory": ["DDR4 RAM", "DDR5 RAM", "desktop RAM", "laptop RAM"],
        "truck parts": ["Dodge Ram parts", "Ram 1500", "Ram 2500", "Ram truck"]
    },
    "charger": {
        "electronics": ["phone charger", "USB charger", "laptop charger", "battery charger"],
        "vehicle": ["Dodge Charger parts", "Charger RT", "Charger Hellcat"]
    },
    "switch": {
        "gaming": ["Nintendo Switch", "Switch OLED", "Switch Lite", "Switch games"],
        "networking": ["network switch", "ethernet switch", "Cisco switch"],
        "electrical": ["light switch", "wall switch", "dimmer switch"]
    },
    "pilot": {
        "vehicle": ["Honda Pilot", "Pilot SUV", "Honda Pilot parts"],
        "pens": ["Pilot pen", "G2 Pilot", "Pilot G2"],
        "aviation": ["pilot headset", "pilot supplies"]
    },
    "element": {
        "vehicle": ["Honda Element", "Element SUV", "Honda Element parts"],
        "speakers": ["Element speakers", "Element audio"],
    },
    "explorer": {
        "vehicle": ["Ford Explorer", "Explorer SUV", "Explorer parts"],
        "software": ["Internet Explorer", "File Explorer"]
    },
    "titan": {
        "graphics": ["NVIDIA Titan", "Titan RTX", "GTX Titan"],
        "vehicle": ["Nissan Titan", "Titan truck"]
    },
}


async def evaluate_search_term(
    term: str,
    ollama_url: str = "http://localhost:11434",
    model: str = "qwen2.5"
) -> dict:
    """
    Evaluate a search term for eBay search quality.
    
    The LLM thinks about what eBay would return for this term and determines
    if the results would be muddied/mixed across unrelated product categories.
    
    Returns dict with:
        - needs_clarification: bool
        - interpretations: list of possible meanings with their eBay search terms
        - reasoning: str explanation
    """
    # Check hardcoded always-ambiguous list first (bypass LLM for consistency)
    term_lower = term.lower().strip()
    if term_lower in ALWAYS_AMBIGUOUS:
        meanings = ALWAYS_AMBIGUOUS[term_lower]
        interpretations = [
            {"meaning": meaning, "search_terms": terms}
            for meaning, terms in meanings.items()
        ]
        return {
            "needs_clarification": True,
            "interpretations": interpretations,
            "reasoning": f"'{term}' is a known ambiguous term with multiple meanings"
        }
    
    if not HAS_HTTPX:
        return {"needs_clarification": False, "reasoning": "httpx not available", "interpretations": []}
    
    prompt = f"""You are an eBay search expert. Your job is to determine if a search term would return MIXED, UNRELATED product categories.

Search term: "{term}"

IMPORTANT: Be VERY conservative about flagging terms as muddied. Most terms are NOT muddied.

A term is ONLY muddied if searching it on eBay LITERALLY returns completely different product types on the first page.

ACTUALLY MUDDIED (these literally return mixed categories):
- "ram" → computer RAM sticks AND Dodge Ram truck parts (the word "ram" is used by both)
- "charger" → phone chargers AND Dodge Charger car parts  
- "switch" → Nintendo Switch AND network switches AND light switches
- "pilot" → Honda Pilot parts AND G2 Pilot pens AND pilot supplies
- "element" → Honda Element parts AND Element speakers

NOT MUDDIED (these return ONE clear category):
- "gpu" → ONLY graphics cards (no cars, no trucks, no other categories)
- "nvidia" → ONLY NVIDIA products (graphics cards, Shield, etc.)
- "intel" → ONLY Intel products (CPUs, NUCs, etc.)
- "amd" → ONLY AMD products
- "ddr4" / "ddr5" → ONLY computer memory
- "rtx 3080" → ONLY that specific GPU
- "iphone" → ONLY Apple phones
- "macbook" → ONLY Apple laptops
- "playstation" → ONLY PlayStation products
- "xbox" → ONLY Xbox products

The key test: Does the WORD itself have multiple meanings in commerce?
- "ram" = YES (memory AND truck brand)
- "gpu" = NO (only means graphics processing unit)
- "nvidia" = NO (only means the company NVIDIA)

If the term is NOT muddied, respond:
MUDDIED: no
REASONING: [one sentence]

If the term IS muddied, respond:
MUDDIED: yes
INTERPRETATIONS:
- meaning1: search term 1, search term 2
- meaning2: search term 1, search term 2
REASONING: [one sentence about the actual ambiguity]"""

    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.post(
                f"{ollama_url}/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": False
                }
            )
            
            if response.status_code != 200:
                return {"needs_clarification": False, "reasoning": f"LLM error: {response.status_code}", "interpretations": []}
            
            result = response.json().get('response', '')
            
            # Parse the response
            result_lower = result.lower()
            needs_clarification = (
                "muddied: yes" in result_lower or 
                "muddied:yes" in result_lower
            )
            
            # Extract interpretations
            interpretations = []
            reasoning = ""
            in_interpretations = False
            
            for line in result.split('\n'):
                line = line.strip()
                if line.lower().startswith('interpretations:'):
                    in_interpretations = True
                    continue
                elif line.lower().startswith('reasoning:'):
                    in_interpretations = False
                    reasoning = line.split(':', 1)[1].strip()
                elif in_interpretations and line.startswith('- ') and ':' in line:
                    # Parse: "- meaning: term1, term2"
                    parts = line[2:].split(':', 1)
                    if len(parts) == 2:
                        meaning = parts[0].strip()
                        terms = [t.strip() for t in parts[1].split(',')]
                        interpretations.append({
                            "meaning": meaning,
                            "search_terms": terms
                        })
            
            return {
                "needs_clarification": needs_clarification,
                "interpretations": interpretations,
                "reasoning": reasoning,
                "raw_response": result
            }
            
    except Exception as e:
        return {"needs_clarification": False, "reasoning": f"Error: {e}", "interpretations": []}


async def clarify_search_terms(
    terms: list[str],
    ollama_url: str = "http://localhost:11434",
    model: str = "qwen2.5"
) -> list[str]:
    """
    Evaluate all search terms and interactively clarify any that would produce muddied eBay results.
    
    For each term:
    1. LLM evaluates if eBay search would be muddied
    2. If muddied, shows user the possible interpretations
    3. User picks their intended meaning
    4. Returns the optimized search terms for that meaning
    
    Returns list of clarified/optimized search terms.
    """
    final_terms = []
    
    for term in terms:
        print(f"\n🔍 Evaluating: '{term}'...")
        
        result = await evaluate_search_term(term, ollama_url, model)
        
        if result.get('needs_clarification') and result.get('interpretations'):
            interpretations = result['interpretations']
            
            print(f"\n⚠️  '{term}' would return mixed eBay results!")
            print(f"   {result.get('reasoning', '')}")
            print(f"\n   What did you mean?")
            
            for i, interp in enumerate(interpretations, 1):
                meaning = interp['meaning']
                terms_list = interp['search_terms']
                print(f"   {i}. {meaning}")
                print(f"      → Would search: {', '.join(terms_list[:3])}")
            
            print(f"   0. Skip this term")
            
            while True:
                try:
                    choice = input(f"\n   Choice (1-{len(interpretations)}, or 0 to skip): ").strip()
                    if choice == '0':
                        print(f"   → Skipped '{term}'")
                        break
                    
                    choice_num = int(choice)
                    if 1 <= choice_num <= len(interpretations):
                        selected = interpretations[choice_num - 1]
                        optimized = selected['search_terms']
                        print(f"   → Using: {', '.join(optimized)}")
                        final_terms.extend(optimized)
                        break
                    else:
                        print(f"   Invalid choice. Enter 1-{len(interpretations)} or 0.")
                except ValueError:
                    print(f"   Invalid input. Enter a number.")
        else:
            # Term is clear, use as-is
            print(f"   ✓ Clear search term")
            final_terms.append(term)
    
    return final_terms


def clarify_search_terms_sync(
    terms: list[str],
    ollama_url: str = "http://localhost:11434",
    model: str = "qwen2.5"
) -> list[str]:
    """Synchronous wrapper for clarify_search_terms"""
    return asyncio.run(clarify_search_terms(terms, ollama_url, model))


if __name__ == "__main__":
    interactive_term_manager()
