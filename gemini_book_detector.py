"""
Gemini AI Book Detection Module
Uses Google Gemini to detect books from bookshelf images
"""

from google import genai
from google.genai import types
import json
import os
from typing import List, Dict, Optional
from pathlib import Path
import time
import mimetypes

class GeminiBookDetector:
    """Uses Google Gemini to detect books from images"""
    
    # Anti-hallucination prompt
    DETECTION_PROMPT = """You are analyzing a bookshelf photograph to identify visible books.

CRITICAL RULES:
1. ONLY return books you can ACTUALLY SEE in the image
2. DO NOT hallucinate or invent book titles
3. If text is unclear or partially visible, mark lower confidence
4. If you cannot read a spine clearly, skip it
5. Return ONLY what is genuinely visible

For each clearly visible book spine, extract:
- title: The exact title as written (keep original language/capitalization)
- author: Author name if visible, or null if not readable
- confidence: 0.0-1.0 based on text clarity
  * 0.9-1.0: Text is crystal clear
  * 0.7-0.9: Text is readable but some uncertainty
  * 0.5-0.7: Text is partially obscured or unclear
  * Below 0.5: Don't include the book

Return as JSON array with this exact structure:
[
  {
    "title": "Exact Title As Written",
    "author": "Author Name" or null,
    "confidence": 0.95
  }
]

Be conservative. Quality over quantity. Only return books you can genuinely read."""

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
        Detect books from an image using Gemini
        
        Args:
            image_path: Path to the bookshelf image
            
        Returns:
            List of detected books with title, author, confidence
        """
        print(f"Loading image: {image_path}")
        
        # Read image file
        with open(image_path, 'rb') as f:
            image_bytes = f.read()
        
        # Determine mime type
        mime_type, _ = mimetypes.guess_type(image_path)
        if not mime_type or not mime_type.startswith('image/'):
            mime_type = 'image/jpeg'
        
        print(f"Sending request to Gemini ({self.model_name})...")
        
        try:
            # Create content with text prompt and image
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=[
                    types.Part.from_text(text=self.DETECTION_PROMPT),
                    types.Part.from_bytes(
                        data=image_bytes,
                        mime_type=mime_type
                    )
                ],
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    top_p=0.8,
                    max_output_tokens=8192,
                )
            )
            
            # Extract JSON from response
            response_text = response.text.strip()
            
            # Remove markdown code blocks if present
            if response_text.startswith('```json'):
                response_text = response_text[7:]
            if response_text.startswith('```'):
                response_text = response_text[3:]
            if response_text.endswith('```'):
                response_text = response_text[:-3]
            response_text = response_text.strip()
            
            # Parse JSON
            try:
                books = json.loads(response_text)
                print(f"✓ Successfully detected {len(books)} books")
                return books
            except json.JSONDecodeError as e:
                print(f"Failed to parse JSON response: {e}")
                print(f"Response text: {response_text[:500]}")
                raise ValueError(f"Invalid JSON response from Gemini: {e}")
                
        except Exception as e:
            print(f"Error calling Gemini API: {e}")
            raise
    
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
