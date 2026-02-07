"""
Search Term Generator - Cascading Specificity Check

Flow:
1. Image recognition (OpenAI GPT-4o) tries to identify the item
2. Qwen evaluates: is the image match specific enough for eBay?
   - YES → use as search term
   - NO → continue
3. Qwen synthesizes title + description into a candidate term
4. Qwen evaluates: is this term specific enough?
   - YES → use as search term
   - NO → continue
5. Qwen synthesizes title + description + image description
6. Qwen evaluates: is this term specific enough?
   - YES → use as search term
   - NO → DROP the listing (return None)
"""
import asyncio
import base64
import os
from dataclasses import dataclass
from typing import Optional

import httpx


@dataclass
class SearchTermResult:
    """Result of search term generation for a single item"""
    search_term: Optional[str]  # None = listing should be dropped
    source: str  # "image", "title+description", "title+description+image", "dropped"
    reasoning: str
    raw_responses: dict  # Store all LLM responses for debugging
    
    @property
    def should_drop(self) -> bool:
        return self.search_term is None


@dataclass 
class MultiItemResult:
    """Result of search term generation for a listing (may contain multiple items)"""
    items: list[SearchTermResult]  # One per identified item
    is_multi_item: bool  # Was this listing split into multiple items?
    original_title: str
    
    @property
    def valid_items(self) -> list[SearchTermResult]:
        """Get items that weren't dropped"""
        return [item for item in self.items if not item.should_drop]
    
    @property
    def all_dropped(self) -> bool:
        """True if all items were dropped"""
        return len(self.valid_items) == 0


class SearchTermGenerator:
    """
    Generate eBay search terms using cascading specificity checks.
    
    Uses:
    - Google Gemini for image recognition (free tier)
    - qwen2.5 (local ollama) for specificity evaluation and synthesis
    """
    
    def __init__(
        self,
        ollama_url: str = "http://localhost:11434",
        text_model: str = "qwen2.5",
        gemini_api_key: Optional[str] = None,
        gemini_model: str = "gemini-2.0-flash"
    ):
        self.ollama_url = ollama_url
        self.text_model = text_model
        self.gemini_api_key = gemini_api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        self.gemini_model = gemini_model
        self._client: Optional[httpx.AsyncClient] = None
    
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
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
            response = await client.get(url, follow_redirects=True, headers=headers)
            if response.status_code == 200:
                return response.content
            else:
                print(f"      ⚠️ Image download failed: HTTP {response.status_code}")
        except Exception as e:
            print(f"      ⚠️ Failed to download image: {e}")
        return None
    
    async def _call_ollama(
        self,
        model: str,
        prompt: str,
        images: Optional[list[str]] = None
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
    
    async def _identify_from_image(self, image_url: str, title_hint: str) -> Optional[str]:
        """
        Step 1: Use Google Gemini vision to identify what's in the image.
        Returns a description/identification, or None if unavailable.
        """
        if not self.gemini_api_key:
            print("      ⚠️ No Gemini API key configured")
            return None
        
        try:
            client = await self._get_client()
            
            # First download the image and convert to base64
            image_data = await self._download_image(image_url)
            if not image_data:
                return None
            
            import base64
            img_b64 = base64.b64encode(image_data).decode('utf-8')
            
            # Detect mime type from image data
            mime_type = "image/jpeg"  # default
            if image_data[:4] == b'\x89PNG':
                mime_type = "image/png"
            elif image_data[:4] == b'RIFF' and image_data[8:12] == b'WEBP':
                mime_type = "image/webp"
            elif image_data[:3] == b'GIF':
                mime_type = "image/gif"
            
            prompt = f"""Look at this image. The listing title says: "{title_hint}"

Identify what this item is as specifically as possible. Include:
- Brand name if visible
- Model name/number if visible
- Product type
- Any distinguishing features (year, size, color, edition)

Be as specific as possible. If you can identify the exact product, name it.
If you can only identify a general category, say so.

Respond with ONLY the product identification, nothing else.
Example good responses:
- "2024 American Silver Eagle 1oz BU coin"
- "Nintendo Switch OLED White"
- "EVGA RTX 3080 FTW3 Ultra graphics card"

Example bad responses (too vague):
- "A coin"
- "Gaming console"
- "Computer part"
"""

            response = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{self.gemini_model}:generateContent?key={self.gemini_api_key}",
                headers={"Content-Type": "application/json"},
                json={
                    "contents": [{
                        "parts": [
                            {"text": prompt},
                            {
                                "inline_data": {
                                    "mime_type": mime_type,
                                    "data": img_b64
                                }
                            }
                        ]
                    }]
                },
                timeout=30.0
            )
            
            if response.status_code != 200:
                error_text = response.text[:200]
                print(f"      ⚠️ Gemini API error {response.status_code}: {error_text}")
                return None
            
            result = response.json()
            
            # Parse Gemini response
            try:
                identification = result["candidates"][0]["content"]["parts"][0]["text"].strip()
            except (KeyError, IndexError):
                print(f"      ⚠️ Unexpected Gemini response format")
                return None
            
            return identification if identification else None
            
        except Exception as e:
            print(f"      ⚠️ Gemini vision failed: {e}")
            return None
    
    async def _is_specific_enough(self, term: str, context: str = "") -> tuple[bool, str]:
        """
        Ask Qwen: Is this term specific enough to meaningfully search eBay?
        
        Returns: (is_specific: bool, reasoning: str)
        """
        prompt = f"""Is this search term SPECIFIC ENOUGH for eBay price comparison?

TERM: "{term}"
{f'CONTEXT: {context}' if context else ''}

SPECIFIC = has a recognizable BRAND/MANUFACTURER or PRODUCT LINE NAME
(something a company trademarked or produced, not just a category)

✅ SPECIFIC (has brand/product line):
- "American Silver Eagle 2024" (US Mint product line)
- "Nintendo Switch OLED" (Nintendo product)
- "iPhone 13 Pro" (Apple product)
- "EVGA RTX 3080" (brand + model)
- "Morgan Dollar 1921" (historic US coin series)
- "Herman Miller Aeron Chair" (brand + product)
- "KitchenAid Stand Mixer" (brand + product type)

❌ NOT SPECIFIC (just categories, no brand):
- "gaming storage tower" (what brand? just furniture)
- "silver coin" (which one?)
- "gaming console" (which brand?)
- "graphics card" (no brand/model)
- "laptop" (no brand)
- "vintage jewelry" (no brand/maker)
- "office chair" (no brand)
- "kitchen appliance" (no brand)

KEY TEST: Would searching this on eBay return items from ONE specific manufacturer/product line, or a mix of different brands?

Answer ONLY:
YES or NO
REASON: [brief explanation]"""

        response = await self._call_ollama(self.text_model, prompt)
        
        # Parse response - handle multiple formats
        is_specific = False
        reasoning = ""
        
        lines = response.strip().split('\n')
        for i, line in enumerate(lines):
            line = line.strip()
            
            # Format 1: "SPECIFIC: YES"
            if line.upper().startswith('SPECIFIC:'):
                val = line.split(':', 1)[1].strip().upper()
                is_specific = val in ('YES', 'TRUE', '1')
            # Format 2: Just "YES" or "NO" on first line
            elif i == 0 and line.upper() in ('YES', 'NO', 'TRUE', 'FALSE'):
                is_specific = line.upper() in ('YES', 'TRUE')
            # Format 3: "REASON: ..."
            elif line.upper().startswith('REASON:'):
                reasoning = line.split(':', 1)[1].strip()
            # Format 4: Second line is the reason (no prefix)
            elif i == 1 and not reasoning and not line.upper().startswith(('YES', 'NO', 'SPECIFIC')):
                reasoning = line
        
        return is_specific, reasoning
    
    async def _synthesize_term(
        self,
        title: str,
        description: Optional[str] = None,
        image_description: Optional[str] = None
    ) -> str:
        """
        Ask Qwen to synthesize a specific search term from available info.
        """
        sources = [f"LISTING TITLE: {title}"]
        if description:
            sources.append(f"DESCRIPTION: {description}")
        if image_description:
            sources.append(f"IMAGE SHOWS: {image_description}")
        
        sources_text = "\n".join(sources)
        
        prompt = f"""Generate an eBay search term from this listing:

{sources_text}

RULES:
- Extract the core product identity (what would you search on eBay?)
- Keep brand + model/type if present
- Remove junk words: "must sell", "great deal", "OBO", locations, conditions like "like new"
- 2-6 words is ideal
- NEVER include "Unknown", "Unidentified", "Generic", or placeholder words
- If you can't identify the brand, just use the product type (e.g., "Gaming PC RTX 4080")

EXAMPLES:
- "iPhone 13 Pro 256GB Like New Pittsburgh" → "iPhone 13 Pro 256GB"
- "1 oz American Silver Eagle 2024 BU in capsule" → "American Silver Eagle 2024 1oz"
- "Nintendo Switch OLED White Must Sell" → "Nintendo Switch OLED White"
- "RTX 3080 FTW3 Ultra works great" → "EVGA RTX 3080 FTW3 Ultra"

Only respond CANNOT_IDENTIFY if the listing is truly unidentifiable (e.g., "random stuff lot", "misc electronics").
Most listings with product names ARE identifiable.

Respond with ONLY the search term:"""

        response = await self._call_ollama(self.text_model, prompt)
        
        # Clean up response
        term = response.strip()
        if term.startswith("SEARCH TERM:"):
            term = term.replace("SEARCH TERM:", "").strip()
        
        # Filter out bad placeholder terms
        bad_words = ['unknown', 'unidentified', 'generic', 'unbranded', 'n/a', 'none']
        term_lower = term.lower()
        for bad in bad_words:
            if bad in term_lower:
                return "CANNOT_IDENTIFY"
        
        return term
    
    async def _is_multi_item_listing(
        self,
        title: str,
        description: Optional[str] = None,
        image_description: Optional[str] = None
    ) -> tuple[bool, list[str]]:
        """
        Ask Qwen: Does this listing contain multiple distinct items?
        
        Returns: (is_multi: bool, list of individual item descriptions)
        """
        context_parts = [f"TITLE: {title}"]
        if description:
            context_parts.append(f"DESCRIPTION: {description}")
        if image_description:
            context_parts.append(f"IMAGE SHOWS: {image_description}")
        
        context = "\n".join(context_parts)
        
        prompt = f"""Analyze this listing. Does it contain MULTIPLE DISTINCT ITEMS that should be searched separately on eBay?

{context}

MULTI-ITEM examples:
- "Nintendo Switch + 3 games + Pro Controller" → YES, 5 items
- "Lot of 5 silver coins: Morgan, Peace, Eagles" → YES, 5 items  
- "PS5 bundle with 2 controllers and 4 games" → YES, multiple items
- "Gaming PC: RTX 4080, i9-13900K, 64GB RAM, 2TB NVMe" → YES, extract each component!

PC/COMPUTER LISTINGS - IMPORTANT:
If a PC listing includes specs, list the PC FIRST, then extract EACH component:
1. The PC itself (e.g., "Gaming PC RTX 4080 i9-13900K 64GB")
2. CPU (e.g., "Intel Core i9-13900K")
3. GPU (e.g., "EVGA RTX 4080 FTW3 Ultra") 
4. RAM (e.g., "Corsair Vengeance 64GB DDR5")
5. Storage (e.g., "Samsung 990 Pro 2TB NVMe")
6. Motherboard, PSU, Case, Cooler if mentioned

SINGLE ITEM examples:
- "iPhone 13 with case and charger" → NO (accessories bundled)
- "Nintendo Switch OLED with dock" → NO (dock is standard)
- "Gaming PC" (no specs listed) → NO (can't extract components)

IMPORTANT: When listing items, be SPECIFIC with full product names:
- ❌ "GPU" → ✅ "EVGA RTX 4080 FTW3 Ultra"
- ❌ "Zelda game" → ✅ "Legend of Zelda Tears of the Kingdom Switch"
- ❌ "RAM" → ✅ "Corsair Vengeance 64GB DDR5-6000"

Use info from title AND description to get specific names.

Respond with:
MULTI_ITEM: YES or NO
ITEMS: [List each item with FULL SPECIFIC product name, one per line]"""

        response = await self._call_ollama(self.text_model, prompt)
        
        # Parse response
        is_multi = False
        items = []
        
        lines = response.strip().split('\n')
        in_items = False
        
        for line in lines:
            line = line.strip()
            if line.upper().startswith('MULTI_ITEM:'):
                val = line.split(':', 1)[1].strip().upper()
                is_multi = val in ('YES', 'TRUE')
            elif line.upper().startswith('ITEMS:'):
                in_items = True
                # Check if items are on same line
                rest = line.split(':', 1)[1].strip()
                if rest and rest not in ('', '-', 'N/A'):
                    items.append(rest)
            elif in_items and line and not line.startswith(('MULTI', 'SINGLE')):
                # Clean up list markers
                clean = line.lstrip('•-*123456789. ')
                if clean:
                    items.append(clean)
        
        # If not multi but we have no items, use title as single item
        if not items:
            items = [title]
        
        return is_multi, items
    
    def _is_iso_listing(self, title: str, description: Optional[str], image_description: Optional[str]) -> tuple[bool, str]:
        """
        Check if this is an ISO (In Search Of) / WTB (Want To Buy) post.
        Returns (is_iso, reason)
        """
        iso_keywords = [
            'looking for', 'looking to buy', 'iso', 'in search of',
            'wanted', 'wtb', 'want to buy', 'searching for', 'need a',
            'anyone have', 'anyone selling', 'does anyone', 'seeking',
            'in need of', 'trying to find', 'hoping to find'
        ]
        
        # Check title
        title_lower = title.lower()
        for kw in iso_keywords:
            if kw in title_lower:
                return True, f"Title contains '{kw}'"
        
        # Check description
        if description:
            desc_lower = description.lower()
            for kw in iso_keywords:
                if kw in desc_lower:
                    return True, f"Description contains '{kw}'"
        
        # Check image description
        if image_description:
            img_lower = image_description.lower()
            for kw in iso_keywords:
                if kw in img_lower:
                    return True, f"Image contains '{kw}'"
        
        return False, ""
    
    async def generate_search_terms_multi(
        self,
        title: str,
        description: Optional[str] = None,
        image_url: Optional[str] = None
    ) -> MultiItemResult:
        """
        Generate search terms, handling multi-item listings.
        
        Flow:
        1. Check if listing contains multiple items
        2. If yes, split into individual items
        3. Generate search term for each item
        4. Return list of results (valid items + dropped items)
        """
        raw_responses = {}
        
        print(f"   🔍 Analyzing listing: {title[:50]}...")
        
        # Step 0: Get image identification if available (used for both multi-check and search)
        image_description = None
        if image_url:
            print(f"   📷 Analyzing image with Gemini...")
            image_description = await self._identify_from_image(image_url, title)
            if image_description:
                print(f"      → {image_description[:60]}...")
                raw_responses['image_description'] = image_description
        
        # Step 0.5: Check if this is an ISO/WTB post (not a sale)
        is_iso, iso_reason = self._is_iso_listing(title, description, image_description)
        if is_iso:
            print(f"   🚫 ISO/WTB post detected: {iso_reason}")
            return MultiItemResult(
                items=[SearchTermResult(
                    search_term=None,
                    source="dropped",
                    reasoning=f"ISO/WTB post: {iso_reason}",
                    raw_responses=raw_responses
                )],
                is_multi_item=False,
                original_title=title
            )
        
        # Step 1: Check if this is a multi-item listing
        print(f"   🔢 Checking for multiple items...")
        is_multi, item_descriptions = await self._is_multi_item_listing(
            title, description, image_description
        )
        raw_responses['multi_item_check'] = {'is_multi': is_multi, 'items': item_descriptions}
        
        if is_multi and len(item_descriptions) > 1:
            print(f"      → Found {len(item_descriptions)} items in listing:")
            for i, item in enumerate(item_descriptions, 1):
                print(f"         {i}. {item[:50]}")
        else:
            print(f"      → Single item listing")
        
        # Step 2: Generate search term for each item
        results = []
        
        for i, item_desc in enumerate(item_descriptions):
            if len(item_descriptions) > 1:
                print(f"\n   📦 Item {i+1}/{len(item_descriptions)}: {item_desc[:40]}...")
            
            # For individual items in a multi-item listing, we use the item description
            # as the "title" and pass along the image description if available
            result = await self.generate_search_term(
                title=item_desc,
                description=description if not is_multi else None,  # Only use desc for single items
                image_url=None  # Don't re-analyze image, use cached description
            )
            
            # If we have an image description and this is a single item, 
            # and the first attempt failed, try with image context
            if result.should_drop and image_description and len(item_descriptions) == 1:
                print(f"      🔄 Retrying with image context...")
                # Manually try synthesis with image description
                synthesized = await self._synthesize_term(
                    item_desc, description, image_description
                )
                if synthesized and synthesized != "CANNOT_IDENTIFY":
                    is_specific, reasoning = await self._is_specific_enough(synthesized)
                    if is_specific:
                        result = SearchTermResult(
                            search_term=synthesized,
                            source="title+description+image",
                            reasoning=reasoning,
                            raw_responses=result.raw_responses
                        )
            
            results.append(result)
            
            if result.should_drop:
                print(f"      🚫 Dropped: {result.reasoning[:50]}")
            else:
                print(f"      ✅ Search term: {result.search_term}")
        
        return MultiItemResult(
            items=results,
            is_multi_item=is_multi and len(item_descriptions) > 1,
            original_title=title
        )
    
    async def generate_search_term(
        self,
        title: str,
        description: Optional[str] = None,
        image_url: Optional[str] = None
    ) -> SearchTermResult:
        """
        Main entry point: Generate a search term using cascading specificity checks.
        
        Returns SearchTermResult with:
        - search_term: The eBay search term to use (or None if listing should be dropped)
        - source: Where the term came from
        - reasoning: Why this term was chosen
        """
        raw_responses = {}
        
        print(f"   🔍 Generating search term for: {title[:50]}...")
        
        # ═══════════════════════════════════════════════════════════════
        # STEP 1: Try image recognition
        # ═══════════════════════════════════════════════════════════════
        image_identification = None
        if image_url:
            print(f"   📷 Step 1: Analyzing image with Google {self.gemini_model}...")
            image_identification = await self._identify_from_image(image_url, title)
            raw_responses['image_identification'] = image_identification
            
            if image_identification:
                print(f"      → Image identified: {image_identification}")
                
                # ═══════════════════════════════════════════════════════
                # STEP 2: Is the image identification specific enough?
                # ═══════════════════════════════════════════════════════
                print(f"   🤔 Step 2: Is image identification specific enough?")
                is_specific, reasoning = await self._is_specific_enough(
                    image_identification,
                    context="This was identified from the listing image"
                )
                raw_responses['image_specificity_check'] = {'is_specific': is_specific, 'reasoning': reasoning}
                
                if is_specific:
                    print(f"      ✅ YES - using image identification")
                    return SearchTermResult(
                        search_term=image_identification,
                        source="image",
                        reasoning=reasoning,
                        raw_responses=raw_responses
                    )
                else:
                    print(f"      ❌ NO - {reasoning}")
            else:
                print(f"      ⚠️ Could not identify from image")
        else:
            print(f"   📷 Step 1: No image available, skipping")
        
        # ═══════════════════════════════════════════════════════════════
        # STEP 3: Synthesize from title + description
        # ═══════════════════════════════════════════════════════════════
        print(f"   📝 Step 3: Synthesizing from title + description...")
        synthesized_v1 = await self._synthesize_term(title, description, image_description=None)
        raw_responses['synthesized_v1'] = synthesized_v1
        
        if synthesized_v1 and synthesized_v1 != "CANNOT_IDENTIFY":
            print(f"      → Synthesized: {synthesized_v1}")
            
            # ═══════════════════════════════════════════════════════════
            # STEP 4: Is this synthesized term specific enough?
            # ═══════════════════════════════════════════════════════════
            print(f"   🤔 Step 4: Is synthesized term specific enough?")
            is_specific, reasoning = await self._is_specific_enough(
                synthesized_v1,
                context=f"Synthesized from title: '{title}'" + (f" and description" if description else "")
            )
            raw_responses['v1_specificity_check'] = {'is_specific': is_specific, 'reasoning': reasoning}
            
            if is_specific:
                print(f"      ✅ YES - using synthesized term")
                return SearchTermResult(
                    search_term=synthesized_v1,
                    source="title+description",
                    reasoning=reasoning,
                    raw_responses=raw_responses
                )
            else:
                print(f"      ❌ NO - {reasoning}")
        else:
            print(f"      ⚠️ Could not synthesize from title+description")
        
        # ═══════════════════════════════════════════════════════════════
        # STEP 5: Synthesize from title + description + image description
        # ═══════════════════════════════════════════════════════════════
        if image_identification:
            print(f"   📝 Step 5: Synthesizing from title + description + image...")
            synthesized_v2 = await self._synthesize_term(
                title,
                description,
                image_description=image_identification
            )
            raw_responses['synthesized_v2'] = synthesized_v2
            
            if synthesized_v2 and synthesized_v2 != "CANNOT_IDENTIFY":
                print(f"      → Synthesized: {synthesized_v2}")
                
                # ═══════════════════════════════════════════════════════
                # STEP 6: Is THIS synthesized term specific enough?
                # ═══════════════════════════════════════════════════════
                print(f"   🤔 Step 6: Is final synthesized term specific enough?")
                is_specific, reasoning = await self._is_specific_enough(
                    synthesized_v2,
                    context=f"Synthesized from title, description, and image analysis"
                )
                raw_responses['v2_specificity_check'] = {'is_specific': is_specific, 'reasoning': reasoning}
                
                if is_specific:
                    print(f"      ✅ YES - using final synthesized term")
                    return SearchTermResult(
                        search_term=synthesized_v2,
                        source="title+description+image",
                        reasoning=reasoning,
                        raw_responses=raw_responses
                    )
                else:
                    print(f"      ❌ NO - {reasoning}")
            else:
                print(f"      ⚠️ Could not synthesize with all sources")
        else:
            print(f"   📝 Step 5: Skipped (no image identification available)")
        
        # ═══════════════════════════════════════════════════════════════
        # FINAL: Could not generate specific search term - DROP
        # ═══════════════════════════════════════════════════════════════
        print(f"   🚫 DROPPED: Could not generate specific search term")
        return SearchTermResult(
            search_term=None,
            source="dropped",
            reasoning="Could not generate a specific enough search term from title, description, or image",
            raw_responses=raw_responses
        )


async def test_generator():
    """Test the search term generator"""
    generator = SearchTermGenerator()
    
    test_cases = [
        {
            "title": "1 oz American Silver Eagle 2024 BU in capsule",
            "description": "Selling my silver eagle, great condition",
            "image_url": None  # Test text-only path
        },
        {
            "title": "Gaming storage tower great deal Pittsburgh",
            "description": "Storage for games",
            "image_url": None
        },
        {
            "title": "Nintendo Switch OLED White Like New Must Sell",
            "description": "Barely used Switch, comes with dock and joycons",
            "image_url": None
        },
        {
            "title": "Silver coin for sale",  # Vague title - needs image
            "description": "Selling this coin",
            "image_url": "https://i.ebayimg.com/images/g/O~kAAOSwlMNnxIiN/s-l1600.webp"  # eBay image of silver eagle
        },
    ]
    
    for i, case in enumerate(test_cases, 1):
        print(f"\n{'='*60}")
        print(f"TEST CASE {i}")
        print(f"{'='*60}")
        
        result = await generator.generate_search_term(
            title=case["title"],
            description=case.get("description"),
            image_url=case.get("image_url")
        )
        
        print(f"\n📦 RESULT:")
        print(f"   Search Term: {result.search_term or 'DROPPED'}")
        print(f"   Source: {result.source}")
        print(f"   Reasoning: {result.reasoning}")
    
    await generator.close()


if __name__ == "__main__":
    asyncio.run(test_generator())
