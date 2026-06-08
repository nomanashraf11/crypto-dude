# 🚀 START HERE — Crypto Trading Command Center

**This is the home directory for everything.** Run all commands from here:
```bash
cd ~/git/crypto-alert
```

---

## ⚡ Quick Commands

### OKX (primary — $1,481, EU account)
```bash
python3 okx_order.py orders              # show position + TP/SL
python3 okx_order.py balance             # USDT balance
python3 okx_order.py enter BTC buy 0.03 61600 62900 60790   # coin side size entry TP SL (one-shot)
python3 okx_order.py oco 62900 60790     # set TP + SL in one OCO order
python3 okx_order.py tp 63000            # update TP
python3 okx_order.py sl be               # SL to breakeven
python3 okx_order.py sl trail 3%         # server-side trailing stop
```

### Kraken (secondary)
```bash
python3 kraken_order.py orders           # show position
python3 kraken_order.py tp 1620          # update TP
python3 kraken_order.py sl trail 29      # trail SL
```

### Alerts bot (Pushover + Telegram, runs on Railway)
```bash
python3 bot.py list                      # show active alerts
python3 bot.py add BTC 60500 below "note"   # add alert
python3 bot.py test                      # test Pushover ring
# After ANY alert change → MUST push (Railway watches GitHub):
git add alerts.json && git commit -m "update alerts" && git push origin main
```

---

## 📂 Files

| File | What |
|------|------|
| `okx_order.py` | OKX order manager — enter/oco/tp/sl/trail. Has the $1,606 guard. |
| `kraken_order.py` | Kraken order manager |
| `bot.py` | Price-alert bot (Pushover emergency rings + Telegram) |
| `alerts.json` | Active price alerts — **push to GitHub after every edit** |
| `RULES.md` | All trading order rules (TP=limit, SL, mark trigger, OCO, $1,606 guard) — research-backed |
| `structure.py` | SMC structure logic (BOS, CHOCH, order blocks, FVG) |
| `trade_log.py` / `trades.json` | Trade journaling |
| `dashboard.py` / `dashboard_server.py` | Trade dashboard |

---

## 🔑 Where secrets live (NOT in this repo)

- **OKX keys** → macOS Keychain (`security find-generic-password -a okx_futures`) — read by `okx_order.py`
- **Kraken keys** → macOS Keychain (`-a kraken_futures`) — read by `kraken_order.py`
- **OKX MCP config** → `~/.okx/config.toml` (read-only key, EEA site)
- **Bot tokens** (Pushover/Telegram) → Railway env vars

---

## 🛠️ Tools available in Claude Code

- **TradingView MCP** — `coin_analysis`, `multi_timeframe_analysis` (structure, RSI, ATR)
- **OKX MCP** (read-only) — account/positions/market data. NOTE: Smart Money + news are region-blocked on EU (404). Only account + market work.
- **crypto-trade-prompts skill** — auto-runs full analysis when you say "check [COIN]"
- **Liq data** — OKX public API script (long/short liquidations) — built into the skill

---

## 📌 How to resume next session

Just open Claude Code in this folder and say what you want:
- *"check BTC"* / *"check HYPE"* → full multi-TF + liq analysis
- *"what are my open alerts?"* → reads alerts.json
- *"show my OKX position"* → live via MCP or `okx_order.py orders`

Current watches are always in **`alerts.json`** — that's the source of truth for what we're waiting on.
