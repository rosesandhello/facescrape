"""
AI-Powered Title Identifier

Uses local LLMs to:
1. Identify the actual product from FB listing (title + image)
2. Generate optimal eBay search title
3. Create search variations (synonyms, abbreviations, common misspellings)
"""
import asyncio
import base64
import json
import re
from dataclasses import dataclass, field
from typing import Optional

import httpx


@dataclass
class IdentifiedProduct:
    """Result of product identification"""
    original_title: str
    identified_title: str  # Clean, standardized product name
    brand: str
    model: str
    category: str
    condition: str
    is_defective: bool  # "for parts", "no core", "broken", "not working"
    defect_reason: str  # Why it's flagged (e.g., "no GPU core", "screen cracked")
    is_vague: bool = False  # Too generic to search meaningfully
    vague_reason: str = ""  # Why it's flagged as vague
    search_variations: list[str] = field(default_factory=list)  # Different ways to search for this item
    confidence: float = 0.0
    reasoning: str = ""
    raw_vision_response: str = ""
    raw_text_response: str = ""
    identification_source: str = ""  # "title", "description", "image", or combination
    
    def get_search_queries(self, max_queries: int = 3) -> list[str]:
        """
        Get the best search queries to use.
        
        Priority:
        1. If original title is specific (has brand/model keywords), use cleaned original
        2. Otherwise use AI-generated identified_title
        3. Add AI variations as fallbacks
        """
        queries = []
        
        # Check if original title looks specific enough to use directly
        cleaned_original = self._clean_title_for_search(self.original_title)
        
        if self._title_is_specific(self.original_title):
            # Original title is specific - use it as primary
            queries.append(cleaned_original)
            # Add AI title as fallback if different
            if self.identified_title.lower() != cleaned_original.lower():
                queries.append(self.identified_title)
        else:
            # Original is vague - rely on AI identification
            queries.append(self.identified_title)
            # Add cleaned original as fallback
            if cleaned_original.lower() != self.identified_title.lower():
                queries.append(cleaned_original)
        
        # Add AI-generated variations
        for var in self.search_variations:
            if var.lower() not in [q.lower() for q in queries]:
                queries.append(var)
            if len(queries) >= max_queries:
                break
        
        return queries[:max_queries]
    
    def _clean_title_for_search(self, title: str) -> str:
        """Clean title for eBay search - remove junk but preserve product identity"""
        import re
        
        cleaned = title
        
        # Remove common FB junk
        junk_patterns = [
            r'\b(obo|or best offer|firm|no lowball|must sell|need gone)\b',
            r'\b(local pickup|pick up only|cash only|venmo|zelle)\b',
            r'\b(great deal|excellent condition|like new condition)\b',
            r'\b(pittsburgh|pa|ohio|oh|wv|west virginia)\b',  # Location
            r'\b\d+ miles? away\b',
            r'\blisted \d+ \w+ ago\b',
            r'[!]{2,}',  # Multiple exclamation marks
        ]
        
        for pattern in junk_patterns:
            cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)
        
        # Clean up whitespace
        cleaned = ' '.join(cleaned.split())
        cleaned = cleaned.strip('.,;:!? ')
        
        return cleaned
    
    def _title_is_specific(self, title: str) -> bool:
        """
        Check if title contains specific product identifiers.
        
        Specific = has recognizable brand AND model/version info
        """
        import re
        title_lower = title.lower()
        
        # Known brands that indicate specificity
        brands = [
            'nintendo', 'sony', 'microsoft', 'apple', 'samsung', 'lg',
            'dell', 'hp', 'lenovo', 'asus', 'acer', 'msi', 'gigabyte',
            'nvidia', 'amd', 'intel', 'evga', 'zotac', 'corsair', 'logitech',
            'playstation', 'xbox', 'iphone', 'ipad', 'macbook', 'galaxy',
            'american silver eagle', 'silver eagle', 'morgan dollar',
            'peace dollar', 'walking liberty', 'gold eagle', 'maple leaf'
        ]
        
        has_brand = any(brand in title_lower for brand in brands)
        
        # Model patterns (numbers, version indicators)
        model_patterns = [
            r'\b(rtx|gtx|rx)\s*\d{3,4}',  # GPU models
            r'\b(i[3579]|ryzen\s*[3579])',  # CPU models
            r'\bswitch\s*(oled|lite)?\b',  # Nintendo Switch
            r'\b(ps[45]|playstation\s*[45])\b',  # PlayStation
            r'\b(xbox\s*(one|series)\s*[xs]?)\b',  # Xbox
            r'\biphone\s*\d{1,2}',  # iPhone
            r'\bipad\s*(pro|air|mini)?\b',  # iPad
            r'\b\d+\s*(oz|gram|g)\b',  # Weight (coins/bullion)
            r'\b(20\d{2}|19\d{2})\b',  # Year (coins, dated items)
            r'\b\d{3,4}\s*(gb|tb)\b',  # Storage capacity
        ]
        
        has_model = any(re.search(p, title_lower) for p in model_patterns)
        
        return has_brand or has_model
    
    def should_skip(self) -> bool:
        """Return True if this listing should be skipped for arbitrage"""
        return self.is_defective or self.is_vague
    
    def skip_reason(self) -> str:
        """Get reason for skipping"""
        if self.is_defective:
            return f"Defective: {self.defect_reason}"
        if self.is_vague:
            return f"Vague: {self.vague_reason}"
        return ""


class TitleIdentifier:
    """
    Identifies products from FB listings using local LLMs.
    
    Uses Ollama with:
    - Moondream (vision) for image analysis
    - Qwen (text) for title generation and variations
    """
    
    def __init__(
        self,
        ollama_url: str = "http://localhost:11434",
        vision_model: str = "llava:13b",
        text_model: str = "qwen2.5"
    ):
        self.ollama_url = ollama_url
        self.vision_model = vision_model
        self.text_model = text_model
        self._client = None
    
    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=90.0)
        return self._client
    
    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None
    
    async def _download_image(self, url: str) -> Optional[bytes]:
        """Download image from URL"""
        try:
            client = await self._get_client()
            response = await client.get(url, follow_redirects=True)
            if response.status_code == 200:
                return response.content
        except Exception as e:
            print(f"      ⚠️ Failed to download image: {e}")
        return None
    
    def extract_model_from_description(self, description: str) -> dict:
        """
        Extract brand/model info from listing description.
        
        Returns dict with brand, model, and any extracted specs.
        """
        if not description:
            return {}
        
        desc_lower = description.lower()
        result = {}
        
        # Brand patterns
        brand_patterns = {
            'nvidia': r'\b(nvidia|geforce)\b',
            'amd': r'\b(amd|radeon)\b',
            'intel': r'\b(intel|core i[3579])\b',
            'apple': r'\b(apple|iphone|ipad|macbook|airpods)\b',
            'samsung': r'\b(samsung|galaxy)\b',
            'sony': r'\b(sony|playstation|ps[45])\b',
            'microsoft': r'\b(microsoft|xbox)\b',
            'nintendo': r'\b(nintendo|switch)\b',
            'asus': r'\b(asus|rog)\b',
            'msi': r'\b(msi)\b',
            'gigabyte': r'\b(gigabyte|aorus)\b',
            'evga': r'\b(evga)\b',
            'zotac': r'\b(zotac)\b',
            'corsair': r'\b(corsair)\b',
            'dell': r'\b(dell|alienware)\b',
            'hp': r'\b(hp|hewlett|pavilion|omen)\b',
            'lenovo': r'\b(lenovo|thinkpad|legion)\b',
        }
        
        for brand, pattern in brand_patterns.items():
            if re.search(pattern, desc_lower):
                result['brand'] = brand.title()
                break
        
        # Model patterns - extract specific model numbers
        model_patterns = [
            (r'\b(rtx\s*\d{4}(?:\s*ti)?)\b', 'gpu'),
            (r'\b(gtx\s*\d{4}(?:\s*ti)?)\b', 'gpu'),
            (r'\b(rx\s*\d{4}(?:\s*xt)?)\b', 'gpu'),
            (r'\b(i[3579][-\s]*\d{4,5}[a-z]*)\b', 'cpu'),
            (r'\b(ryzen\s*[3579]\s*\d{4}[a-z]*)\b', 'cpu'),
            (r'\b(iphone\s*\d{1,2}(?:\s*pro)?(?:\s*max)?)\b', 'phone'),
            (r'\b(ipad\s*(?:pro|air|mini)?(?:\s*\d+)?)\b', 'tablet'),
            (r'\b(galaxy\s*s\d{2}(?:\s*ultra|\+)?)\b', 'phone'),
            (r'\b(switch\s*(?:oled|lite)?)\b', 'console'),
            (r'\b(ps[45](?:\s*pro)?)\b', 'console'),
            (r'\b(xbox\s*(?:one|series)\s*[xs]?)\b', 'console'),
            (r'\b(\d+\s*(?:oz|gram|g)\s*(?:silver|gold))\b', 'bullion'),
            (r'\b(silver\s*eagle|american\s*eagle)\b', 'coin'),
            (r'\b(morgan|peace)\s*dollar\b', 'coin'),
        ]
        
        for pattern, category in model_patterns:
            match = re.search(pattern, desc_lower)
            if match:
                result['model'] = match.group(1).strip()
                result['category'] = category
                break
        
        # Extract storage/memory specs
        storage_match = re.search(r'\b(\d+)\s*(gb|tb)\b', desc_lower)
        if storage_match:
            result['storage'] = f"{storage_match.group(1)}{storage_match.group(2).upper()}"
        
        return result
    
    async def _call_ollama(
        self,
        model: str,
        prompt: str,
        images: list[str] = None
    ) -> str:
        """Call Ollama API"""
        try:
            client = await self._get_client()
            
            payload = {
                "model": model,
                "prompt": prompt,
                "stream": False
            }
            
            if images:
                payload["images"] = images
            
            response = await client.post(
                f"{self.ollama_url}/api/generate",
                json=payload,
                timeout=90.0
            )
            
            if response.status_code != 200:
                return f"Error: {response.status_code}"
            
            result = response.json()
            return result.get('response', '')
            
        except Exception as e:
            return f"Error: {e}"
    
    async def identify_from_image(
        self,
        image_url: str,
        original_title: str
    ) -> dict:
        """
        Use vision model to identify product from image.
        
        Returns dict with product details.
        """
        image_data = await self._download_image(image_url)
        if not image_data:
            return {"error": "Could not download image"}
        
        img_b64 = base64.b64encode(image_data).decode('utf-8')
        
        prompt = f"""Analyze this product image. The listing title says: "{original_title}"

CRITICAL CHECKS:

1. DEFECTIVE/FOR PARTS - Flag if:
   - "for parts" / "parts only" / "no core" / "broken" / "not working" / "as-is"
   - Missing critical components (GPU with no chip, phone with cracked screen)

2. TOO VAGUE TO SEARCH - Flag if you CANNOT identify a specific brand AND model:
   - "Gaming storage tower" = VAGUE (no brand/model)
   - "Old laptop for parts" = VAGUE (no brand/model)
   - "Vintage jewelry lot" = VAGUE (no specific item)
   - "Random electronics bundle" = VAGUE
   - Generic furniture, clothing without brand = VAGUE
   
   SPECIFIC means: "Nintendo Switch OLED", "iPhone 13 Pro", "1oz Silver Eagle 2024"
   VAGUE means: "gaming console", "smartphone", "silver coins"

Identify:
1. BRAND: The manufacturer/brand name (or "unknown")
2. MODEL: The specific model name/number (or "unknown")
3. PRODUCT: What type of product
4. CONDITION: new | like_new | used | fair | poor
5. IS_DEFECTIVE: yes/no
6. DEFECT_REASON: If defective, why
7. IS_VAGUE: yes/no - Can you identify a SPECIFIC searchable product with brand+model?
8. VAGUE_REASON: If vague, explain (e.g., "no brand or model identifiable")
9. KEY_FEATURES: Notable features

Format your response EXACTLY as:
BRAND: [brand or unknown]
MODEL: [model or unknown] 
PRODUCT: [product type]
CONDITION: [condition]
IS_DEFECTIVE: [yes/no]
DEFECT_REASON: [reason or none]
IS_VAGUE: [yes/no]
VAGUE_REASON: [reason or none]
KEY_FEATURES: [features]"""

        response = await self._call_ollama(self.vision_model, prompt, images=[img_b64])
        
        # Parse response
        result = {
            "brand": "unknown",
            "model": "unknown", 
            "product": "unknown",
            "condition": "used",
            "is_defective": False,
            "defect_reason": "",
            "is_vague": False,
            "vague_reason": "",
            "features": "",
            "raw_response": response
        }
        
        for line in response.split('\n'):
            line = line.strip()
            if line.startswith('BRAND:'):
                result['brand'] = line.replace('BRAND:', '').strip()
            elif line.startswith('MODEL:'):
                result['model'] = line.replace('MODEL:', '').strip()
            elif line.startswith('PRODUCT:'):
                result['product'] = line.replace('PRODUCT:', '').strip()
            elif line.startswith('CONDITION:'):
                result['condition'] = line.replace('CONDITION:', '').strip()
            elif line.startswith('IS_DEFECTIVE:'):
                val = line.replace('IS_DEFECTIVE:', '').strip().lower()
                result['is_defective'] = val in ('yes', 'true', '1')
            elif line.startswith('DEFECT_REASON:'):
                reason = line.replace('DEFECT_REASON:', '').strip()
                if reason.lower() not in ('none', 'n/a', ''):
                    result['defect_reason'] = reason
            elif line.startswith('IS_VAGUE:'):
                val = line.replace('IS_VAGUE:', '').strip().lower()
                result['is_vague'] = val in ('yes', 'true', '1')
            elif line.startswith('VAGUE_REASON:'):
                reason = line.replace('VAGUE_REASON:', '').strip()
                if reason.lower() not in ('none', 'n/a', ''):
                    result['vague_reason'] = reason
            elif line.startswith('KEY_FEATURES:'):
                result['features'] = line.replace('KEY_FEATURES:', '').strip()
        
        # Also check title for defect keywords (belt and suspenders)
        title_lower = original_title.lower()
        defect_keywords = [
            'for parts', 'parts only', 'no core', 'no chip', 'no gpu', 'no cpu',
            'not working', 'broken', 'dead', 'as-is', 'as is', 'defective',
            'cracked screen', 'water damage', "won't turn on", 'doesnt work',
            "doesn't work", 'for repair', 'needs repair'
        ]
        for keyword in defect_keywords:
            if keyword in title_lower and not result['is_defective']:
                result['is_defective'] = True
                result['defect_reason'] = f"Title contains '{keyword}'"
                break
        
        # Check for vague listings if brand AND model are unknown
        if result['brand'].lower() == 'unknown' and result['model'].lower() == 'unknown':
            # Check if title is too generic
            vague_indicators = [
                'lot', 'bundle', 'misc', 'random', 'various', 'assorted',
                'vintage', 'antique', 'old', 'storage', 'tower', 'stand',
                'shelf', 'rack', 'organizer', 'holder', 'case', 'bag'
            ]
            for indicator in vague_indicators:
                if indicator in title_lower:
                    result['is_vague'] = True
                    result['vague_reason'] = f"Generic item ('{indicator}'), no brand/model"
                    break
            
            # If still not flagged but brand+model unknown, flag it
            if not result['is_vague'] and not result['is_defective']:
                result['is_vague'] = True
                result['vague_reason'] = "Cannot identify specific brand and model"
        
        return result
    
    async def generate_search_title(
        self,
        original_title: str,
        product_info: dict
    ) -> tuple[str, list[str], str]:
        """
        Use text LLM to generate optimal eBay search title and variations.
        
        Returns: (main_title, [variations], raw_response)
        """
        brand = product_info.get('brand', 'unknown')
        model = product_info.get('model', 'unknown')
        product = product_info.get('product', 'unknown')
        features = product_info.get('features', '')
        is_defective = product_info.get('is_defective', False)
        defect_reason = product_info.get('defect_reason', '')
        
        defect_note = ""
        if is_defective:
            defect_note = f"""
⚠️ WARNING: This item is DEFECTIVE/FOR PARTS: {defect_reason}
When generating search queries, include "for parts" or the defect type so we compare against similar defective items, NOT working units!
"""
        
        prompt = f"""You are an eBay search optimization expert. Generate search queries to find sold listings for this item.

ORIGINAL FB LISTING TITLE: "{original_title}"
{defect_note}
IDENTIFIED PRODUCT INFO:
- Brand: {brand}
- Model: {model}
- Product Type: {product}
- Features: {features}

Generate:
1. MAIN_TITLE: The single best eBay search query (clean, standardized product name with brand and model)
2. VARIATIONS: 2-3 alternative search queries that might find the same product

Rules:
- Remove seller junk like "must sell", "great deal", location names
- Include brand and model when known
- Keep queries concise (3-6 words ideal)
- Include common abbreviations or alternate names
- For coins/bullion: include weight, purity, type
- FOR DEFECTIVE ITEMS: Include "for parts" or defect in the query!

Format your response EXACTLY as:
MAIN_TITLE: [title]
VARIATION: [alt1]
VARIATION: [alt2]
VARIATION: [alt3]"""

        response = await self._call_ollama(self.text_model, prompt)
        
        # Parse response
        main_title = original_title  # Fallback
        variations = []
        
        for line in response.split('\n'):
            line = line.strip()
            if line.startswith('MAIN_TITLE:'):
                main_title = line.replace('MAIN_TITLE:', '').strip()
            elif line.startswith('VARIATION:'):
                var = line.replace('VARIATION:', '').strip()
                if var and var not in variations:
                    variations.append(var)
        
        return main_title, variations, response
    
    async def identify_product(
        self,
        original_title: str,
        image_url: Optional[str] = None,
        description: str = ""
    ) -> IdentifiedProduct:
        """
        Full product identification pipeline.
        
        Priority for identification:
        1. Title - if it's already specific (has brand+model)
        2. Description - if it contains model info title lacks
        3. Image - use vision AI to identify from picture
        
        Returns IdentifiedProduct with search queries ready to use.
        """
        print(f"   🔍 Identifying product: {original_title[:50]}...")
        
        identification_sources = []
        product_info = {
            "brand": "unknown",
            "model": "unknown",
            "product": "unknown",
            "condition": "used",
            "is_defective": False,
            "defect_reason": "",
            "is_vague": False,
            "vague_reason": "",
            "features": "",
            "raw_response": ""
        }
        
        title_lower = original_title.lower()
        
        # Check for defect keywords first (applies to all sources)
        defect_keywords = [
            'for parts', 'parts only', 'no core', 'no chip', 'no gpu', 'no cpu',
            'not working', 'broken', 'dead', 'as-is', 'as is', 'defective',
            'cracked screen', 'water damage', "won't turn on", 'doesnt work',
            "doesn't work", 'for repair', 'needs repair'
        ]
        for keyword in defect_keywords:
            if keyword in title_lower or (description and keyword in description.lower()):
                product_info['is_defective'] = True
                product_info['defect_reason'] = f"Contains '{keyword}'"
                break
        
        # === SOURCE 1: Check if TITLE is specific enough ===
        # Use the _title_is_specific method from IdentifiedProduct
        title_has_brand = False
        title_has_model = False
        
        # Check for brands in title
        brands = [
            'nvidia', 'amd', 'intel', 'apple', 'samsung', 'sony', 'microsoft',
            'nintendo', 'asus', 'msi', 'gigabyte', 'evga', 'zotac', 'corsair',
            'dell', 'hp', 'lenovo', 'logitech', 'playstation', 'xbox',
            'iphone', 'ipad', 'macbook', 'galaxy', 'silver eagle', 'gold eagle'
        ]
        for brand in brands:
            if brand in title_lower:
                product_info['brand'] = brand.title()
                title_has_brand = True
                break
        
        # Check for model patterns in title
        model_patterns = [
            r'\b(rtx|gtx|rx)\s*\d{3,4}',
            r'\b(i[3579][-\s]*\d{4,5})',
            r'\b(ryzen\s*[3579])',
            r'\b(switch\s*(?:oled|lite)?)\b',
            r'\b(ps[45])\b',
            r'\b(xbox\s*(?:one|series))',
            r'\biphone\s*\d{1,2}',
            r'\b\d+\s*(oz|gram)',
        ]
        for pattern in model_patterns:
            match = re.search(pattern, title_lower)
            if match:
                product_info['model'] = match.group(0)
                title_has_model = True
                break
        
        if title_has_brand or title_has_model:
            identification_sources.append("title")
            print(f"   📝 Title is specific: brand={product_info['brand']}, model={product_info['model']}")
        
        # === SOURCE 2: Check DESCRIPTION for model info ===
        if description and (not title_has_brand or not title_has_model):
            desc_info = self.extract_model_from_description(description)
            if desc_info:
                if desc_info.get('brand') and product_info['brand'] == 'unknown':
                    product_info['brand'] = desc_info['brand']
                    identification_sources.append("description")
                    print(f"   📄 Description has brand: {desc_info['brand']}")
                if desc_info.get('model') and product_info['model'] == 'unknown':
                    product_info['model'] = desc_info['model']
                    if "description" not in identification_sources:
                        identification_sources.append("description")
                    print(f"   📄 Description has model: {desc_info['model']}")
                if desc_info.get('storage'):
                    product_info['features'] = desc_info['storage']
        
        # === SOURCE 3: Use IMAGE if still need identification ===
        if image_url and product_info['brand'] == 'unknown' and product_info['model'] == 'unknown':
            print(f"   🖼️ Analyzing image with {self.vision_model}...")
            vision_info = await self.identify_from_image(image_url, original_title)
            
            if 'error' not in vision_info:
                if vision_info.get('brand', 'unknown') != 'unknown':
                    product_info['brand'] = vision_info['brand']
                    identification_sources.append("image")
                if vision_info.get('model', 'unknown') != 'unknown':
                    product_info['model'] = vision_info['model']
                    if "image" not in identification_sources:
                        identification_sources.append("image")
                
                # Copy other vision info
                if vision_info.get('product', 'unknown') != 'unknown':
                    product_info['product'] = vision_info['product']
                if vision_info.get('condition'):
                    product_info['condition'] = vision_info['condition']
                if vision_info.get('is_defective'):
                    product_info['is_defective'] = vision_info['is_defective']
                    product_info['defect_reason'] = vision_info.get('defect_reason', '')
                if vision_info.get('is_vague'):
                    product_info['is_vague'] = vision_info['is_vague']
                    product_info['vague_reason'] = vision_info.get('vague_reason', '')
                product_info['raw_response'] = vision_info.get('raw_response', '')
                
                print(f"      Brand: {product_info.get('brand', 'unknown')}")
                print(f"      Model: {product_info.get('model', 'unknown')}")
        
        # === Check if still VAGUE after all sources ===
        if product_info['brand'] == 'unknown' and product_info['model'] == 'unknown':
            # Check for vague indicators
            vague_indicators = [
                'lot', 'bundle', 'misc', 'random', 'various', 'assorted',
                'storage tower', 'gaming tower', 'shelf', 'rack', 'organizer',
                'holder', 'stand', 'furniture', 'decor', 'vintage', 'antique'
            ]
            for indicator in vague_indicators:
                if indicator in title_lower:
                    product_info['is_vague'] = True
                    product_info['vague_reason'] = f"Generic item ('{indicator}')"
                    break
            
            if not product_info['is_vague'] and not product_info['is_defective']:
                product_info['is_vague'] = True
                product_info['vague_reason'] = "Cannot identify brand/model from title, description, or image"
        
        # === Generate search queries ===
        print(f"   📝 Generating search queries with {self.text_model}...")
        main_title, variations, raw_text_response = await self.generate_search_title(
            original_title, product_info
        )
        
        print(f"      Main: {main_title}")
        for i, var in enumerate(variations, 1):
            print(f"      Alt {i}: {var}")
        
        # Calculate confidence based on how much we identified
        confidence = 0.5  # Base
        if product_info.get('brand') != 'unknown':
            confidence += 0.2
        if product_info.get('model') != 'unknown':
            confidence += 0.2
        if image_url:
            confidence += 0.1
        
        # Log defective/vague items prominently
        is_defective = product_info.get('is_defective', False)
        defect_reason = product_info.get('defect_reason', '')
        is_vague = product_info.get('is_vague', False)
        vague_reason = product_info.get('vague_reason', '')
        
        if is_defective:
            print(f"   ⚠️ DEFECTIVE: {defect_reason}")
        if is_vague:
            print(f"   🔸 VAGUE: {vague_reason}")
        
        # Build identification source string
        source_str = "+".join(identification_sources) if identification_sources else "none"
        print(f"   📍 Identified from: {source_str}")
        
        return IdentifiedProduct(
            original_title=original_title,
            identified_title=main_title,
            brand=product_info.get('brand', 'unknown'),
            model=product_info.get('model', 'unknown'),
            category=product_info.get('product', 'unknown'),
            condition=product_info.get('condition', 'used'),
            is_defective=is_defective,
            defect_reason=defect_reason,
            is_vague=is_vague,
            vague_reason=vague_reason,
            search_variations=variations,
            confidence=min(confidence, 1.0),
            reasoning=f"Vision: {self.vision_model}, Text: {self.text_model}",
            raw_vision_response=product_info.get('raw_response', ''),
            raw_text_response=raw_text_response,
            identification_source=source_str
        )


async def test_identifier():
    """Test the title identifier"""
    identifier = TitleIdentifier()
    
    # Test with a sample title (no image)
    result = await identifier.identify_product(
        original_title="Nintendo Switch OLED White Like New Must Sell Pittsburgh PA",
        image_url=None
    )
    
    print(f"\n📦 Identification Result:")
    print(f"   Original: {result.original_title}")
    print(f"   Identified: {result.identified_title}")
    print(f"   Brand: {result.brand}")
    print(f"   Model: {result.model}")
    print(f"   Search queries: {result.get_search_queries()}")
    print(f"   Confidence: {result.confidence:.0%}")
    
    await identifier.close()


if __name__ == "__main__":
    asyncio.run(test_identifier())
