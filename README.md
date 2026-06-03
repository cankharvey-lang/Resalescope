# PriceScout 🏷️
**eBay sold listings price checker — no AI needed**

Scan a barcode or search a product name to instantly see what it sold for on eBay in the last 30 days.

---

## Setup (Node.js)

```bash
npm install
node server.js
# Open http://localhost:3000
```

## Setup (Python)

```bash
pip install flask flask-cors requests
python server.py
# Open http://localhost:3000
```

Your eBay App ID is already built in:
`HarveyCa-PriceSco-PRD-88b00c500-2ca7bf20`

If you ever need to change it, edit line 11 of server.js:
```js
const EBAY_APP_ID = 'your-new-app-id-here';
```

---

## Deploy online (free — Railway)

1. Go to https://railway.app and sign up
2. Click **New Project → Deploy from GitHub**
3. Upload this folder or connect your GitHub repo
4. Railway auto-detects Node.js and runs `npm start`
5. Copy your Railway URL (e.g. `https://pricescout.railway.app`)
6. Update `API` in `public/index.html` line 2:
   ```js
   const API = 'https://pricescout.railway.app';
   ```
7. Upload the updated `index.html` to AppGeyser

---

## Features
- ✅ Barcode scanning (camera, real-time)
- ✅ Manual product name search
- ✅ Upload photo (then type product name to search)
- ✅ Average sold price, units sold, low/high
- ✅ Price range bar
- ✅ Last 12 sold listings with images, dates, condition
- ✅ Direct link to eBay sold listings page
- ✅ Works on mobile & desktop
- ✅ Dark mode support
