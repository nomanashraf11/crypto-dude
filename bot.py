import requests
import json
import time
import os
from datetime import datetime

# ─── CONFIG ───────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID          = os.environ.get("CHAT_ID")
PUSHOVER_TOKEN   = os.environ.get("PUSHOVER_TOKEN")
PUSHOVER_USER    = os.environ.get("PUSHOVER_USER")
ALERTS_FILE      = os.environ.get("ALERTS_FILE", "alerts.json")
CHECK_INTERVAL   = int(os.environ.get("CHECK_INTERVAL", "60"))
# ──────────────────────────────────────────────────────

def get_price(symbol):
    coin = symbol.upper().replace("USDT", "")

    # 1. Binance
    try:
        r = requests.get(f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}", timeout=10)
        data = r.json()
        if "price" in data:
            return float(data["price"])
    except Exception:
        pass

    # 2. CryptoCompare (very reliable, no geo block)
    try:
        r = requests.get(
            f"https://min-api.cryptocompare.com/data/price?fsym={coin}&tsyms=USD",
            timeout=10, headers={"User-Agent": "Mozilla/5.0"}
        )
        data = r.json()
        if "USD" in data:
            return float(data["USD"])
    except Exception:
        pass

    # 3. CoinGecko fallback
    try:
        coin_map = {
            "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
            "ZEC": "zcash", "BNB": "binancecoin", "WLD": "worldcoin-wld",
        }
        coin_id = coin_map.get(coin, coin.lower())
        r = requests.get(
            f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies=usd",
            timeout=10, headers={"User-Agent": "Mozilla/5.0"}
        )
        data = r.json()
        if coin_id in data:
            return float(data[coin_id]["usd"])
    except Exception:
        pass

    raise Exception(f"All sources failed for {symbol}")

def send_pushover(title, message, priority=1):
    if not PUSHOVER_TOKEN or not PUSHOVER_USER:
        return
    try:
        requests.post("https://api.pushover.net/1/messages.json", data={
            "token":    PUSHOVER_TOKEN,
            "user":     PUSHOVER_USER,
            "title":    title,
            "message":  message,
            "priority": priority,  # 1 = high priority (bypasses quiet hours), 2 = requires ack
            "sound":    "siren",
        }, timeout=10)
    except Exception as e:
        print(f"Pushover error: {e}")

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }, timeout=10)

def load_alerts():
    with open(ALERTS_FILE) as f:
        return json.load(f)

def save_alerts(alerts):
    with open(ALERTS_FILE, "w") as f:
        json.dump(alerts, f, indent=2)

def check_alerts():
    alerts = load_alerts()
    triggered = []

    for alert in alerts:
        try:
            price = get_price(alert["symbol"])
            hit = (
                alert["direction"] == "below" and price <= alert["price"]
            ) or (
                alert["direction"] == "above" and price >= alert["price"]
            )

            if hit:
                emoji = "📉" if alert["direction"] == "below" else "📈"
                msg = (
                    f"{emoji} <b>ALERT: {alert['coin']}</b>\n\n"
                    f"Target: ${alert['price']:,}\n"
                    f"Current: ${price:,.2f}\n"
                    f"Direction: {alert['direction'].upper()}\n\n"
                    f"📝 {alert['note']}\n\n"
                    f"⏰ {datetime.now().strftime('%H:%M:%S')}\n\n"
                    f"👉 Ask Claude to analyze before entering!"
                )
                send_telegram(msg)
                send_pushover(
                    title=f"{'📉' if alert['direction'] == 'below' else '📈'} {alert['coin']} ALERT",
                    message=f"${price:,.2f} hit {alert['direction']} ${alert['price']:,}\n\n{alert['note']}"
                )
                print(f"✅ Alert sent: {alert['coin']} @ ${price:,.2f}")
                triggered.append(alert)

        except Exception as e:
            print(f"Error checking {alert.get('coin','?')}: {e}")

    if triggered:
        remaining = [a for a in alerts if a not in triggered]
        save_alerts(remaining)
        print(f"Removed {len(triggered)} triggered. {len(remaining)} remaining.")

def add_alert(coin, price, direction, note=""):
    alerts = load_alerts()
    alerts.append({
        "coin": coin.upper(),
        "symbol": coin.upper() + "USDT",
        "price": float(price),
        "direction": direction.lower(),
        "note": note or f"{coin.upper()} {direction} ${price}"
    })
    save_alerts(alerts)
    print(f"✅ Added: {coin.upper()} {direction} ${price}")

def list_alerts():
    alerts = load_alerts()
    if not alerts:
        print("No active alerts.")
        return
    print(f"\n{'─'*45}")
    print(f"{'#':<3} {'Coin':<8} {'Dir':<8} {'Price':>10}  Note")
    print(f"{'─'*45}")
    for i, a in enumerate(alerts, 1):
        print(f"{i:<3} {a['coin']:<8} {a['direction']:<8} ${a['price']:>9,.0f}  {a['note'][:25]}")
    print(f"{'─'*45}\n")

def main():
    print(f"🚀 Crypto Alert Bot started — {len(load_alerts())} alerts active")
    try:
        send_telegram(
            f"🤖 <b>Crypto Alert Bot Online!</b>\n\n"
            f"Monitoring {len(load_alerts())} alert(s)\n"
            f"Interval: every {CHECK_INTERVAL}s\n\n"
            f"I will ping you when price hits your level 🎯"
        )
        send_pushover(
            title="Crypto Alert Bot Online",
            message=f"Monitoring {len(load_alerts())} alert(s). Will ring when price hits.",
            priority=0
        )
    except Exception as e:
        print(f"Startup message failed: {e}")

    while True:
        try:
            now = datetime.now().strftime("%H:%M:%S")
            alerts = load_alerts()
            if alerts:
                print(f"[{now}] Checking {len(alerts)} alert(s)...", end=" ", flush=True)
                check_alerts()
                print("✓")
            else:
                print(f"[{now}] No active alerts.")
        except Exception as e:
            print(f"Loop error: {e}")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "add" and len(sys.argv) >= 5:
            note = " ".join(sys.argv[5:]) if len(sys.argv) > 5 else ""
            add_alert(sys.argv[2], sys.argv[3], sys.argv[4], note)
        elif cmd == "list":
            list_alerts()
        elif cmd == "test":
            print("Sending test message...")
            send_telegram("✅ Bot test successful! Alerts are working.")
            print("Done.")
        elif cmd == "remove":
            idx = int(sys.argv[2]) - 1
            alerts = load_alerts()
            removed = alerts.pop(idx)
            save_alerts(alerts)
            print(f"Removed: {removed['coin']} {removed['direction']} ${removed['price']}")
    else:
        main()
