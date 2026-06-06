#!/usr/bin/env python3
"""
Signal Validator — paste friend's signal, get instant liq data for Claude to analyze

Usage:
  python3 validate.py "#ENA LONG / ENTRY: 0.087 / TARGET: 0.108 / STOPLOSS: 0.079"
  python3 validate.py "ETHUSDT long 1540 tp 1645 sl 1488"

Output: parsed signal + live liq data + positioning — Claude does the final analysis
"""

import sys, re, json, urllib.request

def parse_signal(raw):
    raw = raw.upper()
    result = {}

    # coin — look for $COIN or #COIN or known patterns
    coin_match = re.search(r'[\$\#]([A-Z]{2,10})', raw)
    if coin_match:
        result["coin"] = coin_match.group(1)

    # fallback: first all-caps word before LONG/SHORT
    if "coin" not in result:
        m = re.search(r'\b([A-Z]{2,10})USDT?\b', raw)
        if m:
            result["coin"] = m.group(1).replace("USDT","")

    # direction
    if "LONG" in raw or "BUY" in raw:
        result["direction"] = "LONG"
    elif "SHORT" in raw or "SELL" in raw:
        result["direction"] = "SHORT"

    # entry — handle ranges like "0.087 - 0.097"
    entry_m = re.search(r'ENTRY[:\s]+([0-9]+\.?[0-9]*)\s*[-–to]*\s*([0-9]+\.?[0-9]*)?', raw)
    if entry_m:
        lo = float(entry_m.group(1))
        hi = float(entry_m.group(2)) if entry_m.group(2) else lo
        result["entry_low"]  = min(lo, hi)
        result["entry_high"] = max(lo, hi)
        result["entry"]      = round((lo + hi) / 2, 8)

    # target / TP
    tp_m = re.search(r'(?:TARGET|TP|TAKE.?PROFIT)[:\s]+([0-9]+\.?[0-9]*)', raw)
    if tp_m:
        result["tp"] = float(tp_m.group(1))

    # stoploss / SL
    sl_m = re.search(r'(?:STOPLOSS|STOP.?LOSS|SL)[:\s]+([0-9]+\.?[0-9]*)', raw)
    if sl_m:
        result["sl"] = float(sl_m.group(1))

    # R:R
    if "entry" in result and "tp" in result and "sl" in result:
        risk   = abs(result["entry"] - result["sl"])
        reward = abs(result["tp"] - result["entry"])
        result["rr"] = round(reward / risk, 2) if risk > 0 else 0

    return result

def fetch_liq(coin):
    try:
        req = urllib.request.Request(
            f'https://www.okx.com/api/v5/public/liquidation-orders?instType=SWAP&uly={coin}-USDT&state=filled&limit=100',
            headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.load(r)

        longs, shorts = [], []
        for item in data.get('data', []):
            for det in item.get('details', []):
                price = float(det.get('bkPx', 0))
                size  = float(det.get('sz', 0))
                side  = det.get('side', '')
                if side == 'sell': longs.append((price, size))
                elif side == 'buy': shorts.append((price, size))

        return longs, shorts
    except Exception as e:
        return [], []

def fetch_positioning(coin):
    symbol = f"{coin}USDT"
    result = {}
    try:
        for url, key in [
            (f'https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={symbol}&period=1h&limit=1', 'ls'),
            (f'https://fapi.binance.com/futures/data/takerlongshortRatio?symbol={symbol}&period=1h&limit=1', 'taker'),
        ]:
            with urllib.request.urlopen(url, timeout=8) as r:
                d = json.load(r)[0]
            if key == 'ls':
                result['ls_ratio']  = float(d['longShortRatio'])
                result['long_pct']  = round(float(d['longAccount']) * 100, 1)
            elif key == 'taker':
                result['taker']     = float(d['buySellRatio'])
    except Exception:
        pass
    return result

def current_price(coin):
    try:
        with urllib.request.urlopen(f'https://api.binance.com/api/v3/ticker/price?symbol={coin}USDT', timeout=5) as r:
            return float(json.load(r)['price'])
    except Exception:
        return None

def fmt(liqs, label, n=5):
    if not liqs:
        return f"  {label}: no data"
    total = sum(s for _, s in liqs)
    avg   = sum(p * s for p, s in liqs) / total
    top   = sorted(liqs, key=lambda x: x[1], reverse=True)[:n]
    lines = [f"  {label}: {len(liqs)} events | avg ${avg:,.6g}"]
    for p, s in top:
        lines.append(f"    ${p:,.6g} — {s:.2f}")
    return "\n".join(lines)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    raw    = " ".join(sys.argv[1:])
    signal = parse_signal(raw)

    if "coin" not in signal:
        print("❌ Could not parse coin from signal. Check format.")
        sys.exit(1)

    coin = signal["coin"]

    print(f"\n{'═'*55}")
    print(f"  SIGNAL PARSED")
    print(f"{'─'*55}")
    print(f"  Coin:      {coin}")
    print(f"  Direction: {signal.get('direction','?')}")
    if "entry_low" in signal and signal["entry_low"] != signal["entry_high"]:
        print(f"  Entry:     ${signal['entry_low']} – ${signal['entry_high']} (mid ${signal['entry']})")
    else:
        print(f"  Entry:     ${signal.get('entry','?')}")
    print(f"  TP:        ${signal.get('tp','?')}")
    print(f"  SL:        ${signal.get('sl','?')}")
    print(f"  R:R:       {signal.get('rr','?')}:1")

    print(f"\n{'─'*55}")
    print(f"  LIVE DATA — {coin}")
    print(f"{'─'*55}")

    price = current_price(coin)
    if price:
        print(f"  Current price: ${price:,.6g}")

    print(f"\n  Fetching liquidations...")
    longs, shorts = fetch_liq(coin)
    print(fmt(longs,  "LONG LIQS  (cascade risk)"))
    print(fmt(shorts, "SHORT LIQS (squeeze fuel)"))

    print(f"\n  Fetching positioning...")
    pos = fetch_positioning(coin)
    if pos:
        print(f"  L/S Ratio:  {pos.get('ls_ratio','?')} ({pos.get('long_pct','?')}% long)")
        print(f"  Taker B/S:  {pos.get('taker','?')}")

    print(f"\n{'═'*55}")
    print(f"  → Paste this output to Claude for full validation")
    print(f"{'═'*55}\n")
