# India-Trading EOD Signals

A tiny, free, self-updating dashboard of end-of-day trading signals for an NSE watchlist.

- **`generate_digest.py`** — fetches daily prices/volume via `yfinance`, computes breakout / stop / approaching signals against the levels in `watchlist.json`, and renders `docs/index.html`.
- **GitHub Actions** (`.github/workflows/digest.yml`) runs it every weekday at **10:15 UTC / 15:45 IST** (after NSE close), commits the page, and GitHub Pages serves it.
- **Signals only** — no cost basis, quantities, or portfolio value are stored or shown (the page is public).

## Update the fetch list
Edit `watchlist.json` (add/remove tickers; adjust `pivot` / `stop` / `trigger` levels) and commit. Levels are static snapshots from the last screen — refresh periodically.

## Run locally
```bash
pip install -r requirements.txt
python generate_digest.py   # writes docs/index.html
```

*Educational signals from public price data — not investment advice. Patterns fail; always use a stop and confirm volume before acting.*
