"""
Boekenbalie.nl API Client
Handles all communication with the boekenbalie.nl buyback API
"""

import requests
import uuid
import time
from typing import Optional, Dict


class BoekenbalieAPI:
    """Client for interacting with boekenbalie.nl API"""

    BASE_URL = "https://api.boekenbalie.nl"

    def __init__(self, auth_token: str, rate_limit_delay: float = 0.5, max_requests_per_minute: int = 60):
        self.auth_token = auth_token
        self.rate_limit_delay = rate_limit_delay
        self.max_requests_per_minute = max_requests_per_minute
        self.last_request_time = 0
        self.request_timestamps = []
        self.total_requests = 0

        self.session = requests.Session()
        self.session.headers.update({
            'Accept': 'application/json',
            'Authorization': f'Bearer {auth_token}',
        })

    def _wait_for_rate_limit(self):
        current_time = time.time()
        self.request_timestamps = [t for t in self.request_timestamps if current_time - t < 60]

        if len(self.request_timestamps) >= self.max_requests_per_minute:
            oldest_request = min(self.request_timestamps)
            wait_time = 60 - (current_time - oldest_request)
            if wait_time > 0:
                time.sleep(wait_time)
                current_time = time.time()

        time_since_last = current_time - self.last_request_time
        if time_since_last < self.rate_limit_delay:
            time.sleep(self.rate_limit_delay - time_since_last)
            current_time = time.time()

        self.last_request_time = current_time
        self.request_timestamps.append(current_time)
        self.total_requests += 1

    def check_interest(self, isbn: str) -> Optional[Dict]:
        """Check if boekenbalie wants to buy this ISBN. Returns dict or None."""
        isbn_clean = isbn.replace('-', '').replace(' ', '')
        url = f"{self.BASE_URL}/api/v2/books/{isbn_clean}"
        self._wait_for_rate_limit()
        try:
            response = self.session.get(url, headers={'Request_id': str(uuid.uuid4())})
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 404:
                return None
            else:
                print(f"  ⚠️  Boekenbalie error {response.status_code}: {response.text[:100]}")
                return None
        except Exception as e:
            print(f"  ❌ Boekenbalie request failed: {e}")
            return None

    def get_price(self, book_id: str) -> Optional[float]:
        """Get the buy-back price in euros for a book_id. Returns float or None."""
        url = f"{self.BASE_URL}/api/v2/books/{book_id}/price"
        self._wait_for_rate_limit()
        try:
            response = self.session.get(url, headers={'Request_id': str(uuid.uuid4())})
            if response.status_code == 200:
                data = response.json()
                if 'price' in data and data['price'] is not None:
                    return float(data['price']) / 100
                elif 'offer_price' in data and data['offer_price'] is not None:
                    return float(data['offer_price']) / 100
                return None
            else:
                print(f"  ⚠️  Price error {response.status_code}: {response.text[:100]}")
                return None
        except Exception as e:
            print(f"  ❌ Price request failed: {e}")
            return None
