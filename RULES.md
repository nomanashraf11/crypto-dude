# Trading Rules — Verified by Research
> Built from live trading experience + Kraken official API docs + professional trading research.
> Every rule here has a reason. Don't break them without understanding why.

---

## ORDER EXECUTION RULES

### Rule 1 — TP must always be `take_profit` with `limitPrice`
- **Order type:** `take_profit` (NOT `lmt`, NOT `mkt`)
- **Required fields:** `stopPrice` + `limitPrice` + `triggerSignal: mark` + `reduceOnly: true`
- **limitPrice formula:** `stopPrice × 0.999` (0.1% below trigger)
- **Why:** Plain `lmt` sits visible on the book — market makers see it and front-run it. `take_profit` stays hidden until triggered.
- **Source:** Kraken docs confirm "only take_profit_limit is currently enabled" — it's the only real TP option.

```python
# Correct TP
{
  "orderType":     "take_profit",
  "stopPrice":     1620,
  "limitPrice":    1618.38,   # stopPrice × 0.999
  "triggerSignal": "mark",
  "reduceOnly":    True,
  "side":          "sell"
}
```

---

### Rule 2 — SL must always be stop-market (NO limitPrice)
- **Order type:** `stp` with NO `limitPrice`
- **Required fields:** `stopPrice` + `triggerSignal: mark` + `reduceOnly: true`
- **Why:** Stop-limit on SL can go UNFILLED in a flash crash. ETH drops $1,488 → $1,400 in 2 seconds — your limitPrice $1,480 never appears, you stay long and bleed. Stop-market has 1% collar = worst fill is $1,473, but you ARE out.
- **Slippage on SL:** max 1% (Kraken collar). Acceptable. Not filling = catastrophic.

```python
# Correct SL
{
  "orderType":     "stp",
  "stopPrice":     1488,
  "triggerSignal": "mark",
  "reduceOnly":    True,
  "side":          "sell"
}
```

---

### Rule 3 — ALWAYS edit stopPrice AND limitPrice together
- **Never edit only one field on a TP order.**
- If you change `stopPrice`, you MUST also recalculate and send `limitPrice`.
- **Why this matters:** The $1,606 fill bug — TP stopPrice was moved to $1,620, limitPrice stayed at $1,606 from the original order. Triggered at $1,620, filled at $1,606. $14 slippage because we forgot to update limitPrice.
- **Rule:** `limitPrice` always = `stopPrice × 0.999`. Recalculate every time.

---

### Rule 4 — Always use `triggerSignal: mark` (never `last`)
- **Why:** `last` price = single trade on Kraken's book. Can be manipulated or moved by one big order. `mark` price = derived from CME CF Index basket of spot exchanges — manipulation-resistant.
- **Kraken's own warning:** "If stop-loss uses Last Price as trigger, a minor Last Price deviation could trigger your stop before the Mark Price even gets close."
- `mark` is also the same price Kraken uses to calculate liquidations — your SL and TP should align with that.

---

### Rule 5 — Always use `reduceOnly: true` on all TP/SL orders
- **Why:** Without it, if you close your position manually and forget to cancel the TP/SL, the order will OPEN A NEW POSITION in the opposite direction.
- `reduceOnly: true` = order can only close/reduce. Never opens. Never flips.
- Kraken docs: standalone `take_profit` orders are NOT reduce-only by default. Must set explicitly.

---

## TRADE MANAGEMENT RULES

### Rule 6 — Never move SL without a structural reason
Only valid reasons to move SL:
- A key support level has structurally broken
- A new wick formed a new liquidity zone below
- Trade thesis is invalidated

NOT valid reasons:
- "Price is near SL and I'm nervous"
- "Let me give it more room"
- Any emotional reason

---

### Rule 7 — Multiple TP levels (partial exits)
- TP1: 80% of position at first resistance
- TP2: 20% at next structural level
- Each TP is a separate order with correct `take_profit` setup
- When TP1 fires — immediately update SL size to match remaining position

---

### Rule 8 — Check liq data BEFORE every entry
Run OKX liq script + Binance positioning before entering:
- Long liqs firing at current price → DO NOT enter long
- L/S ratio > 2.0 (67%+ longs) → crowded, reduce size
- Taker B/S < 0.8 → sellers aggressive, wait
- Short liqs > Long liqs → squeeze direction up → potential long entry

---

### Rule 9 — Kill zones (highest win rate windows)
- 02:00 UTC — 80% WR
- 10:00 UTC — 88.9% WR
- 17:00 UTC — 78.7% WR
- 20:00 UTC — 98.4% WR

Enter during or just before kill zones. Avoid entering in dead hours (03:00–09:00 UTC).

---

### Rule 10 — Daily RSI extreme = hold thesis, don't panic
- Daily RSI < 15 = extreme oversold. Bounce is coming. Do not panic close.
- Daily RSI > 85 = extreme overbought. Don't enter long.
- Zoom out before touching position. 15m looks like disaster. Monthly looks like a normal wick.

---

### Rule 11 — ATR-based SL sizing
- ETH/BTC: SL = ATR(14) × 1.5
- DOGE/SOL/small caps: SL = ATR(14) × 2.5
- Never use a fixed % SL — candle noise eats it

---

### Rule 12 — Push alerts.json to GitHub after EVERY change
Railway deploys from GitHub. Local edits = bot sees old file = no alerts.
```bash
cd /Users/nomanashraf/git/crypto-alert && git add alerts.json && git commit -m "Update alerts" && git push origin main
```

---

## PSYCHOLOGY RULES

### Rule 13 — If you're not sure, close it
Uncertainty IS the signal. A confident trader holds without asking. If you're asking "should I close?" — the answer is probably yes, or at minimum protect with SL.

### Rule 14 — Green = close (default bias toward profit)
The market does not owe you your target price. A locked profit is real. An unrealized profit is not.

### Rule 15 — No revenge trading
After a loss: minimum 1 hour break. Run psychology check (Prompt 7) before next entry.

---

## QUICK REFERENCE

```
TP order:  take_profit | stopPrice + limitPrice(×0.999) | mark | reduceOnly
SL order:  stp         | stopPrice only                 | mark | reduceOnly
Edit rule: ALWAYS send both stopPrice + limitPrice together on TP edits
```

---

# OKX RULES — Verified by Research (my.okx.com EU)

> OKX has more flexibility than Kraken. These rules document what works and why.

---

## OKX ORDER EXECUTION RULES

### OKX Rule 1 — Enter with attachAlgoOrds (one call = entry + TP + SL)
- **Best practice:** attach TP + SL to the entry limit order in one API call
- When limit fills → TP and SL are automatically live, no second step
- **Why:** Eliminates the window between entry fill and TP/SL placement. You can never be in a position without protection.
- **Command:** `python3 okx_order.py enter ETH buy 0.5 1540 1620 1488`
  (coin · side · size · entry · TP · SL — `buy`=long, `sell`=short)

```python
POST /api/v5/trade/order
{
  "ordType": "limit",  "px": "1540",  "side": "buy",  "sz": "5",
  "attachAlgoOrds": [{
    "tpTriggerPx": "1620",  "tpOrdPx": "1618.38",  "tpTriggerPxType": "mark",
    "slTriggerPx": "1488",  "slOrdPx": "1483.54",  "slTriggerPxType": "mark"
  }]
}
```

---

### OKX Rule 2 — Use OCO for post-entry TP+SL (not two separate orders)
- **OCO** (`ordType: oco`) = one order holds both TP and SL
- When one triggers → the other is automatically cancelled by OKX
- **Why:** Two separate orders = race condition. If TP fills and you're slow cancelling SL, SL can re-open a new short. OCO is atomic — impossible to have both fire.
- **Command:** `python3 okx_order.py oco 1620 1488`

---

### OKX Rule 3 — Trailing stop replaces manual SL trail
- **`move_order_stop`** = OKX tracks price peak and moves SL automatically
- `callbackRatio: "0.03"` = SL stays 3% behind the highest price reached
- `callbackSpread: "100"` = SL stays $100 behind peak (use for fixed dollar distance)
- `activePx` = don't start trailing until price reaches this level
- **Why:** Better than manually running `sl trail` every few minutes. OKX handles it server-side — works even if your computer is off.
- **Command:** `python3 okx_order.py sl trail 3%`

---

### OKX Rule 4 — Always use mark price trigger (same as Kraken Rule 4)
- `tpTriggerPxType: "mark"` and `slTriggerPxType: "mark"` on everything
- **Never use `last`** — single trades can wick and trigger your order
- **Never use `index`** — can lag real futures price
- Mark price = OKX's manipulation-resistant fair value, same as liquidation price

---

### OKX Rule 5 — Both TP and SL use limit fills with a buffer
- TP: `tpOrdPx = tpTriggerPx × 0.999` — limit 0.1% below trigger
- SL: `slOrdPx = slTriggerPx × 0.997` — limit 0.3% below trigger
- **Why SL is limit not market:** On liquid OKX EU perps (ETH, BTC), price would need to gap 0.3% in one tick to miss the fill — extremely rare. Limit SL gives you a known worst-case fill price instead of unlimited slippage from a market order.
- **When to use market SL instead:** Very low liquidity coins, news events, or if you're trading on a thin book — change `SL_LIMIT_BUFFER` to `0` and pass `"-1"` manually.
- `SL_LIMIT_BUFFER = 0.003` is the constant in `okx_order.py` — increase it for more buffer, decrease it if you prefer tighter.

---

### OKX Rule 6 — reduceOnly on everything
- `reduceOnly: "true"` on all TP, SL, OCO, trailing stop orders
- Without it: if you manually close and forget to cancel algo orders → they reopen in opposite direction
- Note: OKX takes `"true"` as string, not boolean

---

### OKX Rule 7 — Available algo order types (what each does)
| ordType | Use for |
|---------|---------|
| `conditional` | Single TP only or single SL only |
| `oco` | Both TP + SL in one order (best for managing open position) |
| `move_order_stop` | Trailing stop — auto-moves with price |
| `trigger` | Conditional entry — fires limit order when price hits a level |
| `iceberg` | Split large order into smaller visible chunks |

---

### OKX Rule 8 — Edit algo orders with amend-algos (not cancel+replace)
- Endpoint: `POST /api/v5/trade/amend-algos`
- Can edit: `newTpTriggerPx`, `newTpOrdPx`, `newSlTriggerPx`, `newSlOrdPx` on live orders
- **Why:** Editing in-place is faster and preserves the order ID. Cancel+replace has a window where you're unprotected.
- When editing TP: always send both `newTpTriggerPx` AND `newTpOrdPx` together (same rule as Kraken $1,606 bug)

### OKX Rule 10 — The $1,606 GUARD (hard-enforced in code)
- **What it prevents:** editing a trigger price while leaving the old fill price stale → triggers at the new level but fills at the old (the $1,606 bug).
- **How:** `_assert_paired()` runs on EVERY outgoing POST in `okx_order.py`. If a payload contains a trigger price (`tpTriggerPx`/`slTriggerPx`/`newTpTriggerPx`/`newSlTriggerPx`) WITHOUT its matching fill price (`tpOrdPx`/`slOrdPx`/...), it raises and refuses to send. Also checks nested `attachAlgoOrds` on entry orders.
- **Result:** a half-update physically cannot reach OKX. Trigger and fill always travel together, or the order is blocked before sending.
- Trailing stops (`move_order_stop`, callback-based) and market SL (`slOrdPx="-1"`) both pass — they have no stale-price risk.

---

### OKX Rule 9 — OKX EU platform (my.okx.com)
- API base URL: `https://my.okx.com` (NOT www.okx.com — different database, keys won't work cross-platform)
- Same API v5 surface — all order types, all algo features available
- API keys: okx.com → Account → API → Create → needs Read + Trade permissions + Passphrase

---

## SHARED RULE (both exchanges) — Price precision must scale with coin price

- **The bug:** rounding limit/floor prices to a fixed 2 decimals works for BTC/ETH ($1000s) but **destroys the buffer on sub-dollar coins.**
  - WLD TP: `0.505 × 0.999 = 0.504495` → rounds to **0.50** (a 1% floor, not 0.1%)
  - WLD SL on OKX: `0.4485 × 0.997 = 0.4471` → rounds to **0.45**, which is *above* the trigger = **a broken stop** (limit on the wrong side)
- **The fix (`_price_decimals` in both `kraken_order.py` and `okx_order.py`):** scale decimals to price magnitude —
  ```
  price ≥ 1000 → 1 dp     price ≥ 10 → 3 dp     price < 1 → 5 dp
  price ≥ 100  → 2 dp     price ≥ 1  → 4 dp
  ```
  Verified: `0.505→0.5045`, `57.6→57.542`, `1620→1618.4`, `62900→62837.1`.
- **Why it matters:** you trade WLD / ENA / DOGE — all sub-$1. Without this, every TP floor is too loose and (on OKX) every SL limit is broken. Always confirm the printed `limitPrice` / `slOrdPx` is on the correct side of the trigger before trusting an order on a low-priced coin.

---

## OKX QUICK REFERENCE

```
New trade:   enter ETH buy  0.5 <entry> <tp> <sl>   ← LONG:  TP > entry > SL
             enter ETH sell 0.5 <entry> <tp> <sl>   ← SHORT: TP < entry < SL
                   coin side size                      one call = entry + TP + SL

Post-entry:  oco <tp> <sl>                  ← TP + SL in one OCO order
             sl trail 3%                    ← trailing stop, 3% behind peak
             tp +50                         ← move TP up $50
             sl be                          ← SL to breakeven

LONG  closes with SELL → limit fills BELOW trigger
SHORT closes with BUY  → limit fills ABOVE trigger   (buffer flips automatically)

TP:  tpTriggerPxType=mark  tpOrdPx=trigger ±0.1%  reduceOnly=true
SL:  slTriggerPxType=mark  slOrdPx=trigger ±0.3%  reduceOnly=true
```
