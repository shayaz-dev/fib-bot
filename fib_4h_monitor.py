# -*- coding: utf-8 -*-
"""
Fibonacci 4H Level Monitor — Monitor ANY coin on Kraken
Type any coin symbol and the bot will try to find it live.

COMMANDS:
  /start      — Welcome + instructions
  /add SOL    — Start monitoring any coin
  /remove SOL — Stop monitoring a coin
  /coins      — Show currently monitored coins
  /fibs       — Show current Fib levels for all monitored coins
  /stop       — Stop the bot
"""

import time
import threading
import requests

# ------------------ TELEGRAM CONFIG ------------------
TELEGRAM_TOKEN = '8759282338:AAFLP2MQCQLuQidPxi_41L6KFrs8qUYroPs'
MY_CHAT_ID     = 7822181453

# ------------------ FIB CONFIG ------------------
FIB_RATIOS = [0.5, 0.618, 1.0, 1.272, 1.618]
FIB_LABELS = {
    0.5:   '0.500 (Mid)',
    0.618: '0.618 (Golden)',
    1.0:   '1.000 (Full Retrace)',
    1.272: '1.272 (Extension)',
    1.618: '1.618 (Golden Ext)',
}

ALERT_THRESHOLD_PCT = 0.5   # alert when within 0.5% of a level
CHECK_INTERVAL      = 240   # seconds between checks
CANDLE_LIMIT        = 100   # 4H candles to look back

# Common quote currencies to try when resolving a symbol
QUOTE_CURRENCIES = ['USD', 'USDT', 'USDC', 'EUR']

# Kraken renames some assets — map common names to Kraken codes
KRAKEN_ASSET_MAP = {
    'BTC':  'XBT',
    'DOGE': 'XDG',
    'ETH':  'XETH',
    'XRP':  'XXRP',
    'LTC':  'XLTC',
}

# ------------------ STATE ------------------
monitored_coins  = {}   # kraken_pair -> display_symbol
last_alert_state = {}   # kraken_pair -> { ratio: 'above'|'below'|None }
last_update_id   = 0
bot_running      = True


# ------------------ TELEGRAM ------------------
def send_message(text, chat_id=MY_CHAT_ID):
    url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    try:
        r = requests.post(url, data=data, timeout=10)
        if not r.json().get("ok"):
            print(f"Telegram send failed: {r.json()}")
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


# ------------------ KRAKEN PAIR RESOLVER ------------------
def resolve_kraken_pair(user_input):
    """
    Try to find a valid Kraken pair for any coin the user types.
    Tries multiple quote currencies and Kraken asset name variants.
    Returns (kraken_pair, display_symbol) or (None, None).
    """
    base = user_input.strip().upper()

    # Build list of base variants to try (e.g. BTC -> XBT, DOGE -> XDG)
    base_variants = [base]
    if base in KRAKEN_ASSET_MAP:
        base_variants.append(KRAKEN_ASSET_MAP[base])
    # Also try with X prefix (Kraken legacy format)
    base_variants.append('X' + base)

    pairs_to_try = []
    for b in base_variants:
        for quote in QUOTE_CURRENCIES:
            pairs_to_try.append(b + quote)
            pairs_to_try.append('X' + b + 'Z' + quote)  # Kraken legacy e.g. XXBTZUSD

    # Ask Kraken which ones actually exist
    url = "https://api.kraken.com/0/public/AssetPairs"
    try:
        r    = requests.get(url, timeout=15)
        data = r.json()
        if data.get("error"):
            return None, None

        available = data.get("result", {})

        for candidate in pairs_to_try:
            if candidate in available:
                info           = available[candidate]
                wsname         = info.get("wsname", candidate)   # e.g. "SOL/USD"
                display_symbol = wsname if "/" in wsname else candidate
                return candidate, display_symbol

        # Last resort: search by wsname containing the base symbol
        for pair_key, info in available.items():
            wsname = info.get("wsname", "")
            parts  = wsname.split("/")
            if len(parts) == 2:
                ws_base  = parts[0].upper()
                ws_quote = parts[1].upper()
                if ws_base == base and ws_quote in QUOTE_CURRENCIES:
                    return pair_key, wsname

        return None, None
    except Exception as e:
        print(f"Pair resolve error: {e}")
        return None, None


# ------------------ KRAKEN DATA ------------------
def get_ohlcv_4h(pair, limit=100):
    url    = "https://api.kraken.com/0/public/OHLC"
    params = {"pair": pair, "interval": 240}
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        if data.get("error"):
            return []
        key     = list(data["result"].keys())[0]
        candles = data["result"][key][-limit:]
        return [{"High": float(c[2]), "Low": float(c[3]), "Close": float(c[4])} for c in candles]
    except Exception as e:
        print(f"Kraken 4H error for {pair}: {e}")
        return []


def get_current_price(pair):
    url = "https://api.kraken.com/0/public/Ticker"
    try:
        r    = requests.get(url, params={"pair": pair}, timeout=10)
        data = r.json()
        key  = list(data["result"].keys())[0]
        return float(data["result"][key]["c"][0])
    except Exception as e:
        print(f"Price error for {pair}: {e}")
        return None


# ------------------ HELPERS ------------------
def fmt(price):
    if price < 0.0001:
        return f"${price:.8f}"
    elif price < 1:
        return f"${price:.6f}"
    elif price < 1000:
        return f"${price:.4f}"
    else:
        return f"${price:,.2f}"


def get_fib_levels(high, low):
    diff = high - low
    return {ratio: high - diff * ratio for ratio in FIB_RATIOS}


# ------------------ COMMAND HANDLERS ------------------
def cmd_start():
    send_message(
        "📐 *4H Fibonacci Monitor*\n\n"
        "Monitor *any coin on Kraken* — just type its symbol!\n\n"
        "*Levels:* `0.5 · 0.618 · 1.0 · 1.272 · 1.618`\n\n"
        "*Commands:*\n"
        "`/add SOL` — Monitor any coin\n"
        "`/add PEPE` — Works for any Kraken-listed coin\n"
        "`/remove SOL` — Stop monitoring\n"
        "`/coins` — Show monitored coins\n"
        "`/fibs` — Show live Fib levels\n"
        "`/stop` — Stop the bot"
    )


def cmd_add(coin_input):
    if not coin_input:
        send_message("Usage: `/add SOL`\nWorks for *any coin listed on Kraken*.")
        return

    send_message(f"🔍 Looking up *{coin_input.upper()}* on Kraken...")

    pair, display = resolve_kraken_pair(coin_input)

    if not pair:
        send_message(
            f"❌ Could not find *{coin_input.upper()}* on Kraken.\n\n"
            f"Make sure the coin is listed on Kraken.\n"
            f"Try the exact ticker symbol e.g. `PEPE`, `WIF`, `SOL`, `BTC`."
        )
        return

    if pair in monitored_coins:
        send_message(f"ℹ️ *{display}* is already being monitored.")
        return

    monitored_coins[pair]  = display
    last_alert_state[pair] = {r: None for r in FIB_RATIOS}
    send_message(
        f"✅ Now monitoring *{display}* on 4H Fibonacci levels.\n"
        f"Kraken pair: `{pair}`\n"
        f"Alerts on: `0.5 · 0.618 · 1.0 · 1.272 · 1.618`"
    )


def cmd_remove(coin_input):
    if not coin_input:
        send_message("Usage: `/remove SOL`")
        return

    # Match by symbol name or display
    target = coin_input.strip().upper()
    found_pair = None

    for pair, display in monitored_coins.items():
        base = display.split("/")[0].upper()
        if base == target or pair.upper() == target:
            found_pair = pair
            break

    if not found_pair:
        # Try resolving to find a match
        pair, _ = resolve_kraken_pair(coin_input)
        if pair and pair in monitored_coins:
            found_pair = pair

    if not found_pair:
        send_message(f"❌ *{coin_input.upper()}* is not in your monitored list.\nUse `/coins` to see what's active.")
        return

    symbol = monitored_coins.pop(found_pair)
    last_alert_state.pop(found_pair, None)
    send_message(f"🗑 Stopped monitoring *{symbol}*.")


def cmd_coins():
    if not monitored_coins:
        send_message("ℹ️ No coins being monitored.\nUse `/add SOL` to start — works for any Kraken coin!")
        return
    lines = ["📡 *Currently Monitoring:*\n"]
    for pair, symbol in monitored_coins.items():
        lines.append(f"  • *{symbol}* `({pair})`")
    lines.append(f"\n_Threshold: ±{ALERT_THRESHOLD_PCT}% | Interval: {CHECK_INTERVAL}s_")
    send_message("\n".join(lines))


def cmd_fibs():
    if not monitored_coins:
        send_message("ℹ️ No coins monitored. Use `/add SOL` first.")
        return
    send_message("⏳ Fetching Fib levels...")
    for pair, symbol in list(monitored_coins.items()):
        try:
            candles = get_ohlcv_4h(pair, limit=CANDLE_LIMIT)
            if not candles:
                send_message(f"⚠️ No data for {symbol}")
                continue
            swing_high = max(c["High"] for c in candles)
            swing_low  = min(c["Low"]  for c in candles)
            fib        = get_fib_levels(swing_high, swing_low)
            price      = get_current_price(pair)
            lines = [f"📐 *{symbol} — 4H Fibonacci Levels*\n"]
            lines.append(f"📈 Swing High : `{fmt(swing_high)}`")
            lines.append(f"📉 Swing Low  : `{fmt(swing_low)}`")
            lines.append(f"💰 Current    : `{fmt(price) if price else 'N/A'}`\n")
            lines.append("*Levels:*")
            for ratio in FIB_RATIOS:
                lvl   = fib[ratio]
                arrow = ""
                dist  = ""
                if price:
                    arrow = "🔼" if price > lvl else "🔽"
                    dist  = f"  {((price - lvl) / lvl * 100):+.2f}%"
                lines.append(f"  `{ratio:.3f}` → `{fmt(lvl)}` {arrow}{dist}")
            send_message("\n".join(lines))
            time.sleep(2)
        except Exception as e:
            send_message(f"⚠️ Error for {symbol}: {e}")


def cmd_stop():
    global bot_running
    send_message("🛑 *4H Fibonacci Monitor stopped.*")
    bot_running = False


# ------------------ COMMAND DISPATCHER ------------------
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
    else: send_message("❓ Unknown command. Send `/start` for help.")


# ------------------ POLLING THREAD ------------------
def polling_thread():
    global last_update_id
    print("📨 Telegram polling started...")
    while bot_running:
        try:
            updates = get_updates(offset=last_update_id + 1)
            for update in updates:
                last_update_id = update["update_id"]
                msg = update.get("message", {})
                if msg.get("chat", {}).get("id") != MY_CHAT_ID:
                    continue
                text = msg.get("text", "")
                if text.startswith("/"):
                    print(f"📩 Command: {text}")
                    handle_command(text)
        except Exception as e:
            print(f"Polling error: {e}")
        time.sleep(1)


# ------------------ FIB ALERT ------------------
def format_fib_alert(symbol, price, ratio, fib_price, swing_high, swing_low):
    side = "🔼 ABOVE" if price > fib_price else "🔽 BELOW"
    dist = abs(price - fib_price) / fib_price * 100
    return (
        f"🎯 *Fibonacci Touch — {symbol}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📍 Level     : `{FIB_LABELS[ratio]}`\n"
        f"💲 Fib Price : `{fmt(fib_price)}`\n"
        f"💰 Current   : `{fmt(price)}` {side}\n"
        f"📏 Distance  : `{dist:.3f}%`\n"
        f"📈 Swing High: `{fmt(swing_high)}`\n"
        f"📉 Swing Low : `{fmt(swing_low)}`\n"
        f"⏱ Timeframe : 4H\n"
        f"🕐 {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"⚠️ _Not financial advice. DYOR._"
    )


def check_fib_levels(pair, symbol):
    try:
        candles = get_ohlcv_4h(pair, limit=CANDLE_LIMIT)
        if len(candles) < 10:
            print(f"{symbol}: Not enough candles ({len(candles)})")
            return
        swing_high = max(c["High"] for c in candles)
        swing_low  = min(c["Low"]  for c in candles)
        fib_levels = get_fib_levels(swing_high, swing_low)
        price      = get_current_price(pair)
        if price is None:
            return
        print(f"{symbol} | Price: {fmt(price)} | H: {fmt(swing_high)} | L: {fmt(swing_low)}")
        if pair not in last_alert_state:
            last_alert_state[pair] = {r: None for r in FIB_RATIOS}
        for ratio, fib_price in fib_levels.items():
            pct_diff = abs(price - fib_price) / fib_price * 100
            if pct_diff <= ALERT_THRESHOLD_PCT:
                current_side = "above" if price >= fib_price else "below"
                if last_alert_state[pair].get(ratio) != current_side:
                    print(f"  🎯 {symbol} touching Fib {ratio} at {fmt(fib_price)}")
                    send_message(format_fib_alert(symbol, price, ratio, fib_price, swing_high, swing_low))
                    last_alert_state[pair][ratio] = current_side
            else:
                last_alert_state[pair][ratio] = None
    except Exception as e:
        print(f"{symbol} check error: {e}")


# ------------------ MAIN LOOP ------------------
def main():
    send_message(
        "📐 *4H Fibonacci Monitor Started!*\n\n"
        "You can monitor *any coin listed on Kraken*.\n"
        "➡️ Send `/add SOL` to start\n"
        "➡️ Send `/add PEPE` — works for any coin!\n"
        "➡️ Send `/start` for full instructions"
    )

    t = threading.Thread(target=polling_thread, daemon=True)
    t.start()

    print("🤖 Bot running. Send /add SOL to your Telegram bot to get started.\n")

    while bot_running:
        try:
            if monitored_coins:
                print(f"\n--- Fib check @ {time.strftime('%Y-%m-%d %H:%M:%S')} | Watching: {', '.join(monitored_coins.values())} ---")
                for pair, symbol in list(monitored_coins.items()):
                    check_fib_levels(pair, symbol)
                    time.sleep(2)
                print(f"Next check in {CHECK_INTERVAL}s...")
            else:
                print(f"[{time.strftime('%H:%M:%S')}] No coins yet — send /add SOL to your bot")
            time.sleep(CHECK_INTERVAL)
        except KeyboardInterrupt:
            print("\n🛑 Stopped.")
            send_message("🛑 *4H Fibonacci Monitor stopped manually.*")
            break
        except Exception as e:
            print(f"Main loop error: {e}")
            time.sleep(60)


if __name__ == "__main__":
    main()
