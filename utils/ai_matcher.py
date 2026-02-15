"""
AI-Powered Item Matcher

Holistic comparison: synthesizes what each listing IS from title + description + image,
then determines the probability they're the same or basically the same item.

Match if more likely than not (>50%).
"""
import asyncio
import base64
import os
import re
from dataclasses import dataclass
from typing import Optional

import httpx


@dataclass
class MatchResult:
    """Result of AI matching between two listings"""
    is_match: bool
    confidence: float  # 0.0 to 1.0
    fb_synthesis: str  # What the FB listing is
    ebay_synthesis: str  # What the eBay listing is
    reasoning: str
    
    def __str__(self):
        status = "✅ MATCH" if self.is_match else "❌ NO MATCH"
        return f"{status} ({self.confidence:.0%}) - {self.reasoning[:80]}"


class AIItemMatcher:
    """
    Matches items using holistic AI synthesis.
    
    Process:
    1. Synthesize what the FB listing IS (from title + description + image)
    2. Synthesize what the eBay listing IS
    3. Determine probability they're the same or basically the same item
    4. Match if >50% probability
    """
    
    def __init__(
        self,
        gemini_api_key: str = None,
        gemini_model: str = "gemini-2.0-flash",
        match_threshold: float = 0.5  # Match if >50% likely
    ):
        self.gemini_api_key = gemini_api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        self.gemini_model = gemini_model
        self.match_threshold = match_threshold
        self._client = None
    
    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=60.0)
        return self._client
    
    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None
    
    async def _download_image(self, url: str) -> Optional[bytes]:
        """Download image from URL"""
        if not url:
            return None
        try:
            client = await self._get_client()
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
            response = await client.get(url, follow_redirects=True, headers=headers)
            if response.status_code == 200:
                return response.content
        except Exception:
            pass
        return None
    
    def _detect_mime_type(self, image_data: bytes) -> str:
        """Detect image MIME type from bytes"""
        if image_data[:4] == b'\x89PNG':
            return "image/png"
        elif image_data[:4] == b'RIFF' and image_data[8:12] == b'WEBP':
            return "image/webp"
        elif image_data[:3] == b'GIF':
            return "image/gif"
        return "image/jpeg"
    
    async def _call_gemini(
        self,
        prompt: str,
        images: list[tuple[bytes, str]] = None  # List of (image_data, mime_type)
    ) -> Optional[str]:
        """Call Gemini API with text and optional images"""
        if not self.gemini_api_key:
            return None
        
        try:
            client = await self._get_client()
            
            # Build content parts
            parts = [{"text": prompt}]
            
            if images:
                for img_data, mime_type in images:
                    img_b64 = base64.b64encode(img_data).decode('utf-8')
                    parts.append({
                        "inline_data": {"mime_type": mime_type, "data": img_b64}
                    })
            
            response = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{self.gemini_model}:generateContent?key={self.gemini_api_key}",
                headers={"Content-Type": "application/json"},
                json={"contents": [{"parts": parts}]},
                timeout=45.0
            )
            
            if response.status_code != 200:
                return None
            
            result = response.json()
            try:
                return result["candidates"][0]["content"]["parts"][0]["text"]
            except (KeyError, IndexError):
                return None
                
        except Exception:
            return None
    
    async def compare_listings(
        self,
        fb_title: str,
        fb_description: str,
        fb_image_url: Optional[str],
        ebay_title: str,
        ebay_description: str = "",
        ebay_image_url: Optional[str] = None,
        ebay_price: float = None
    ) -> MatchResult:
        """
        Compare a Facebook listing to an eBay listing using holistic synthesis.
        
        Process:
        1. Feed the LLM everything we know about both listings
        2. Ask it to synthesize what each item IS
        3. Determine probability they're the same item
        4. Match if >50%
        """
        # Download images if available
        fb_image = await self._download_image(fb_image_url) if fb_image_url else None
        ebay_image = await self._download_image(ebay_image_url) if ebay_image_url else None
        
        # Build the comparison prompt
        prompt = f"""You are an expert at identifying products and determining if two listings are for the same item.

=== FACEBOOK MARKETPLACE LISTING ===
Title: {fb_title}
Description: {fb_description or "(no description)"}
{"[Image attached below]" if fb_image else "(no image)"}

=== EBAY SOLD LISTING ===
Title: {ebay_title}
Description: {ebay_description or "(no description)"}
{f"Sold Price: ${ebay_price:.2f}" if ebay_price else ""}
{"[Image attached below]" if ebay_image else "(no image)"}

Based on ALL available information (titles, descriptions, and images), determine:

1. SYNTHESIZE: What specific product is the Facebook listing selling? (brand, model, variant, condition)
2. SYNTHESIZE: What specific product is the eBay listing showing? (brand, model, variant, condition)
3. MATCH PROBABILITY: What is the probability (0-100%) that these are the SAME or BASICALLY THE SAME item?
   - "Same" means: same brand, same model/product line, same general type
   - Minor differences in color, condition, or accessories are OK
   - Different models/generations are NOT the same (e.g., iPhone 14 vs iPhone 15)

Respond in this EXACT format:
FB_ITEM: [what the Facebook listing is selling]
EBAY_ITEM: [what the eBay listing is showing]
PROBABILITY: [0-100]
REASONING: [one sentence explaining your judgment]"""

        # Prepare images for API call
        images = []
        if fb_image:
            images.append((fb_image, self._detect_mime_type(fb_image)))
        if ebay_image:
            images.append((ebay_image, self._detect_mime_type(ebay_image)))
        
        # Call Gemini
        response = await self._call_gemini(prompt, images if images else None)
        
        if not response:
            # Fallback: no AI available, use simple heuristic
            return self._fallback_comparison(fb_title, ebay_title)
        
        # Parse response
        fb_synthesis = ""
        ebay_synthesis = ""
        probability = 0
        reasoning = ""
        
        for line in response.split('\n'):
            line = line.strip()
            if line.startswith('FB_ITEM:'):
                fb_synthesis = line.split(':', 1)[1].strip()
            elif line.startswith('EBAY_ITEM:'):
                ebay_synthesis = line.split(':', 1)[1].strip()
            elif line.startswith('PROBABILITY:'):
                try:
                    prob_str = line.split(':', 1)[1].strip()
                    # Extract number from string like "85" or "85%"
                    prob_match = re.search(r'(\d+)', prob_str)
                    if prob_match:
                        probability = int(prob_match.group(1))
                except:
                    pass
            elif line.startswith('REASONING:'):
                reasoning = line.split(':', 1)[1].strip()
        
        confidence = probability / 100.0
        is_match = confidence > self.match_threshold
        
        return MatchResult(
            is_match=is_match,
            confidence=confidence,
            fb_synthesis=fb_synthesis,
            ebay_synthesis=ebay_synthesis,
            reasoning=reasoning or f"Match probability: {probability}%"
        )
    
    def _fallback_comparison(self, fb_title: str, ebay_title: str) -> MatchResult:
        """Simple fallback when AI is unavailable"""
        # Basic word overlap check
        fb_words = set(fb_title.lower().split())
        ebay_words = set(ebay_title.lower().split())
        
        # Remove common words
        stopwords = {'the', 'a', 'an', 'and', 'or', 'for', 'with', 'in', 'on', '-', '–', '|'}
        fb_words -= stopwords
        ebay_words -= stopwords
        
        if not fb_words or not ebay_words:
            return MatchResult(
                is_match=False,
                confidence=0.0,
                fb_synthesis=fb_title,
                ebay_synthesis=ebay_title,
                reasoning="Cannot compare: insufficient data"
            )
        
        overlap = len(fb_words & ebay_words)
        total = len(fb_words | ebay_words)
        confidence = overlap / total if total > 0 else 0
        
        return MatchResult(
            is_match=confidence > self.match_threshold,
            confidence=confidence,
            fb_synthesis=fb_title,
            ebay_synthesis=ebay_title,
            reasoning=f"Word overlap: {overlap}/{total} ({confidence:.0%})"
        )
    
    async def find_best_match(
        self,
        fb_listing: dict,
        ebay_results: list[dict],
        max_candidates: int = 5
    ) -> Optional[tuple[dict, MatchResult]]:
        """
        Find the best matching eBay result for a FB listing.
        
        Args:
            fb_listing: Dict with 'title', 'description', 'image_url'
            ebay_results: List of dicts with same fields + 'price'
            max_candidates: Max number to compare (for rate limiting)
            
        Returns:
            Tuple of (best_ebay_match, match_result) or None
        """
        fb_title = fb_listing.get('title', '')
        fb_desc = fb_listing.get('description', '')
        fb_image = fb_listing.get('image_url')
        
        # Quick pre-filter: basic word overlap to find likely candidates
        candidates = []
        for ebay in ebay_results:
            ebay_title = ebay.get('title', '')
            
            # Simple relevance score based on shared words
            fb_words = set(fb_title.lower().split())
            ebay_words = set(ebay_title.lower().split())
            overlap = len(fb_words & ebay_words)
            
            candidates.append((ebay, overlap))
        
        # Sort by overlap, take top N
        candidates.sort(key=lambda x: x[1], reverse=True)
        top_candidates = [c[0] for c in candidates[:max_candidates]]
        
        # Full AI comparison on top candidates
        best_match = None
        best_result = None
        
        for ebay in top_candidates:
            result = await self.compare_listings(
                fb_title=fb_title,
                fb_description=fb_desc,
                fb_image_url=fb_image,
                ebay_title=ebay.get('title', ''),
                ebay_description=ebay.get('description', ''),
                ebay_image_url=ebay.get('image_url'),
                ebay_price=ebay.get('price')
            )
            
            if result.is_match:
                if best_result is None or result.confidence > best_result.confidence:
                    best_match = ebay
                    best_result = result
        
        if best_match:
            return (best_match, best_result)
        return None


async def test_matcher():
    """Test the AI matcher"""
    matcher = AIItemMatcher()
    
    # Test with sample listings
    print("🔍 Testing holistic matcher...")
    
    result = await matcher.compare_listings(
        fb_title="Nintendo Switch OLED White - Like New",
        fb_description="Barely used, comes with dock and joycons",
        fb_image_url=None,
        ebay_title="Nintendo Switch OLED Model White Joy-Con Console",
        ebay_description="Used, tested working",
        ebay_image_url=None,
        ebay_price=299.99
    )
    
    print(f"\nResult: {result}")
    print(f"  FB item: {result.fb_synthesis}")
    print(f"  eBay item: {result.ebay_synthesis}")
    print(f"  Confidence: {result.confidence:.0%}")
    print(f"  Reasoning: {result.reasoning}")
    
    await matcher.close()


if __name__ == "__main__":
    asyncio.run(test_matcher())
