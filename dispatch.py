#!/usr/bin/env python3
"""
Market Dispatch — Stable Edition with Rich Format
==================================================
- yfinance with retry to handle close-data delay
- Lenient holiday detection (skips only weekends + 2+ day gaps)
- Rich Telegram HTML message: indices, FX, vol, commodities, sectors, headlines, editor's note
"""

import html
import os
import sys
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

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
    )["Close"]

    # Use S&P 500 as the anchor for "did US markets actually trade this day?"
    # FX trades 24/7, so a row might exist with only JPY=X data but no real US close.
    # Drop those phantom rows so we never report partial data as today's close.
    if "^GSPC" in df.columns:
        df = df.dropna(subset=["^GSPC"])
    else:
        df = df.dropna(how="all")

    if len(df) < 2:
        raise RuntimeError("Not enough data")

    prev_row, curr_row = df.iloc[-2], df.iloc[-1]
    out = {}
    for name, tkr in TICKERS.items():
        if tkr not in curr_row or curr_row[tkr] != curr_row[tkr]:  # NaN check
            print(f"  ⚠ no data for {name} ({tkr}), skipping")
            continue
        prev = float(prev_row[tkr])
        curr = float(curr_row[tkr])
        out[name] = {"level": curr, "change": (curr - prev) / prev * 100}

    return out, df.index[-1].date()


def fetch_with_retry(max_retry=2, wait_sec=60):
    """Fetch market data, with quick retry only on errors (not on stale data).
    Stale data handling is delegated to should_skip()."""
    last_err = None
    for i in range(max_retry):
        try:
            print(f"→ Fetch attempt {i+1}/{max_retry}")
            data, latest = fetch_market_data()
            today_et = datetime.now(ZoneInfo("America/New_York")).date()
            delta = (today_et - latest).days
            print(f"  ✓ Got close from {latest} (Δ {delta} days from today_ET)")
            return data, latest
        except Exception as e:
            last_err = e
            print(f"  ✗ Fetch failed: {e}")
            if i < max_retry - 1:
                print(f"  ⏳ Retrying in {wait_sec}s...")
                time.sleep(wait_sec)

    raise RuntimeError(f"All fetch attempts failed: {last_err}")


def should_skip(latest_close_date):
    """Skip only on real weekends or holidays (data 2+ days stale)."""
    today_et = datetime.now(ZoneInfo("America/New_York")).date()

    if today_et.weekday() >= 5:
        print(f"⚠ {today_et} is a weekend — skipping")
        return True

    delta = (today_et - latest_close_date).days
    if delta >= 2:
        print(f"⚠ Latest close {latest_close_date} is {delta} days old — likely holiday, skipping")
        return True

    return False


# ─── News ──────────────────────────────────────────────────────────
def fetch_headlines(max_items=4):
    print("→ Fetching headlines...")
    seen, items = set(), []

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
def vix_regime(v):
    if v < 15:  return "Calm 😌"
    if v < 20:  return "Quiet"
    if v < 30:  return "Elevated ⚠️"
    return "Stressed 🚨"


def sector_sentiment(c):
    if c > 1.0:  return "Bullish 🟢"
    if c < -1.0: return "Bearish 🔴"
    return "Neutral"


def write_closing_note(data, date_str):
    indices = {k: data[k] for k in ("sp500", "nasdaq", "dow") if k in data}
    if not indices:
        return "Markets close mixed."

    leader = max(indices, key=lambda k: indices[k]["change"])
    leader_name = {"sp500": "S&P 500", "nasdaq": "NASDAQ", "dow": "Dow Jones"}[leader]
    leader_chg = indices[leader]["change"]

    if not ANTHROPIC_KEY:
        tone = "broad rally" if leader_chg > 0.5 else ("broad decline" if leader_chg < -0.5 else "mixed session")
        return (f"Markets close in a {tone} with {leader_name} leading at {leader_chg:+.2f}%. "
                f"VIX at {data['vix']['level']:.2f}, USD/JPY at {data['usdjpy']['level']:.2f}.")

    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=ANTHROPIC_KEY)

        gold_line = f"• Gold:       {data['gold']['level']:.2f}    ({data['gold']['change']:+.2f}%)" if "gold" in data else ""
        oil_line  = f"• Oil (WTI):  {data['oil']['level']:.2f}    ({data['oil']['change']:+.2f}%)"  if "oil"  in data else ""

        prompt = f"""You are the editor of a daily financial newspaper called "The Closing Bell".

Today's US market close ({date_str}):
• S&P 500:    {data['sp500']['level']:.2f}  ({data['sp500']['change']:+.2f}%)
• NASDAQ:     {data['nasdaq']['level']:.2f} ({data['nasdaq']['change']:+.2f}%)
• Dow Jones:  {data['dow']['level']:.2f}    ({data['dow']['change']:+.2f}%)
• USD/JPY:    {data['usdjpy']['level']:.2f} ({data['usdjpy']['change']:+.2f}%)
• VIX:        {data['vix']['level']:.2f}    ({data['vix']['change']:+.2f}%)
• Tech XLK:   {data['tech']['change']:+.2f}%
• Energy XLE: {data['energy']['change']:+.2f}%
{gold_line}
{oil_line}

Write the closing note in exactly 2 sentences, max 50 words total.
Editorial newspaper voice — observational and dry, not breathless.
Mention the day's leader and one notable cross-asset signal (FX, vol, commodities, or sector divergence).
No emojis. No headers. No quotation marks. Just the prose."""

        msg = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip().strip('"').strip("'")
    except Exception as e:
        print(f"  ⚠ Claude API failed ({e}), using fallback note")
        tone = "broad rally" if leader_chg > 0.5 else ("broad decline" if leader_chg < -0.5 else "mixed session")
        return (f"Markets close in a {tone} with {leader_name} leading at {leader_chg:+.2f}%. "
                f"VIX at {data['vix']['level']:.2f}, USD/JPY at {data['usdjpy']['level']:.2f}.")


# ─── Telegram message ──────────────────────────────────────────────
def emoji_for(c):
    if c > 0:  return "🟢"
    if c < 0:  return "🔴"
    return "⚪"


def fmt_pct(n):
    return f"{n:+.2f}%"


def build_message(data, latest_close_date, headlines, note):
    """Build Telegram-flavored HTML message."""
    e = html.escape
    date_str = latest_close_date.strftime("%Y-%m-%d")
    day_str  = latest_close_date.strftime("%a")

    # Risk regime banner
    indices_chg = [data[k]["change"] for k in ("sp500", "nasdaq", "dow") if k in data]
    avg_chg = sum(indices_chg) / len(indices_chg) if indices_chg else 0
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
        if key not in data: continue
        d = data[key]
        lines.append(f"{emoji_for(d['change'])} <b>{name}</b> · <code>{d['level']:,.2f}</code> · <b>{fmt_pct(d['change'])}</b>")
    lines.append("")

    # FX & Vol
    lines.append("💱 <b>FX &amp; Vol</b>")
    if "usdjpy" in data:
        fx = data["usdjpy"]
        lines.append(f"{emoji_for(fx['change'])} <b>USD/JPY</b> · <code>{fx['level']:.2f}</code> · <b>{fmt_pct(fx['change'])}</b>")
    if "vix" in data:
        vix = data["vix"]
        lines.append(f"🔵 <b>VIX</b> · <code>{vix['level']:.2f}</code> · <b>{fmt_pct(vix['change'])}</b> · <i>{vix_regime(vix['level'])}</i>")
    lines.append("")

    # Commodities
    if "gold" in data or "oil" in data:
        lines.append("🪙 <b>Commodities</b>")
        if "gold" in data:
            g = data["gold"]
            lines.append(f"{emoji_for(g['change'])} <b>Gold</b> · <code>{g['level']:,.2f}</code> · <b>{fmt_pct(g['change'])}</b>")
        if "oil" in data:
            o = data["oil"]
            lines.append(f"{emoji_for(o['change'])} <b>Crude Oil (WTI)</b> · <code>{o['level']:.2f}</code> · <b>{fmt_pct(o['change'])}</b>")
        lines.append("")

    # Sectors
    if "tech" in data or "energy" in data:
        lines.append("🔥 <b>Sectors</b>")
        for label, key in [("Tech 💻","tech"), ("Energy 🛢","energy")]:
            if key not in data: continue
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
        print("⚠ Telegram credentials missing — printing locally:")
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
    print("┌" + "─" * 50 + "┐")
    print("│  Market Dispatch · Stable + Rich Edition        │")
    print("└" + "─" * 50 + "┘")

    data, latest_close_date = fetch_with_retry()

    if should_skip(latest_close_date):
        return

    headlines = fetch_headlines(max_items=4)
    print(f"  Got {len(headlines)} headlines")

    date_str = latest_close_date.strftime("%Y-%m-%d")
    note = write_closing_note(data, date_str)
    print(f"  Note: {note}")

    message = build_message(data, latest_close_date, headlines, note)
    send_telegram(message)
    print("✓ Done.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"✗ Failed: {e}", file=sys.stderr)
        sys.exit(1)
