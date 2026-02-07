"""
AI-Powered Item Matcher

Uses local AI (Ollama with Moondream) to verify if FB listings match eBay sold items.
Combines:
- Title/text similarity
- Image comparison via vision model
- Description analysis
"""
import asyncio
import base64
import json
import os
import re
import tempfile
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx


@dataclass
class MatchResult:
    """Result of AI matching between two listings"""
    is_match: bool
    confidence: float  # 0.0 to 1.0
    title_similarity: float
    image_match: bool
    image_confidence: float
    reasoning: str
    
    def __str__(self):
        status = "✅ MATCH" if self.is_match else "❌ NO MATCH"
        return f"{status} ({self.confidence:.0%}) - {self.reasoning[:80]}"


class AIItemMatcher:
    """
    Matches items using AI vision model.
    
    Uses Google Gemini for image comparison.
    Falls back to text-only matching if images unavailable.
    """
    
    def __init__(
        self,
        gemini_api_key: str = None,
        gemini_model: str = "gemini-2.0-flash",
        min_title_similarity: float = 0.3,  # Lowered - FB/eBay titles often differ
        min_overall_confidence: float = 0.5  # Lowered for better recall
    ):
        self.gemini_api_key = gemini_api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        self.gemini_model = gemini_model
        self.min_title_similarity = min_title_similarity
        self.min_overall_confidence = min_overall_confidence
        self._client = None
    
    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=60.0)
        return self._client
    
    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None
    
    def _text_similarity(self, text1: str, text2: str) -> float:
        """Calculate text similarity using SequenceMatcher"""
        if not text1 or not text2:
            return 0.0
        
        # Normalize texts
        t1 = self._normalize_text(text1)
        t2 = self._normalize_text(text2)
        
        return SequenceMatcher(None, t1, t2).ratio()
    
    def _normalize_text(self, text: str) -> str:
        """Normalize text for comparison"""
        text = text.lower()
        # Remove common filler words
        stopwords = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by'}
        words = text.split()
        words = [w for w in words if w not in stopwords]
        # Remove punctuation
        text = ' '.join(words)
        text = re.sub(r'[^\w\s]', '', text)
        # Remove extra whitespace
        text = ' '.join(text.split())
        return text
    
    def _extract_key_features(self, title: str) -> set:
        """Extract key product features from title"""
        title = title.lower()
        
        features = set()
        
        # Extract brand names (common patterns)
        brand_patterns = [
            r'\b(nintendo|sony|microsoft|apple|samsung|lg|dell|hp|lenovo|asus|acer)\b',
            r'\b(xbox|playstation|ps[1-5]|wii|switch)\b',
            r'\b(iphone|ipad|macbook|airpods|galaxy)\b',
        ]
        for pattern in brand_patterns:
            matches = re.findall(pattern, title)
            features.update(matches)
        
        # Extract model numbers
        model_patterns = [
            r'\b([a-z]+\s*\d{3,}[a-z]*)\b',  # e.g., "RTX 3080"
            r'\b(\d+\s*gb|\d+\s*tb)\b',  # Storage sizes
            r'\b(\d+\s*inch|\d+")\b',  # Screen sizes
        ]
        for pattern in model_patterns:
            matches = re.findall(pattern, title)
            features.update(matches)
        
        # Extract colors
        colors = ['black', 'white', 'silver', 'gold', 'blue', 'red', 'green', 'gray', 'grey']
        for color in colors:
            if color in title:
                features.add(color)
        
        # Extract conditions
        conditions = ['new', 'used', 'refurbished', 'sealed', 'open box', 'mint']
        for cond in conditions:
            if cond in title:
                features.add(cond)
        
        return features
    
    def _feature_overlap(self, title1: str, title2: str) -> float:
        """Calculate feature overlap between titles"""
        f1 = self._extract_key_features(title1)
        f2 = self._extract_key_features(title2)
        
        if not f1 or not f2:
            return 0.5  # Neutral if can't extract features
        
        intersection = f1 & f2
        union = f1 | f2
        
        if not union:
            return 0.5
        
        return len(intersection) / len(union)
    
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
        except Exception as e:
            pass  # Silently fail - image comparison is optional
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
    
    async def _compare_images_gemini(
        self,
        image1_data: bytes,
        image2_data: bytes,
        title1: str,
        title2: str
    ) -> tuple[bool, float, str]:
        """
        Compare two images using Google Gemini vision.
        
        Returns: (is_match, confidence, reasoning)
        """
        if not self.gemini_api_key:
            return (False, 0.0, "No Gemini API key")
        
        try:
            client = await self._get_client()
            
            # Encode images
            img1_b64 = base64.b64encode(image1_data).decode('utf-8')
            img2_b64 = base64.b64encode(image2_data).decode('utf-8')
            mime1 = self._detect_mime_type(image1_data)
            mime2 = self._detect_mime_type(image2_data)
            
            prompt = f"""Compare these two product images.

Image 1 is from Facebook Marketplace: "{title1}"
Image 2 is from eBay sold listings: "{title2}"

Are these the SAME or EQUIVALENT product? (same type, brand, model - minor color/condition differences OK)

Respond ONLY with:
MATCH: YES or NO
CONFIDENCE: 0-100
REASON: one sentence"""

            response = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{self.gemini_model}:generateContent?key={self.gemini_api_key}",
                headers={"Content-Type": "application/json"},
                json={
                    "contents": [{
                        "parts": [
                            {"text": prompt},
                            {"inline_data": {"mime_type": mime1, "data": img1_b64}},
                            {"inline_data": {"mime_type": mime2, "data": img2_b64}}
                        ]
                    }]
                },
                timeout=30.0
            )
            
            if response.status_code != 200:
                return (False, 0.0, f"Gemini API error: {response.status_code}")
            
            result = response.json()
            
            try:
                text = result["candidates"][0]["content"]["parts"][0]["text"]
            except (KeyError, IndexError):
                return (False, 0.0, "Failed to parse Gemini response")
            
            # Parse response
            text_upper = text.upper()
            is_match = "MATCH: YES" in text_upper or "MATCH:YES" in text_upper
            
            # Extract confidence
            conf_match = re.search(r'CONFIDENCE[:\s]*(\d+)', text, re.IGNORECASE)
            confidence = int(conf_match.group(1)) / 100 if conf_match else (0.7 if is_match else 0.3)
            
            # Extract reason
            reason_match = re.search(r'REASON[:\s]*(.+?)(?:\n|$)', text, re.IGNORECASE)
            reason = reason_match.group(1).strip() if reason_match else "No reason given"
            
            return (is_match, confidence, reason)
            
        except Exception as e:
            return (False, 0.0, f"Gemini vision failed: {e}")
    
    async def compare_listings(
        self,
        fb_title: str,
        fb_description: str,
        fb_image_url: Optional[str],
        ebay_title: str,
        ebay_description: str = "",
        ebay_image_url: Optional[str] = None
    ) -> MatchResult:
        """
        Compare a Facebook listing to an eBay listing.
        
        Uses both text similarity and image comparison.
        """
        # Calculate title similarity
        title_sim = self._text_similarity(fb_title, ebay_title)
        feature_sim = self._feature_overlap(fb_title, ebay_title)
        
        # Combined title score (weight features higher)
        combined_title_sim = (title_sim * 0.4) + (feature_sim * 0.6)
        
        # Quick reject if titles are too dissimilar
        if combined_title_sim < self.min_title_similarity:
            return MatchResult(
                is_match=False,
                confidence=combined_title_sim,
                title_similarity=combined_title_sim,
                image_match=False,
                image_confidence=0.0,
                reasoning=f"Titles too dissimilar ({combined_title_sim:.0%})"
            )
        
        # Try image comparison if both images available
        image_match = False
        image_confidence = 0.0
        image_reason = "No images to compare"
        
        if fb_image_url and ebay_image_url:
            print(f"      🖼️ Comparing images...")
            
            # Download both images
            fb_img = await self._download_image(fb_image_url)
            ebay_img = await self._download_image(ebay_image_url)
            
            if fb_img and ebay_img:
                image_match, image_confidence, image_reason = await self._compare_images_gemini(
                    fb_img, ebay_img, fb_title, ebay_title
                )
        
        # Calculate overall confidence
        if fb_image_url and ebay_image_url and image_confidence > 0:
            # Weight image comparison heavily when available
            overall_confidence = (combined_title_sim * 0.3) + (image_confidence * 0.7)
            is_match = image_match and combined_title_sim >= self.min_title_similarity
        else:
            # Text-only matching
            overall_confidence = combined_title_sim
            is_match = combined_title_sim >= 0.6
        
        # Final decision
        is_match = is_match and overall_confidence >= self.min_overall_confidence
        
        # Build reasoning
        if is_match:
            reasoning = f"Title: {combined_title_sim:.0%} similar"
            if image_confidence > 0:
                reasoning += f", Image: {image_confidence:.0%} match - {image_reason}"
        else:
            if image_confidence > 0:
                reasoning = f"Image mismatch: {image_reason}"
            else:
                reasoning = f"Low title similarity ({combined_title_sim:.0%})"
        
        return MatchResult(
            is_match=is_match,
            confidence=overall_confidence,
            title_similarity=combined_title_sim,
            image_match=image_match,
            image_confidence=image_confidence,
            reasoning=reasoning
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
            max_candidates: Max number to run vision comparison on
            
        Returns:
            Tuple of (best_ebay_match, match_result) or None
        """
        fb_title = fb_listing.get('title', '')
        fb_desc = fb_listing.get('description', '')
        fb_image = fb_listing.get('image_url')
        
        # First pass: rank by title similarity
        candidates = []
        for ebay in ebay_results:
            title_sim = self._text_similarity(fb_title, ebay.get('title', ''))
            feature_sim = self._feature_overlap(fb_title, ebay.get('title', ''))
            score = (title_sim * 0.4) + (feature_sim * 0.6)
            candidates.append((ebay, score))
        
        # Sort by score, take top N
        candidates.sort(key=lambda x: x[1], reverse=True)
        top_candidates = candidates[:max_candidates]
        
        # Second pass: full comparison with images
        best_match = None
        best_result = None
        
        for ebay, _ in top_candidates:
            result = await self.compare_listings(
                fb_title=fb_title,
                fb_description=fb_desc,
                fb_image_url=fb_image,
                ebay_title=ebay.get('title', ''),
                ebay_description=ebay.get('description', ''),
                ebay_image_url=ebay.get('image_url')
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
    
    # Test with sample titles
    fb_title = "Nintendo Switch OLED White - Like New"
    ebay_title = "Nintendo Switch OLED Model White Joy-Con Console"
    
    print(f"🔍 Comparing:")
    print(f"   FB: {fb_title}")
    print(f"   eBay: {ebay_title}")
    
    result = await matcher.compare_listings(
        fb_title=fb_title,
        fb_description="",
        fb_image_url=None,
        ebay_title=ebay_title
    )
    
    print(f"\n{result}")
    await matcher.close()


if __name__ == "__main__":
    asyncio.run(test_matcher())
