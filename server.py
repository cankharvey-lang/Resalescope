"""
PriceScout — Python Backend (Updated)
Tries Finding API first, falls back to eBay UK scrape
"""

import os, json, requests
from datetime import datetime, timedelta, timezone
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder='public')
CORS(app)

EBAY_APP_ID = os.getenv('EBAY_APP_ID', 'HarveyCa-PriceSco-PRD-88b00c500-2ca7bf20')
PORT        = int(os.getenv('PORT', 8080))


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

    # ── Try Official Finding API ──────────────────────────────────────────────
    params = {
        'OPERATION-NAME':                 'findCompletedItems',
        'SERVICE-VERSION':                '1.0.0',
        'SECURITY-APPNAME':               EBAY_APP_ID,
        'RESPONSE-DATA-FORMAT':           'JSON',
        'keywords':                       query,
        'itemFilter(0).name':             'SoldItemsOnly',
        'itemFilter(0).value':            'true',
        'itemFilter(1).name':             'EndTimeFrom',
        'itemFilter(1).value':            thirty_ago,
        'sortOrder':                      'EndTimeSoonest',
        'paginationInput.entriesPerPage': str(limit),
        'GLOBAL-ID':                      'EBAY-GB',
    }

    try:
        r = requests.get('https://svcs.ebay.com/services/search/FindingService/v1', params=params, timeout=10)
        data = r.json()
        raw = data.get('findCompletedItemsResponse', [{}])[0]
        ack = raw.get('ack', [''])[0]

        if ack in ('Success', 'Warning'):
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
                'stats': {'count': len(listings), 'avg': avg,
                          'low': round(prices[0], 2) if prices else 0,
                          'high': round(prices[-1], 2) if prices else 0,
                          'median': round(prices[len(prices)//2], 2) if prices else 0}
            })
    except Exception as e:
        print(f'Finding API error: {e}')

    # ── Fallback: scrape eBay UK completed listings ───────────────────────────
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1'}
        r2 = requests.get('https://www.ebay.co.uk/sch/i.html',
                          params={'_nkw': query, 'LH_Sold': '1', 'LH_Complete': '1', '_ipg': '40'},
                          headers=headers, timeout=10)

        import re
        # Extract prices
        prices_raw = re.findall(r'class="s-item__price"[^>]*>.*?£([\d,]+\.?\d*)', r2.text, re.DOTALL)
        titles_raw = re.findall(r'class="s-item__title"[^>]*><span[^>]*>(.*?)</span>', r2.text)
        urls_raw   = re.findall(r'class="s-item__link"[^>]*href="(https://www\.ebay\.co\.uk/itm/[^"]+)"', r2.text)
        imgs_raw   = re.findall(r's-item__image-img[^>]+src="([^"]+)"', r2.text)

        listings = []
        for i in range(min(len(prices_raw), len(titles_raw), 20)):
            try:
                price = float(prices_raw[i].replace(',', ''))
                title = re.sub('<[^>]+>', '', titles_raw[i]).strip()
                if title and price > 0 and title != 'Shop on eBay':
                    listings.append({
                        'id': str(i), 'title': title, 'price': price,
                        'currency': 'GBP', 'condition': 'Unknown',
                        'soldDate': '', 'location': 'United Kingdom',
                        'url': urls_raw[i] if i < len(urls_raw) else '',
                        'imageUrl': imgs_raw[i] if i < len(imgs_raw) else '',
                    })
            except:
                continue

        if listings:
            prices = sorted([l['price'] for l in listings])
            avg = round(sum(prices) / len(prices), 2)
            return jsonify({
                'query': query, 'totalSold': len(listings), 'listings': listings,
                'stats': {'count': len(listings), 'avg': avg,
                          'low': prices[0], 'high': prices[-1],
                          'median': prices[len(prices)//2]}
            })
    except Exception as e:
        print(f'Scrape error: {e}')

    return jsonify({'error': 'Could not fetch eBay data. Please try again.'}), 502


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
