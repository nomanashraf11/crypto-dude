#!/usr/bin/env python3
"""
Kraken Futures — fast TP/SL updater

COMMANDS:
  orders           — refresh cache, show position
  tp 1590          — set TP to exact price
  tp add 1650 20%  — add second TP at $1,650 for 20% of position
  sl 1488          — set SL to exact price
  tp +50           — move TP up by $50
  sl +30           — move SL up by $30 (tighten)
  tp +2%           — move TP up by 2%
  sl -1%           — move SL down by 1%
  sl be            — SL to breakeven (entry price)
  sl be+           — SL to entry + $5 (just above breakeven, locks small profit)
  sl trail         — SL to current price - 1.5% (smart default, scales with coin price)
  sl trail 2%      — SL to current price - 2% (percentage buffer)
  sl trail 50      — SL to current price - $50 (absolute, use only if you know the coin)

EXAMPLES during trade:
  sl be            — trade now risk free
  sl trail         — ride the move, auto-close on reversal
  tp +50           — extend TP without typing exact price
"""

import sys, time, hashlib, hmac, base64, json, urllib.request, urllib.parse, subprocess

CACHE_FILE    = "/tmp/kraken_cache.json"
TRAIL_DEFAULT_PCT = 1.5  # default trailing buffer = 1.5% of current price

def keychain_get(key_name):
    r = subprocess.run(
        ["security", "find-generic-password", "-a", "kraken_futures", "-s", key_name, "-w"],
        capture_output=True, text=True
    )
    if r.returncode != 0:
        print(f"❌ '{key_name}' not found in Keychain.")
        print(f'   security add-generic-password -a kraken_futures -s {key_name} -w "YOUR_VALUE"')
        sys.exit(1)
    return r.stdout.strip()

API_KEY  = keychain_get("KRAKEN_KEY")
API_SEC  = keychain_get("KRAKEN_SECRET")
BASE_URL = "https://futures.kraken.com"

def sign(endpoint, nonce, post_data=""):
    message = post_data + nonce + endpoint.split("?")[0].replace("/derivatives", "")
    sha256  = hashlib.sha256(message.encode()).digest()
    secret  = base64.b64decode(API_SEC)
    return base64.b64encode(hmac.new(secret, sha256, hashlib.sha512).digest()).decode()

def api(method, endpoint, data=None):
    nonce     = str(int(time.time() * 1000))
    post_data = urllib.parse.urlencode(data) if data else ""
    headers   = {"APIKey": API_KEY, "Nonce": nonce, "Authent": sign(endpoint, nonce, post_data)}
    url       = BASE_URL + endpoint
    if method == "GET":
        req = urllib.request.Request(url, headers=headers)
    else:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(url, data=post_data.encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)

def current_price(symbol):
    coin = symbol.replace("PF_", "").replace("PI_", "").replace("USD", "")
    try:
        with urllib.request.urlopen(f"https://api.binance.com/api/v3/ticker/price?symbol={coin}USDT", timeout=5) as r:
            return float(json.load(r)["price"])
    except Exception:
        return None

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
    orders_data = api("GET", "/derivatives/api/v3/openorders")
    pos_data    = api("GET", "/derivatives/api/v3/openpositions")
    orders      = orders_data.get("openOrders", [])

    cache = {"tp_id": None, "sl_id": None, "tp_price": None, "sl_price": None,
             "symbol": None, "size": None, "entry": None}

    for o in orders:
        ot = o.get("orderType", "")
        if "take_profit" in ot:
            cache["tp_id"]    = o["order_id"]
            cache["tp_price"] = o.get("stopPrice")
        elif "stop" in ot:
            cache["sl_id"]    = o["order_id"]
            cache["sl_price"] = o.get("stopPrice")
        if not cache["symbol"]:
            cache["symbol"] = o.get("symbol")

    for p in pos_data.get("openPositions", []):
        if p.get("symbol") == cache["symbol"]:
            cache["size"]  = abs(float(p.get("size", 0)))
            cache["entry"] = float(p.get("price", 0))

    save_cache(cache)
    if not silent:
        print(f"\n{'─'*55}")
        print(f"  Entry:    ${cache['entry']}")
        print(f"  TP:       ${cache['tp_price']}")
        print(f"  SL:       ${cache['sl_price']}")
        print(f"  Size:     {cache['size']} ETH")
        print(f"  Symbol:   {cache['symbol']}")
        print(f"{'─'*55}")
        print(f"  Cache saved ✅\n")
    return cache

def resolve_price(arg, current, label):
    """Parse price: '1590' / '+50' / '-30' / '+2%' / '-1%'"""
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

def place_tp(symbol, size, price):
    """Place a limit sell order at TP price (closes portion of long position)."""
    result = api("POST", "/derivatives/api/v3/sendorder", {
        "orderType":  "lmt",
        "symbol":     symbol,
        "side":       "sell",
        "size":       size,
        "limitPrice": price,
    })
    status   = result.get("sendStatus", {}).get("status", "?")
    order_id = result.get("sendStatus", {}).get("order_id", "?")
    return status, order_id

def add_tp(price_arg, size_pct):
    cache  = get_cache()
    price  = resolve_price(price_arg, cache["tp_price"], "TP2")
    size   = round(cache["size"] * size_pct / 100, 8)
    print(f"  Adding TP at ${price} for {size_pct}% = {size} ETH")
    status, order_id = place_tp(cache["symbol"], size, price)
    print(f"✅ TP added  ${price}  size={size}  |  {status}  id={order_id}")

def edit(order_id, size, new_price):
    result = api("POST", "/derivatives/api/v3/editorder", {
        "orderId":   order_id,
        "size":      size,
        "stopPrice": new_price,
    })
    return result.get("editStatus", {}).get("status", "?")

def update_tp(arg, size_pct=None):
    cache    = get_cache()
    old      = cache["tp_price"]
    new      = resolve_price(arg, old, "TP")
    size     = cache["size"]
    if size_pct is not None:
        size = round(size * size_pct / 100, 8)
        print(f"  Partial TP: {size_pct}% of position = {size} ETH")
    status   = edit(cache["tp_id"], size, new)
    cache["tp_price"] = new
    save_cache(cache)
    print(f"✅ TP  ${old} → ${new}  |  {status}")

def update_sl(arg):
    cache  = get_cache()
    symbol = cache["symbol"]

    if arg == "be":
        new = cache["entry"]
    elif arg == "be+":
        new = round(cache["entry"] + 5, 2)
    elif arg == "trail" or (arg.startswith("trail") and len(arg.split()) == 1):
        price = current_price(symbol)
        if not price:
            print("❌ Could not fetch current price")
            return
        new = round(price - TRAIL_DEFAULT, 2)
        print(f"  Current price: ${price} | Trail buffer: ${TRAIL_DEFAULT}")
    else:
        old = cache["sl_price"]
        new = resolve_price(arg, old, "SL")

    old    = cache["sl_price"]
    status = edit(cache["sl_id"], cache["size"], new)
    cache["sl_price"] = new
    save_cache(cache)
    print(f"✅ SL  ${old} → ${new}  |  {status}")

def update_sl_trail(arg=None):
    cache  = get_cache()
    symbol = cache["symbol"]
    price  = current_price(symbol)
    if not price:
        print("❌ Could not fetch current price")
        return

    # parse arg: "2%" = percentage, "50" = absolute, None = default %
    if arg is None:
        buffer = round(price * TRAIL_DEFAULT_PCT / 100, 8)
        label  = f"{TRAIL_DEFAULT_PCT}%"
    elif str(arg).endswith("%"):
        pct    = float(str(arg)[:-1])
        buffer = round(price * pct / 100, 8)
        label  = f"{pct}%"
    else:
        buffer = float(arg)
        label  = f"${buffer}"

    new    = round(price - buffer, 8)
    old    = cache["sl_price"]
    print(f"  Mark price: ${price} | Buffer: {label} = ${buffer:.6g}")
    status = edit(cache["sl_id"], cache["size"], new)
    if status != "invalidPrice":
        cache["sl_price"] = new
        save_cache(cache)
    print(f"✅ SL  ${old} → ${new}  |  {status}")

if __name__ == "__main__":
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)

    cmd = args[0].lower()

    if cmd == "orders":
        refresh_cache()
    elif cmd == "tp" and len(args) >= 2:
        if args[1].lower() == "add" and len(args) == 4:
            # tp add 1650 20%
            pct = float(args[3].replace("%",""))
            add_tp(args[2], pct)
        else:
            pct = float(args[2].replace("%","")) if len(args) == 3 and "%" in args[2] else None
            update_tp(args[1], size_pct=pct)
    elif cmd == "sl" and len(args) == 1:
        print(__doc__)
    elif cmd == "sl" and len(args) >= 2:
        val = args[1].lower()
        if val.startswith("trail"):
            buf = args[2] if len(args) == 3 else None
            update_sl_trail(buf)
        else:
            update_sl(val)
    else:
        print(__doc__)
