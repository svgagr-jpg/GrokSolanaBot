
import os
import time
import requests
from datetime import datetime, timezone, timedelta

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_SECRET_KEY"]

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}

STARTING_CASH = 1000
TRADE_AMOUNT = 25
MAX_POSITIONS = 3
MIN_SCORE = 70
STOP_LOSS = 10
TAKE_PROFIT = 30
DAILY_LOSS_LIMIT = 50
COOLDOWN_HOURS = 3

PROFILES_URL = "https://api.dexscreener.com/token-profiles/latest/v1"
PAIRS_URL = "https://api.dexscreener.com/token-pairs/v1/solana/{}"


def db(method, table, data=None, params=None):
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    response = requests.request(
        method,
        url,
        headers=HEADERS,
        json=data,
        params=params,
        timeout=20
    )
    response.raise_for_status()
    if response.text:
        return response.json()
    return []


def number(value):
    try:
        return float(value or 0)
    except (ValueError, TypeError):
        return 0.0


def get_pairs(address):
    try:
        r = requests.get(PAIRS_URL.format(address), timeout=15)
        r.raise_for_status()
        pairs = r.json()
        return [
            p for p in pairs
            if p.get("chainId") == "solana"
        ] if isinstance(pairs, list) else []
    except Exception:
        return []


def best_pair(pairs):
    if not pairs:
        return None
    return max(
        pairs,
        key=lambda p: number(
            (p.get("liquidity") or {}).get("usd")
        )
    )


def analyze(pair):
    base = pair.get("baseToken") or {}
    address = base.get("address", "")
    price = number(pair.get("priceUsd"))
    liquidity = number((pair.get("liquidity") or {}).get("usd"))
    volume = number((pair.get("volume") or {}).get("h24"))
    change = number((pair.get("priceChange") or {}).get("h24"))

    txns = pair.get("txns") or {}
    h24 = txns.get("h24") or {}
    buys = number(h24.get("buys"))
    sells = number(h24.get("sells"))
    total = buys + sells

    flags = []
    if liquidity < 5000:
        flags.append("LOW LIQUIDITY")
    if volume < 2000:
        flags.append("LOW VOLUME")
    if total < 10:
        flags.append("LOW ACTIVITY")
    if change < -40:
        flags.append("LARGE PRICE DROP")
    if change > 150:
        flags.append("EXTREME PRICE MOVE")
    if buys > 15 and sells == 0:
        flags.append("NO RECORDED SELLS")

    liquidity_score = min(liquidity / 2500, 35)
    volume_score = min(
        (volume / max(liquidity, 1)) / 1.5, 1
    ) * 25

    if 0 < change <= 50:
        momentum_score = 20
    elif 50 < change <= 100:
        momentum_score = 12
    elif -10 <= change <= 0:
        momentum_score = 12
    elif change > 100:
        momentum_score = 4
    else:
        momentum_score = 0

    activity_score = min(total / 10, 20)
    score = int(min(
        100,
        max(0, liquidity_score + volume_score +
            momentum_score + activity_score)
    ))

    severe = (
        price <= 0
        or liquidity < 1500
        or change < -60
        or "NO RECORDED SELLS" in flags
    )

    signal = "AVOID"
    if not severe and score >= 70 and liquidity >= 5000 \
            and volume >= 5000 and total >= 10 and change < 100:
        signal = "BUY"
    elif not severe and score >= 30:
        signal = "WATCH"

    return {
        "token_address": address,
        "symbol": base.get("symbol", "UNKNOWN"),
        "name": base.get("name", "UNKNOWN"),
        "price": price,
        "liquidity": liquidity,
        "volume": volume,
        "change": change,
        "score": score,
        "signal": signal
    }


def get_trades(status=None):
    params = {"select": "*", "order": "id.asc"}
    if status:
        params["status"] = f"eq.{status}"
    return db("GET", "sniper_trades", params=params)


def get_cash():
    trades = get_trades()
    invested = sum(
        number(t["amount"])
        for t in trades if t["status"] == "OPEN"
    )
    realized = sum(
        number(t["pnl"])
        for t in trades if t["status"] == "CLOSED"
    )
    return STARTING_CASH - invested + realized


def on_cooldown(address):
    rows = db(
        "GET", "cooldowns",
        params={
            "select": "last_trade",
            "address": f"eq.{address}"
        }
    )
    if not rows:
        return False
    return time.time() - number(rows[0]["last_trade"]) < COOLDOWN_HOURS * 3600


def daily_loss():
    today = datetime.now(timezone.utc).date().isoformat()
    trades = db(
        "GET", "sniper_trades",
        params={
            "select": "pnl,close_time",
            "status": "eq.CLOSED"
        }
    )
    return sum(
        number(t["pnl"])
        for t in trades
        if t.get("close_time", "").startswith(today)
    )


def open_trade(token):
    if token["price"] <= 0 or get_cash() < TRADE_AMOUNT:
        return

    positions = get_trades("OPEN")
    if len(positions) >= MAX_POSITIONS:
        return

    if any(
        t["token_address"] == token["token_address"]
        for t in positions
    ):
        return

    if on_cooldown(token["token_address"]):
        return

    db("POST", "sniper_trades", {
        "token_address": token["token_address"],
        "symbol": token["symbol"],
        "name": token["name"],
        "entry_price": token["price"],
        "current_price": token["price"],
        "amount": TRADE_AMOUNT,
        "quantity": TRADE_AMOUNT / token["price"],
        "pnl": 0,
        "pnl_pct": 0,
        "status": "OPEN",
        "entry_time": datetime.now(timezone.utc).isoformat(),
        "auto_trade": True
    })

    db("POST", "cooldowns", {
        "address": token["token_address"],
        "last_trade": time.time()
    }, params={"on_conflict": "address"})


def monitor():
    for trade in get_trades("OPEN"):
        pair = best_pair(get_pairs(trade["token_address"]))
        if not pair:
            continue

        price = number(pair.get("priceUsd"))
        entry = number(trade["entry_price"])
        quantity = number(trade["quantity"])
        if price <= 0 or entry <= 0:
            continue

        pnl = (price - entry) * quantity
        pct = (price / entry - 1) * 100

        payload = {
            "current_price": price,
            "pnl": pnl,
            "pnl_pct": pct
        }

        if pct <= -STOP_LOSS or pct >= TAKE_PROFIT:
            payload.update({
                "status": "CLOSED",
                "close_time": datetime.now(timezone.utc).isoformat(),
                "close_reason": (
                    "STOP LOSS" if pct <= -STOP_LOSS
                    else "TAKE PROFIT"
                )
            })

        db(
            "PATCH", "sniper_trades",
            payload,
            params={"id": f"eq.{trade['id']}"}
        )


def run():
    if daily_loss() <= -DAILY_LOSS_LIMIT:
        print("DAILY LOSS LIMIT HIT")
        return

    profiles_response = requests.get(
        PROFILES_URL, timeout=20
    )
    profiles_response.raise_for_status()
    profiles = profiles_response.json()

    candidates = []
    seen = set()

    for profile in profiles[:35]:
        if profile.get("chainId") != "solana":
            continue

        address = profile.get("tokenAddress")
        if not address or address in seen:
            continue
        seen.add(address)

        pair = best_pair(get_pairs(address))
        if not pair:
            continue

        token = analyze(pair)

        if token["price"] > 0:
            db("POST", "price_history", {
                "address": address,
                "symbol": token["symbol"],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "price": token["price"],
                "volume": token["volume"]
            })
            if token["signal"] == "BUY":
                candidates.append(token)

    monitor()

    for token in sorted(
        candidates,
        key=lambda t: t["score"],
        reverse=True
    ):
        if len(get_trades("OPEN")) >= MAX_POSITIONS:
            break
        open_trade(token)

    print("Worker finished. BUY candidates:", len(candidates))


if __name__ == "__main__":
    run()

