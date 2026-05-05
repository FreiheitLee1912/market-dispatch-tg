#!/usr/bin/env python3
"""
Market Dispatch — Stable Edition
Fixes:
- Handles yfinance delay with retry
- Avoids false "not trading day"
- Skips only real weekends/holidays
"""

import html
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import feedparser
import requests
import yfinance as yf

# ─── Config ────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT  = os.environ.get("TELEGRAM_CHAT_ID")

TICKERS = {
    "sp500":  "^GSPC",
    "nasdaq": "^IXIC",
    "dow":    "^DJI",
    "vix":    "^VIX",
    "usdjpy": "JPY=X",
    "tech":   "XLK",
    "energy": "XLE",
    "gold":   "GC=F",
    "oil":    "CL=F",
}

NEWS_FEEDS = [
    ("CNBC",        "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("Reuters",     "https://feeds.reuters.com/reuters/businessNews"),
]

# ─── Market Data ───────────────────────────────────────────────────
def fetch_market_data():
    df = yf.download(
        list(TICKERS.values()),
        period="7d",
        auto_adjust=False,
        progress=False,
    )["Close"].dropna(how="all")

    if len(df) < 2:
        raise RuntimeError("Not enough data")

    prev_row, curr_row = df.iloc[-2], df.iloc[-1]
    out = {}

    for name, tkr in TICKERS.items():
        if tkr not in curr_row or curr_row[tkr] != curr_row[tkr]:
            continue
        prev = float(prev_row[tkr])
        curr = float(curr_row[tkr])
        out[name] = {"level": curr, "change": (curr - prev) / prev * 100}

    latest_close_date = df.index[-1].date()
    return out, latest_close_date


# ─── Retry wrapper ─────────────────────────────────────────────────
def fetch_with_retry(max_retry=4, wait_sec=600):
    """
    Retry until we get today's US close or give up.
    """
    today_et = datetime.now(ZoneInfo("America/New_York")).date()

    for i in range(max_retry):
        print(f"→ Fetch attempt {i+1}/{max_retry}")
        data, latest = fetch_market_data()

        delta = (today_et - latest).days

        if delta == 0:
            print("✓ Got today's close")
            return data, latest

        if delta == 1:
            print("⚠ Data not updated yet (lagging 1 day)")

        if i < max_retry - 1:
            print(f"⏳ Waiting {wait_sec//60} minutes...")
            time.sleep(wait_sec)

    print("⚠ Using latest available data")
    return data, latest


# ─── Trading Day Check ─────────────────────────────────────────────
def should_skip(latest_close_date):
    today_et = datetime.now(ZoneInfo("America/New_York")).date()

    # Weekend
    if today_et.weekday() >= 5:
        print("⚠ Weekend — skipping")
        return True

    # If data is too old → likely holiday
    if (today_et - latest_close_date).days >= 2:
        print("⚠ Holiday or no recent data — skipping")
        return True

    return False


# ─── News ──────────────────────────────────────────────────────────
def fetch_headlines(max_items=4):
    items, seen = [], set()

    for source, url in NEWS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:6]:
                title = (entry.get("title") or "").strip()
                if not title or title.lower() in seen:
                    continue
                seen.add(title.lower())

                items.append({
                    "title": title[:140],
                    "url": entry.get("link", ""),
                    "source": source,
                })
                if len(items) >= max_items:
                    return items
        except:
            continue

    return items


# ─── Message ───────────────────────────────────────────────────────
def build_message(data, date_str):
    def emoji(c): return "🟢" if c > 0 else ("🔴" if c < 0 else "⚪")

    lines = []
    lines.append(f"🏛 <b>The Closing Bell</b>")
    lines.append(f"<i>{date_str} · US market close</i>\n")

    for k, name in [("sp500","S&P 500"), ("nasdaq","NASDAQ"), ("dow","Dow")]:
        d = data[k]
        lines.append(f"{emoji(d['change'])} {name}: {d['level']:.2f} ({d['change']:+.2f}%)")

    lines.append("")
    vix = data["vix"]
    lines.append(f"VIX: {vix['level']:.2f}")

    return "\n".join(lines)


# ─── Telegram ──────────────────────────────────────────────────────
def send(msg):
    if not TELEGRAM_TOKEN:
        print(msg)
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, data={
        "chat_id": TELEGRAM_CHAT,
        "text": msg,
        "parse_mode": "HTML"
    })


# ─── Main ──────────────────────────────────────────────────────────
def main():
    print("=== Market Dispatch (Stable) ===")

    data, latest_close_date = fetch_with_retry()

    if should_skip(latest_close_date):
        return

    date_str = latest_close_date.strftime("%Y-%m-%d")
    msg = build_message(data, date_str)

    send(msg)
    print("✓ Sent")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("✗ Failed:", e)
        sys.exit(1)
