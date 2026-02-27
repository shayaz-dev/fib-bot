# -*- coding: utf-8 -*-
"""
Fibonacci 4H Level Monitor — Monitor ANY coin on Kraken
Token is loaded from environment variable (safe for GitHub)

COMMANDS:
  /start      — Welcome + instructions
  /add SOL    — Start monitoring any coin
  /remove SOL — Stop monitoring a coin
  /coins      — Show currently monitored coins
  /fibs       — Show current Fib levels
  /stop       — Stop the bot
"""

import time
import threading
import requests
import os

# ------------------ TELEGRAM CONFIG ------------------
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
MY_CHAT_ID     = int(os.environ.get('CHAT_ID', '7822181453'))

if not TELEGRAM_TOKEN:
    print("ERROR: TELEGRAM_TOKEN not set!")
    exit(1)

# ------------------ FIB CONFIG ------------------
FIB_RATIOS = [0.5, 0.618, 1.0, 1.272, 1.618]
FIB_LABELS = {
    0.5:   '0.500 (Mid)',
    0.618: '0.618 (Golden)',
    1.0:   '1.000 (Full Retrace)',
    1.272: '1.272 (Extension)',
    1.618: '1.618 (Golden Ext)',
}

ALERT_THRESHOLD_PCT = 0.5
CHECK_INTERVAL      = 240
CANDLE_LIMIT        = 100

# ------------------ STATE ------------------
monitored_coins  = {}
last_alert_state = {}
last_update_id   = 0
bot_running      = True
pair_cache       = {}


# ------------------ TELEGRAM ------------------
def send_message(text):
    url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": MY_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try:
        r = requests.post(url, data=data, timeout=10)
        if not r.json().get("ok"):
            print(f"Telegram failed: {r.json()}")
    except Exception as e:
        print(f"Telegram error: {e}")


def get_updates(offset=0):
    url    = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    params = {"timeout": 10, "offset": offset, "allowed_updates": ["message"]}
    try:
        r = requests.get(url, params=params, timeout=15)
        return r.json().get("result", [])
    except Exception:
        return []


# ------------------ KRAKEN PAIR FINDER ------------------
def find_kraken_pair(symbol):
    symbol = symbol.upper().strip()

    if symbol in pair_cache:
        return pair_cache[symbol]

    # Common Kraken name remaps
    remap = {'BTC': 'XBT', 'DOGE': 'XDG'}
    kraken_base = remap.get(symbol, symbol)

    # Try direct common formats first
    candidates = [
        symbol + 'USD',
        symbol + 'USDT',
        kraken_base + 'USD',
        'X' + kraken_base + 'ZUSD',
    ]

    try:
        r    = requests.get("https://api.kraken.com/0/public/AssetPairs", timeout=15)
        data = r.json().get("result", {})

        # Try direct candidates
        for c in candidates:
            if c in data:
                pair_cache[symbol] = c
                return c

        # Search by wsname e.g. "SOL/USD"
        for pair_key, info in data.items():
            wsname = info.get("wsname", "")
            if wsname.upper().startswith(symbol + "/"):
                pair_cache[symbol] = pair_key
                return pair_key

        return None
    except Exception as e:
        print(f"Pair lookup error: {e}")
        return None


# ------------------ KRAKEN DATA ------------------
def get_ohlcv_4h(pair):
    url    = "https://api.kraken.com/0/public/OHLC"
    params = {"pair": pair, "interval": 240}
    try:
        r = requests.get(url, params=params, timeout=15)
        data = r.json()
        if data.get("error"):
            return []
        key = list(data["result"].keys())[0]
        return [{"High": float(c[2]), "Low": float(c[3]), "Close": float(c[4])}
                for c in data["result"][key][-CANDLE_LIMIT:]]
    except Exception as e:
        print(f"OHLCV error {pair}: {e}")
        return []


def get_current_price(pair):
    url = "https://api.kraken.com/0/public/Ticker"
    try:
        r    = requests.get(url, params={"pair": pair}, timeout=10)
        data = r.json()
        key  = list(data["result"].keys())[0]
        return float(data["result"][key]["c"][0])
    except:
        return None


# ------------------ HELPERS ------------------
def fmt(price):
    if price < 0.0001:  return f"${price:.8f}"
    elif price < 1:     return f"${price:.6f}"
    elif price < 1000:  return f"${price:.4f}"
    else:               return f"${price:,.2f}"


def get_fib_levels(high, low):
    diff = high - low
    return {r: high - diff * r for r in FIB_RATIOS}


# ------------------ COMMANDS ------------------
def cmd_start():
    send_message(
        "📐 *4H Fibonacci Monitor*\n\n"
        "Monitor *any coin on Kraken!*\n\n"
        "*Levels:* `0.5 · 0.618 · 1.0 · 1.272 · 1.618`\n\n"
        "*Commands:*\n"
        "`/add SOL` — Monitor any coin\n"
        "`/remove SOL` — Stop monitoring\n"
        "`/coins` — Show monitored coins\n"
        "`/fibs` — Show live Fib levels\n"
        "`/stop` — Stop the bot"
    )


def cmd_add(coin):
    if not coin:
        send_message("Usage: `/add SOL`")
        return
    send_message(f"🔍 Looking up *{coin.upper()}*...")
    pair = find_kraken_pair(coin)
    if not pair:
        send_message(f"❌ *{coin.upper()}* not found on Kraken.")
        return
    if pair in monitored_coins:
        send_message(f"ℹ️ Already monitoring *{coin.upper()}*")
        return
    monitored_coins[pair]  = coin.upper()
    last_alert_state[pair] = {r: None for r in FIB_RATIOS}
    send_message(f"✅ Monitoring *{coin.upper()}* (`{pair}`)\nAlerts on: `0.5 · 0.618 · 1.0 · 1.272 · 1.618`")


def cmd_remove(coin):
    if not coin:
        send_message("Usage: `/remove SOL`")
        return
    found = None
    for pair, sym in monitored_coins.items():
        if sym == coin.upper():
            found = pair
            break
    if not found:
        pair = find_kraken_pair(coin)
        if pair and pair in monitored_coins:
            found = pair
    if not found:
        send_message(f"❌ *{coin.upper()}* not in monitored list.")
        return
    sym = monitored_coins.pop(found)
    last_alert_state.pop(found, None)
    send_message(f"🗑 Stopped monitoring *{sym}*")


def cmd_coins():
    if not monitored_coins:
        send_message("ℹ️ No coins monitored. Use `/add SOL`")
        return
    lines = ["📡 *Currently Monitoring:*\n"]
    for pair, sym in monitored_coins.items():
        lines.append(f"  • *{sym}* `({pair})`")
    send_message("\n".join(lines))


def cmd_fibs():
    if not monitored_coins:
        send_message("ℹ️ No coins. Use `/add SOL`")
        return
    send_message("⏳ Fetching levels...")
    for pair, sym in list(monitored_coins.items()):
        try:
            candles = get_ohlcv_4h(pair)
            if not candles:
                send_message(f"⚠️ No data for {sym}")
                continue
            high  = max(c["High"] for c in candles)
            low   = min(c["Low"]  for c in candles)
            fib   = get_fib_levels(high, low)
            price = get_current_price(pair)
            lines = [f"📐 *{sym} — 4H Fibonacci*\n"]
            lines.append(f"📈 High: `{fmt(high)}` | 📉 Low: `{fmt(low)}`")
            lines.append(f"💰 Now:  `{fmt(price) if price else 'N/A'}`\n")
            for ratio in FIB_RATIOS:
                lvl   = fib[ratio]
                arrow = "🔼" if price and price > lvl else "🔽"
                dist  = f"{((price-lvl)/lvl*100):+.2f}%" if price else ""
                lines.append(f"  `{ratio:.3f}` → `{fmt(lvl)}` {arrow} {dist}")
            send_message("\n".join(lines))
            time.sleep(2)
        except Exception as e:
            send_message(f"⚠️ Error for {sym}: {e}")


def cmd_stop():
    global bot_running
    send_message("🛑 *Bot stopped.*")
    bot_running = False


# ------------------ DISPATCHER ------------------
def handle_command(text):
    parts   = text.strip().split()
    command = parts[0].lower().lstrip("/").split("@")[0]
    arg     = parts[1] if len(parts) > 1 else ""

    if command == "start":    cmd_start()
    elif command == "add":    cmd_add(arg)
    elif command == "remove": cmd_remove(arg)
    elif command == "coins":  cmd_coins()
    elif command == "fibs":   cmd_fibs()
    elif command == "stop":   cmd_stop()
    else: send_message("❓ Unknown. Send `/start` for help.")


# ------------------ POLLING ------------------
def polling_thread():
    global last_update_id
    print("📨 Polling started...")
    while bot_running:
        try:
            updates = get_updates(offset=last_update_id + 1)
            for u in updates:
                last_update_id = u["update_id"]
                msg = u.get("message", {})
                if msg.get("chat", {}).get("id") != MY_CHAT_ID:
                    continue
                text = msg.get("text", "")
                if text.startswith("/"):
                    print(f"📩 {text}")
                    handle_command(text)
        except Exception as e:
            print(f"Polling error: {e}")
        time.sleep(1)


# ------------------ FIB MONITOR ------------------
def format_alert(sym, price, ratio, fib_price, high, low):
    side = "🔼 ABOVE" if price > fib_price else "🔽 BELOW"
    dist = abs(price - fib_price) / fib_price * 100
    return (
        f"🎯 *Fibonacci Touch — {sym}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📍 Level     : `{FIB_LABELS[ratio]}`\n"
        f"💲 Fib Price : `{fmt(fib_price)}`\n"
        f"💰 Current   : `{fmt(price)}` {side}\n"
        f"📏 Distance  : `{dist:.3f}%`\n"
        f"📈 Swing High: `{fmt(high)}`\n"
        f"📉 Swing Low : `{fmt(low)}`\n"
        f"⏱ Timeframe : 4H\n"
        f"🕐 {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"⚠️ _Not financial advice. DYOR._"
    )


def check_fib(pair, sym):
    try:
        candles = get_ohlcv_4h(pair)
        if len(candles) < 10:
            return
        high  = max(c["High"] for c in candles)
        low   = min(c["Low"]  for c in candles)
        fib   = get_fib_levels(high, low)
        price = get_current_price(pair)
        if not price:
            return
        print(f"{sym} | {fmt(price)} | H:{fmt(high)} L:{fmt(low)}")
        if pair not in last_alert_state:
            last_alert_state[pair] = {r: None for r in FIB_RATIOS}
        for ratio, fib_price in fib.items():
            pct = abs(price - fib_price) / fib_price * 100
            if pct <= ALERT_THRESHOLD_PCT:
                side = "above" if price >= fib_price else "below"
                if last_alert_state[pair].get(ratio) != side:
                    send_message(format_alert(sym, price, ratio, fib_price, high, low))
                    last_alert_state[pair][ratio] = side
            else:
                last_alert_state[pair][ratio] = None
    except Exception as e:
        print(f"{sym} error: {e}")


# ------------------ MAIN ------------------
def main():
    print(f"🤖 Bot starting | Chat ID: {MY_CHAT_ID}")
    send_message(
        "📐 *4H Fibonacci Monitor Started!*\n\n"
        "➡️ Send `/add SOL` to monitor any coin\n"
        "➡️ Send `/start` for all commands"
    )

    t = threading.Thread(target=polling_thread, daemon=True)
    t.start()

    while bot_running:
        try:
            if monitored_coins:
                print(f"\n[{time.strftime('%H:%M:%S')}] Checking {list(monitored_coins.values())}")
                for pair, sym in list(monitored_coins.items()):
                    check_fib(pair, sym)
                    time.sleep(2)
            else:
                print(f"[{time.strftime('%H:%M:%S')}] Waiting — send /add SOL to bot")
            time.sleep(CHECK_INTERVAL)
        except KeyboardInterrupt:
            send_message("🛑 *Bot stopped manually.*")
            break
        except Exception as e:
            print(f"Main error: {e}")
            time.sleep(60)


if __name__ == "__main__":
    main()
