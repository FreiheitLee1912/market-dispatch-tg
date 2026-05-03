#!/usr/bin/env python3
"""
Market Dispatch — Daily US Market Telegram Bot
==============================================
Sends a clean Telegram-formatted text message every weekday morning JST.

Pipeline:
  1. yfinance   → fetch latest US market closes
  2. RSS feeds  → CNBC / MarketWatch / Reuters top headlines
  3. Claude API → editorial closing note (optional, falls back to template)
  4. Telegram   → sendMessage with HTML formatting
"""

import html
import os
import sys
from datetime import datetime, timezone

import feedparser
import requests
import yfinance as yf

# ─── Config ────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT  = os.environ.get("TELEGRAM_CHAT_ID")
ANTHROPIC_KEY  = os.environ.get("ANTHROPIC_API_KEY")
CLAUDE_MODEL   = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")

TICKERS = {
    "sp500":  "^GSPC",
    "nasdaq": "^IXIC",
    "dow":    "^DJI",
    "vix":    "^VIX",
    "usdjpy": "JPY=X",
    "tech":   "XLK",
    "energy": "XLE",
}

NEWS_FEEDS = [
    ("CNBC",        "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("Reuters",     "https://feeds.reuters.com/reuters/businessNews"),
]


# ─── Data ──────────────────────────────────────────────────────────
def fetch_market_data():
    print("→ Fetching market data...")
    df = yf.download(
        list(TICKERS.values()),
        period="7d",
        auto_adjust=False,
        progress=False,
    )["Close"].dropna(how="all")

    if len(df) < 2:
        raise RuntimeError(f"Not enough data: only {len(df)} rows")

    prev_row, curr_row = df.iloc[-2], df.iloc[-1]
    out = {}
    for name, tkr in TICKERS.items():
        prev = float(prev_row[tkr])
        curr = float(curr_row[tkr])
        out[name] = {"level": curr, "change": (curr - prev) / prev * 100}

    return out, df.index[-1].strftime("%Y-%m-%d"), df.index[-1].strftime("%a")


def vix_regime(v):
    if v < 15:  return "Calm 😌"
    if v < 20:  return "Quiet"
    if v < 30:  return "Elevated ⚠️"
    return "Stressed 🚨"


def sector_sentiment(change):
    if change > 1.0:  return "Bullish 🟢"
    if change < -1.0: return "Bearish 🔴"
    return "Neutral"


# ─── News ──────────────────────────────────────────────────────────
def fetch_headlines(max_items=4):
    print("→ Fetching headlines...")
    seen = set()
    items = []

    for source, url in NEWS_FEEDS:
        try:
            feed = feedparser.parse(url, request_headers={"User-Agent": "Mozilla/5.0"})
            for entry in feed.entries[:6]:
                title = (entry.get("title") or "").strip()
                if not title or title.lower() in seen:
                    continue
                seen.add(title.lower())

                pub = entry.get("published_parsed") or entry.get("updated_parsed")
                age = ""
                if pub:
                    delta = datetime.now(timezone.utc) - datetime(*pub[:6], tzinfo=timezone.utc)
                    h = int(delta.total_seconds() / 3600)
                    age = f"{int(delta.total_seconds()/60)}m" if h < 1 else (f"{h}h" if h < 24 else f"{h//24}d")

                items.append({
                    "title":  title[:140],
                    "url":    entry.get("link", ""),
                    "source": source,
                    "age":    age,
                })
                if len(items) >= max_items:
                    return items
        except Exception as e:
            print(f"  ⚠ {source} feed failed: {e}")

    return items


# ─── Closing note ──────────────────────────────────────────────────
def write_closing_note(data, date_str):
    indices = {k: data[k] for k in ("sp500", "nasdaq", "dow")}
    leader = max(indices, key=lambda k: indices[k]["change"])
    leader_name = {"sp500": "S&P 500", "nasdaq": "NASDAQ", "dow": "Dow Jones"}[leader]
    leader_chg = indices[leader]["change"]

    if not ANTHROPIC_KEY:
        tone = "broad rally" if leader_chg > 0.5 else ("broad decline" if leader_chg < -0.5 else "mixed session")
        return (f"Markets close in a {tone} with {leader_name} leading at {leader_chg:+.2f}%. "
                f"VIX at {data['vix']['level']:.2f}, USD/JPY at {data['usdjpy']['level']:.2f}.")

    from anthropic import Anthropic
    client = Anthropic(api_key=ANTHROPIC_KEY)
    prompt = f"""You are the editor of a daily financial newspaper called "The Closing Bell".

Today's US market close ({date_str}):
• S&P 500:    {data['sp500']['level']:.2f}  ({data['sp500']['change']:+.2f}%)
• NASDAQ:     {data['nasdaq']['level']:.2f} ({data['nasdaq']['change']:+.2f}%)
• Dow Jones:  {data['dow']['level']:.2f}    ({data['dow']['change']:+.2f}%)
• USD/JPY:    {data['usdjpy']['level']:.2f} ({data['usdjpy']['change']:+.2f}%)
• VIX:        {data['vix']['level']:.2f}    ({data['vix']['change']:+.2f}%)
• Tech XLK:   {data['tech']['change']:+.2f}%
• Energy XLE: {data['energy']['change']:+.2f}%

Write the closing note in exactly 2 sentences, max 45 words total.
Editorial newspaper voice — observational and dry, not breathless.
Mention the day's leader and one notable cross-market signal.
No emojis. No headers. No quotation marks. Just the prose."""

    msg = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip().strip('"').strip("'")


# ─── Format Telegram message ───────────────────────────────────────
def emoji_for(change):
    if change > 0:  return "🟢"
    if change < 0:  return "🔴"
    return "⚪"


def fmt_pct(n):
    return f"{n:+.2f}%"


def build_message(data, date_str, day_str, headlines, note):
    """Returns Telegram-flavored HTML — optimized for mobile rendering."""
    e = html.escape

    avg_chg = sum(data[k]["change"] for k in ("sp500", "nasdaq", "dow")) / 3
    if avg_chg > 0.5:    tone = "🟢 Risk On"
    elif avg_chg < -0.5: tone = "🔴 Risk Off"
    else:                tone = "⚪ Mixed"

    lines = []

    # Header
    lines.append(f"🏛 <b>The Closing Bell</b> · {tone}")
    lines.append(f"<i>{day_str} · {date_str} · US market close</i>")
    lines.append("")

    # Indices
    lines.append("🇺🇸 <b>Indices</b>")
    for key, name in [("sp500","S&P 500"), ("nasdaq","NASDAQ"), ("dow","Dow Jones")]:
        d = data[key]
        em = emoji_for(d["change"])
        lines.append(f"{em} <b>{name}</b> · <code>{d['level']:,.2f}</code> · <b>{fmt_pct(d['change'])}</b>")
    lines.append("")

    # FX & Volatility
    lines.append("💱 <b>FX &amp; Vol</b>")
    fx = data["usdjpy"]
    lines.append(f"{emoji_for(fx['change'])} <b>USD/JPY</b> · <code>{fx['level']:.2f}</code> · <b>{fmt_pct(fx['change'])}</b>")
    vix = data["vix"]
    lines.append(f"🔵 <b>VIX</b> · <code>{vix['level']:.2f}</code> · <b>{fmt_pct(vix['change'])}</b> · <i>{vix_regime(vix['level'])}</i>")
    lines.append("")

    # Sectors
    lines.append("🔥 <b>Sectors</b>")
    for label, key in [("Tech 💻","tech"), ("Energy 🛢","energy")]:
        chg = data[key]["change"]
        lines.append(f"{emoji_for(chg)} <b>{label}</b> · <b>{fmt_pct(chg)}</b> · <i>{sector_sentiment(chg)}</i>")
    lines.append("")

    # Headlines
    if headlines:
        lines.append("📰 <b>Headlines</b>")
        for i, h in enumerate(headlines, 1):
            title = e(h["title"])
            url   = h["url"]
            src   = e(h["source"])
            age   = h.get("age", "")
            meta  = f"{src} · {age}" if age else src

            if url:
                lines.append(f"<b>{i}.</b> <a href=\"{e(url)}\">{title}</a>")
            else:
                lines.append(f"<b>{i}.</b> {title}")
            lines.append(f"   <i>— {meta}</i>")
        lines.append("")

    # Editor's Note
    lines.append(f"<blockquote>💬 {e(note)}</blockquote>")

    return "\n".join(lines)


# ─── Telegram ──────────────────────────────────────────────────────
def send_telegram(message):
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT):
        print("⚠ Telegram credentials missing — printing locally only:")
        print("─" * 60)
        print(message)
        print("─" * 60)
        return

    print("→ Sending to Telegram via sendMessage...")
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    r = requests.post(url, data={
        "chat_id": TELEGRAM_CHAT,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }, timeout=30)
    if not r.ok:
        print(f"✗ Telegram error {r.status_code}: {r.text}")
        r.raise_for_status()
    print(f"✓ Sent to chat {TELEGRAM_CHAT}")


# ─── Main ──────────────────────────────────────────────────────────
def main():
    print("┌" + "─" * 48 + "┐")
    print("│  Market Dispatch · Telegram Text Edition       │")
    print("└" + "─" * 48 + "┘")

    data, date_str, day_str = fetch_market_data()
    print(f"  Latest close: {date_str} ({day_str})")

    headlines = fetch_headlines(max_items=4)
    print(f"  Got {len(headlines)} headlines")

    note = write_closing_note(data, date_str)
    print(f"  Note: {note}")

    message = build_message(data, date_str, day_str, headlines, note)
    send_telegram(message)
    print("✓ Done.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"✗ Failed: {e}", file=sys.stderr)
        sys.exit(1)
