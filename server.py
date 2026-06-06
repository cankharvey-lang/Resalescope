"""
PriceScout — Python Backend (Optimised)
"""
import os, json, requests, base64 as b64lib, time, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from flask_compress import Compress

app = Flask(__name__, static_folder='public')
CORS(app)
Compress(app)  # gzip compression on all responses

RAPIDAPI_KEY      = os.getenv('RAPIDAPI_KEY', '')
ANTHROPIC_KEY     = os.getenv('ANTHROPIC_KEY', '')
STRIPE_SECRET_KEY = os.getenv('STRIPE_SECRET_KEY', '')
STRIPE_WEBHOOK_SECRET = os.getenv('STRIPE_WEBHOOK_SECRET', '')
STRIPE_PRICE_ID   = os.getenv('STRIPE_PRICE_ID', 'price_1TeY0HHzkJINbfejP0vlCO98')
EBAY_APP_ID       = os.getenv('EBAY_APP_ID', 'HarveyCa-PriceSco-PRD-88b00c500-2ca7bf20')
PORT              = int(os.getenv('PORT', 8080))

# ── Search cache ──────────────────────────────────────────────────────────────
_cache = {}
CACHE_TTL = 3600  # 1 hour

def cache_get(key):
    if key in _cache:
        data, ts = _cache[key]
        if time.time() - ts < CACHE_TTL:
            return data
        del _cache[key]
    return None

def cache_set(key, data):
    _cache[key] = (data, time.time())
    if len(_cache) > 200:
        oldest = min(_cache.keys(), key=lambda k: _cache[k][1])
        del _cache[oldest]

# ── Vinted session cookie ─────────────────────────────────────────────────────
_vinted_cookie = None
_vinted_cookie_ts = 0
VINTED_COOKIE_TTL = 1800  # 30 min

def get_vinted_cookie():
    global _vinted_cookie, _vinted_cookie_ts
    if _vinted_cookie and time.time() - _vinted_cookie_ts < VINTED_COOKIE_TTL:
        return _vinted_cookie
    try:
        r = requests.get(
            'https://www.vinted.co.uk',
            headers={'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15'},
            timeout=8, allow_redirects=True
        )
        cookies = r.cookies.get_dict()
        if cookies:
            _vinted_cookie = '; '.join([f'{k}={v}' for k, v in cookies.items()])
            _vinted_cookie_ts = time.time()
            return _vinted_cookie
    except Exception as e:
        print(f'Vinted cookie error: {e}')
    return None

# Pre-fetch Vinted cookie on startup in background
threading.Thread(target=get_vinted_cookie, daemon=True).start()

def parse_price(val):
    try:
        return round(float(str(val).replace('£','').replace('$','').replace(',','').strip()), 2)
    except:
        return 0.0
@app.route('/health')
def health():
    return jsonify({
        'status':    'ok',
        'anthropic': bool(ANTHROPIC_KEY),
        'rapidapi':  bool(RAPIDAPI_KEY),
        'stripe':    bool(STRIPE_SECRET_KEY),
        'ebay':      bool(EBAY_APP_ID),
    })

# ── Stripe: create checkout session ──────────────────────────────────────────
@app.route('/api/stripe/checkout', methods=['POST'])
def stripe_checkout():
    if not STRIPE_SECRET_KEY:
        return jsonify({'error': 'Stripe not configured'}), 500
    body = request.get_json() or {}
    user_id    = body.get('userId', '')
    user_email = body.get('email', '')
    success_url = body.get('successUrl', 'https://compassionate-patience-production-4d82.up.railway.app?upgraded=true')
    cancel_url  = body.get('cancelUrl',  'https://compassionate-patience-production-4d82.up.railway.app?cancelled=true')
    try:
        r = requests.post(
            'https://api.stripe.com/v1/checkout/sessions',
            auth=(STRIPE_SECRET_KEY, ''),
            data={
                'payment_method_types[]':     'card',
                'mode':                        'subscription',
                'line_items[0][price]':        STRIPE_PRICE_ID,
                'line_items[0][quantity]':     '1',
                'customer_email':              user_email,
                'metadata[userId]':            user_id,
                'success_url':                 success_url,
                'cancel_url':                  cancel_url,
            },
            timeout=10
        )
        r.raise_for_status()
        session = r.json()
        return jsonify({'url': session['url'], 'sessionId': session['id']})
    except Exception as e:
        print(f'Stripe checkout error: {e}')
        return jsonify({'error': 'Could not create checkout session: ' + str(e)}), 502

# ── Stripe: webhook (updates Firebase plan after payment) ────────────────────
@app.route('/api/stripe/webhook', methods=['POST'])
def stripe_webhook():
    payload    = request.get_data()
    sig_header = request.headers.get('Stripe-Signature', '')

    # Verify webhook signature if secret is set
    if STRIPE_WEBHOOK_SECRET:
        try:
            import hmac, hashlib, time
            elements   = dict(e.split('=', 1) for e in sig_header.split(',') if '=' in e)
            timestamp  = elements.get('t', '')
            signature  = elements.get('v1', '')
            signed_payload = f'{timestamp}.{payload.decode("utf-8")}'
            expected   = hmac.new(STRIPE_WEBHOOK_SECRET.encode(), signed_payload.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, signature):
                return jsonify({'error': 'Invalid signature'}), 400
        except Exception as e:
            print(f'Webhook signature error: {e}')
            return jsonify({'error': 'Signature verification failed'}), 400

    try:
        event = json.loads(payload)
        event_type = event.get('type', '')
        print(f'Stripe webhook: {event_type}')

        # Payment succeeded — upgrade user to Pro
        if event_type in ('checkout.session.completed', 'invoice.payment_succeeded'):
            session  = event.get('data', {}).get('object', {})
            user_id  = session.get('metadata', {}).get('userId', '')
            customer = session.get('customer', '')

            if user_id:
                # Update Firebase via REST API
                firebase_url = f'https://firestore.googleapis.com/v1/projects/pricescout-2e8a3/databases/(default)/documents/users/{user_id}'
                update_data  = {
                    'fields': {
                        'plan':             {'stringValue': 'pro'},
                        'stripeCustomerId': {'stringValue': customer},
                        'upgradedAt':       {'timestampValue': __import__('datetime').datetime.utcnow().isoformat() + 'Z'}
                    }
                }
                r = requests.patch(
                    firebase_url,
                    json=update_data,
                    params={'updateMask.fieldPaths': ['plan', 'stripeCustomerId', 'upgradedAt']},
                    timeout=10
                )
                print(f'Firebase update status: {r.status_code} for user {user_id}')

        # Subscription cancelled — downgrade to free
        elif event_type in ('customer.subscription.deleted', 'invoice.payment_failed'):
            session  = event.get('data', {}).get('object', {})
            customer = session.get('customer', '')
            if customer:
                # Find user by stripeCustomerId
                query_url = 'https://firestore.googleapis.com/v1/projects/pricescout-2e8a3/databases/(default)/documents:runQuery'
                query = {'structuredQuery': {'from': [{'collectionId': 'users'}], 'where': {'fieldFilter': {'field': {'fieldPath': 'stripeCustomerId'}, 'op': 'EQUAL', 'value': {'stringValue': customer}}}}}
                r = requests.post(query_url, json=query, timeout=10)
                docs = r.json()
                for doc in docs:
                    doc_name = doc.get('document', {}).get('name', '')
                    if doc_name:
                        requests.patch(
                            f'https://firestore.googleapis.com/v1/{doc_name}',
                            json={'fields': {'plan': {'stringValue': 'free'}}},
                            params={'updateMask.fieldPaths': ['plan']},
                            timeout=10
                        )
                        print(f'Downgraded user to free: {doc_name}')

        return jsonify({'received': True})
    except Exception as e:
        print(f'Webhook error: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/')
def index():
    return send_from_directory('public', 'index.html')

@app.route('/privacy')
def privacy():
    return send_from_directory('public', 'privacy-policy.html')

@app.route('/terms')
def terms():
    return send_from_directory('public', 'terms-of-service.html')

@app.route('/delete-account')
def delete_account():
    return send_from_directory('public', 'delete-account.html')

@app.route('/api/delete-request', methods=['POST'])
def delete_request():
    body = request.get_json() or {}
    email = body.get('email', '')
    reason = body.get('reason', '')
    print(f'DELETION REQUEST — email: {email}, reason: {reason}')
    # In production you would email yourself or log to a database
    return jsonify({'received': True})
# ── Search cache (reduces API calls) ─────────────────────────────────────────
import time
_cache = {}
CACHE_TTL = 3600  # cache results for 1 hour

def cache_get(key):
    if key in _cache:
        data, ts = _cache[key]
        if time.time() - ts < CACHE_TTL:
            print(f'Cache hit: {key}')
            return data
        del _cache[key]
    return None

def cache_set(key, data):
    _cache[key] = (data, time.time())
    # Keep cache small — remove oldest if over 100 entries
    if len(_cache) > 100:
        oldest = min(_cache.keys(), key=lambda k: _cache[k][1])
        del _cache[oldest]

@app.route('/api/ebay/sold')
def ebay_sold():
    query = request.args.get('q', '').strip()
    limit = min(int(request.args.get('limit', 20)), 100)
    if not query:
        return jsonify({'error': 'q parameter required'}), 400

    # Check cache first
    cache_key = query.lower().strip()
    cached = cache_get(cache_key)
    if cached:
        return jsonify(cached)
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
        result = {
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
        }
        cache_set(cache_key, result)
        return jsonify(result)
    except Exception as e:
        print(f'RapidAPI error: {e} — trying eBay Finding API fallback')

    # ── Fallback: eBay Finding API ────────────────────────────────────────────
    try:
        from datetime import datetime, timedelta, timezone
        thirty_ago = (datetime.now(timezone.utc) - timedelta(days=30)).strftime('%Y-%m-%dT%H:%M:%S.000Z')
        params = {
            'OPERATION-NAME': 'findCompletedItems',
            'SERVICE-VERSION': '1.0.0',
            'SECURITY-APPNAME': EBAY_APP_ID,
            'RESPONSE-DATA-FORMAT': 'JSON',
            'keywords': query,
            'itemFilter(0).name': 'SoldItemsOnly',
            'itemFilter(0).value': 'true',
            'itemFilter(1).name': 'EndTimeFrom',
            'itemFilter(1).value': thirty_ago,
            'sortOrder': 'EndTimeSoonest',
            'paginationInput.entriesPerPage': str(limit),
            'GLOBAL-ID': 'EBAY-GB',
        }
        r = requests.get('https://svcs.ebay.com/services/search/FindingService/v1', params=params, timeout=10)
        print(f'eBay fallback status: {r.status_code}, length: {len(r.text)}, content: {r.text[:200]}')
        if not r.text.strip():
            print('eBay returned empty response')
            raise Exception('Empty response from eBay')
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
            avg = round(sum(prices)/len(prices), 2) if prices else 0
            total = int(raw.get('paginationOutput', [{}])[0].get('totalEntries', [len(listings)])[0])
            result = {
                'query': query, 'totalSold': total, 'listings': listings,
                'stats': {'count': len(listings), 'avg': avg,
                          'low': prices[0] if prices else 0,
                          'high': prices[-1] if prices else 0,
                          'median': prices[len(prices)//2] if prices else 0}
            }
            cache_set(cache_key, result)
            return jsonify(result)
    except Exception as e2:
        print(f'eBay fallback error: {e2}')

    return jsonify({'error': 'Search limit reached for today. Please try again tomorrow or upgrade to Pro for priority access.'}), 502
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
        payload = {
            'model': 'claude-haiku-4-5-20251001',
            'max_tokens': 200,
            'messages': [{
                'role': 'user',
                'content': [
                    {'type': 'image', 'source': {'type': 'base64', 'media_type': media_type, 'data': base64_data}},
                    {'type': 'text', 'text': 'Identify this product for eBay search. Reply ONLY with valid JSON no markdown: {"name":"brand model spec","category":"category"}'}
                ]
            }]
        }
        headers = {
            'x-api-key': ANTHROPIC_KEY,
            'anthropic-version': '2023-06-01',
            'content-type': 'application/json'
        }
        print(f'Calling Anthropic with key: configured')
        r = requests.post('https://api.anthropic.com/v1/messages', json=payload, headers=headers, timeout=30)
        print(f'Anthropic status: {r.status_code}')
        r.raise_for_status()
        text   = ''.join(c.get('text','') for c in r.json().get('content', []))
        parsed = json.loads(text.replace('```json','').replace('```','').strip())
        return jsonify(parsed)
    except Exception as e:
        print(f'AI identify error: {e}')
        return jsonify({'error': 'AI identification failed: ' + str(e)}), 502
@app.route('/api/vinted/search')
def vinted_search():
    query = request.args.get('q', '').strip()
    limit = min(int(request.args.get('limit', 20)), 50)
    if not query:
        return jsonify({'error': 'q parameter required'}), 400

    global _vinted_cookie

    headers = {
        'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'en-GB,en;q=0.9',
        'Referer': 'https://www.vinted.co.uk/',
        'Origin': 'https://www.vinted.co.uk',
    }

    # Try to get/refresh cookie
    if not _vinted_cookie:
        get_vinted_cookie()
    if _vinted_cookie:
        headers['Cookie'] = _vinted_cookie

    params = {
        'search_text': query,
        'per_page': str(limit),
        'order': 'newest_first',
        'currency': 'GBP',
    }

    try:
        r = requests.get(
            'https://www.vinted.co.uk/api/v2/catalog/items',
            headers=headers, params=params, timeout=10
        )

        # If blocked, refresh cookie and retry
        if r.status_code in (401, 403):
            print('Vinted cookie expired, refreshing...')
            get_vinted_cookie()
            if _vinted_cookie:
                headers['Cookie'] = _vinted_cookie
            r = requests.get('https://www.vinted.co.uk/api/v2/catalog/items', headers=headers, params=params, timeout=10)

        r.raise_for_status()
        data = r.json()
        raw_items = data.get('items', [])

        listings = []
        for item in raw_items[:limit]:
            price_raw = item.get('price', {})
            price = float(price_raw.get('amount', 0)) if isinstance(price_raw, dict) else float(price_raw or 0)
            photo = item.get('photo', {})
            img_url = ''
            if isinstance(photo, dict):
                img_url = photo.get('url', photo.get('full_size_url', ''))
            elif isinstance(photo, list) and photo:
                img_url = photo[0].get('url', '') if isinstance(photo[0], dict) else ''

            listings.append({
                'id':        str(item.get('id', '')),
                'title':     item.get('title', query),
                'price':     price,
                'currency':  'GBP',
                'condition': item.get('status', 'Unknown'),
                'brand':     item.get('brand_title', ''),
                'size':      item.get('size_title', ''),
                'url':       item.get('url', f'https://www.vinted.co.uk/items/{item.get("id","")}'),
                'imageUrl':  img_url,
                'location':  item.get('user', {}).get('location', 'United Kingdom') if isinstance(item.get('user'), dict) else 'United Kingdom',
                'favourites': item.get('favourite_count', 0),
            })

        prices = sorted([l['price'] for l in listings if l['price'] > 0])
        avg   = round(sum(prices)/len(prices), 2) if prices else 0
        low   = prices[0] if prices else 0
        high  = prices[-1] if prices else 0
        total = data.get('pagination', {}).get('total_count', len(listings))

        return jsonify({
            'query':    query,
            'total':    total,
            'listings': listings,
            'stats': {'count': len(listings), 'avg': avg, 'low': low, 'high': high}
        })

    except Exception as e:
        print(f'Vinted error: {e}')
        return jsonify({'error': 'Could not fetch Vinted listings: ' + str(e)}), 502


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
