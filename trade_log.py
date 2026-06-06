#!/usr/bin/env python3
"""
Trade Journal — log every trade, track performance

COMMANDS:
  open  SYMBOL DIRECTION ENTRY SL TP [note]   — open new trade
  close PRICE [note]                           — close active trade
  view                                         — show all trades
  active                                       — show open trade
  stats                                        — win rate, avg P&L, mistakes

EXAMPLES:
  python3 trade_log.py open ENAUSDT long 0.087 0.079 0.108 20000 "friend signal validated"
  python3 trade_log.py open ETHUSDT long 1540 1488 1590 2.75 "liq grab at weekly support"
  python3 trade_log.py close 1590 "hit TP, short squeeze played out"
  python3 trade_log.py close 1488 "SL hit, cascade continued"
  python3 trade_log.py view
  python3 trade_log.py stats
"""

import sys, json, os
from datetime import datetime

LOG_FILE = os.path.join(os.path.dirname(__file__), "trades.json")

def load():
    if not os.path.exists(LOG_FILE):
        return []
    with open(LOG_FILE) as f:
        return json.load(f)

def save(trades):
    with open(LOG_FILE, "w") as f:
        json.dump(trades, f, indent=2)

def active_trade(trades):
    return next((t for t in trades if t["status"] == "open"), None)

def open_trade(args):
    trades = load()

    if active_trade(trades):
        print("❌ Already have an open trade. Close it first.")
        active(trades)
        return

    if len(args) < 5:
        print("Usage: open SYMBOL DIRECTION ENTRY SL TP [SIZE] [note]")
        return

    symbol    = args[0].upper()
    direction = args[1].lower()
    entry     = float(args[2])
    sl        = float(args[3])
    tp        = float(args[4])

    # 6th arg: size (numeric) or start of note
    size = None
    note_start = 5
    if len(args) > 5:
        try:
            size = float(args[5])
            note_start = 6
        except ValueError:
            pass
    note = " ".join(args[note_start:]) if len(args) > note_start else ""

    risk      = abs(entry - sl)
    reward    = abs(tp - entry)
    rr        = round(reward / risk, 2) if risk > 0 else 0

    trade = {
        "id":        len(trades) + 1,
        "symbol":    symbol,
        "direction": direction,
        "entry":     entry,
        "sl":        sl,
        "tp":        tp,
        "rr":        rr,
        "size":      size,
        "open_time": datetime.now().isoformat(),
        "note":      note,
        "status":    "open",
        "exit":      None,
        "exit_time": None,
        "pnl_pct":   None,
        "pnl_usd":   None,
        "pnl_eur":   None,
        "result":    None,
        "close_note": None,
    }

    trades.append(trade)
    save(trades)

    sign = 1 if direction == "long" else -1
    print(f"\n{'─'*50}")
    print(f"  ✅ Trade #{trade['id']} OPENED")
    print(f"  {symbol} {direction.upper()} @ ${entry}")
    print(f"  SL: ${sl}  |  TP: ${tp}  |  R:R {rr}:1")
    if note:
        print(f"  Note: {note}")
    print(f"{'─'*50}\n")

def close_trade(args):
    trades = load()
    trade  = active_trade(trades)

    if not trade:
        print("❌ No open trade to close.")
        return

    if len(args) < 1:
        print("Usage: close PRICE [note]")
        return

    exit_price = float(args[0])
    note       = " ".join(args[1:]) if len(args) > 1 else ""

    entry = trade["entry"]
    if trade["direction"] == "long":
        pnl_pct = round((exit_price - entry) / entry * 100, 2)
    else:
        pnl_pct = round((entry - exit_price) / entry * 100, 2)

    # calculate actual USD and EUR P&L if size is known
    pnl_usd = pnl_eur = None
    if trade.get("size"):
        multiplier = 1 if trade["direction"] == "long" else -1
        pnl_usd = round((exit_price - entry) * trade["size"] * multiplier, 2)
        try:
            with __import__("urllib.request", fromlist=["urlopen"]).urlopen(
                "https://api.frankfurter.app/latest?from=USD&to=EUR", timeout=5
            ) as r:
                eur_rate = __import__("json").load(r)["rates"]["EUR"]
            pnl_eur = round(pnl_usd * eur_rate, 2)
        except Exception:
            pnl_eur = round(pnl_usd / 1.08, 2)  # fallback rate

    result = "WIN" if pnl_pct > 0 else "LOSS" if pnl_pct < 0 else "BREAKEVEN"

    trade["exit"]       = exit_price
    trade["exit_time"]  = datetime.now().isoformat()
    trade["pnl_pct"]    = pnl_pct
    trade["pnl_usd"]    = pnl_usd
    trade["pnl_eur"]    = pnl_eur
    trade["result"]     = result
    trade["close_note"] = note
    trade["status"]     = "closed"

    # prompt for mistakes
    print(f"\n{'─'*50}")
    print(f"  {'✅' if pnl_pct > 0 else '❌'} Trade #{trade['id']} CLOSED — {result}")
    print(f"  {trade['symbol']} {trade['direction'].upper()}")
    print(f"  Entry: ${entry}  →  Exit: ${exit_price}")
    print(f"  P&L: {'+' if pnl_pct >= 0 else ''}{pnl_pct}%", end="")
    if pnl_usd is not None:
        print(f"  |  {'+'if pnl_usd>=0 else ''}${pnl_usd}  |  {'+'if pnl_eur>=0 else ''}€{pnl_eur}", end="")
    print()
    if note:
        print(f"  Note: {note}")
    print(f"{'─'*50}")
    print()

    save(trades)

def active(trades=None):
    if trades is None:
        trades = load()
    trade = active_trade(trades)
    if not trade:
        print("No open trade.")
        return
    print(f"\n{'─'*50}")
    print(f"  OPEN: #{trade['id']} {trade['symbol']} {trade['direction'].upper()}")
    print(f"  Entry: ${trade['entry']}  SL: ${trade['sl']}  TP: ${trade['tp']}")
    print(f"  R:R {trade['rr']}:1  |  Opened: {trade['open_time'][:16].replace('T',' ')}")
    if trade['note']:
        print(f"  Note: {trade['note']}")
    print(f"{'─'*50}\n")

def view():
    trades = load()
    if not trades:
        print("No trades logged yet.")
        return

    print(f"\n{'─'*75}")
    print(f"  {'#':<3} {'Symbol':<10} {'Dir':<6} {'Entry':>8} {'Exit':>8} {'P&L':>7} {'Result':<10} Note")
    print(f"{'─'*75}")

    for t in trades:
        pnl    = f"{'+' if (t['pnl_pct'] or 0) >= 0 else ''}{t['pnl_pct']}%" if t['pnl_pct'] is not None else "OPEN"
        exit_p = f"${t['exit']}" if t['exit'] else "—"
        result = t['result'] or "OPEN"
        note   = (t['close_note'] or t['note'] or "")[:25]
        print(f"  {t['id']:<3} {t['symbol']:<10} {t['direction']:<6} ${t['entry']:>7} {exit_p:>8} {pnl:>7} {result:<10} {note}")

    print(f"{'─'*75}\n")

def stats():
    trades = load()
    closed = [t for t in trades if t["status"] == "closed"]

    if not closed:
        print("No closed trades yet.")
        return

    wins   = [t for t in closed if t["result"] == "WIN"]
    losses = [t for t in closed if t["result"] == "LOSS"]
    pnls   = [t["pnl_pct"] for t in closed if t["pnl_pct"] is not None]

    wr      = round(len(wins) / len(closed) * 100, 1)
    avg_win = round(sum(t["pnl_pct"] for t in wins) / len(wins), 2) if wins else 0
    avg_loss= round(sum(t["pnl_pct"] for t in losses) / len(losses), 2) if losses else 0
    total   = round(sum(pnls), 2)

    print(f"\n{'─'*50}")
    print(f"  TRADE STATS ({len(closed)} closed trades)")
    print(f"{'─'*50}")
    print(f"  Win rate:    {wr}%  ({len(wins)}W / {len(losses)}L)")
    print(f"  Avg win:     +{avg_win}%")
    print(f"  Avg loss:    {avg_loss}%")
    print(f"  Total P&L:   {'+' if total >= 0 else ''}{total}%")
    print(f"{'─'*50}")
    print(f"\n  Recent trades:")
    for t in closed[-5:]:
        icon = "✅" if t["result"] == "WIN" else "❌"
        print(f"  {icon} {t['symbol']} {t['direction']} → {'+' if t['pnl_pct'] >= 0 else ''}{t['pnl_pct']}%  |  {t['close_note'] or t['note'] or '—'}")
    print()

if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)

    cmd = args[0].lower()

    if cmd == "open":
        open_trade(args[1:])
    elif cmd == "close":
        close_trade(args[1:])
    elif cmd == "active":
        active()
    elif cmd == "view":
        view()
    elif cmd == "stats":
        stats()
    else:
        print(__doc__)
