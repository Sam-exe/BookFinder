"""
Gemini AI Book Detection Module
Uses Google Gemini to detect books from bookshelf images
"""

from google import genai
from google.genai import types
import json
import re
import os
from typing import List, Dict, Optional
from pathlib import Path
import time
import mimetypes

class GeminiBookDetector:
    """Uses Google Gemini to detect books from images"""

    DETECTION_PROMPT = """You are analyzing a bookshelf photograph to identify visible books.

CRITICAL RULES:
1. ONLY return books you can ACTUALLY SEE in the image.
2. DO NOT hallucinate or invent book titles.
3. If text is unclear or partially visible, mark lower confidence.
4. If you cannot read a spine clearly, skip it entirely.
5. Return ONLY what is genuinely visible.

SHELF NUMBERING: Count shelves from top to bottom starting at 1.
POSITION NUMBERING: Count books on each shelf from left to right starting at 1.

For each clearly visible book spine, extract:
- title: The exact title as written (keep original language/capitalisation)
- author: Author name if visible, or null if not readable
- confidence: 0.0-1.0 based on text clarity
  * 0.9-1.0: Text is crystal clear
  * 0.7-0.9: Readable but some uncertainty
  * 0.5-0.7: Partially obscured or unclear
  * Below 0.5: Skip the book
- shelf: Which shelf number the book is on (integer, counting top-to-bottom)
- position: Position from the left on that shelf (integer)

Return a JSON array. Every element must follow this exact structure:
[
  {
    "title": "Exact Title As Written",
    "author": "Author Name or null",
    "confidence": 0.95,
    "shelf": 1,
    "position": 3
  }
]

Be conservative. Quality over quantity. Only include books you can genuinely read."""

    def __init__(self, api_key: Optional[str] = None, model_name: str = "gemini-2.5-flash"):
        """
        Initialize Gemini detector
        
        Args:
            api_key: Google AI API key (uses GEMINI_API_KEY env var if not provided)
            model_name: Gemini model to use (gemini-1.5-flash-8b, gemini-1.5-pro, gemini-1.5-flash, etc.)
        """
        self.api_key = api_key or os.getenv('GEMINI_API_KEY')
        if not self.api_key:
            raise ValueError("Gemini API key required. Set GEMINI_API_KEY env var or pass api_key parameter")
        
        self.client = genai.Client(api_key=self.api_key)
        self.model_name = model_name
        
    def detect_books_from_image(self, image_path: str) -> List[Dict]:
        """
        Detect books from an image using Gemini.
        Returns list of dicts with title, author, confidence, shelf, position.
        """
        print(f"Loading image: {image_path}")

        with open(image_path, 'rb') as f:
            image_bytes = f.read()

        mime_type, _ = mimetypes.guess_type(image_path)
        if not mime_type or not mime_type.startswith('image/'):
            mime_type = 'image/jpeg'

        print(f"Sending request to Gemini ({self.model_name})...")

        # Try up to 2 times — second attempt uses response_mime_type to force JSON
        for attempt in range(2):
            try:
                config_kwargs = dict(
                    temperature=0.1,
                    top_p=0.8,
                    max_output_tokens=16384,
                )
                if attempt == 1:
                    config_kwargs['response_mime_type'] = 'application/json'

                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=[
                        types.Part.from_text(text=self.DETECTION_PROMPT),
                        types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
                    ],
                    config=types.GenerateContentConfig(**config_kwargs)
                )

                books = self._parse_response(response.text)
                print(f"Successfully detected {len(books)} books")
                return books

            except (ValueError, json.JSONDecodeError) as e:
                if attempt == 0:
                    print(f"Attempt 1 failed ({e}), retrying with forced JSON mode...")
                    time.sleep(1)
                else:
                    print(f"Both attempts failed: {e}")
                    return []
            except Exception as e:
                print(f"Gemini API error: {e}")
                raise

        return []

    def _parse_response(self, text: str) -> List[Dict]:
        """Extract and validate book JSON from Gemini response text."""
        text = text.strip()

        # Strip markdown code fences
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
        text = text.strip()

        # Try direct parse first
        try:
            data = json.loads(text)
            return self._validate_books(data)
        except json.JSONDecodeError:
            pass

        # Find the outermost JSON array
        match = re.search(r'(\[.*\])', text, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(1))
                return self._validate_books(data)
            except json.JSONDecodeError:
                pass

        # Last resort: extract individual {...} objects
        objects = re.findall(r'\{[^{}]+\}', text, re.DOTALL)
        books = []
        for obj in objects:
            try:
                books.append(json.loads(obj))
            except json.JSONDecodeError:
                continue
        if books:
            return self._validate_books(books)

        raise ValueError(f"Could not extract valid JSON from response: {text[:300]}")

    def _validate_books(self, data) -> List[Dict]:
        """Ensure each book dict has required fields and sensible values."""
        if not isinstance(data, list):
            raise ValueError("Expected a JSON array")
        result = []
        for item in data:
            if not isinstance(item, dict):
                continue
            title = item.get('title', '').strip()
            if not title:
                continue
            confidence = float(item.get('confidence', 0.8))
            if confidence < 0.5:
                continue
            result.append({
                'title': title,
                'author': item.get('author') or None,
                'confidence': confidence,
                'shelf': int(item.get('shelf', 1)),
                'position': int(item.get('position', 0)),
            })
        return result
    
    def save_results(self, books: List[Dict], output_path: str):
        """Save detected books to JSON file"""
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(books, f, indent=2, ensure_ascii=False)
        print(f"Saved {len(books)} books to {output_path}")


def main():
    """Test the Gemini book detector"""
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python gemini_book_detector.py <image_path> [output_json]")
        print("Environment variable required: GEMINI_API_KEY")
        sys.exit(1)
    
    image_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else "gemini_detected_books.json"
    
    # Check if image exists
    if not os.path.exists(image_path):
        print(f"Error: Image not found: {image_path}")
        sys.exit(1)
    
    try:
        # Initialize detector
        detector = GeminiBookDetector()
        
        # Detect books
        books = detector.detect_books_from_image(image_path)
        
        # Display results
        print(f"\n{'='*60}")
        print(f"DETECTED BOOKS ({len(books)} total)")
        print(f"{'='*60}\n")
        
        for i, book in enumerate(books, 1):
            title = book.get('title', 'Unknown')
            author = book.get('author', 'N/A')
            confidence = book.get('confidence', 0)
            print(f"{i:2d}. {title}")
            print(f"    Author: {author}")
            print(f"    Confidence: {confidence:.2f}")
            print()
        
        # Save results
        detector.save_results(books, output_path)
        
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
