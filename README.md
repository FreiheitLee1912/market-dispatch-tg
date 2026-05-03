# Market Dispatch · Telegram Bot

每天美股收盤後，自動推送整理過的市場摘要到 Telegram。**純文字訊息**，無圖片，載入瞬間。

## 訊息範例

```
🏛 The Closing Bell · 🟢 Risk On
Sat · 2026-05-02 · US market close

🇺🇸 Indices
🟢 S&P 500 · 7,230.12 · +1.32%
🟢 NASDAQ · 25,114.44 · +1.79%
🟢 Dow Jones · 49,499.27 · +1.30%

💱 FX & Vol
🔴 USD/JPY · 157.03 · -1.97%
🔵 VIX · 16.99 · +0.59% · Quiet

🔥 Sectors
🟢 Tech 💻 · +1.73% · Bullish
🔴 Energy 🛢 · -0.30% · Neutral

📰 Headlines
1. Fed signals patience on rate cuts...
   — CNBC · 2h
2. Tech rally lifts Nasdaq to record close...
   — Reuters · 3h
3. ...
4. ...

╭ 💬 Markets close in a broad rally with NASDAQ
╰   leading at +1.79%...
```

## 架設

1. **GitHub Secrets**（Settings → Secrets and variables → Actions）
   - `TELEGRAM_BOT_TOKEN` — 從 @BotFather
   - `TELEGRAM_CHAT_ID` — 從 @userinfobot
   - `ANTHROPIC_API_KEY`（可選）— 用來生成編輯室評論

2. **手動觸發**：Actions tab → Daily Market Dispatch → Run workflow

3. **自動排程**：每週一到週五 21:00 UTC = 06:00 JST 自動跑

## 排程

預設 06:00 JST。要改時間，編輯 `.github/workflows/daily.yml` 的 cron 欄位。

| JST 時間 | UTC cron |
|---|---|
| 06:00（預設）| `0 21 * * 1-5` |
| 07:00 | `0 22 * * 1-5` |
| 09:00 | `0 0 * * 2-6` |

## 檔案

```
market-dispatch-tg/
├── dispatch.py              主腳本
├── requirements.txt         Python 依賴
├── .github/workflows/
│   └── daily.yml           排程
└── README.md
```
