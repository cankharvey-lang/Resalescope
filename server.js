/**
 * PriceScout — Node.js Backend
 * ─────────────────────────────
 * npm install express cors axios dotenv
 * node server.js
 */

require('dotenv').config();
const express = require('express');
const cors    = require('cors');
const axios   = require('axios');

const app  = express();
const PORT = process.env.PORT || 3000;

app.use(cors());
app.use(express.json());
app.use(express.static('public'));

const EBAY_APP_ID = process.env.EBAY_APP_ID || 'HarveyCa-PriceSco-PRD-88b00c500-2ca7bf20';

// ─── Health ───────────────────────────────────────────────────────────────────
app.get('/health', (_, res) => res.json({ status: 'ok', ebay: !!EBAY_APP_ID }));

// ─── eBay Sold Listings ───────────────────────────────────────────────────────
app.get('/api/ebay/sold', async (req, res) => {
  const query = (req.query.q || '').trim();
  const limit = Math.min(parseInt(req.query.limit) || 20, 100);
  if (!query) return res.status(400).json({ error: 'q parameter required' });

  const thirtyDaysAgo = new Date(Date.now() - 30 * 24 * 60 * 60 * 1000)
    .toISOString().split('.')[0] + '.000Z';

  try {
    const response = await axios.get(
      'https://svcs.ebay.com/services/search/FindingService/v1',
      {
        params: {
          'OPERATION-NAME':                 'findCompletedItems',
          'SERVICE-VERSION':                '1.0.0',
          'SECURITY-APPNAME':               EBAY_APP_ID,
          'RESPONSE-DATA-FORMAT':           'JSON',
          'REST-PAYLOAD':                   '',
          'keywords':                       query,
          'itemFilter(0).name':             'SoldItemsOnly',
          'itemFilter(0).value':            'true',
          'itemFilter(1).name':             'EndTimeFrom',
          'itemFilter(1).value':            thirtyDaysAgo,
          'sortOrder':                      'EndTimeSoonest',
          'paginationInput.entriesPerPage': limit,
        },
        timeout: 10000
      }
    );

    const raw = response.data?.findCompletedItemsResponse?.[0];
    const ack = raw?.ack?.[0];

    if (ack !== 'Success' && ack !== 'Warning') {
      const msg = raw?.errorMessage?.[0]?.error?.[0]?.message?.[0] || 'Unknown eBay error';
      return res.status(502).json({ error: msg });
    }

    const items = raw?.searchResult?.[0]?.item || [];

    const listings = items.map(item => {
      const priceBlock = item.sellingStatus?.[0]?.convertedCurrentPrice?.[0];
      return {
        id:        item.itemId?.[0],
        title:     item.title?.[0],
        price:     parseFloat(priceBlock?.['__value__'] || 0),
        currency:  priceBlock?.['@currencyId'] || 'GBP',
        condition: item.condition?.[0]?.conditionDisplayName?.[0] || 'Unknown',
        soldDate:  item.listingInfo?.[0]?.endTime?.[0],
        url:       item.viewItemURL?.[0],
        imageUrl:  item.galleryURL?.[0] || '',
        location:  item.location?.[0] || '',
      };
    });

    const prices = listings.map(l => l.price).filter(p => p > 0);
    const avg    = prices.length ? prices.reduce((a, b) => a + b, 0) / prices.length : 0;
    const sorted = [...prices].sort((a, b) => a - b);

    res.json({
      query,
      totalSold: parseInt(raw?.paginationOutput?.[0]?.totalEntries?.[0] || listings.length),
      listings,
      stats: {
        count:  listings.length,
        avg:    +avg.toFixed(2),
        low:    +(sorted[0] || 0).toFixed(2),
        high:   +(sorted[sorted.length - 1] || 0).toFixed(2),
        median: +(sorted[Math.floor(sorted.length / 2)] || 0).toFixed(2),
      }
    });

  } catch (err) {
    console.error('eBay error:', err.message);
    res.status(502).json({ error: 'eBay API failed: ' + err.message });
  }
});

// ─── Barcode Lookup (free, no key needed) ────────────────────────────────────
app.get('/api/barcode/:code', async (req, res) => {
  const { code } = req.params;

  // 1. Open Food Facts (food/grocery)
  try {
    const r = await axios.get(
      `https://world.openfoodfacts.org/api/v0/product/${code}.json`,
      { timeout: 5000 }
    );
    if (r.data?.status === 1) {
      const p = r.data.product;
      const name = p.product_name || p.product_name_en || '';
      if (name) return res.json({ name: `${p.brands ? p.brands + ' ' : ''}${name}`.trim(), category: 'Food & Grocery', source: 'OpenFoodFacts' });
    }
  } catch (_) {}

  // 2. UPC Item DB (electronics/general, 100 req/day free)
  try {
    const r = await axios.get(
      `https://api.upcitemdb.com/prod/trial/lookup?upc=${code}`,
      { timeout: 5000 }
    );
    if (r.data?.items?.length) {
      const item = r.data.items[0];
      if (item.title) return res.json({ name: item.title, brand: item.brand || '', category: item.category || 'General', source: 'UPCItemDB' });
    }
  } catch (_) {}

  // 3. Return raw code so user can search manually
  res.json({ name: code, category: 'General', source: 'raw' });
});

app.listen(PORT, () => {
  console.log(`\n✅  PriceScout running → http://localhost:${PORT}`);
  console.log(`    eBay App ID: ${EBAY_APP_ID ? EBAY_APP_ID.slice(0, 20) + '…' : '❌ MISSING'}\n`);
});
