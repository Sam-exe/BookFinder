"""
Book Profitability Web API
Flask backend - streaming SSE responses for real-time progress
"""

from flask import Flask, request, Response, render_template, stream_with_context
from flask_cors import CORS
from werkzeug.utils import secure_filename
import os
import json
from pathlib import Path
from dotenv import load_dotenv
import time

from gemini_book_detector import GeminiBookDetector
from book_profitability_checker import BookProfitabilityChecker, BookISBNLookup
from boekenbalie_api import BoekenbalieAPI

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY', 'dev-secret-key-change-in-production')
app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024  # 20MB
app.config['UPLOAD_FOLDER'] = Path('uploads')
app.config['UPLOAD_FOLDER'].mkdir(exist_ok=True)

CORS(app)

ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png', 'webp', 'heic', 'heif'}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


def load_token():
    """Load Boekenbalie token from env or config.json fallback."""
    t = os.getenv('BOEKENBALIE_API_TOKEN')
    if t:
        return t
    try:
        with open('config.json') as f:
            return json.load(f).get('auth_token')
    except Exception:
        return None


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/health')
def health():
    return {'status': 'ok', 'timestamp': time.time()}


@app.route('/api/analyze', methods=['POST'])
def analyze():
    """
    Accepts multipart/form-data: 'image' file + 'purchase_price'.
    Returns text/event-stream of JSON progress events.
    """
    if 'image' not in request.files:
        return Response(sse({'type': 'error', 'message': 'Geen afbeelding meegestuurd'}),
                        mimetype='text/event-stream')

    file = request.files['image']
    if not file.filename or not allowed_file(file.filename):
        return Response(sse({'type': 'error', 'message': 'Ongeldig bestandstype'}),
                        mimetype='text/event-stream')

    try:
        purchase_price = float(request.form.get('purchase_price', 1.0))
    except ValueError:
        purchase_price = 1.0

    filename = secure_filename(file.filename)
    filepath = app.config['UPLOAD_FOLDER'] / f"{int(time.time())}_{filename}"
    file.save(filepath)

    token = load_token()

    def generate():
        try:
            # Step 1 – Gemini detection
            yield sse({'type': 'status', 'step': 1, 'total': 3,
                       'message': 'Boeken herkennen met AI...'})

            gemini = GeminiBookDetector()
            detected = gemini.detect_books_from_image(str(filepath))

            yield sse({'type': 'detected', 'count': len(detected),
                       'message': f'{len(detected)} boeken herkend'})

            if not detected:
                yield sse({'type': 'done',
                           'summary': {'detected': 0, 'with_isbn': 0, 'profitable': 0,
                                       'purchase_price': purchase_price},
                           'books': []})
                return

            # Step 2 – ISBN lookup
            yield sse({'type': 'status', 'step': 2, 'total': 3,
                       'message': f'ISBN nummers zoeken voor {len(detected)} boeken...'})

            isbn_lookup = BookISBNLookup()
            books_with_isbn = []

            for i, book in enumerate(detected):
                title = book.get('title', '')
                author = book.get('author')

                yield sse({'type': 'isbn_progress', 'index': i + 1,
                           'total': len(detected), 'title': title})

                editions = isbn_lookup.find_all_isbns(title, author)
                if editions:
                    primary = editions[0]
                    authors = primary.get('authors', [])
                    books_with_isbn.append({
                        'title': primary.get('title', title),
                        'author': ', '.join(authors) if authors else (author or 'Onbekend'),
                        'isbns': editions,
                        'confidence': book.get('confidence', 0),
                        'detected_title': title,
                        'detected_author': author,
                        'shelf': book.get('shelf', 1),
                        'position': book.get('position', 0),
                    })
                    yield sse({'type': 'isbn_found', 'index': i + 1,
                               'title': primary.get('title', title),
                               'isbn': primary['isbn'],
                               'edition_count': len(editions)})
                else:
                    yield sse({'type': 'isbn_missing', 'index': i + 1, 'title': title})

                time.sleep(0.3)

            # Step 3 – Boekenbalie pricing
            yield sse({'type': 'status', 'step': 3, 'total': 3,
                       'message': f'Prijzen checken bij Boekenbalie ({len(books_with_isbn)} boeken)...'})

            if not token:
                yield sse({'type': 'error', 'message': 'BOEKENBALIE_API_TOKEN niet ingesteld'})
                return

            api = BoekenbalieAPI(token)
            checker = BookProfitabilityChecker(api)
            profitable = []

            for i, book in enumerate(books_with_isbn):
                yield sse({'type': 'price_progress', 'index': i + 1,
                           'total': len(books_with_isbn), 'title': book['title']})

                # Check every edition against Boekenbalie
                edition_results = []
                for ed in book['isbns']:
                    res = checker.check_book(isbn=ed['isbn'], title=book['title'],
                                             your_purchase_price=purchase_price)
                    interested = bool(res and res.get('boekenbalie_price') is not None)
                    sell_price = res['boekenbalie_price'] if interested else None
                    edition_results.append({
                        'isbn': ed['isbn'],
                        'publisher': ed.get('publisher', ''),
                        'published_date': ed.get('published_date', ''),
                        'language': ed.get('language', ''),
                        'interested': interested,
                        'sell_price': sell_price,
                        'profit': (sell_price - purchase_price) if interested else None,
                    })
                    time.sleep(0.1)

                bought = [e for e in edition_results if e['interested']]

                if not bought:
                    yield sse({'type': 'book_skip', 'title': book['title'], 'index': i + 1})
                    continue

                best = max(bought, key=lambda e: e['sell_price'])
                sell = best['sell_price']
                profit = sell - purchase_price
                margin = (profit / purchase_price * 100) if purchase_price > 0 else 0
                chance = len(bought) / len(edition_results) * 100

                entry = {
                    'title': book['title'],
                    'author': book['author'],
                    'isbn': best['isbn'],
                    'detected_title': book['detected_title'],
                    'confidence': book['confidence'],
                    'shelf': book.get('shelf', 1),
                    'position': book.get('position', 0),
                    'purchase_price': purchase_price,
                    'sell_price': sell,
                    'profit': profit,
                    'margin_percent': margin,
                    'editions_checked': len(edition_results),
                    'editions_bought': len(bought),
                    'chance_percent': chance,
                    'all_editions': edition_results,
                }
                profitable.append(entry)
                yield sse({'type': 'book_result', 'book': entry})

            profitable.sort(key=lambda x: x['profit'], reverse=True)

            yield sse({'type': 'done',
                       'summary': {
                           'detected': len(detected),
                           'with_isbn': len(books_with_isbn),
                           'profitable': len(profitable),
                           'purchase_price': purchase_price,
                       },
                       'books': profitable})

        except Exception as e:
            import traceback
            traceback.print_exc()
            yield sse({'type': 'error', 'message': str(e)})
        finally:
            try:
                filepath.unlink()
            except Exception:
                pass

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    debug = os.getenv('FLASK_DEBUG', '0') == '1'
    print(f"\n{'='*50}\nBook Profitability Checker — http://localhost:{port}\n{'='*50}\n")
    app.run(host='0.0.0.0', port=port, debug=debug)

