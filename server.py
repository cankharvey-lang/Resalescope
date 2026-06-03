"""
PriceScout — Python Backend
Uses RapidAPI eBay Average Selling Price API
"""

import os, json, requests
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder='public')
CORS(app)

RAPIDAPI_KEY = os.getenv('RAPIDAPI_KEY', 'ad18cc42aamshcf9a7059a2cf7b0p1a5cc2jsn47199c0efcc3')
PORT         = int(os.getenv('PORT', 8080))


@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'rapidapi': bool(RAPIDAPI_KEY)})


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
        'x-rapidapi-host': 'ebay-average-selling-price.p.rapidapi.com'
    }

    params = {
        'keywords':    query,
        'excluded_keywords': '',
        'max_search_results': '240',
        'aspects':     '',
        'site_id':     '3',   # 3 = eBay UK
        'remove_outliers': '1',
        'spelling_correction': '1',
    }

    try:
        r = requests.get(
            'https://ebay-average-selling-price.p.rapidapi.com/findCompletedItems',
            headers=headers, params=params, timeout=15
        )
        r.raise_for_status()
        data = r.json()

        # Build listings from results
        listings = []
        raw_items = data.get('results', [])
        for i, item in enumerate(raw_items[:20]):
            listings.append({
                'id':        str(i),
                'title':     item.get('title', query),
                'price':     float(item.get('sold_price', 0)),
                'currency':  'GBP',
                'condition': item.get('condition', 'Unknown'),
                'soldDate':  item.get('end_date', ''),
                'url':       item.get('url', f'https://www.ebay.co.uk/sch/i.html?_nkw={query}&LH_Sold=1'),
                'imageUrl':  item.get('image', ''),
                'location':  item.get('location', 'United Kingdom'),
            })

        prices = sorted([l['price'] for l in listings if l['price'] > 0])
        avg    = float(data.get('average_price', round(sum(prices)/len(prices), 2) if prices else 0))
        total  = int(data.get('total_results', len(listings)))

        return jsonify({
            'query':     query,
            'totalSold': total,
            'listings':  listings,
            'stats': {
                'count':  len(listings),
                'avg':    round(avg, 2),
                'low':    round(prices[0], 2) if prices else 0,
                'high':   round(prices[-1], 2) if prices else 0,
                'median': round(prices[len(prices)//2], 2) if prices else 0,
            }
        })

    except Exception as e:
        print(f'RapidAPI error: {e}')
        return jsonify({'error': 'Could not fetch eBay data: ' + str(e)}), 502


@app.route('/api/barcode/<code>')
def barcode(code):
    try:
        r = requests.get(f'https://world.openfoodfacts.org/api/v0/product/{code}.json', timeout=5)
        d = r.json()
        if d.get('status') == 1:
            p = d['product']
            name = p.get('product_name') or p.get('product_name_en', '')
            if name:
                return jsonify({'name': f"{p.get('brands','')} {name}".strip(), 'category': 'Food & Grocery', 'source': 'OpenFoodFacts'})
    except:
        pass
    try:
        r = requests.get(f'https://api.upcitemdb.com/prod/trial/lookup?upc={code}', timeout=5)
        items = r.json().get('items', [])
        if items and items[0].get('title'):
            i = items[0]
            return jsonify({'name': i['title'], 'brand': i.get('brand',''), 'category': i.get('category','General'), 'source': 'UPCItemDB'})
    except:
        pass
    return jsonify({'name': code, 'category': 'General', 'source': 'raw'})


if __name__ == '__main__':
    print(f'\n✅  PriceScout running → http://localhost:{PORT}')
    app.run(host='0.0.0.0', port=PORT, debug=False)
