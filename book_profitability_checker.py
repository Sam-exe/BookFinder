"""  
Book Profitability Checker
Checks if books are profitable to buy and resell using boekenbalie.nl API
"""

import requests
import json
import csv
import uuid
from typing import Optional, Dict, List
from datetime import datetime
import time
from bs4 import BeautifulSoup
from xml.etree import ElementTree as ET
import urllib.parse

from boekenbalie_api import BoekenbalieAPI


class BookISBNLookup:
    """Client for finding ISBNs using public APIs"""
    
    GOOGLE_BOOKS_API = "https://www.googleapis.com/books/v1/volumes"
    
    def __init__(self, rate_limit_delay: float = 0.5):
        """
        Initialize the ISBN lookup client
        
        Args:
            rate_limit_delay: Delay between API requests in seconds
        """
        self.rate_limit_delay = rate_limit_delay
        self.last_request_time = 0
        self.session = requests.Session()
    
    def _wait_for_rate_limit(self):
        """Enforce rate limiting before making a request"""
        current_time = time.time()
        time_since_last = current_time - self.last_request_time
        
        if time_since_last < self.rate_limit_delay:
            wait_time = self.rate_limit_delay - time_since_last
            time.sleep(wait_time)
        
        self.last_request_time = time.time()
    
    def find_isbn(self, title: str, author: str = "") -> Optional[Dict]:
        """
        Find ISBN for a book using Google Books API
        
        Args:
            title: Book title
            author: Book author (optional but improves accuracy)
            
        Returns:
            Dictionary with ISBN and book info, or None if not found
        """
        # Build search query
        query_parts = []
        if title:
            query_parts.append(f'intitle:{title}')
        if author:
            query_parts.append(f'inauthor:{author}')
        
        if not query_parts:
            return None
        
        query = '+'.join(query_parts)
        
        params = {
            'q': query,
            'maxResults': 5,
            'printType': 'books',
            'langRestrict': 'nl'  # Prefer Dutch books
        }
        
        self._wait_for_rate_limit()
        
        try:
            response = self.session.get(self.GOOGLE_BOOKS_API, params=params, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                
                if data.get('totalItems', 0) == 0:
                    return None
                
                # Get the first result (most relevant)
                for item in data.get('items', []):
                    volume_info = item.get('volumeInfo', {})
                    
                    # Extract ISBNs
                    industry_identifiers = volume_info.get('industryIdentifiers', [])
                    isbn_13 = None
                    isbn_10 = None
                    
                    for identifier in industry_identifiers:
                        if identifier.get('type') == 'ISBN_13':
                            isbn_13 = identifier.get('identifier')
                        elif identifier.get('type') == 'ISBN_10':
                            isbn_10 = identifier.get('identifier')
                    
                    # Prefer ISBN-13, fallback to ISBN-10
                    isbn = isbn_13 or isbn_10
                    
                    if isbn:
                        return {
                            'isbn': isbn,
                            'isbn_13': isbn_13,
                            'isbn_10': isbn_10,
                            'title': volume_info.get('title', ''),
                            'authors': volume_info.get('authors', []),
                            'publisher': volume_info.get('publisher', ''),
                            'published_date': volume_info.get('publishedDate', ''),
                            'description': volume_info.get('description', ''),
                            'page_count': volume_info.get('pageCount', 0),
                            'language': volume_info.get('language', ''),
                            'thumbnail': volume_info.get('imageLinks', {}).get('thumbnail', ''),
                            'google_books_id': item.get('id', '')
                        }
                
                return None
                
            else:
                print(f"  ⚠️  Google Books API error {response.status_code}")
                return None
                
        except Exception as e:
            print(f"  ❌ Error searching Google Books: {e}")
            return None
    
    def find_all_isbns(self, title: str, author: str = "") -> List[Dict]:
        """
        Find all physical ISBN editions for a book via Google Books.
        Dutch editions are returned first (most likely to be bought by Boekenbalie).
        Ebooks, audiobooks and digital-only editions are excluded.
        Two passes: first Dutch-only, then all languages to catch extra editions.
        """
        query_parts = []
        if title:
            query_parts.append(f'intitle:{title}')
        if author:
            query_parts.append(f'inauthor:{author}')
        if not query_parts:
            return []

        query = '+'.join(query_parts)
        seen: set = set()
        editions: List[Dict] = []

        def _fetch(extra_params: dict) -> None:
            params = {
                'q': query,
                'maxResults': 10,
                'printType': 'books',
                **extra_params,
            }
            try:
                self._wait_for_rate_limit()
                resp = self.session.get(self.GOOGLE_BOOKS_API, params=params, timeout=10)
                if resp.status_code != 200:
                    return
                data = resp.json()
                for item in data.get('items', []):
                    vol = item.get('volumeInfo', {})
                    # Skip ebooks and audiobooks
                    if item.get('saleInfo', {}).get('isEbook', False):
                        continue
                    if vol.get('printType', 'BOOK') != 'BOOK':
                        continue
                    idents = vol.get('industryIdentifiers', [])
                    isbn_13 = next((x['identifier'] for x in idents if x.get('type') == 'ISBN_13'), None)
                    isbn_10 = next((x['identifier'] for x in idents if x.get('type') == 'ISBN_10'), None)
                    isbn = isbn_13 or isbn_10
                    if not isbn or isbn in seen:
                        continue
                    seen.add(isbn)
                    editions.append({
                        'isbn': isbn,
                        'isbn_13': isbn_13,
                        'isbn_10': isbn_10,
                        'title': vol.get('title', title),
                        'authors': vol.get('authors', []),
                        'publisher': vol.get('publisher', ''),
                        'published_date': vol.get('publishedDate', ''),
                        'language': vol.get('language', ''),
                    })
            except Exception as e:
                print(f"  Error searching Google Books editions: {e}")

        # Pass 1: Dutch editions first — these are what Boekenbalie typically buys
        _fetch({'langRestrict': 'nl'})
        # Pass 2: all other physical editions for a complete picture
        _fetch({})

        # Sort: Dutch editions first, then other languages
        dutch  = [e for e in editions if e.get('language') == 'nl']
        others = [e for e in editions if e.get('language') != 'nl']
        return dutch + others

    def lookup_books_from_json(self, json_file: str) -> List[Dict]:
        """
        Read books from JSON and find their ISBNs
        
        Args:
            json_file: Path to JSON file with book titles and authors
            
        Returns:
            List of books with ISBNs added
        """
        print(f"\n📖 Reading books from: {json_file}")
        
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                books = json.load(f)
            
            print(f"✅ Found {len(books)} books in file")
            
            results = []
            
            for i, book in enumerate(books, 1):
                title = book.get('title', '').strip()
                author = book.get('author', '').strip()
                confidence = book.get('confidence', 0)
                
                if not title:
                    print(f"\n[{i}/{len(books)}] ⚠️  Skipping entry without title")
                    continue
                
                print(f"\n[{i}/{len(books)}] 🔍 Looking up: '{title}' by {author or 'Unknown'}")
                
                isbn_info = self.find_isbn(title, author)
                
                if isbn_info:
                    print(f"  ✅ Found ISBN: {isbn_info['isbn']}")
                    print(f"     Match: {isbn_info['title']}")
                    if isbn_info.get('authors'):
                        print(f"     Authors: {', '.join(isbn_info['authors'])}")
                    
                    result = {
                        'original_title': title,
                        'original_author': author,
                        'confidence': confidence,
                        **isbn_info
                    }
                    results.append(result)
                else:
                    print(f"  ❌ ISBN not found")
                    results.append({
                        'original_title': title,
                        'original_author': author,
                        'confidence': confidence,
                        'isbn': None
                    })
            
            return results
            
        except FileNotFoundError:
            print(f"❌ File not found: {json_file}")
            return []
        except json.JSONDecodeError as e:
            print(f"❌ Error parsing JSON: {e}")
            return []
        except Exception as e:
            print(f"❌ Error reading file: {e}")
            return []


class BoekenkraamScraper:
    """Client for scraping books from boekenkraam.nl"""
    
    BASE_URL = "https://boekenkraam.nl"
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'Accept': 'application/json',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
    
    def search_products(self, page: int = 1, num_results: int = 12, min_price: float = 0, max_price: float = 55, sort: str = "bestSold") -> List[Dict]:
        """
        Search for products on boekenkraam.nl
        
        Args:
            page: Page number to fetch
            num_results: Number of results per page
            min_price: Minimum price filter
            max_price: Maximum price filter
            sort: Sort order (bestSold, price, etc.)
            
        Returns:
            List of book dictionaries with EAN, title, price, etc.
        """
        url = f"{self.BASE_URL}/search/search-product"
        
        params = {
            'params[queries][]': '',
            'params[price][]': [min_price, max_price],
            'params[sort]': sort,
            'params[numResultsPerPage]': num_results,
            'params[page]': page
        }
        
        try:
            response = self.session.get(url, params=params)
            
            if response.status_code == 200:
                data = response.json()
                
                # Parse Elasticsearch response
                hits = data.get('hits', {}).get('hits', [])
                books = []
                
                for hit in hits:
                    source = hit.get('_source', {})
                    
                    # Extract relevant data
                    book = {
                        'ean': source.get('ean', ''),
                        'title': source.get('title', ''),
                        'description': source.get('description', ''),
                        'authors': source.get('authors', ''),
                        'publisher': source.get('publisher', ''),
                        'language': source.get('language', ''),
                        'model': source.get('model', ''),
                        'in_stock': source.get('inStock', 0),
                        'state': source.get('stateDescription', ''),
                        'price': float(source.get('prices', {}).get('sell', {}).get('inclVat', 0)),
                        'retail_price': float(source.get('prices', {}).get('retail', {}).get('inclVat', 0)),
                        'url': source.get('urlFront', ''),
                        'cover_url': source.get('urlCover', '')
                    }
                    
                    books.append(book)
                
                return books
            else:
                print(f"⚠️  Error fetching from boekenkraam: {response.status_code}")
                return []
                
        except Exception as e:
            print(f"❌ Error scraping boekenkraam: {e}")
            return []
    
    def get_books_to_check(self, max_pages: int = 1, start_page: int = 1, **search_params) -> List[Dict]:
        """
        Get multiple pages of books from boekenkraam
        
        Args:
            max_pages: Maximum number of pages to fetch
            start_page: Starting page number (offset)
            **search_params: Additional search parameters
            
        Returns:
            List of all books from all pages
        """
        all_books = []
        end_page = start_page + max_pages - 1
        
        for page in range(start_page, end_page + 1):
            print(f"🔍 Fetching page {page} from boekenkraam.nl...")
            books = self.search_products(page=page, **search_params)
            all_books.extend(books)
            
            if books:
                print(f"   Found {len(books)} books on page {page}")
            
            # Be nice to the server
            if page < end_page:
                time.sleep(1)
        
        print(f"\n✅ Total books found: {len(all_books)}")
        return all_books


class BoekwinkeltjesScraper:
    """Scraper for boekwinkeltjes.nl books"""
    
    BASE_URL = "https://www.boekwinkeltjes.nl"
    
    def __init__(self, rate_limit_delay: float = 0.5):
        """
        Initialize the scraper
        
        Args:
            rate_limit_delay: Delay between requests in seconds (default: 0.5)
        """
        self.rate_limit_delay = rate_limit_delay
        self.last_request_time = 0
        self.total_requests = 0
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'nl,en-US;q=0.7,en;q=0.3',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
        })
        self.books = []
    
    def _wait_for_rate_limit(self):
        """Enforce rate limiting before making a request"""
        current_time = time.time()
        time_since_last = current_time - self.last_request_time
        
        if time_since_last < self.rate_limit_delay:
            wait_time = self.rate_limit_delay - time_since_last
            time.sleep(wait_time)
        
        self.last_request_time = time.time()
        self.total_requests += 1
    
    def search_books(self, query: str = "", prijsvan: float = 0.00, prijstot: float = 5.00, 
                     page: int = 1, **kwargs) -> List[str]:
        """
        Search for books on boekwinkeltjes.nl and extract book URLs from results
        
        Args:
            query: Search query (e.g., "Kunst")
            prijsvan: Minimum price
            prijstot: Maximum price
            page: Page number
            **kwargs: Additional search parameters (zip, dist, lang, tl, img, shfee, oud, t, sort, order)
            
        Returns:
            List of book URLs found on the page
        """
        # Build search URL
        params = {
            'q': query,
            'prijsvan': f"{prijsvan:.2f}",
            'prijstot': f"{prijstot:.2f}",
            'p': page,
            't': kwargs.get('t', 1),  # Book type
            'n': kwargs.get('n', 0),
            'zip': kwargs.get('zip', ''),
            'dist': kwargs.get('dist', 0),
            'lang': kwargs.get('lang', ''),
            'tl': kwargs.get('tl', ''),
            'img': kwargs.get('img', 0),
            'shfee': kwargs.get('shfee', 0),
            'oud': kwargs.get('oud', 0),
            'sort': kwargs.get('sort', 'titel'),
            'order': kwargs.get('order', 0),
        }
        
        url = f"{self.BASE_URL}/s/"
        
        try:
            self._wait_for_rate_limit()
            response = self.session.get(url, params=params, timeout=10)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Find all clickable rows with book links
                book_urls = []
                rows = soup.find_all('tr', class_='clickable-row')
                
                for row in rows:
                    data_href = row.get('data-href')
                    if data_href:
                        # Convert relative URL to absolute
                        full_url = f"{self.BASE_URL}{data_href}" if data_href.startswith('/') else data_href
                        book_urls.append(full_url)
                
                return book_urls
            else:
                print(f"  ⚠️  Error fetching search page {page}: {response.status_code}")
                return []
                
        except Exception as e:
            print(f"  ❌ Error searching page {page}: {e}")
            return []
        """
        Fetch and parse a sitemap XML to get all book URLs
        
        Args:
            sitemap_url: URL of the sitemap XML file
            
        Returns:
            List of book URLs from the sitemap
        """
        print(f"\n🗺️  Fetching sitemap: {sitemap_url}")
        
        try:
            self._wait_for_rate_limit()
            response = self.session.get(sitemap_url)
            
            if response.status_code == 200:
                # Parse XML
                root = ET.fromstring(response.content)
                
                # Extract URLs from sitemap (handle namespace)
                namespace = {'ns': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
                urls = []
                
                for url_elem in root.findall('ns:url', namespace):
                    loc_elem = url_elem.find('ns:loc', namespace)
                    if loc_elem is not None and loc_elem.text:
                        urls.append(loc_elem.text)
                
                print(f"✅ Found {len(urls)} book URLs in sitemap")
                return urls
            else:
                print(f"❌ Error fetching sitemap: {response.status_code}")
                return []
                
        except Exception as e:
            print(f"❌ Error parsing sitemap: {e}")
            return []
    
    def extract_book_data(self, html_content: str, url: str) -> Optional[Dict]:
        """
        Extract book data from HTML page by parsing JSON-LD structured data
        
        Args:
            html_content: HTML content of the book page
            url: URL of the book page
            
        Returns:
            Dictionary with book information or None if not found
        """
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Find the JSON-LD script tag
            json_ld_script = soup.find('script', type='application/ld+json')
            
            if not json_ld_script:
                print(f"  ⚠️  No JSON-LD data found: {url}")
                return None
            
            # Parse JSON data
            json_data = json.loads(json_ld_script.string)
            
            # Extract relevant information
            book_data = {
                'url': url,
                'title': json_data.get('name', ''),
                'author': json_data.get('author', {}).get('name', '') if isinstance(json_data.get('author'), dict) else '',
                'isbn': json_data.get('isbn', ''),
                'publisher': json_data.get('publisher', {}).get('name', '') if isinstance(json_data.get('publisher'), dict) else '',
                'language': json_data.get('inLanguage', ''),
                'description': json_data.get('description', ''),
                'image_url': json_data.get('image', [''])[0] if isinstance(json_data.get('image'), list) else json_data.get('image', ''),
                'price': float(json_data.get('offers', {}).get('price', 0)) if json_data.get('offers') else 0,
                'currency': json_data.get('offers', {}).get('priceCurrency', 'EUR') if json_data.get('offers') else 'EUR',
                'availability': json_data.get('offers', {}).get('availability', {}).get('@id', '').replace('https://schema.org/', '') if json_data.get('offers') else '',
                'condition': json_data.get('offers', {}).get('itemCondition', {}).get('@id', '').replace('https://schema.org/', '') if json_data.get('offers') else '',
            }
            
            # Try to extract seller information from the page
            seller_elem = soup.find('a', class_='seller-link') or soup.find('div', class_='seller-name')
            if seller_elem:
                book_data['seller'] = seller_elem.get_text(strip=True)
            else:
                book_data['seller'] = ''
            
            return book_data
            
        except json.JSONDecodeError as e:
            print(f"  ❌ JSON parsing error for {url}: {e}")
            return None
        except Exception as e:
            print(f"  ❌ Error extracting data from {url}: {e}")
            return None
    
    def scrape_book_page(self, url: str) -> Optional[Dict]:
        """
        Scrape a single book page
        
        Args:
            url: URL of the book page
            
        Returns:
            Dictionary with book information
        """
        try:
            self._wait_for_rate_limit()
            response = self.session.get(url, timeout=10)
            
            if response.status_code == 200:
                return self.extract_book_data(response.text, url)
            else:
                print(f"  ⚠️  HTTP {response.status_code} for: {url}")
                return None
                
        except requests.exceptions.Timeout:
            print(f"  ⏱️  Timeout scraping: {url}")
            return None
        except Exception as e:
            print(f"  ❌ Error scraping {url}: {e}")
            return None
    
    def scrape_search_results(self, query: str = "", prijsvan: float = 0.00, prijstot: float = 5.00,
                              max_pages: int = 1, start_page: int = 1, **search_params) -> List[Dict]:
        """
        Scrape books from search results across multiple pages
        
        Args:
            query: Search query (e.g., "Kunst")
            prijsvan: Minimum price
            prijstot: Maximum price
            max_pages: Number of pages to scrape
            start_page: Starting page number
            **search_params: Additional search parameters
            
        Returns:
            List of book dictionaries
        """
        print(f"\n🔍 Searching boekwinkeltjes.nl for: '{query}' (€{prijsvan:.2f} - €{prijstot:.2f})")
        print(f"📄 Pages: {start_page} to {start_page + max_pages - 1}")
        
        all_book_urls = []
        end_page = start_page + max_pages - 1
        
        # Collect all book URLs from search results
        for page in range(start_page, end_page + 1):
            print(f"\n  Fetching search results page {page}...")
            
            book_urls = self.search_books(
                query=query,
                prijsvan=prijsvan,
                prijstot=prijstot,
                page=page,
                **search_params
            )
            
            if book_urls:
                print(f"  ✅ Found {len(book_urls)} books on page {page}")
                all_book_urls.extend(book_urls)
            else:
                print(f"  ⚠️  No books found on page {page}")
        
        print(f"\n📚 Total book URLs collected: {len(all_book_urls)}")
        
        # Now scrape each book page for full details
        print(f"\n🔍 Scraping individual book pages for details...")
        
        books = []
        for i, url in enumerate(all_book_urls, 1):
            if i % 10 == 0 or i == 1:
                print(f"  Progress: {i}/{len(all_book_urls)} ({i/len(all_book_urls)*100:.1f}%)")
            
            book_data = self.scrape_book_page(url)
            if book_data:
                books.append(book_data)
        
        print(f"\n✅ Successfully scraped {len(books)} books")
        self.books = books
        return books


class BookProfitabilityChecker:
    """Main class for checking book profitability"""
    
    def __init__(self, api: BoekenbalieAPI, min_profit_margin: float = 2.0):
        self.api = api
        self.min_profit_margin = min_profit_margin
        self.results = []
    
    def check_book(self, isbn: str, title: str = "", your_purchase_price: float = 0.0) -> Dict:
        """
        Check if a single book is profitable
        
        Args:
            isbn: Book ISBN
            title: Book title (optional, for reference)
            your_purchase_price: What you would pay to acquire the book
            
        Returns:
            Dictionary with profitability analysis
        """
        print(f"\n📚 Checking: {title or isbn}")
        
        result = {
            'isbn': isbn,
            'title': title,
            'your_purchase_price': your_purchase_price,
            'timestamp': datetime.now().isoformat(),
            'interested': False,
            'boekenbalie_price': None,
            'profit': None,
            'profit_margin': None,
            'profitable': False,
            'book_info': None
        }
        
        # Check if boekenbalie is interested
        interest_data = self.api.check_interest(isbn)
        
        if not interest_data:
            print(f"  ℹ️  Not interested or not found")
            return result
        
        result['interested'] = interest_data.get('interested', False)
        result['book_info'] = interest_data
        
        if not result['interested']:
            print(f"  ❌ Not interested")
            return result
        
        print(f"  ✅ Interested!")
        print(f"     Title: {interest_data.get('title', 'N/A')}")
        print(f"     Authors: {interest_data.get('authors', 'N/A')}")
        print(f"     Segment: {interest_data.get('segment', 'N/A')}")
        
        # Get buying price from boekenbalie
        book_id = interest_data.get('book_id')
        if book_id:
            boekenbalie_price = self.api.get_price(book_id)
            
            if boekenbalie_price is not None:
                result['boekenbalie_price'] = boekenbalie_price
                print(f"  💰 Boekenbalie offers: €{boekenbalie_price:.2f}")
                
                if your_purchase_price > 0:
                    profit = boekenbalie_price - your_purchase_price
                    profit_margin = (profit / your_purchase_price) * 100 if your_purchase_price > 0 else 0
                    
                    result['profit'] = profit
                    result['profit_margin'] = profit_margin
                    result['profitable'] = profit >= self.min_profit_margin
                    
                    print(f"  📊 Your cost: €{your_purchase_price:.2f}")
                    print(f"  💵 Profit: €{profit:.2f} ({profit_margin:.1f}%)")
                    
                    if result['profitable']:
                        print(f"  ✅ PROFITABLE! (Min margin: €{self.min_profit_margin:.2f})")
                    else:
                        print(f"  ❌ Not profitable enough")
        
        self.results.append(result)
        return result
    
    def check_books_from_csv(self, csv_file: str):
        """
        Check multiple books from a CSV file
        
        CSV format: isbn,title,purchase_price
        """
        print(f"\n📖 Reading books from: {csv_file}")
        
        try:
            with open(csv_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                
                for row in reader:
                    isbn = row.get('isbn', '').strip()
                    title = row.get('title', '').strip()
                    purchase_price = float(row.get('purchase_price', 0))
                    
                    if isbn:
                        self.check_book(isbn, title, purchase_price)
                        # Be nice to the API - small delay between requests
                        time.sleep(0.5)
                        
        except FileNotFoundError:
            print(f"❌ File not found: {csv_file}")
        except Exception as e:
            print(f"❌ Error reading CSV: {e}")
    
    def check_books_from_boekwinkeltjes(self, scraper: 'BoekwinkeltjesScraper', query: str = "", 
                                        prijsvan: float = 0.00, prijstot: float = 5.00,
                                        max_pages: int = 1, start_page: int = 1, **search_params):
        """
        Check books directly from boekwinkeltjes.nl search results
        
        Args:
            scraper: BoekwinkeltjesScraper instance
            query: Search query (e.g., "Kunst")
            prijsvan: Minimum price
            prijstot: Maximum price
            max_pages: Number of pages to scrape
            start_page: Starting page number
            **search_params: Additional search parameters
        """
        print("\n" + "="*60)
        print("🌐 FETCHING BOOKS FROM BOEKWINKELTJES.NL")
        print("="*60)
        
        # Scrape books from boekwinkeltjes search
        books = scraper.scrape_search_results(
            query=query,
            prijsvan=prijsvan,
            prijstot=prijstot,
            max_pages=max_pages,
            start_page=start_page,
            **search_params
        )
        
        if not books:
            print("❌ No books found on boekwinkeltjes")
            return
        
        print("\n" + "="*60)
        print(f"💰 CHECKING PROFITABILITY FOR {len(books)} BOOKS")
        print("="*60)
        
        # Check each book
        for i, book in enumerate(books, 1):
            isbn = book.get('isbn', '')
            title = book.get('title', '')
            price = book.get('price', 0)
            
            if not isbn:
                print(f"\n[{i}/{len(books)}] ⚠️  Skipping book without ISBN: {title}")
                continue
            
            print(f"\n{'='*60}")
            print(f"[{i}/{len(books)}] Processing from boekwinkeltjes:")
            print(f"  🏷️  Title: {title}")
            print(f"  📖 ISBN: {isbn}")
            print(f"  💶 Boekwinkeltjes price: €{price:.2f}")
            if book.get('condition'):
                print(f"  📦 Condition: {book['condition']}")
            if book.get('seller'):
                print(f"  🏪 Seller: {book['seller']}")
            if book.get('author'):
                print(f"  ✍️  Author: {book['author']}")
            
            # Check profitability (rate limiting is handled inside the API calls)
            self.check_book(
                isbn=isbn,
                title=title,
                your_purchase_price=price
            )
    
    def check_books_from_json(self, isbn_lookup: 'BookISBNLookup', json_file: str, your_purchase_price: float = 0.0):
        """
        Check books from a JSON file (e.g., from OCR)
        
        Args:
            isbn_lookup: BookISBNLookup instance
            json_file: Path to JSON file with book titles and authors
            your_purchase_price: Default purchase price if not specified per book
        """
        print("\n" + "="*60)
        print("📸 CHECKING BOOKS FROM JSON FILE (OCR)")
        print("="*60)
        
        # Lookup ISBNs
        books = isbn_lookup.lookup_books_from_json(json_file)
        
        if not books:
            print("❌ No books found in JSON")
            return
        
        # Filter to only books with ISBNs
        books_with_isbn = [b for b in books if b.get('isbn')]
        books_without_isbn = [b for b in books if not b.get('isbn')]
        
        print("\n" + "="*60)
        print(f"📊 ISBN LOOKUP RESULTS")
        print("="*60)
        print(f"Books with ISBN found: {len(books_with_isbn)}")
        print(f"Books without ISBN: {len(books_without_isbn)}")
        
        if books_without_isbn:
            print("\n⚠️  Could not find ISBN for:")
            for book in books_without_isbn[:10]:  # Show first 10
                print(f"   - {book['original_title']} by {book['original_author']}")
            if len(books_without_isbn) > 10:
                print(f"   ... and {len(books_without_isbn) - 10} more")
        
        if not books_with_isbn:
            print("\n❌ No books with ISBNs to check")
            return
        
        print("\n" + "="*60)
        print(f"💰 CHECKING PROFITABILITY FOR {len(books_with_isbn)} BOOKS")
        print("="*60)
        
        # Check each book
        for i, book in enumerate(books_with_isbn, 1):
            isbn = book['isbn']
            title = book.get('title', book.get('original_title', ''))
            price = book.get('purchase_price', your_purchase_price)
            
            print(f"\n{'='*60}")
            print(f"[{i}/{len(books_with_isbn)}] Processing from JSON:")
            print(f"  🏷️  Original: {book['original_title']} by {book.get('original_author', 'Unknown')}")
            print(f"  📖 Matched: {title}")
            print(f"  🔢 ISBN: {isbn}")
            print(f"  📊 Confidence: {book.get('confidence', 0):.0%}")
            if book.get('authors'):
                print(f"  ✍️  Authors: {', '.join(book['authors'])}")
            
            # Check profitability
            self.check_book(
                isbn=isbn,
                title=title,
                your_purchase_price=price
            )
    
    def check_books_from_boekenkraam(self, scraper: 'BoekenkraamScraper', max_pages: int = 1, start_page: int = 1, **search_params):
        """
        Check books directly from boekenkraam.nl
        
        Args:
            scraper: BoekenkraamScraper instance
            max_pages: Number of pages to fetch from boekenkraam
            start_page: Starting page number (offset)
            **search_params: Additional search parameters for boekenkraam
        """
        print("\n" + "="*60)
        print("🌐 FETCHING BOOKS FROM BOEKENKRAAM.NL")
        print("="*60)
        print(f"📄 Pages: {start_page} to {start_page + max_pages - 1}\n")
        
        # Get books from boekenkraam
        books = scraper.get_books_to_check(max_pages=max_pages, start_page=start_page, **search_params)
        
        if not books:
            print("❌ No books found on boekenkraam")
            return
        
        print("\n" + "="*60)
        print(f"💰 CHECKING PROFITABILITY FOR {len(books)} BOOKS")
        print("="*60)
        
        # Check each book
        for i, book in enumerate(books, 1):
            ean = book.get('ean', '')
            title = book.get('title', '')
            price = book.get('price', 0)
            
            if not ean:
                print(f"\n[{i}/{len(books)}] ⚠️  Skipping book without EAN: {title}")
                continue
            
            print(f"\n{'='*60}")
            print(f"[{i}/{len(books)}] Processing from boekenkraam:")
            print(f"  🏷️  Title: {title}")
            print(f"  📖 EAN: {ean}")
            print(f"  💶 Boekenkraam price: €{price:.2f}")
            if book.get('state'):
                print(f"  📦 Condition: {book['state']}")
            if book.get('in_stock'):
                print(f"  📊 In stock: {book['in_stock']}")
            
            # Check profitability (rate limiting is handled inside the API calls)
            self.check_book(
                isbn=ean,
                title=title,
                your_purchase_price=price
            )
    
    def save_results(self, output_file: str = "results.json"):
        """Save results to JSON file"""
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(self.results, f, indent=2, ensure_ascii=False)
        print(f"\n💾 Results saved to: {output_file}")
    
    def save_results_csv(self, output_file: str = "results.csv"):
        """Save results to CSV file"""
        if not self.results:
            print("No results to save")
            return
        
        with open(output_file, 'w', encoding='utf-8', newline='') as f:
            fieldnames = ['isbn', 'title', 'interested', 'your_purchase_price', 
                         'boekenbalie_price', 'profit', 'profit_margin', 'profitable', 'timestamp']
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            
            writer.writeheader()
            for result in self.results:
                row = {k: result.get(k) for k in fieldnames}
                writer.writerow(row)
        
        print(f"💾 Results saved to: {output_file}")
    
    def print_summary(self):
        """Print a summary of all checked books"""
        if not self.results:
            print("\nNo books checked yet")
            return
        
        print("\n" + "="*60)
        print("📊 SUMMARY")
        print("="*60)
        
        total = len(self.results)
        interested = sum(1 for r in self.results if r['interested'])
        profitable = sum(1 for r in self.results if r['profitable'])
        
        total_profit = sum(r['profit'] for r in self.results if r['profit'])
        
        print(f"Total books checked: {total}")
        print(f"Books they're interested in: {interested}")
        print(f"Profitable books: {profitable}")
        if total_profit:
            print(f"Total potential profit: €{total_profit:.2f}")
        
        if profitable > 0:
            # Get profitable books
            profitable_books = [r for r in self.results if r['profitable']]
            
            # Sort by absolute profit
            by_profit = sorted(profitable_books, key=lambda x: x['profit'], reverse=True)
            
            # Sort by profit margin %
            by_margin = sorted(profitable_books, key=lambda x: x['profit_margin'], reverse=True)
            
            # Display sorted by absolute profit
            print("\n" + "="*60)
            print("💰 TOP BOOKS BY ABSOLUTE PROFIT")
            print("="*60)
            for i, r in enumerate(by_profit[:10], 1):  # Top 10
                margin = r['profit_margin']
                cost = r['your_purchase_price']
                sell = r['boekenbalie_price']
                title = r['title'][:45] if r['title'] else r['isbn']
                print(f"{i:2}. {title:45} │ Cost: €{cost:5.2f} → Sell: €{sell:5.2f} │ Profit: €{r['profit']:5.2f} ({margin:5.1f}%)")
            
            # Display sorted by profit margin %
            print("\n" + "="*60)
            print("📈 TOP BOOKS BY PROFIT MARGIN %")
            print("="*60)
            for i, r in enumerate(by_margin[:10], 1):  # Top 10
                margin = r['profit_margin']
                cost = r['your_purchase_price']
                sell = r['boekenbalie_price']
                title = r['title'][:45] if r['title'] else r['isbn']
                print(f"{i:2}. {title:45} │ Cost: €{cost:5.2f} → Sell: €{sell:5.2f} │ Profit: €{r['profit']:5.2f} ({margin:5.1f}%)")


def main():
    """Main entry point"""
    # Load configuration
    try:
        with open('config.json', 'r') as f:
            config = json.load(f)
            auth_token = config['auth_token']
            min_profit_margin = config.get('min_profit_margin', 2.0)
            rate_limit_delay = config.get('rate_limit_delay', 1.0)
            max_requests_per_minute = config.get('max_requests_per_minute', 30)
            start_page = config.get('start_page', 1)
            max_pages = config.get('max_pages', 1)
            
            # Source selection
            source = config.get('source', 'boekenkraam')  # 'boekenkraam', 'boekwinkeltjes', or 'json'
            
            # JSON file specific config
            json_file = config.get('json_file', 'example_booklist.json')
            json_default_price = config.get('json_default_price', 0.0)
            
            # Boekwinkeltjes specific config (search-based)
            boekwinkeltjes_query = config.get('boekwinkeltjes_query', 'Kunst')
            boekwinkeltjes_prijsvan = config.get('boekwinkeltjes_prijsvan', 0.00)
            boekwinkeltjes_prijstot = config.get('boekwinkeltjes_prijstot', 5.00)
            boekwinkeltjes_language = config.get('boekwinkeltjes_language', '')
            
    except FileNotFoundError:
        print("⚠️  config.json not found. Please create it with your API token.")
        print("See config.example.json for the format")
        return
    
    # Initialize API client with rate limiting
    api = BoekenbalieAPI(
        auth_token=auth_token,
        rate_limit_delay=rate_limit_delay,
        max_requests_per_minute=max_requests_per_minute
    )
    checker = BookProfitabilityChecker(api, min_profit_margin)
    
    print("="*60)
    print("📚 Book Profitability Checker")
    print("="*60)
    print(f"\n⚙️  Rate limiting: {rate_limit_delay}s between requests, max {max_requests_per_minute} req/min")
    
    if source == 'json':
        print(f"\nMode: JSON File → ISBN Lookup → Boekenbalie.nl")
        print("Strategy: Find ISBNs from titles/authors and check profitability\n")
        
        # Initialize ISBN lookup
        isbn_lookup = BookISBNLookup(rate_limit_delay=0.5)
        
        # Check books from JSON
        checker.check_books_from_json(
            isbn_lookup=isbn_lookup,
            json_file=json_file,
            your_purchase_price=json_default_price
        )
        
        # Save results
        checker.print_summary()
        checker.save_results('results_json.json')
        checker.save_results_csv('results_json.csv')
        
        print("\n" + "="*60)
        print("✅ COMPLETE!")
        print("="*60)
        print(f"\n📊 API Statistics:")
        print(f"  Total API requests made: {api.total_requests}")
        if api.request_timestamps:
            print(f"  Average rate: {api.total_requests / ((time.time() - api.request_timestamps[0]) / 60):.1f} requests/min")
        print("\nResults saved to:")
        print("  📄 results_json.json")
        print("  📊 results_json.csv")
        
    elif source == 'boekwinkeltjes':
        print(f"\nMode: Boekwinkeltjes.nl → Boekenbalie.nl")
        print("Strategy: Search boekwinkeltjes and check if profitable to resell\n")
        
        # Initialize boekwinkeltjes scraper
        scraper = BoekwinkeltjesScraper(rate_limit_delay=0.5)
        
        # Check books from boekwinkeltjes search
        checker.check_books_from_boekwinkeltjes(
            scraper=scraper,
            query=boekwinkeltjes_query,
            prijsvan=boekwinkeltjes_prijsvan,
            prijstot=boekwinkeltjes_prijstot,
            max_pages=max_pages,
            start_page=start_page,
            lang=boekwinkeltjes_language
        )
        
        # Save results
        checker.print_summary()
        checker.save_results('results_boekwinkeltjes.json')
        checker.save_results_csv('results_boekwinkeltjes.csv')
        
        print("\n" + "="*60)
        print("✅ COMPLETE!")
        print("="*60)
        print(f"\n📊 API Statistics:")
        print(f"  Total API requests made: {api.total_requests}")
        print(f"  Average rate: {api.total_requests / ((time.time() - api.request_timestamps[0]) / 60) if api.request_timestamps else 0:.1f} requests/min")
        print("\nResults saved to:")
        print("  📄 results_boekwinkeltjes.json")
        print("  📊 results_boekwinkeltjes.csv")
        
    else:  # boekenkraam
        print("\nMode: Boekenkraam.nl → Boekenbalie.nl")
        print("Strategy: Find books on boekenkraam and check if profitable to resell\n")
        
        # Initialize boekenkraam scraper
        scraper = BoekenkraamScraper()
        
        # Check books from boekenkraam
        checker.check_books_from_boekenkraam(
            scraper=scraper,
            max_pages=max_pages,
            start_page=start_page,
            num_results=12,
            min_price=0,
            max_price=55,
            sort="bestSold"
        )
        
        # Print summary and save results
        checker.print_summary()
        checker.save_results('results_boekenkraam.json')
        checker.save_results_csv('results_boekenkraam.csv')
        
        print("\n" + "="*60)
        print("✅ COMPLETE!")
        print("="*60)
        print(f"\n📊 API Statistics:")
        print(f"  Total API requests made: {api.total_requests}")
        print(f"  Average rate: {api.total_requests / ((time.time() - api.request_timestamps[0]) / 60) if api.request_timestamps else 0:.1f} requests/min")
        print("\nResults saved to:")
        print("  📄 results_boekenkraam.json")
        print("  📊 results_boekenkraam.csv")


if __name__ == "__main__":
    main()
