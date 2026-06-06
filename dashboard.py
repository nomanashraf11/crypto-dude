#!/usr/bin/env python3
"""
Trade Dashboard — generates a visual HTML report and opens in browser
Usage: python3 dashboard.py
"""

import json, os, webbrowser, tempfile, subprocess, base64, hashlib, hmac, time, urllib.request
from datetime import datetime

LOG_FILE = os.path.join(os.path.dirname(__file__), "trades.json")

def keychain_get(k):
    r = subprocess.run(["security","find-generic-password","-a","kraken_futures","-s",k,"-w"], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None

def kraken_account():
    try:
        KEY = keychain_get("KRAKEN_KEY")
        SEC = keychain_get("KRAKEN_SECRET")
        if not KEY or not SEC: return None
        nonce = str(int(time.time()*1000))
        ep    = "/derivatives/api/v3/accounts"
        msg   = nonce + ep.replace("/derivatives","")
        sha   = hashlib.sha256(msg.encode()).digest()
        sig   = base64.b64encode(hmac.new(base64.b64decode(SEC), sha, hashlib.sha512).digest()).decode()
        req   = urllib.request.Request("https://futures.kraken.com"+ep, headers={"APIKey":KEY,"Nonce":nonce,"Authent":sig})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)
    except Exception:
        return None

def load():
    if not os.path.exists(LOG_FILE):
        return []
    with open(LOG_FILE) as f:
        return json.load(f)

def generate(trades, account=None):
    closed = [t for t in trades if t["status"] == "closed"]
    open_t = [t for t in trades if t["status"] == "open"]
    wins   = [t for t in closed if t["result"] == "WIN"]
    losses = [t for t in closed if t["result"] == "LOSS"]
    pnls   = [t["pnl_pct"] for t in closed if t["pnl_pct"] is not None]

    wr         = round(len(wins) / len(closed) * 100, 1) if closed else 0
    avg_win    = round(sum(t["pnl_pct"] for t in wins) / len(wins), 2) if wins else 0
    avg_loss   = round(sum(t["pnl_pct"] for t in losses) / len(losses), 2) if losses else 0
    total      = round(sum(pnls), 2) if pnls else 0
    total_eur  = round(sum(t["pnl_eur"] for t in closed if t.get("pnl_eur")), 2)
    total_usd  = round(sum(t["pnl_usd"] for t in closed if t.get("pnl_usd")), 2)

    # account balance
    acc_eur = acc_usd = unrealized_usd = port_val = None
    if account:
        flex = account.get("accounts", {}).get("flex", {})
        cur  = flex.get("currencies", {})
        acc_eur       = cur.get("EUR", {}).get("quantity", 0)
        acc_usd       = cur.get("USD", {}).get("quantity", 0)
        unrealized_usd= round(flex.get("pnl", 0), 2)
        port_val      = round(flex.get("portfolioValue", 0), 2)
        eur_rate      = cur.get("EUR",{}).get("value",0) / acc_eur if acc_eur else 1.08
        acc_eur_total = round(acc_eur + acc_usd / eur_rate, 2)

    # account bar HTML
    if account:
        unr_cls = "green" if unrealized_usd >= 0 else "red"
        unr_sign = "+" if unrealized_usd >= 0 else ""
        account_bar = f"""<div class="account-bar">
  <div class="acc-item"><span class="acc-label">Account EUR</span><span class="acc-val">€{round(acc_eur,2):,}</span></div>
  <div class="acc-item"><span class="acc-label">Account USD</span><span class="acc-val">${round(acc_usd,2):,}</span></div>
  <div class="acc-item"><span class="acc-label">Total Balance</span><span class="acc-val green">€{acc_eur_total:,}</span></div>
  <div class="acc-item"><span class="acc-label">Unrealized P&L</span><span class="acc-val {unr_cls}">{unr_sign}${unrealized_usd}</span></div>
  <div class="acc-item"><span class="acc-label">Portfolio Value</span><span class="acc-val">${port_val:,}</span></div>
</div>"""
    else:
        account_bar = ""

    def trade_rows():
        rows = ""
        for t in reversed(trades):
            is_open  = t["status"] == "open"
            pnl      = t["pnl_pct"]
            pnl_str  = f"+{pnl}%" if pnl and pnl > 0 else (f"{pnl}%" if pnl else "—")
            pnl_cls  = "win" if pnl and pnl > 0 else ("loss" if pnl and pnl < 0 else "")
            result   = t["result"] or "OPEN"
            res_cls  = "badge-win" if result == "WIN" else ("badge-loss" if result == "LOSS" else "badge-open")
            dir_cls  = "long" if t["direction"] == "long" else "short"
            note     = t.get("close_note") or t.get("note") or "—"
            date     = t["open_time"][:10] if t.get("open_time") else "—"
            exit_p   = f"${t['exit']}" if t.get("exit") else "—"
            rr       = t.get("rr", "—")

            rows += f"""
            <tr class="{'open-row' if is_open else ''}">
                <td class="id">#{t['id']}</td>
                <td><span class="symbol">{t['symbol'].replace('USDT','')}</span></td>
                <td><span class="dir {dir_cls}">{t['direction'].upper()}</span></td>
                <td>${t['entry']}</td>
                <td>{exit_p}</td>
                <td>${t['sl']}</td>
                <td>${t['tp']}</td>
                <td class="rr">{rr}:1</td>
                <td class="{pnl_cls} pnl">{pnl_str}</td>
                <td class="{pnl_cls} pnl">{('+'if (t.get('pnl_eur') or 0)>=0 else '')+'€'+str(t['pnl_eur']) if t.get('pnl_eur') is not None else '—'}</td>
                <td><span class="badge {res_cls}">{result}</span></td>
                <td class="note">{note}</td>
                <td class="date">{date}</td>
            </tr>"""
        return rows

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Trade Journal</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    background: #0d1117;
    color: #e6edf3;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    font-size: 14px;
    min-height: 100vh;
    padding: 32px 24px;
  }}
  h1 {{
    font-size: 22px;
    font-weight: 600;
    color: #f0f6fc;
    margin-bottom: 6px;
  }}
  .subtitle {{
    color: #7d8590;
    font-size: 13px;
    margin-bottom: 32px;
  }}

  /* Stats */
  .stats {{
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    gap: 16px;
    margin-bottom: 32px;
  }}
  .stat {{
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 10px;
    padding: 20px;
  }}
  .stat-label {{
    color: #7d8590;
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-bottom: 10px;
  }}
  .stat-value {{
    font-size: 28px;
    font-weight: 700;
    color: #f0f6fc;
  }}
  .stat-value.green {{ color: #3fb950; }}
  .stat-value.red   {{ color: #f85149; }}
  .stat-value.blue  {{ color: #58a6ff; }}

  /* Open trade banner */
  .open-banner {{
    background: #1c2128;
    border: 1px solid #388bfd44;
    border-left: 3px solid #388bfd;
    border-radius: 10px;
    padding: 16px 20px;
    margin-bottom: 24px;
    display: flex;
    align-items: center;
    gap: 24px;
  }}
  .open-banner .label {{
    background: #388bfd22;
    color: #58a6ff;
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
  }}
  .open-banner .info {{ color: #e6edf3; font-size: 13px; }}
  .open-banner .info span {{ color: #7d8590; margin-right: 4px; }}

  /* Table */
  .table-wrap {{
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 10px;
    overflow: hidden;
  }}
  .table-title {{
    padding: 16px 20px;
    font-weight: 600;
    font-size: 14px;
    color: #f0f6fc;
    border-bottom: 1px solid #21262d;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
  }}
  th {{
    padding: 10px 16px;
    text-align: left;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: #7d8590;
    background: #0d1117;
    border-bottom: 1px solid #21262d;
  }}
  td {{
    padding: 12px 16px;
    border-bottom: 1px solid #21262d;
    color: #c9d1d9;
  }}
  tr:last-child td {{ border-bottom: none; }}
  tr:hover td {{ background: #1c2128; }}
  tr.open-row td {{ background: #1c2128; }}

  .id    {{ color: #7d8590; font-size: 12px; }}
  .symbol {{ font-weight: 600; color: #f0f6fc; }}
  .dir   {{ padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }}
  .long  {{ background: #1a3a2a; color: #3fb950; }}
  .short {{ background: #3a1a1a; color: #f85149; }}
  .pnl   {{ font-weight: 600; }}
  .win   {{ color: #3fb950; }}
  .loss  {{ color: #f85149; }}
  .rr    {{ color: #7d8590; }}
  .note  {{ color: #7d8590; font-size: 12px; max-width: 200px; }}
  .date  {{ color: #7d8590; font-size: 12px; }}

  .badge {{
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 600;
  }}
  .badge-win  {{ background: #1a3a2a; color: #3fb950; }}
  .badge-loss {{ background: #3a1a1a; color: #f85149; }}
  .badge-open {{ background: #1a2a3a; color: #58a6ff; }}

  .empty {{
    text-align: center;
    padding: 60px;
    color: #7d8590;
  }}

  .account-bar {{
    display: flex;
    gap: 0;
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 10px;
    padding: 16px 24px;
    margin-bottom: 24px;
    justify-content: space-between;
  }}
  .acc-item {{ display: flex; flex-direction: column; gap: 6px; }}
  .acc-label {{ color: #7d8590; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; }}
  .acc-val {{ font-size: 20px; font-weight: 700; color: #f0f6fc; }}
  .acc-val.green {{ color: #3fb950; }}
  .acc-val.red   {{ color: #f85149; }}

  .updated {{
    text-align: right;
    color: #7d8590;
    font-size: 12px;
    margin-top: 24px;
  }}
</style>
</head>
<body>

<h1>Trade Journal</h1>
<p class="subtitle">visioneth · {len(trades)} total trades · {len(open_t)} open</p>

{account_bar}

<div class="stats">
  <div class="stat">
    <div class="stat-label">Win Rate</div>
    <div class="stat-value {'green' if wr >= 50 else 'red'}">{wr}%</div>
  </div>
  <div class="stat">
    <div class="stat-label">Total P&L %</div>
    <div class="stat-value {'green' if total >= 0 else 'red'}">{'+' if total >= 0 else ''}{total}%</div>
  </div>
  <div class="stat">
    <div class="stat-label">Total P&L €</div>
    <div class="stat-value {'green' if total_eur >= 0 else 'red'}">{'+' if total_eur >= 0 else ''}€{total_eur}</div>
  </div>
  <div class="stat">
    <div class="stat-label">Avg Win</div>
    <div class="stat-value green">+{avg_win}%</div>
  </div>
  <div class="stat">
    <div class="stat-label">Trades</div>
    <div class="stat-value blue">{len(wins)}W / {len(losses)}L</div>
  </div>
</div>

{''.join(f'''
<div class="open-banner">
  <span class="label">🟢 Live Trade</span>
  <div class="info"><span>Coin</span>{t["symbol"].replace("USDT","")}</div>
  <div class="info"><span>Direction</span>{t["direction"].upper()}</div>
  <div class="info"><span>Entry</span>${t["entry"]}</div>
  <div class="info"><span>SL</span>${t["sl"]}</div>
  <div class="info"><span>TP</span>${t["tp"]}</div>
  <div class="info"><span>R:R</span>{t.get("rr","—")}:1</div>
  <div class="info"><span>Note</span>{t.get("note","—")}</div>
</div>''' for t in open_t)}

<div class="table-wrap">
  <div class="table-title">Trade History</div>
  {'<div class="empty">No trades logged yet.<br><br>Run: python3 trade_log.py open ETHUSDT long 1540 1488 1590</div>' if not trades else f'''
  <table>
    <thead>
      <tr>
        <th>#</th><th>Coin</th><th>Dir</th><th>Entry</th><th>Exit</th>
        <th>SL</th><th>TP</th><th>R:R</th><th>P&L %</th><th>P&L €</th><th>Result</th>
        <th>Note</th><th>Date</th>
      </tr>
    </thead>
    <tbody>{trade_rows()}</tbody>
  </table>'''}
</div>

<p class="updated">Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
</body>
</html>"""
    return html

if __name__ == "__main__":
    trades  = load()
    account = kraken_account()
    html    = generate(trades, account)

    tmp = tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w")
    tmp.write(html)
    tmp.close()

    webbrowser.open(f"file://{tmp.name}")
    print(f"✅ Dashboard opened in browser")
    print(f"   Run again to refresh after new trades")
