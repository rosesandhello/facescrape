"""
Search term expansion with synonyms and common misspellings.
Supports both built-in expansions and user-defined custom terms.
"""
import json
import re
from pathlib import Path

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


if __name__ == "__main__":
    interactive_term_manager()
