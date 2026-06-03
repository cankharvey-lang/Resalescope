"""
PriceScout — Python Backend
Uses RapidAPI eBay Average Selling Price API (POST)
Returns avg, min, max and individual listings
"""
import os, requests, base64 as b64lib
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
app = Flask(__name__, static_folder='public')
CORS(app)
RAPIDAPI_KEY  = os.getenv('RAPIDAPI_KEY', 'ad18cc42aamshcf9a7059a2cf7b0p1a5cc2jsn47199c0efcc3')
ANTHROPIC_KEY = os.getenv('ANTHROPIC_KEY', '')
PORT         = int(os.getenv('PORT', 8080))
def parse_price(val):
    try:
        return round(float(str(val).replace('£','').replace('$','').replace(',','').strip()), 2)
    except:
        return 0.0
@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'anthropic': bool(ANTHROPIC_KEY), 'rapidapi': bool(RAPIDAPI_KEY)})
@app.route('/')
def index():
    return send_from_directory('public', 'index.html')
@app.route('/api/ebay/sold')
def ebay_sold():
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify({'error': 'q parameter required'}), 400
    headers = {
        'x-rapidapi-key':  RAPIDAPI_KEY,
        'x-rapidapi-host': 'ebay-average-selling-price.p.rapidapi.com',
        'Content-Type':    'application/json'
    }
    payload = {
        'keywords':            query,
        'excluded_keywords':   '',
        'max_search_results':  '240',
        'aspects':             [],
        'site_id':             '3',
        'remove_outliers':     '1',
        'spelling_correction': '1',
    }
    try:
        r = requests.post(
            'https://ebay-average-selling-price.p.rapidapi.com/findCompletedItems',
            headers=headers, json=payload, timeout=15
        )
        r.raise_for_status()
        data = r.json()
        print('API response keys:', list(data.keys()) if isinstance(data, dict) else type(data))
        # ── Top-level stats from API ──────────────────────────────────────────
        avg       = parse_price(data.get('average_price', 0))
        low       = parse_price(data.get('min_price',     0))
        high      = parse_price(data.get('max_price',     0))
        total     = int(data.get('total_results', 0) or 0)
        # ── Individual listings ───────────────────────────────────────────────
        raw_items = data.get('results', [])
        if not isinstance(raw_items, list):
            raw_items = []
        listings = []
        for i, item in enumerate(raw_items[:20]):
            if not isinstance(item, dict):
                continue
            price = parse_price(item.get('sold_price') or item.get('price') or 0)
            listings.append({
                'id':        str(i),
                'title':     str(item.get('title', query)),
                'price':     price,
                'currency':  'GBP',
                'condition': str(item.get('condition', 'Unknown')),
                'soldDate':  str(item.get('end_date') or item.get('date') or ''),
                'url':       str(item.get('url') or item.get('itemUrl') or
                               f'https://www.ebay.co.uk/sch/i.html?_nkw={query}&LH_Sold=1'),
                'imageUrl':  str(item.get('image') or item.get('imageUrl') or ''),
                'location':  str(item.get('location') or 'United Kingdom'),
            })
        # If API gave us min/max use those, else derive from listings
        item_prices = sorted([l['price'] for l in listings if l['price'] > 0])
        if low == 0 and item_prices:
            low = item_prices[0]
        if high == 0 and item_prices:
            high = item_prices[-1]
        if avg == 0 and item_prices:
            avg = round(sum(item_prices) / len(item_prices), 2)
        if total == 0:
            total = len(listings)
        median = item_prices[len(item_prices)//2] if item_prices else avg
        return jsonify({
            'query':     query,
            'totalSold': total,
            'listings':  listings,
            'stats': {
                'count':  len(listings),
                'avg':    avg,
                'low':    low,
                'high':   high,
                'median': median,
            }
        })
    except Exception as e:
        print(f'RapidAPI error: {e}')
        return jsonify({'error': 'Could not fetch eBay data: ' + str(e)}), 502
@app.route('/api/identify', methods=['POST'])
def identify():
    body = request.get_json() or {}
    image = body.get('image', '')
    if not image:
        return jsonify({'error': 'image required'}), 400
    if not ANTHROPIC_KEY:
        return jsonify({'error': 'AI not configured'}), 500
    base64_data = image.split(',')[1] if ',' in image else image
    mime_raw    = image.split(';')[0].split(':')[1] if ';' in image else 'image/jpeg'
    media_type  = mime_raw if mime_raw in ('image/jpeg','image/png','image/webp','image/gif') else 'image/jpeg'
    try:
        r = requests.post(
            'https://api.anthropic.com/v1/messages',
            json={
                'model': 'claude-3-5-sonnet-20241022',
                'max_tokens': 200,
                'messages': [{
                    'role': 'user',
                    'content': [
                        {'type': 'image', 'source': {'type': 'base64', 'media_type': media_type, 'data': base64_data}},
                        {'type': 'text', 'text': 'Identify this product for eBay search. Reply ONLY with JSON: {"name":"brand model spec","category":"category"}'}
                    ]
                }]
            },
            headers={'x-api-key': ANTHROPIC_KEY, 'anthropic-version': '2023-06-01'},
            timeout=30
        )
        r.raise_for_status()
        text   = ''.join(c.get('text','') for c in r.json().get('content', []))
        parsed = json.loads(text.replace('```json','').replace('```','').strip())
        return jsonify(parsed)
    except Exception as e:
        print(f'AI identify error: {e}')
        return jsonify({'error': 'AI identification failed'}), 502
@app.route('/api/barcode/<code>')
def barcode(code):
    try:
        r = requests.get(f'https://world.openfoodfacts.org/api/v0/product/{code}.json', timeout=5)
        d = r.json()
        if d.get('status') == 1:
            p = d['product']
            name = p.get('product_name') or p.get('product_name_en', '')
            if name:
                return jsonify({'name': f"{p.get('brands','')} {name}".strip(),
                                'category': 'Food & Grocery', 'source': 'OpenFoodFacts'})
    except:
        pass
    try:
        r = requests.get(f'https://api.upcitemdb.com/prod/trial/lookup?upc={code}', timeout=5)
        items = r.json().get('items', [])
        if items and items[0].get('title'):
            i = items[0]
            return jsonify({'name': i['title'], 'brand': i.get('brand',''),
                            'category': i.get('category','General'), 'source': 'UPCItemDB'})
    except:
        pass
    return jsonify({'name': code, 'category': 'General', 'source': 'raw'})
if __name__ == '__main__':
    print(f'\n✅  PriceScout running → http://localhost:{PORT}')
    app.run(host='0.0.0.0', port=PORT, debug=False)
