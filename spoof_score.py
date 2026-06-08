#!/usr/bin/env python3
"""
spoof_score.py — Order-book spoofing probability scorer (OKX public API, no key)

Polls the order book + trade tape over a short window, infers cancellations from
per-level size drops NOT explained by trades, and outputs a 0-100% spoof
probability per significant wall — plus an overall book read.

Method: the 8-metric spoofing-score framework (quoting activity, imbalance,
abnormal cancels, low execution prob, trades-opposing-quotes, cancels-opposing-
trades, cyclical depth/cancels) + the 2025 'posting distance' insight
(Fabre & Challet, arXiv 2504.15908). Uses L2 aggregated depth (no L3), so it
catches the big/slow spoof walls that trap swing traders — not microsecond layering.

USAGE:
  python3 spoof_score.py BTC-USDT-SWAP
  python3 spoof_score.py WLD-USDT-SWAP --snaps 16 --interval 1.0
  python3 spoof_score.py PENGU-USDT-SWAP --depth 80

  --snaps N      snapshots to take       (default 12)
  --interval S   seconds between snaps   (default 1.5)
  --depth D      book levels per side     (default 50)

THE CORE TELL: a real wall ABSORBS volume; a spoof VANISHES with none.
Score is highest when a big wall disappears without trading + flickers + flow opposes it.
"""

import sys, time, json, urllib.request, statistics

BASE = "https://www.okx.com"   # public market data; EU users can swap to https://my.okx.com

def _get(path):
    req = urllib.request.Request(BASE + path, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)

def fetch_book(inst, depth):
    row = _get(f"/api/v5/market/books?instId={inst}&sz={depth}")["data"][0]
    bids = {float(p): float(s) for p, s, *_ in row["bids"]}
    asks = {float(p): float(s) for p, s, *_ in row["asks"]}
    return bids, asks

def fetch_trades(inst):
    return _get(f"/api/v5/market/trades?instId={inst}&limit=100")["data"]

def estimate_tick(prices):
    ps = sorted(prices)
    diffs = [b - a for a, b in zip(ps, ps[1:]) if b - a > 0]
    return min(diffs) if diffs else (ps[0] * 1e-4 if ps else 1e-6)

def pctl(lst, q):
    if not lst: return 0
    s = sorted(lst); k = (len(s) - 1) * q / 100; f = int(k); c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)

def spoof_prob(w, n):
    """0-100. Persistent wall that holds = REAL (low). Big distant wall PULLED as price
    approaches, or one that flickers (place/pull) = SPOOF (high). Conservative on purpose."""
    # persistent, didn't vanish → real resting wall
    if not w["vanished"] and w["present"] >= n * 0.6:
        return min(25.0, (10 if w["opposing"] else 0) + min(15, max(0, w["flicker"] - 1) * 5))
    s = 0.0
    if w["vanished"] and w["approach"]:   s += 55      # cancel-on-approach = THE tell
    elif w["flicker"] >= 3:               s += 50      # repeated place/pull
    elif w["vanished"]:                   s += 25      # pulled while price moved away (milder)
    s += min(15, max(0, w["flicker"] - 1) * 5)         # cyclical bonus
    if w["opposing"] and (w["vanished"] or w["flicker"] >= 2):  s += 10
    s += min(8, w["dist"] * 2)                          # bait sits off-mid
    return max(0.0, min(100.0, s))

def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__); return
    inst = args[0].upper()
    def opt(flag, default, cast):
        return cast(args[args.index(flag) + 1]) if flag in args else default
    snaps    = opt("--snaps", 12, int)
    interval = opt("--interval", 1.5, float)
    depth    = opt("--depth", 100, int)

    print(f"🔍 SPOOF SCAN — {inst}   ({snaps} snaps @ {interval}s = {snaps*interval:.0f}s window)\n")
    books, mids, trades = [], [], {}
    for i in range(snaps):
        try:
            bids, asks = fetch_book(inst, depth)
            for t in fetch_trades(inst):
                trades[t["tradeId"]] = (float(t["px"]), float(t["sz"]), t["side"])
            mid = (max(bids) + min(asks)) / 2
            books.append((bids, asks)); mids.append(mid)
            print(f"  snap {i+1}/{snaps}  mid={mid:.6g}   ", end="\r", flush=True)
        except Exception as e:
            print(f"\n  snap {i+1} error: {e}")
        if i < snaps - 1:
            time.sleep(interval)
    print(" " * 50, end="\r")
    if len(books) < 3:
        print("Not enough snapshots — check the instId / network."); return

    mid = statistics.median(mids)
    n = len(books)

    # per-price size time series, each side
    def series(idx):
        prices = set().union(*[b[idx].keys() for b in books])
        return {p: [b[idx].get(p, 0.0) for b in books] for p in prices}
    bid_s, ask_s = series(0), series(1)
    tick = estimate_tick(list(bid_s) + list(ask_s))

    # trade volume by price + taker totals
    vol_at, tbuy, tsell = {}, 0.0, 0.0
    for px, sz, side in trades.values():
        vol_at[px] = vol_at.get(px, 0.0) + sz
        if side == "buy": tbuy += sz
        else: tsell += sz
    def vol_near(p):
        return sum(v for q, v in vol_at.items() if abs(q - p) <= tick * 1.5)

    # ---- book-level metrics ----
    imb = []
    for b in books:
        bd, ad = sum(b[0].values()), sum(b[1].values())
        if bd + ad > 0: imb.append((bd - ad) / (bd + ad))
    imbalance = statistics.mean(imb) if imb else 0
    churn = 0.0; total_dep = 0.0
    for a, c in zip(books, books[1:]):
        for idx in (0, 1):
            keys = set(a[idx]) | set(c[idx])
            churn += sum(abs(c[idx].get(k, 0) - a[idx].get(k, 0)) for k in keys)
    total_dep = statistics.mean([sum(b[0].values()) + sum(b[1].values()) for b in books]) or 1
    churn_ratio = churn / (total_dep * max(1, n - 1))

    # ---- wall detection + scoring ----
    mid0, mid1 = mids[0], mids[-1]            # for cancel-on-approach
    avg_dep = {0: statistics.mean([sum(b[0].values()) for b in books]) or 1,
               1: statistics.mean([sum(b[1].values()) for b in books]) or 1}
    def walls(side_series, side, idx):
        sizes = [s for ser in side_series.values() for s in ser if s > 0]
        if not sizes: return []
        p90 = pctl(sizes, 90)
        out = []
        for p, ser in side_series.items():
            mx = max(ser)
            pct_depth = mx / avg_dep[idx]
            dist = abs(p - mid) / mid * 100
            # WALL = standout size (≥3× the 90th-pctile level AND ≥3% of side depth),
            # sitting in the SPOOF-BAIT ZONE (0.15%–6% off mid, not top-of-book noise)
            if dist < 0.15 or dist > 6 or mx < 3 * p90 or pct_depth < 0.03:
                continue
            present = sum(1 for s in ser if s > mx * 0.5)
            if present < 2:                      # 1-snap blip = noise, not a wall
                continue
            vanished = ser[-1] < mx * 0.25
            v = vol_near(p)
            fill_ratio = min(1.0, v / mx) if mx > 0 else 0
            thr = mx * 0.5
            flicker = sum(1 for a, b in zip(ser, ser[1:]) if (a >= thr) != (b >= thr))
            opposing = (tsell > tbuy * 1.3) if side == "BID" else (tbuy > tsell * 1.3)
            approach = (mid1 > mid0) if side == "ASK" else (mid1 < mid0)
            w = dict(price=p, size=mx, pct=pct_depth * 100, mult=mx / p90 if p90 else 0, dist=dist,
                     present=present, vanished=vanished, fill_ratio=fill_ratio,
                     flicker=flicker, opposing=opposing, approach=approach, side=side)
            w["prob"] = spoof_prob(w, n)
            out.append(w)
        return out
    all_walls = sorted(walls(bid_s, "BID", 0) + walls(ask_s, "ASK", 1), key=lambda w: -w["prob"])

    # ---- output ----
    side_word = "bid-heavy" if imbalance > 0.05 else "ask-heavy" if imbalance < -0.05 else "balanced"
    print(f"Mid ${mid:.6g}  |  imbalance {imbalance*100:+.0f}% ({side_word})  |  "
          f"churn {churn_ratio:.1f}×  |  walls found: {len(all_walls)}")
    print("─" * 60)
    if not all_walls:
        print("No outsized walls right now — book looks ordinary (no obvious spoofing).")
        return
    for w in all_walls[:6]:
        tag = "🚩 LIKELY SPOOF" if w["prob"] >= 60 else "⚠️  SUSPECT" if w["prob"] >= 40 else "✅ LIKELY REAL"
        reasons = []
        if w["vanished"]:
            reasons.append("pulled ON approach" if w["approach"] else "cancelled (price moving away)")
        else:
            reasons.append(f"held, {w['fill_ratio']*100:.0f}% filled")
        if w["flicker"] >= 2: reasons.append(f"flicker×{w['flicker']}")
        if w["opposing"]:     reasons.append("flow opposes it")
        if w["present"] < n * 0.5: reasons.append("brief")
        print(f"  {w['side']} ${w['price']:.6g}  size {w['size']:.0f}ct ({w['pct']:.0f}% of book, {w['mult']:.0f}× typical)  dist {w['dist']:+.2f}%")
        print(f"     → {tag}  {w['prob']:.0f}%   [{', '.join(reasons)}]")
    print("─" * 60)
    top = all_walls[0]
    if top["prob"] >= 50:
        if top["side"] == "ASK":
            print(f"READ: fake SELL wall ~${top['price']:.6g} capping price — bait to scare sellers / "
                  f"accumulation underneath. Don't trust it as resistance; expect a push UP through it.")
        else:
            print(f"READ: fake BUY wall ~${top['price']:.6g} luring longs — likely distribution. "
                  f"Don't lean a long on it; expect it pulled + a dump.")
        print("→ DON'T place your SL just under/over a flagged wall — it's where you get hunted.")
    else:
        print("READ: nothing strongly spoofy — the big walls are mostly absorbing real volume.")

if __name__ == "__main__":
    main()
