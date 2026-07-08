#!/usr/bin/env python3
"""
Tiny local proxy for the TAPE app.
Fetches news directly from Yahoo Finance via the `yfinance` package and
serves it to the browser in the shape tape.html expects. Runs entirely
locally — no API key needed.

Setup:
    pip install yfinance
Usage:
    python3 proxy.py
Then open tape.html (see README note in the app) and it will talk to
this proxy on port 8787.
"""
import json
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler, HTTPServer

import yfinance as yf

PORT = 8787

# Used when no tickers are given, to build a general "market wire".
DEFAULT_WATCHLIST = [
    "^GSPC", "^DJI", "^IXIC", "AAPL", "MSFT", "NVDA", "TSLA",
    "AMZN", "GOOGL", "META",
]


def _iso_from_unix(ts):
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return None


def normalize_item(raw, requested_ticker):
    """Map a yfinance news item (new or old schema) onto the fields
    tape.html reads: id, title, description, publisher.name,
    published_utc, article_url, tickers, insights."""
    content = raw.get("content") if isinstance(raw.get("content"), dict) else None

    if content:
        title = content.get("title") or ""
        description = content.get("summary") or content.get("description") or ""
        provider = content.get("provider") or {}
        publisher_name = provider.get("displayName") or "Yahoo Finance"
        canonical = content.get("canonicalUrl") or {}
        click = content.get("clickThroughUrl") or {}
        article_url = canonical.get("url") or click.get("url") or ""
        pub_date = content.get("pubDate") or content.get("displayTime")
        published_utc = pub_date or _iso_from_unix(raw.get("providerPublishTime"))
        item_id = raw.get("id") or content.get("id") or str(uuid.uuid4())
    else:
        # older yfinance schema (flat dict)
        title = raw.get("title") or ""
        description = raw.get("summary") or ""
        publisher_name = raw.get("publisher") or "Yahoo Finance"
        article_url = raw.get("link") or ""
        published_utc = _iso_from_unix(raw.get("providerPublishTime"))
        item_id = raw.get("uuid") or str(uuid.uuid4())

    tickers = raw.get("relatedTickers") or (content or {}).get("relatedTickers") or []
    if not tickers and requested_ticker:
        tickers = [requested_ticker]

    return {
        "id": item_id,
        "title": title,
        "description": description,
        "publisher": {"name": publisher_name},
        "published_utc": published_utc or datetime.now(timezone.utc).isoformat(),
        "article_url": article_url,
        "tickers": tickers,
        "insights": [],  # yfinance doesn't provide sentiment; feed renders neutral dots
    }


def fetch_news_for(ticker):
    try:
        items = yf.Ticker(ticker).news or []
    except Exception as e:
        print(f"[proxy] error fetching news for {ticker}: {e}")
        return []
    return [normalize_item(raw, ticker) for raw in items]


class ProxyHandler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != "/news":
            self.send_response(404)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"unknown path, expected /news"}')
            return

        qs = parse_qs(parsed.query)
        tickers_param = (qs.get("tickers") or [""])[0]
        tickers = [t.strip().upper() for t in tickers_param.split(",") if t.strip()]
        if not tickers:
            tickers = DEFAULT_WATCHLIST

        try:
            merged = {}
            for t in tickers:
                for article in fetch_news_for(t):
                    merged[article["id"]] = article
            results = sorted(
                merged.values(),
                key=lambda a: a["published_utc"],
                reverse=True,
            )
            body = json.dumps({"results": results}).encode()
            status = 200
        except Exception as e:
            body = json.dumps({"error": f"proxy error: {e}"}).encode()
            status = 502

        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print("[proxy]", fmt % args)


if __name__ == "__main__":
    print(f"TAPE proxy running on http://localhost:{PORT} (serving news via yfinance)")
    HTTPServer(("localhost", PORT), ProxyHandler).serve_forever()
