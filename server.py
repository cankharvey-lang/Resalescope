"""
PriceScout — Python Backend
─────────────────────────────
pip install flask flask-cors requests
python server.py
"""

import os, json, requests
from datetime import datetime, timedelta, timezone
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder='public')
CORS(app)

EBAY_APP_ID = os.getenv('EBAY_APP_ID', 'HarveyCa-PriceSco-PRD-88b00c500-2ca7bf20')
PORT        = int(os.getenv('PORT', 3000))


@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'ebay': bool(EBAY_APP_ID)})


@app.route('/')
def index():
    return send_from_directory('public', 'index.html')


@app.route('/api/ebay/sold')
def ebay_sold():
    query = request.args.get('q', '').strip()
    limit = min(int(request.args.get('limit', 20)), 100)
    if not query:
        return jsonify({'error': 'q parameter required'}), 400

    thirty_ago = (datetime.now(timezone.utc) - timedelta(days=30)).strftime('%Y-%m-%dT%H:%M:%S.000Z')

    params = {
        'OPERATION-NAME':                 'findCompletedItems',
        'SERVICE-VERSION':                '1.0.0',
        'SECURITY-APPNAME':               EBAY_APP_ID,
        'RESPONSE-DATA-FORMAT':           'JSON',
        'REST-PAYLOAD':                   '',
        'keywords':                       query,
        'itemFilter(0).name':             'SoldItemsOnly',
        'itemFilter(0).value':            'true',
        'itemFilter(1).name':             'EndTimeFrom',
        'itemFilter(1).value':            thirty_ago,
        'sortOrder':                      'EndTimeSoonest',
        'paginationInput.entriesPerPage': str(limit),
    }

    try:
        r = requests.get('https://svcs.ebay.com/services/search/FindingService/v1', params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return jsonify({'error': str(e)}), 502

    raw = data.get('findCompletedItemsResponse', [{}])[0]
    ack = raw.get('ack', [''])[0]
    if ack not in ('Success', 'Warning'):
        msg = raw.get('errorMessage', [{}])[0].get('error', [{}])[0].get('message', ['Unknown'])[0]
        return jsonify({'error': msg}), 502

    items = raw.get('searchResult', [{}])[0].get('item', [])
    listings = []
    for item in items:
        pb = item.get('sellingStatus', [{}])[0].get('convertedCurrentPrice', [{}])[0]
        listings.append({
            'id':        item.get('itemId', [''])[0],
            'title':     item.get('title', [''])[0],
            'price':     float(pb.get('__value__', 0)),
            'currency':  pb.get('@currencyId', 'GBP'),
            'condition': item.get('condition', [{}])[0].get('conditionDisplayName', ['Unknown'])[0],
            'soldDate':  item.get('listingInfo', [{}])[0].get('endTime', [''])[0],
            'url':       item.get('viewItemURL', [''])[0],
            'imageUrl':  item.get('galleryURL', [''])[0] if item.get('galleryURL') else '',
            'location':  item.get('location', [''])[0],
        })

    prices = sorted([l['price'] for l in listings if l['price'] > 0])
    avg    = round(sum(prices) / len(prices), 2) if prices else 0
    total  = int(raw.get('paginationOutput', [{}])[0].get('totalEntries', [len(listings)])[0])

    return jsonify({
        'query': query, 'totalSold': total, 'listings': listings,
        'stats': {
            'count':  len(listings),
            'avg':    avg,
            'low':    round(prices[0], 2) if prices else 0,
            'high':   round(prices[-1], 2) if prices else 0,
            'median': round(prices[len(prices)//2], 2) if prices else 0,
        }
    })


@app.route('/api/barcode/<code>')
def barcode(code):
    # 1. Open Food Facts
    try:
        r = requests.get(f'https://world.openfoodfacts.org/api/v0/product/{code}.json', timeout=5)
        d = r.json()
        if d.get('status') == 1:
            p = d['product']
            name = p.get('product_name') or p.get('product_name_en', '')
            if name:
                brand = p.get('brands', '')
                return jsonify({'name': f"{brand} {name}".strip(), 'category': 'Food & Grocery', 'source': 'OpenFoodFacts'})
    except Exception:
        pass

    # 2. UPC Item DB
    try:
        r = requests.get(f'https://api.upcitemdb.com/prod/trial/lookup?upc={code}', timeout=5)
        items = r.json().get('items', [])
        if items and items[0].get('title'):
            i = items[0]
            return jsonify({'name': i['title'], 'brand': i.get('brand',''), 'category': i.get('category','General'), 'source': 'UPCItemDB'})
    except Exception:
        pass

    return jsonify({'name': code, 'category': 'General', 'source': 'raw'})


if __name__ == '__main__':
    print(f'\n✅  PriceScout running → http://localhost:{PORT}')
    print(f'    eBay App ID: {EBAY_APP_ID[:20]}…\n')
    app.run(host='0.0.0.0', port=PORT, debug=False)
