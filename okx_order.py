#!/usr/bin/env python3
"""
OKX Futures — TP/SL order manager (my.okx.com EU)

COMMANDS:
  orders              — show position + all active algo orders
  balance             — show USDT equity

  enter ETH buy 0.5 1540 1620 1488   — open LONG:  coin side size entry TP SL
  enter ETH sell 0.5 1620 1540 1700  — open SHORT: TP below entry, SL above
  oco 1620 1488       — place ONE order with TP=$1,620 + SL=$1,488 (best setup, already in)
  tp 1620             — set/update TP only
  tp add 1700 20%     — add second TP at $1,700 for 20% of position
  sl 1488             — set/update SL only
  tp +50              — move TP up $50
  sl +30              — move SL up $30
  tp +2%              — move TP up 2%
  sl -1%              — move SL down 1%
  sl be               — SL to breakeven (entry price)
  sl be+              — SL to entry + $5
  sl trail 3%         — trailing stop AUTO-follows peak, fills MARKET (slippage = book)
  sl trail 50         — trailing stop: $50 behind peak (absolute), MARKET fill
  sl trail            — trailing stop: 1.5% behind peak (default), MARKET fill
  sl trail 3% limit   — STOP-LIMIT trail: caps slippage 0.3%, MANUAL (re-run as price climbs)

  MARKET trail = native auto-follow, always fills at whatever the book offers
  LIMIT  trail = caps exit price (0.3% floor), but can MISS on a gap + must re-run
  (OKX's regular 'sl <price>' and 'oco' are ALREADY stop-limit / capped slippage.)

OCO = One-Cancels-Other: TP + SL in one order.
  When TP fires → SL auto-cancelled. When SL fires → TP auto-cancelled.
  This is the professional standard on OKX. Use 'oco' for every new trade.

TRAILING STOP (move_order_stop):
  Better than manual sl trail. OKX tracks the peak price and moves SL automatically.
  sl trail 3%  → SL stays 3% behind the highest price reached
  sl trail 100 → SL stays $100 behind the highest price reached
  Use 'activePx' variant to only start trailing once price reaches your TP zone.

ORDER RULES (from RULES.md):
  TP trigger:  mark price (manipulation-resistant, same as liquidation price)
  TP fill:     trigger ± 0.1% limit floor — avoids bad fill, still fills cleanly
  SL trigger:  mark price
  SL fill:     trigger ± 0.3% limit floor — controlled slippage, fills on liquid perps
  reduceOnly:  always true (never opens new position by accident)
  DIRECTION:   long closes with SELL (fill below trigger),
               short closes with BUY (fill above trigger) — buffer flips automatically.

CONTRACT SIZES:
  ETH-USDT-SWAP:  0.1 ETH/contract   BTC-USDT-SWAP:  0.01 BTC/contract
  SOL-USDT-SWAP:  1 SOL/contract     DOGE-USDT-SWAP: 10 DOGE/contract
  XRP-USDT-SWAP:  100 XRP/contract   ENA-USDT-SWAP:  10 ENA/contract
  Script auto-converts — type coin amounts, contracts handled internally.
"""

import sys, time, hashlib, hmac, base64, json, urllib.request, urllib.parse, subprocess
from datetime import datetime, timezone

CACHE_FILE        = "/tmp/okx_cache.json"
TRAIL_DEFAULT_PCT = 1.5
TP_LIMIT_BUFFER   = 0.001  # TP limit floor = 0.1% off trigger
SL_LIMIT_BUFFER   = 0.003  # SL limit floor = 0.3% off trigger
TD_MODE           = "cross"  # margin mode — change to "isolated" if your account uses it

CONTRACT_SIZES = {
    "ETH-USDT-SWAP":  0.1,
    "BTC-USDT-SWAP":  0.01,
    "SOL-USDT-SWAP":  1.0,
    "DOGE-USDT-SWAP": 10.0,
    "XRP-USDT-SWAP":  100.0,
    "BNB-USDT-SWAP":  0.1,
    "AVAX-USDT-SWAP": 1.0,
    "LINK-USDT-SWAP": 1.0,
    "ENA-USDT-SWAP":  10.0,
}

def keychain_get(key_name):
    r = subprocess.run(
        ["security", "find-generic-password", "-a", "okx_futures", "-s", key_name, "-w"],
        capture_output=True, text=True
    )
    if r.returncode != 0:
        print(f"❌ '{key_name}' not found in Keychain.")
        print(f'   security add-generic-password -a okx_futures -s {key_name} -w "YOUR_VALUE"')
        sys.exit(1)
    return r.stdout.strip()

API_KEY    = keychain_get("OKX_KEY")
API_SECRET = keychain_get("OKX_SECRET")
API_PASS   = keychain_get("OKX_PASSPHRASE")
BASE_URL   = "https://my.okx.com"

def _timestamp():
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond//1000:03d}Z"

def sign(ts, method, path, body=""):
    message = ts + method.upper() + path + body
    return base64.b64encode(
        hmac.new(API_SECRET.encode(), message.encode(), hashlib.sha256).digest()
    ).decode()

# Trigger price ↔ fill price must always travel together. Editing a trigger
# without its fill price leaves a stale fill = the Kraken $1,606 bug. These pairs
# are checked on EVERY outgoing order/amend so that mistake can never reach OKX.
_PRICE_PAIRS = [
    ("tpTriggerPx",    "tpOrdPx"),
    ("slTriggerPx",    "slOrdPx"),
    ("newTpTriggerPx", "newTpOrdPx"),
    ("newSlTriggerPx", "newSlOrdPx"),
]

def _assert_paired(data):
    """Block any payload where a trigger is set without its fill price (the $1,606 bug)."""
    def check(d):
        if not isinstance(d, dict):
            return
        for trig, ordp in _PRICE_PAIRS:
            has_trig, has_ord = trig in d, ordp in d
            if has_trig != has_ord:
                raise ValueError(
                    f"🛑 $1,606 GUARD: '{trig}' present={has_trig} but '{ordp}' present={has_ord}. "
                    f"A trigger price must ALWAYS be sent with its fill price — editing one "
                    f"without the other leaves a stale fill. Refusing to send this order."
                )
        for ao in (d.get("attachAlgoOrds") or []):   # entry orders nest TP/SL here
            check(ao)
    if isinstance(data, list):
        for item in data:
            check(item)
    else:
        check(data)

def api(method, path, params=None, data=None):
    if method == "POST" and data is not None:
        _assert_paired(data)
    ts    = _timestamp()
    query = ("?" + urllib.parse.urlencode(params)) if params else ""
    body  = json.dumps(data) if data else ""
    full  = path + query
    sig   = sign(ts, method, full, body)

    headers = {
        "OK-ACCESS-KEY":        API_KEY,
        "OK-ACCESS-SIGN":       sig,
        "OK-ACCESS-TIMESTAMP":  ts,
        "OK-ACCESS-PASSPHRASE": API_PASS,
        "Content-Type":         "application/json",
        "User-Agent":           "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    }
    req = urllib.request.Request(
        BASE_URL + full,
        data=body.encode() if body else None,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body2 = e.read().decode()
        print(f"HTTP {e.code}: {body2}")
        raise

def _price_decimals(price):
    """Decimals that preserve the buffer at any price. 2 dp is fine for BTC/ETH but
    on sub-dollar coins it can flip the limit to the WRONG side of the trigger
    (0.4485*0.997=0.4471 -> 0.45, above trigger = broken stop). Scale to price."""
    p = abs(price)
    if p >= 1000: return 1
    if p >= 100:  return 2
    if p >= 10:   return 3
    if p >= 1:    return 4
    return 5      # sub-dollar coins (WLD, ENA, DOGE...)

def tp_ord_price(trigger, close_side):
    """Limit floor for TP. Long (sell) fills below trigger; short (buy) fills above."""
    sign = -1 if close_side == "sell" else 1
    return round(trigger * (1 + sign * TP_LIMIT_BUFFER), _price_decimals(trigger))

def sl_ord_price(trigger, close_side):
    """Limit floor for SL. Long (sell) fills below trigger; short (buy) fills above."""
    sign = -1 if close_side == "sell" else 1
    return round(trigger * (1 + sign * SL_LIMIT_BUFFER), _price_decimals(trigger))

def normalize_inst(coin):
    """ETH → ETH-USDT-SWAP. Pass-through if already a full instId."""
    coin = coin.upper()
    return coin if coin.endswith("-SWAP") else f"{coin}-USDT-SWAP"

def coins_to_contracts(inst_id, coins):
    return max(1, round(coins / CONTRACT_SIZES.get(inst_id, 1.0)))

def contracts_to_coins(inst_id, contracts):
    return round(contracts * CONTRACT_SIZES.get(inst_id, 1.0), 6)

def current_price(inst_id):
    coin = inst_id.split("-")[0]
    try:
        with urllib.request.urlopen(
            f"https://api.binance.com/api/v3/ticker/price?symbol={coin}USDT", timeout=5
        ) as r:
            return float(json.load(r)["price"])
    except Exception:
        return None

def _parse_status(result):
    d = result.get("data", [{}])
    if isinstance(d, list) and d:
        code = d[0].get("sCode", result.get("code", "?"))
        msg  = d[0].get("sMsg", result.get("msg", ""))
        return ("ok" if code == "0" else f"err:{code} {msg}").strip()
    return "ok" if result.get("code") == "0" else f"err:{result.get('code')} {result.get('msg','')}"

def _algo_id(result):
    d = result.get("data", [{}])
    return d[0].get("algoId", "?") if d else "?"

# ─── CACHE ──────────────────────────────────────────────────────────────────

def load_cache():
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except Exception:
        return None

def save_cache(c):
    with open(CACHE_FILE, "w") as f:
        json.dump({**c, "ts": time.time()}, f)

def get_cache():
    cache = load_cache()
    if cache and (time.time() - cache.get("ts", 0)) < 3600:
        return cache
    print("Cache expired — fetching live data...")
    return refresh_cache(silent=True)

def refresh_cache(silent=False):
    pos_data  = api("GET", "/api/v5/account/positions", params={"instType": "SWAP"})
    algo_data = api("GET", "/api/v5/trade/orders-algo-pending", params={
        "ordType": "conditional", "instType": "SWAP"
    })
    trail_data = api("GET", "/api/v5/trade/orders-algo-pending", params={
        "ordType": "move_order_stop", "instType": "SWAP"
    })

    cache = {
        "oco_id": None, "tp_id": None, "sl_id": None, "trail_id": None,
        "tp_price": None, "sl_price": None,
        "inst_id": None, "size_contracts": None, "size_coins": None, "entry": None,
        "direction": None, "close_side": None,
    }

    for p in pos_data.get("data", []):
        pos_size = float(p.get("pos", 0))
        if pos_size != 0:
            cache["inst_id"]        = p["instId"]
            cache["size_contracts"] = abs(pos_size)
            cache["size_coins"]     = contracts_to_coins(p["instId"], cache["size_contracts"])
            cache["entry"]          = float(p.get("avgPx", 0))
            cache["direction"]      = "long" if pos_size > 0 else "short"
            cache["close_side"]     = "sell" if pos_size > 0 else "buy"
            break

    for o in algo_data.get("data", []):
        tp_px = o.get("tpTriggerPx", "0") or "0"
        sl_px = o.get("slTriggerPx", "0") or "0"
        has_tp = float(tp_px) > 0
        has_sl = float(sl_px) > 0
        if has_tp and has_sl:
            cache["oco_id"]   = o["algoId"]
            cache["tp_price"] = float(tp_px)
            cache["sl_price"] = float(sl_px)
        elif has_tp:
            cache["tp_id"]    = o["algoId"]
            cache["tp_price"] = float(tp_px)
        elif has_sl:
            cache["sl_id"]    = o["algoId"]
            cache["sl_price"] = float(sl_px)

    for o in trail_data.get("data", []):
        cache["trail_id"] = o["algoId"]

    save_cache(cache)

    if not silent:
        oco_label = f"OCO id={cache['oco_id']}" if cache["oco_id"] else "—"
        print(f"\n{'─'*55}")
        print(f"  Instrument: {cache['inst_id']}")
        print(f"  Direction:  {cache['direction']}")
        print(f"  Entry:      ${cache['entry']}")
        print(f"  TP:         ${cache['tp_price']}")
        print(f"  SL:         ${cache['sl_price']}")
        print(f"  OCO:        {oco_label}")
        print(f"  Trail SL:   {'active id='+cache['trail_id'] if cache['trail_id'] else '—'}")
        print(f"  Size:       {cache['size_coins']} coins  ({cache['size_contracts']} contracts)")
        print(f"{'─'*55}\n")

    return cache

# ─── BALANCE ────────────────────────────────────────────────────────────────

def show_balance():
    data = api("GET", "/api/v5/account/balance", params={"ccy": "USDT"})
    for d in data.get("data", []):
        total_eq = float(d.get("totalEq", 0))
        print(f"\n  Account Equity: ${total_eq:,.2f} USDT")
        for detail in d.get("details", []):
            ccy    = detail.get("ccy", "?")
            eq     = float(detail.get("eq",      0))
            avail  = float(detail.get("availEq", 0))
            frozen = float(detail.get("frozenBal", 0))
            print(f"  {ccy:<6} Total=${eq:,.2f}  Available=${avail:,.2f}  Margin=${frozen:,.2f}")
        print()

# ─── PRICE RESOLVER ─────────────────────────────────────────────────────────

def resolve_price(arg, current, label):
    arg = str(arg)
    if arg.startswith("+") and arg.endswith("%"):
        return round(current * (1 + float(arg[1:-1]) / 100), 2)
    elif arg.startswith("-") and arg.endswith("%"):
        return round(current * (1 - float(arg[1:-1]) / 100), 2)
    elif arg.startswith("+"):
        return round(current + float(arg[1:]), 2)
    elif arg.startswith("-"):
        return round(current - float(arg[1:]), 2)
    else:
        return float(arg)

# ─── OCO — TP + SL IN ONE ORDER ─────────────────────────────────────────────

def place_oco(inst_id, contracts, tp_price, sl_price, close_side):
    """Best setup: one order with both TP and SL. When one fires, other auto-cancels."""
    tp_limit = tp_ord_price(tp_price, close_side)
    sl_limit = sl_ord_price(sl_price, close_side)
    result = api("POST", "/api/v5/trade/order-algo", data={
        "instId":           inst_id,
        "tdMode":           TD_MODE,
        "side":             close_side,
        "ordType":          "oco",
        "sz":               str(int(contracts)),
        "tpTriggerPx":      str(tp_price),
        "tpOrdPx":          str(tp_limit),
        "tpTriggerPxType":  "mark",
        "slTriggerPx":      str(sl_price),
        "slOrdPx":          str(sl_limit),
        "slTriggerPxType":  "mark",
        "reduceOnly":       "true",
    })
    status  = _parse_status(result)
    algo_id = _algo_id(result)
    print(f"  TP trigger=${tp_price}  fill=${tp_limit}  |  SL trigger=${sl_price}  fill=${sl_limit}")
    return status, algo_id

def edit_oco(algo_id, inst_id, close_side, tp_price=None, sl_price=None):
    """Edit TP and/or SL on an existing OCO order."""
    data = {"instId": inst_id, "algoId": algo_id}
    if tp_price is not None:
        tp_limit = tp_ord_price(tp_price, close_side)
        data["newTpTriggerPx"]     = str(tp_price)
        data["newTpOrdPx"]         = str(tp_limit)
        data["newTpTriggerPxType"] = "mark"
        print(f"  tpTriggerPx=${tp_price}  tpOrdPx=${tp_limit}")
    if sl_price is not None:
        sl_limit = sl_ord_price(sl_price, close_side)
        data["newSlTriggerPx"]     = str(sl_price)
        data["newSlOrdPx"]         = str(sl_limit)
        data["newSlTriggerPxType"] = "mark"
        print(f"  slTriggerPx=${sl_price}  slOrdPx=${sl_limit}")
    result = api("POST", "/api/v5/trade/amend-algos", data=data)
    return _parse_status(result)

# ─── STANDALONE TP ──────────────────────────────────────────────────────────

def place_tp(inst_id, contracts, tp_price, close_side):
    tp_limit = tp_ord_price(tp_price, close_side)
    result = api("POST", "/api/v5/trade/order-algo", data={
        "instId":           inst_id,
        "tdMode":           TD_MODE,
        "side":             close_side,
        "ordType":          "conditional",
        "sz":               str(int(contracts)),
        "tpTriggerPx":      str(tp_price),
        "tpOrdPx":          str(tp_limit),
        "tpTriggerPxType":  "mark",
        "reduceOnly":       "true",
    })
    status  = _parse_status(result)
    algo_id = _algo_id(result)
    print(f"  tpTriggerPx=${tp_price}  tpOrdPx=${tp_limit}")
    return status, algo_id

def edit_tp_order(algo_id, inst_id, tp_price, close_side):
    tp_limit = tp_ord_price(tp_price, close_side)
    result = api("POST", "/api/v5/trade/amend-algos", data={
        "instId":              inst_id,
        "algoId":              algo_id,
        "newTpTriggerPx":      str(tp_price),
        "newTpOrdPx":          str(tp_limit),
        "newTpTriggerPxType":  "mark",
    })
    print(f"  tpTriggerPx=${tp_price}  tpOrdPx=${tp_limit}")
    return _parse_status(result)

# ─── STANDALONE SL ──────────────────────────────────────────────────────────

def place_sl(inst_id, contracts, sl_price, close_side):
    sl_limit = sl_ord_price(sl_price, close_side)
    result = api("POST", "/api/v5/trade/order-algo", data={
        "instId":           inst_id,
        "tdMode":           TD_MODE,
        "side":             close_side,
        "ordType":          "conditional",
        "sz":               str(int(contracts)),
        "slTriggerPx":      str(sl_price),
        "slOrdPx":          str(sl_limit),
        "slTriggerPxType":  "mark",
        "reduceOnly":       "true",
    })
    status  = _parse_status(result)
    algo_id = _algo_id(result)
    print(f"  slTriggerPx=${sl_price}  slOrdPx=${sl_limit}")
    return status, algo_id

def edit_sl_order(algo_id, inst_id, sl_price, close_side):
    sl_limit = sl_ord_price(sl_price, close_side)
    result = api("POST", "/api/v5/trade/amend-algos", data={
        "instId":              inst_id,
        "algoId":              algo_id,
        "newSlTriggerPx":      str(sl_price),
        "newSlOrdPx":          str(sl_limit),
        "newSlTriggerPxType":  "mark",
    })
    print(f"  slTriggerPx=${sl_price}  slOrdPx=${sl_limit}")
    return _parse_status(result)

# ─── TRAILING STOP ──────────────────────────────────────────────────────────

def place_trailing_sl(inst_id, contracts, close_side, callback_ratio=None, callback_spread=None, active_px=None):
    """
    Trailing stop — OKX tracks price peak and moves SL automatically.
    callback_ratio:  e.g. 0.03 = trail 3% behind peak
    callback_spread: e.g. 100  = trail $100 behind peak (use one or the other)
    active_px:       only start trailing once price reaches this level (optional)
    """
    data = {
        "instId":    inst_id,
        "tdMode":    TD_MODE,
        "side":      close_side,
        "ordType":   "move_order_stop",
        "sz":        str(int(contracts)),
        "reduceOnly": "true",
    }
    if callback_ratio is not None:
        data["callbackRatio"] = str(callback_ratio)
    elif callback_spread is not None:
        data["callbackSpread"] = str(callback_spread)
    if active_px is not None:
        data["activePx"] = str(active_px)

    result  = api("POST", "/api/v5/trade/order-algo", data=data)
    status  = _parse_status(result)
    algo_id = _algo_id(result)
    return status, algo_id

def cancel_algo(inst_id, algo_id):
    result = api("POST", "/api/v5/trade/cancel-algos", data=[{"algoId": algo_id, "instId": inst_id}])
    return _parse_status(result)

# ─── COMMANDS ───────────────────────────────────────────────────────────────

def cmd_oco(tp_arg, sl_arg):
    cache     = get_cache()
    if not cache.get("inst_id"):
        print("❌ No open position. Use 'enter' to open a trade first.")
        return
    tp_price   = resolve_price(tp_arg, cache["tp_price"] or cache["entry"], "TP")
    sl_price   = resolve_price(sl_arg, cache["sl_price"] or cache["entry"], "SL")
    contracts  = cache["size_contracts"]
    close_side = cache["close_side"]

    # Cancel any existing separate TP/SL orders first
    for key in ("tp_id", "sl_id", "oco_id", "trail_id"):
        if cache.get(key):
            cancel_algo(cache["inst_id"], cache[key])
            print(f"  Cancelled old {key}: {cache[key]}")

    status, algo_id = place_oco(cache["inst_id"], contracts, tp_price, sl_price, close_side)
    cache.update({"oco_id": algo_id, "tp_id": None, "sl_id": None, "trail_id": None,
                  "tp_price": tp_price, "sl_price": sl_price})
    save_cache(cache)
    print(f"✅ OCO  TP=${tp_price}  SL=${sl_price}  |  {status}  id={algo_id}")

def cmd_update_tp(arg, size_pct=None):
    cache      = get_cache()
    if not cache.get("inst_id"):
        print("❌ No open position. Use 'enter' to open a trade first.")
        return
    old        = cache["tp_price"]
    new        = resolve_price(arg, old, "TP")
    contracts  = cache["size_contracts"]
    close_side = cache["close_side"]
    if size_pct is not None:
        contracts = max(1, round(contracts * size_pct / 100))

    if cache["oco_id"]:
        status = edit_oco(cache["oco_id"], cache["inst_id"], close_side, tp_price=new)
        cache["tp_price"] = new
    elif cache["tp_id"]:
        status = edit_tp_order(cache["tp_id"], cache["inst_id"], new, close_side)
        cache["tp_price"] = new
    else:
        status, new_id = place_tp(cache["inst_id"], contracts, new, close_side)
        cache.update({"tp_id": new_id, "tp_price": new})

    save_cache(cache)
    print(f"✅ TP  ${old} → ${new}  |  {status}")

def cmd_update_sl(arg):
    cache = get_cache()
    if not cache.get("inst_id"):
        print("❌ No open position. Use 'enter' to open a trade first.")
        return
    close_side = cache["close_side"]

    if arg == "be":
        new = cache["entry"]
    elif arg == "be+":
        # lock small profit: long → entry+5, short → entry-5
        new = round(cache["entry"] + (5 if close_side == "sell" else -5), 2)
    else:
        new = resolve_price(arg, cache["sl_price"], "SL")

    old = cache["sl_price"]

    if cache["oco_id"]:
        status = edit_oco(cache["oco_id"], cache["inst_id"], close_side, sl_price=new)
        cache["sl_price"] = new
    elif cache["sl_id"]:
        status = edit_sl_order(cache["sl_id"], cache["inst_id"], new, close_side)
        cache["sl_price"] = new
    else:
        status, new_id = place_sl(cache["inst_id"], cache["size_contracts"], new, close_side)
        cache.update({"sl_id": new_id, "sl_price": new})

    save_cache(cache)
    print(f"✅ SL  ${old} → ${new}  |  {status}")

def cmd_trail_sl(arg=None, use_limit=False):
    cache = get_cache()
    if not cache.get("inst_id"):
        print("❌ No open position. Use 'enter' to open a trade first.")
        return
    close_side = cache["close_side"]

    # parse buffer → ratio (percent) or spread (absolute)
    if arg is None:
        ratio, spread, label = TRAIL_DEFAULT_PCT / 100, None, f"{TRAIL_DEFAULT_PCT}%"
    elif str(arg).endswith("%"):
        pct = float(str(arg)[:-1]); ratio, spread, label = pct / 100, None, f"{pct}%"
    else:
        ratio, spread, label = None, float(arg), f"${float(arg)}"

    # ── LIMIT trail (manual, caps slippage) ──────────────────────────────
    # OKX's native trailing stop only fills at MARKET. To cap slippage we park the
    # regular conditional stop-LIMIT (slOrdPx 0.3% floor) at price−buffer and re-run
    # it as price moves. Caps slippage, but can MISS on a gap. Best for winners.
    if use_limit:
        price = current_price(cache["inst_id"])
        if not price:
            print("❌ Could not fetch current price"); return
        buf = price * ratio if ratio is not None else spread
        new = round(price - buf if close_side == "sell" else price + buf, _price_decimals(price))
        if cache.get("trail_id"):                       # drop any native trail first
            cancel_algo(cache["inst_id"], cache["trail_id"]); cache["trail_id"] = None
        if cache.get("oco_id"):
            status = edit_oco(cache["oco_id"], cache["inst_id"], close_side, sl_price=new)
        elif cache.get("sl_id"):
            status = edit_sl_order(cache["sl_id"], cache["inst_id"], new, close_side)
        else:
            status, sid = place_sl(cache["inst_id"], cache["size_contracts"], new, close_side)
            cache["sl_id"] = sid
        cache["sl_price"] = new
        save_cache(cache)
        print(f"✅ Trail STOP-LIMIT → ${new}  (buffer {label})  |  {status}")
        print(f"   Caps slippage (0.3% floor). MANUAL — re-run as price climbs. Can miss on a gap.")
        return

    # ── MARKET trail (native auto-follow) ────────────────────────────────
    if cache.get("trail_id"):
        cancel_algo(cache["inst_id"], cache["trail_id"])
        print(f"  Cancelled old trail: {cache['trail_id']}")
    if ratio is not None:
        status, algo_id = place_trailing_sl(cache["inst_id"], cache["size_contracts"], close_side, callback_ratio=ratio)
    else:
        status, algo_id = place_trailing_sl(cache["inst_id"], cache["size_contracts"], close_side, callback_spread=spread)
    cache.update({"trail_id": algo_id})
    save_cache(cache)
    print(f"✅ Trailing SL (auto, MARKET fill)  callback={label}  |  {status}  id={algo_id}")
    print(f"   OKX tracks the peak and fires a MARKET sell if price drops {label}")

def cmd_enter(coin, side, size_coins, entry_price, tp_price, sl_price):
    """
    Open a NEW position: limit entry with TP + SL attached in one API call.
    When the limit fills, TP and SL are automatically live — no second step.

      coin       ETH / BTC / SOL ...  (or full ETH-USDT-SWAP)
      side       buy = long, sell = short
      size_coins position size in coins (e.g. 0.5 ETH)
    """
    inst_id    = normalize_inst(coin)
    side       = side.lower()
    if side not in ("buy", "sell"):
        print(f"❌ side must be 'buy' (long) or 'sell' (short), got '{side}'")
        return
    # Position you're opening closes with the opposite side.
    close_side = "sell" if side == "buy" else "buy"
    direction  = "LONG" if side == "buy" else "SHORT"

    contracts = coins_to_contracts(inst_id, size_coins)
    tp_limit  = tp_ord_price(tp_price, close_side)
    sl_limit  = sl_ord_price(sl_price, close_side)

    # Sanity check: TP/SL on the correct side of entry for the direction
    if side == "buy"  and not (tp_price > entry_price > sl_price):
        print(f"⚠️  LONG expects TP > entry > SL. Got TP={tp_price} entry={entry_price} SL={sl_price}")
    if side == "sell" and not (tp_price < entry_price < sl_price):
        print(f"⚠️  SHORT expects TP < entry < SL. Got TP={tp_price} entry={entry_price} SL={sl_price}")

    result = api("POST", "/api/v5/trade/order", data={
        "instId":  inst_id,
        "tdMode":  TD_MODE,
        "side":    side,
        "ordType": "limit",
        "px":      str(entry_price),
        "sz":      str(int(contracts)),
        "attachAlgoOrds": [{
            "tpTriggerPx":     str(tp_price),
            "tpOrdPx":         str(tp_limit),
            "tpTriggerPxType": "mark",
            "slTriggerPx":     str(sl_price),
            "slOrdPx":         str(sl_limit),
            "slTriggerPxType": "mark",
        }],
    })

    code    = result.get("code", "?")
    status  = "ok" if code == "0" else f"err:{code} {result.get('msg','')}"
    data    = result.get("data", [{}])
    ord_id  = data[0].get("ordId", "?") if data else "?"

    print(f"  {direction} {inst_id}  entry=${entry_price}  size={size_coins} coins ({contracts} contracts)")
    print(f"  TP trigger=${tp_price}  fill=${tp_limit}")
    print(f"  SL trigger=${sl_price}  fill=${sl_limit}")
    print(f"✅ Order placed  ordId={ord_id}  |  {status}")
    print(f"   TP + SL activate automatically when the entry fills.")

def cmd_add_tp(price_arg, size_pct):
    cache     = get_cache()
    if not cache.get("inst_id"):
        print("❌ No open position. Use 'enter' to open a trade first.")
        return
    price     = resolve_price(price_arg, cache["tp_price"] or 0, "TP2")
    contracts = max(1, round(cache["size_contracts"] * size_pct / 100))
    print(f"  Adding TP at ${price} for {size_pct}% = {contracts} contracts")
    status, algo_id = place_tp(cache["inst_id"], contracts, price, cache["close_side"])
    print(f"✅ TP added  ${price}  contracts={contracts}  |  {status}  id={algo_id}")

# ─── MAIN ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)

    cmd = args[0].lower()

    if cmd == "orders":
        refresh_cache()
    elif cmd == "enter" and len(args) == 7:
        cmd_enter(coin=args[1], side=args[2], size_coins=float(args[3]),
                  entry_price=float(args[4]), tp_price=float(args[5]), sl_price=float(args[6]))
    elif cmd == "balance":
        show_balance()
    elif cmd == "oco" and len(args) == 3:
        cmd_oco(args[1], args[2])
    elif cmd == "tp" and len(args) >= 2:
        if args[1].lower() == "add" and len(args) == 4:
            pct = float(args[3].replace("%", ""))
            cmd_add_tp(args[2], pct)
        else:
            pct = float(args[2].replace("%", "")) if len(args) == 3 and "%" in args[2] else None
            cmd_update_tp(args[1], size_pct=pct)
    elif cmd == "sl" and len(args) >= 2:
        rest      = [a.lower() for a in args[1:]]
        use_limit = "limit" in rest
        rest      = [a for a in rest if a != "limit"]   # strip the 'limit' keyword
        val       = rest[0]
        if val.startswith("trail"):
            buf = rest[1] if len(rest) >= 2 else None
            cmd_trail_sl(buf, use_limit=use_limit)
        else:
            cmd_update_sl(val)   # OKX regular SL is already stop-limit (0.3% floor)
    else:
        print(__doc__)
