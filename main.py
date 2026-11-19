import streamlit as st
import sqlite3
import requests
import pandas as pd
import numpy as np
import time
import json
import math
import random
import hashlib
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Any

# ============================================================
# NIGHT RUN ULTRA V7 PRO
# SOLANA MEMECOIN PAPER TRADING TERMINAL
# ============================================================

APP_NAME = "NIGHT RUN ULTRA V7 PRO"
DB_FILE = "night_run_v7.db"
STARTING_BALANCE = 1000.0
VERSION = "7.0.0"
DEX_API = "https://api.dexscreener.com"
REQUEST_TIMEOUT = 12

st.set_page_config(
    page_title=APP_NAME,
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("night_run_v7")

# ============================================================
# THEME
# ============================================================

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&family=Orbitron:wght@500;600;700;800&display=swap');

.stApp {
    background:
        radial-gradient(circle at 10% 0%, #14203b 0%, transparent 30%),
        radial-gradient(circle at 90% 10%, #25113a 0%, transparent 30%),
        #070910;
    color: #e8ecf7;
    font-family: Inter, sans-serif;
}

header[data-testid="stHeader"] {
    background: rgba(5,8,15,0.8);
}

.block-container {
    padding-top: 1.5rem;
    padding-bottom: 3rem;
    max-width: 1600px;
}

h1, h2, h3 {
    font-family: Orbitron, sans-serif !important;
}

[data-testid="stMetric"] {
    background: linear-gradient(145deg,#111a2b,#0b101c);
    border: 1px solid #25334b;
    border-radius: 14px;
    padding: 17px;
}

[data-testid="stMetricValue"] {
    color: #e8f4ff;
}

[data-testid="stMetricLabel"] {
    color: #8da2c4;
}

.stButton button {
    border-radius: 9px;
    border: 1px solid #31496d;
    background: linear-gradient(135deg,#142b48,#17213a);
    color: white;
    font-weight: 700;
    min-height: 42px;
}

.stButton button:hover {
    border-color: #35d8ff;
    color: #35d8ff;
}

div[data-testid="stTabs"] button {
    color: #9eb1d0;
    font-weight: 700;
}

div[data-testid="stTabs"] button[aria-selected="true"] {
    color: #35d8ff;
}

.panel {
    background: linear-gradient(145deg,#111a2a,#0b101b);
    border: 1px solid #26364e;
    border-radius: 15px;
    padding: 18px;
    margin-bottom: 14px;
}

.panel-title {
    color: #35d8ff;
    font-size: 13px;
    font-weight: 900;
    letter-spacing: 2px;
    margin-bottom: 12px;
}

.neon {
    color: #35d8ff;
    text-shadow: 0 0 15px #168bb5;
}

.green {
    color: #48e6a5;
}

.red {
    color: #ff637c;
}

.muted {
    color: #8292ad;
}

hr {
    border-color: #25334b;
}

section[data-testid="stSidebar"] {
    background: #090e18;
    border-right: 1px solid #24344c;
}

section[data-testid="stSidebar"] * {
    color: #dce7f7;
}
</style>
""", unsafe_allow_html=True)

# ============================================================
# SESSION STATE
# ============================================================

DEFAULT_STATE = {
    "last_scan": None,
    "tokens": [],
    "scanner_error": "",
    "auto_enabled": False,
    "scan_counter": 0,
    "last_auto_scan": 0,
    "selected_token": None,
    "notifications": [],
    "scan_running": False
}

for key, value in DEFAULT_STATE.items():
    if key not in st.session_state:
        st.session_state[key] = value

# ============================================================
# DATABASE
# ============================================================

def db():
    return sqlite3.connect(DB_FILE, timeout=30)

def init_database():
    con = db()
    cur = con.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS portfolio (
        id INTEGER PRIMARY KEY CHECK(id=1),
        cash REAL NOT NULL,
        starting_balance REAL NOT NULL,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS positions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        token_address TEXT,
        symbol TEXT,
        name TEXT,
        entry_price REAL,
        current_price REAL,
        quantity REAL,
        invested REAL,
        stop_loss REAL,
        take_profit REAL,
        score REAL,
        opened_at TEXT,
        status TEXT DEFAULT 'OPEN'
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        token_address TEXT,
        symbol TEXT,
        side TEXT,
        price REAL,
        quantity REAL,
        amount REAL,
        pnl REAL DEFAULT 0,
        reason TEXT,
        timestamp TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS price_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        token_address TEXT,
        symbol TEXT,
        price REAL,
        timestamp TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cash REAL,
        market_value REAL,
        equity REAL,
        realized_pnl REAL,
        timestamp TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS cooldowns (
        token_address TEXT PRIMARY KEY,
        last_trade TEXT
    )
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_positions_status
    ON positions(status)
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_trades_timestamp
    ON trades(timestamp)
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_prices_token
    ON price_history(token_address)
    """)

    cur.execute("SELECT COUNT(*) FROM portfolio")
    if cur.fetchone()[0] == 0:
        cur.execute("""
        INSERT INTO portfolio
        (id,cash,starting_balance,created_at)
        VALUES (1,?,?,?)
        """, (
            STARTING_BALANCE,
            STARTING_BALANCE,
            now()
        ))

    con.commit()
    con.close()

def now():
    return datetime.now(timezone.utc).isoformat()

def get_portfolio():
    con = db()
    row = con.execute(
        "SELECT cash,starting_balance FROM portfolio WHERE id=1"
    ).fetchone()
    con.close()
    return row

def set_cash(value):
    con = db()
    con.execute(
        "UPDATE portfolio SET cash=? WHERE id=1",
        (float(value),)
    )
    con.commit()
    con.close()

def get_cash():
    return float(get_portfolio()[0])

def get_positions(status="OPEN"):
    con = db()
    rows = con.execute("""
    SELECT id,token_address,symbol,name,entry_price,
           current_price,quantity,invested,stop_loss,
           take_profit,score,opened_at,status
    FROM positions
    WHERE status=?
    ORDER BY id DESC
    """, (status,)).fetchall()
    con.close()

    columns = [
        "id","token_address","symbol","name","entry_price",
        "current_price","quantity","invested","stop_loss",
        "take_profit","score","opened_at","status"
    ]

    return pd.DataFrame(rows, columns=columns)

def get_trade_history(limit=500):
    con = db()
    rows = con.execute("""
    SELECT token_address,symbol,side,price,quantity,
           amount,pnl,reason,timestamp
    FROM trades
    ORDER BY id DESC
    LIMIT ?
    """, (int(limit),)).fetchall()
    con.close()

    columns = [
        "address","symbol","side","price","quantity",
        "amount","pnl","reason","timestamp"
    ]

    return pd.DataFrame(rows, columns=columns)

def get_realized_pnl():
    con = db()
    value = con.execute("""
    SELECT COALESCE(SUM(pnl),0)
    FROM trades WHERE side='SELL'
    """).fetchone()[0]
    con.close()
    return float(value or 0)

def get_daily_pnl():
    today = datetime.now(timezone.utc).date().isoformat()

    con = db()
    value = con.execute("""
    SELECT COALESCE(SUM(pnl),0)
    FROM trades
    WHERE side='SELL' AND timestamp LIKE ?
    """, (today + "%",)).fetchone()[0]
    con.close()

    return float(value or 0)

def save_price(address, symbol, price):
    con = db()
    con.execute("""
    INSERT INTO price_history
    (token_address,symbol,price,timestamp)
    VALUES (?,?,?,?)
    """, (address, symbol, float(price), now()))
    con.commit()
    con.close()

def save_snapshot():
    cash = get_cash()
    positions = get_positions()

    market_value = 0

    if not positions.empty:
        market_value = float(
            (positions["quantity"] * positions["current_price"]).sum()
        )

    equity = cash + market_value

    con = db()
    con.execute("""
    INSERT INTO snapshots
    (cash,market_value,equity,realized_pnl,timestamp)
    VALUES (?,?,?,?,?)
    """, (
        cash,
        market_value,
        equity,
        get_realized_pnl(),
        now()
    ))
    con.commit()
    con.close()

# ============================================================
# MARKET DATA
# ============================================================

@st.cache_data(ttl=20, show_spinner=False)
def fetch_token_profiles():
    url = f"{DEX_API}/token-profiles/latest/v1"

    response = requests.get(
        url,
        timeout=REQUEST_TIMEOUT,
        headers={"User-Agent": APP_NAME}
    )

    response.raise_for_status()

    data = response.json()

    if not isinstance(data, list):
        return []

    return [
        item for item in data
        if item.get("chainId") == "solana"
        and item.get("tokenAddress")
    ]

@st.cache_data(ttl=15, show_spinner=False)
def fetch_token_pairs(address):
    url = f"{DEX_API}/token-pairs/v1/solana/{address}"

    response = requests.get(
        url,
        timeout=REQUEST_TIMEOUT,
        headers={"User-Agent": APP_NAME}
    )

    response.raise_for_status()

    data = response.json()

    if not isinstance(data, list):
        return []

    return data

def safe_float(value, default=0.0):
    try:
        result = float(value)
        if math.isfinite(result):
            return result
        return default
    except (TypeError, ValueError):
        return default

def extract_market_data(profile):
    address = profile.get("tokenAddress")

    pairs = fetch_token_pairs(address)

    if not pairs:
        return None

    pairs = [
        p for p in pairs
        if p.get("chainId") == "solana"
    ]

    if not pairs:
        return None

    pairs.sort(
        key=lambda p: safe_float(
            (p.get("liquidity") or {}).get("usd")
        ),
        reverse=True
    )

    pair = pairs[0]

    base = pair.get("baseToken") or {}
    liquidity = pair.get("liquidity") or {}
    volume = pair.get("volume") or {}
    txns = pair.get("txns") or {}
    h1 = txns.get("h1") or {}
    h24 = txns.get("h24") or {}
    price_change = pair.get("priceChange") or {}

    return {
        "address": address,
        "symbol": base.get("symbol", "UNKNOWN"),
        "name": base.get("name", "Unknown"),
        "price": safe_float(pair.get("priceUsd")),
        "liquidity": safe_float(liquidity.get("usd")),
        "volume_5m": safe_float(volume.get("m5")),
        "volume_1h": safe_float(volume.get("h1")),
        "volume_24h": safe_float(volume.get("h24")),
        "buys_1h": safe_float(h1.get("buys")),
        "sells_1h": safe_float(h1.get("sells")),
        "buys_24h": safe_float(h24.get("buys")),
        "sells_24h": safe_float(h24.get("sells")),
        "change_5m": safe_float(price_change.get("m5")),
        "change_1h": safe_float(price_change.get("h1")),
        "change_24h": safe_float(price_change.get("h24")),
        "fdv": safe_float(pair.get("fdv")),
        "market_cap": safe_float(pair.get("marketCap")),
        "pair_created": pair.get("pairCreatedAt"),
        "dex": pair.get("dexId", "unknown"),
        "url": pair.get("url", ""),
        "pair_address": pair.get("pairAddress", "")
    }

# ============================================================
# SCORING ENGINE
# ============================================================

def liquidity_score(liquidity):
    if liquidity >= 100000:
        return 20
    if liquidity >= 50000:
        return 17
    if liquidity >= 25000:
        return 14
    if liquidity >= 10000:
        return 10
    if liquidity >= 5000:
        return 6
    return 0

def volume_score(volume):
    if volume >= 500000:
        return 20
    if volume >= 100000:
        return 17
    if volume >= 50000:
        return 14
    if volume >= 20000:
        return 10
    if volume >= 5000:
        return 6
    return 0

def transaction_score(buys, sells):
    total = buys + sells

    if total == 0:
        return 0

    ratio = buys / max(sells, 1)

    score = min(15, total / 4)

    if ratio >= 2:
        score += 5
    elif ratio >= 1.3:
        score += 3

    return min(20, score)

def momentum_score(change_5m, change_1h, change_24h):
    score = 0

    if -5 <= change_5m <= 15:
        score += 5

    if 0 <= change_1h <= 30:
        score += 7

    if 0 <= change_24h <= 100:
        score += 8

    if change_5m > 50:
        score -= 10

    if change_1h > 100:
        score -= 10

    return max(0, min(20, score))

def risk_score(data):
    risk = 0

    if data["liquidity"] < 5000:
        risk += 30

    if data["volume_24h"] < 5000:
        risk += 15

    if data["sells_1h"] == 0 and data["buys_1h"] > 10:
        risk += 15

    if data["change_5m"] > 80:
        risk += 15

    if data["change_1h"] > 150:
        risk += 20

    if data["price"] <= 0:
        risk += 100

    return min(100, risk)

def calculate_score(data):
    liquidity = liquidity_score(data["liquidity"])
    volume = volume_score(data["volume_24h"])

    transactions = transaction_score(
        data["buys_1h"],
        data["sells_1h"]
    )

    momentum = momentum_score(
        data["change_5m"],
        data["change_1h"],
        data["change_24h"]
    )

    risk = risk_score(data)

    raw = liquidity + volume + transactions + momentum

    final_score = max(0, min(100, raw - risk * 0.35))

    if final_score >= 75 and risk < 35:
        signal = "BUY"
    elif final_score >= 50 and risk < 60:
        signal = "WATCH"
    else:
        signal = "AVOID"

    return {
        "score": round(final_score, 2),
        "risk": round(risk, 2),
        "signal": signal,
        "liquidity_score": liquidity,
        "volume_score": volume,
        "transaction_score": round(transactions, 2),
        "momentum_score": momentum
    }

# ============================================================
# TOKEN SCANNER
# ============================================================

def scan_market(max_tokens=40):
    results = []
    errors = 0

    try:
        profiles = fetch_token_profiles()
    except Exception as exc:
        st.session_state.scanner_error = str(exc)
        return []

    profiles = profiles[:max_tokens]

    progress = st.progress(0)
    status = st.empty()

    for index, profile in enumerate(profiles):
        try:
            status.caption(
                f"SCOUT scanning {index + 1}/{len(profiles)}"
            )

            data = extract_market_data(profile)

            if data and data["price"] > 0:
                scores = calculate_score(data)
                data.update(scores)

                results.append(data)

        except Exception:
            errors += 1

        progress.progress(
            min(1.0, (index + 1) / max(1, len(profiles)))
        )

        time.sleep(0.08)

    progress.empty()
    status.empty()

    results.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    st.session_state.scanner_error = (
        f"{errors} token requests failed"
        if errors else ""
    )

    st.session_state.tokens = results
    st.session_state.last_scan = now()
    st.session_state.scan_counter += 1

    return results

# ============================================================
# PAPER TRADING ENGINE
# ============================================================

def is_on_cooldown(address, minutes=30):
    con = db()

    row = con.execute("""
    SELECT last_trade FROM cooldowns
    WHERE token_address=?
    """, (address,)).fetchone()

    con.close()

    if not row:
        return False

    try:
        last = datetime.fromisoformat(row[0])
        age = datetime.now(timezone.utc) - last
        return age < timedelta(minutes=minutes)
    except Exception:
        return False

def set_cooldown(address):
    con = db()

    con.execute("""
    INSERT INTO cooldowns(token_address,last_trade)
    VALUES (?,?)
    ON CONFLICT(token_address)
    DO UPDATE SET last_trade=excluded.last_trade
    """, (address, now()))

    con.commit()
    con.close()

def open_paper_trade(
    token,
    amount,
    stop_loss_pct=10,
    take_profit_pct=30
):
    price = safe_float(token.get("price"))

    if price <= 0:
        return False, "Invalid price"

    if amount <= 0:
        return False, "Invalid amount"

    cash = get_cash()

    if amount > cash:
        return False, "Insufficient virtual cash"

    address = token["address"]

    if is_on_cooldown(address):
        return False, "Token is on cooldown"

    open_positions = get_positions()

    if not open_positions.empty:
        if len(open_positions) >= 10:
            return False, "Maximum open positions reached"

        if address in open_positions["token_address"].values:
            return False, "Position already exists"

    quantity = amount / price

    stop_price = price * (
        1 - stop_loss_pct / 100
    )

    take_price = price * (
        1 + take_profit_pct / 100
    )

    con = db()

    con.execute("""
    INSERT INTO positions (
        token_address,symbol,name,entry_price,current_price,
        quantity,invested,stop_loss,take_profit,score,
        opened_at,status
    )
    VALUES (?,?,?,?,?,?,?,?,?,?,?,'OPEN')
    """, (
        address,
        token.get("symbol", "UNKNOWN"),
        token.get("name", "Unknown"),
        price,
        price,
        quantity,
        amount,
        stop_price,
        take_price,
        safe_float(token.get("score")),
        now()
    ))

    con.execute("""
    INSERT INTO trades (
        token_address,symbol,side,price,quantity,
        amount,pnl,reason,timestamp
    )
    VALUES (?,?,?,?,?,?,0,?,?)
    """, (
        address,
        token.get("symbol", "UNKNOWN"),
        "BUY",
        price,
        quantity,
        amount,
        "PAPER_ENTRY",
        now()
    ))

    con.execute("""
    UPDATE portfolio SET cash=cash-?
    WHERE id=1
    """, (amount,))

    con.commit()
    con.close()

    set_cooldown(address)
    save_price(address, token.get("symbol", ""), price)

    return True, "Paper position opened"

def close_paper_trade(position_id, price, reason="MANUAL"):
    price = safe_float(price)

    if price <= 0:
        return False, "Invalid exit price"

    con = db()

    row = con.execute("""
    SELECT token_address,symbol,quantity,invested,
           entry_price,status
    FROM positions WHERE id=?
    """, (int(position_id),)).fetchone()

    if not row:
        con.close()
        return False, "Position not found"

    address, symbol, quantity, invested, entry, status = row

    if status != "OPEN":
        con.close()
        return False, "Position already closed"

    proceeds = float(quantity) * price
    pnl = proceeds - float(invested)

    con.execute("""
    UPDATE positions
    SET current_price=?,status='CLOSED'
    WHERE id=?
    """, (price, int(position_id)))

    con.execute("""
    INSERT INTO trades (
        token_address,symbol,side,price,quantity,
        amount,pnl,reason,timestamp
    )
    VALUES (?,?,?,?,?,?,?,?,?)
    """, (
        address,
        symbol,
        "SELL",
        price,
        quantity,
        proceeds,
        pnl,
        reason,
        now()
    ))

    con.execute("""
    UPDATE portfolio SET cash=cash+?
    WHERE id=1
    """, (proceeds,))

    con.commit()
    con.close()

    save_price(address, symbol, price)

    return True, pnl

# ============================================================
# PRICE UPDATES AND POSITION MONITOR
# ============================================================

def update_position_prices(tokens):
    if not tokens:
        return

    token_map = {
        t["address"]: t
        for t in tokens
    }

    positions = get_positions()

    if positions.empty:
        return

    con = db()

    for _, position in positions.iterrows():
        address = position["token_address"]

        if address in token_map:
            price = token_map[address]["price"]

            con.execute("""
            UPDATE positions
            SET current_price=?
            WHERE id=?
            """, (price, int(position["id"])))

            save_price(
                address,
                position["symbol"],
                price
            )

    con.commit()
    con.close()

def monitor_positions():
    positions = get_positions()

    if positions.empty:
        return

    for _, position in positions.iterrows():
        price = safe_float(position["current_price"])

        if price <= 0:
            continue

        if price <= position["stop_loss"]:
            close_paper_trade(
                position["id"],
                price,
                "STOP_LOSS"
            )

        elif price >= position["take_profit"]:
            close_paper_trade(
                position["id"],
                price,
                "TAKE_PROFIT"
            )

# ============================================================
# AUTO SNIPER
# ============================================================

def auto_sniper(
    tokens,
    trade_size,
    max_positions,
    minimum_score,
    daily_loss_limit
):
    if get_daily_pnl() <= -abs(daily_loss_limit):
        return 0

    positions = get_positions()

    if len(positions) >= max_positions:
        return 0

    opened = 0

    for token in tokens:
        if token["signal"] != "BUY":
            continue

        if token["score"] < minimum_score:
            continue

        if token["risk"] >= 35:
            continue

        if token["liquidity"] < 5000:
            continue

        if token["volume_24h"] < 5000:
            continue

        success, _ = open_paper_trade(
            token,
            trade_size
        )

        if success:
            opened += 1

        if len(get_positions()) >= max_positions:
            break

    return opened

# ============================================================
# PORTFOLIO ANALYTICS
# ============================================================

def portfolio_metrics():
    cash = get_cash()
    positions = get_positions()

    market_value = 0
    invested = 0
    unrealized = 0

    if not positions.empty:
        market_value = float(
            (
                positions["quantity"]
                * positions["current_price"]
            ).sum()
        )

        invested = float(
            positions["invested"].sum()
        )

        unrealized = market_value - invested

    realized = get_realized_pnl()

    equity = cash + market_value

    starting = float(get_portfolio()[1])

    total_return = (
        (equity / starting - 1) * 100
        if starting else 0
    )

    return {
        "cash": cash,
        "market_value": market_value,
        "invested": invested,
        "unrealized": unrealized,
        "realized": realized,
        "equity": equity,
        "return_pct": total_return
    }

def trade_statistics():
    trades = get_trade_history(5000)

    if trades.empty:
        return {
            "closed": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0,
            "avg_win": 0,
            "avg_loss": 0,
            "profit_factor": 0
        }

    sells = trades[trades["side"] == "SELL"]

    if sells.empty:
        return {
            "closed": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0,
            "avg_win": 0,
            "avg_loss": 0,
            "profit_factor": 0
        }

    wins = sells[sells["pnl"] > 0]
    losses = sells[sells["pnl"] < 0]

    gross_profit = wins["pnl"].sum()
    gross_loss = abs(losses["pnl"].sum())

    profit_factor = (
        gross_profit / gross_loss
        if gross_loss > 0 else 0
    )

    return {
        "closed": len(sells),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(sells) * 100,
        "avg_win": wins["pnl"].mean() if len(wins) else 0,
        "avg_loss": losses["pnl"].mean() if len(losses) else 0,
        "profit_factor": profit_factor
    }

def get_price_history(address):
    con = db()

    rows = con.execute("""
    SELECT price,timestamp
    FROM price_history
    WHERE token_address=?
    ORDER BY id ASC
    """, (address,)).fetchall()

    con.close()

    return pd.DataFrame(
        rows,
        columns=["price", "timestamp"]
    )

def get_equity_history():
    con = db()

    rows = con.execute("""
    SELECT equity,timestamp
    FROM snapshots
    ORDER BY id ASC
    """).fetchall()

    con.close()

    return pd.DataFrame(
        rows,
        columns=["equity", "timestamp"]
    )

# ============================================================
# BACKTESTING
# ============================================================

def backtest_strategy(
    prices,
    initial_balance=1000,
    stop_loss=10,
    take_profit=30
):
    if not prices:
        return None

    balance = float(initial_balance)
    position = None
    trades = []

    for item in prices:
        price = safe_float(item)

        if price <= 0:
            continue

        if position is None:
            position = {
                "entry": price,
                "amount": balance * 0.1
            }

            balance -= position["amount"]

        else:
            change = (
                price / position["entry"] - 1
            ) * 100

            if change <= -stop_loss or change >= take_profit:
                proceeds = (
                    position["amount"]
                    * price
                    / position["entry"]
                )

                pnl = proceeds - position["amount"]

                balance += proceeds

                trades.append({
                    "entry": position["entry"],
                    "exit": price,
                    "pnl": pnl,
                    "return_pct": change
                })

                position = None

    if position:
        final_price = safe_float(prices[-1])

        proceeds = (
            position["amount"]
            * final_price
            / position["entry"]
        )

        balance += proceeds

    return {
        "ending_balance": balance,
        "return_pct": (
            balance / initial_balance - 1
        ) * 100,
        "trades": pd.DataFrame(trades)
    }

# ============================================================
# RISK MANAGEMENT
# ============================================================

def calculate_position_size(
    balance,
    risk_percent,
    entry_price,
    stop_price
):
    if entry_price <= 0 or stop_price <= 0:
        return 0

    risk_amount = balance * (
        risk_percent / 100
    )

    distance = abs(
        entry_price - stop_price
    ) / entry_price

    if distance <= 0:
        return 0

    size = risk_amount / distance

    return min(size, balance * 0.2)

def check_risk_limits(
    amount,
    max_trade_percent=20
):
    balance = get_cash()

    if amount > balance:
        return False

    if amount > balance * (
        max_trade_percent / 100
    ):
        return False

    if get_daily_pnl() <= -50:
        return False

    return True

# ============================================================
# TOKEN DETAILS
# ============================================================

def token_detail_card(token):
    st.markdown(
        f"""
        <div class="panel">
            <div class="panel-title">TOKEN PROFILE</div>
            <h2>{token.get("symbol","UNKNOWN")}</h2>
            <p class="muted">{token.get("name","Unknown")}</p>
            <h2 class="neon">${token.get("price",0):.10f}</h2>
            <p>Score: {token.get("score",0)}/100</p>
            <p>Risk: {token.get("risk",0)}/100</p>
            <p>Liquidity: ${token.get("liquidity",0):,.0f}</p>
            <p>24h Volume: ${token.get("volume_24h",0):,.0f}</p>
        </div>
        """,
        unsafe_allow_html=True
    )

# ============================================================
# DASHBOARD COMPONENTS
# ============================================================

def render_header():
    st.markdown(
        f"""
        <div class="panel">
            <div style="display:flex;justify-content:space-between;align-items:center">
                <div>
                    <div class="neon" style="font-size:12px;letter-spacing:4px">
                        SOLANA INTELLIGENCE TERMINAL
                    </div>
                    <h1 style="margin:5px 0">NIGHT RUN <span class="neon">ULTRA</span></h1>
                    <div class="muted">VERSION {VERSION} | PAPER TRADING SYSTEM</div>
                </div>
                <div style="text-align:right">
                    <h2 class="green">● SYSTEM ONLINE</h2>
                    <div class="muted">{datetime.now().strftime("%H:%M:%S")}</div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

def render_metrics():
    metrics = portfolio_metrics()

    cols = st.columns(5)

    cols[0].metric(
        "TOTAL EQUITY",
        f"${metrics['equity']:,.2f}",
        f"{metrics['return_pct']:+.2f}%"
    )

    cols[1].metric(
        "VIRTUAL CASH",
        f"${metrics['cash']:,.2f}"
    )

    cols[2].metric(
        "MARKET VALUE",
        f"${metrics['market_value']:,.2f}"
    )

    cols[3].metric(
        "UNREALIZED P/L",
        f"${metrics['unrealized']:+,.2f}"
    )

    cols[4].metric(
        "REALIZED P/L",
        f"${metrics['realized']:+,.2f}"
    )

def render_system_status():
    positions = get_positions()

    st.markdown("### SYSTEM STATUS")

    cols = st.columns(4)

    cols[0].metric(
        "OPEN POSITIONS",
        len(positions)
    )

    cols[1].metric(
        "SCANNED TOKENS",
        len(st.session_state.tokens)
    )

    cols[2].metric(
        "SCAN COUNT",
        st.session_state.scan_counter
    )

    cols[3].metric(
        "DAILY P/L",
        f"${get_daily_pnl():+.2f}"
    )

def render_scanner():
    st.markdown("## MARKET SCANNER")

    left, right = st.columns([1, 4])

    with left:
        max_tokens = st.selectbox(
            "Tokens to inspect",
            [10, 20, 30, 40, 60],
            index=2
        )

        if st.button(
            "⚡ SCAN MARKET",
            use_container_width=True
        ):
            with st.spinner("SCOUT scanning Solana..."):
                scan_market(max_tokens)

    with right:
        if st.session_state.last_scan:
            st.caption(
                f"Last scan: {st.session_state.last_scan}"
            )

        if st.session_state.scanner_error:
            st.warning(
                st.session_state.scanner_error
            )

    tokens = st.session_state.tokens

    if not tokens:
        st.info(
            "No scanned tokens yet. Start a market scan."
        )
        return

    df = pd.DataFrame(tokens)

    display_columns = [
        "symbol",
        "name",
        "price",
        "liquidity",
        "volume_24h",
        "change_1h",
        "score",
        "risk",
        "signal"
    ]

    df = df[display_columns]

    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "price": st.column_config.NumberColumn(
                "Price",
                format="%.10f"
            ),
            "liquidity": st.column_config.NumberColumn(
                "Liquidity",
                format="$%.0f"
            ),
            "volume_24h": st.column_config.NumberColumn(
                "24h Volume",
                format="$%.0f"
            ),
            "score": st.column_config.ProgressColumn(
                "Score",
                min_value=0,
                max_value=100
            ),
            "risk": st.column_config.ProgressColumn(
                "Risk",
                min_value=0,
                max_value=100
            )
        }
    )

    symbols = [
        f"{t['symbol']} | {t['address'][:8]}"
        for t in tokens
    ]

    selected = st.selectbox(
        "Select token",
        symbols
    )

    index = symbols.index(selected)
    token = tokens[index]

    token_detail_card(token)

    st.code(
        token["address"],
        language="text"
    )

    st.markdown(
        f"[Open on DexScreener]({token.get('url','#')})"
    )

    trade_amount = st.number_input(
        "Paper trade amount",
        min_value=1.0,
        max_value=max(1.0, get_cash()),
        value=min(50.0, max(1.0, get_cash())),
        step=5.0
    )

    if st.button("OPEN PAPER POSITION"):
        if check_risk_limits(trade_amount):
            success, message = open_paper_trade(
                token,
                trade_amount
            )

            if success:
                st.success(message)
            else:
                st.error(message)
        else:
            st.error("Risk limit blocked this trade")

def render_positions():
    st.markdown("## OPEN POSITIONS")

    positions = get_positions()

    if positions.empty:
        st.info("No open paper positions")
        return

    positions["market_value"] = (
        positions["quantity"]
        * positions["current_price"]
    )

    positions["pnl"] = (
        positions["market_value"]
        - positions["invested"]
    )

    positions["pnl_pct"] = (
        positions["pnl"]
        / positions["invested"]
        * 100
    )

    st.dataframe(
        positions[[
            "symbol",
            "entry_price",
            "current_price",
            "invested",
            "market_value",
            "pnl",
            "pnl_pct",
            "score"
        ]],
        use_container_width=True,
        hide_index=True
    )

    for _, position in positions.iterrows():
        with st.expander(
            f"{position['symbol']} | "
            f"${position['pnl']:+.2f}"
        ):
            st.write(
                f"Entry: ${position['entry_price']:.10f}"
            )

            st.write(
                f"Current: ${position['current_price']:.10f}"
            )

            st.write(
                f"Stop loss: ${position['stop_loss']:.10f}"
            )

            st.write(
                f"Take profit: ${position['take_profit']:.10f}"
            )

            if st.button(
                "CLOSE POSITION",
                key=f"close_{position['id']}"
            ):
                success, result = close_paper_trade(
                    position["id"],
                    position["current_price"],
                    "MANUAL"
                )

                if success:
                    st.success(
                        f"Closed with P/L ${result:+.2f}"
                    )
                    st.rerun()

def render_auto_sniper():
    st.markdown("## AUTO SNIPER")

    st.markdown(
        "Automatic paper trading based on scanner signals"
    )

    enabled = st.toggle(
        "ENABLE AUTO SNIPER",
        value=st.session_state.auto_enabled
    )

    st.session_state.auto_enabled = enabled

    col1, col2, col3 = st.columns(3)

    with col1:
        amount = st.number_input(
            "Trade size",
            min_value=1.0,
            max_value=500.0,
            value=25.0,
            step=5.0
        )

    with col2:
        max_positions = st.slider(
            "Maximum positions",
            1,
            10,
            5
        )

    with col3:
        min_score = st.slider(
            "Minimum score",
            50,
            100,
            75
        )

    daily_limit = st.number_input(
        "Daily realized loss limit",
        min_value=1.0,
        max_value=500.0,
        value=50.0
    )

    if enabled:
        st.success("AUTO SNIPER ENABLED")
    else:
        st.warning("AUTO SNIPER DISABLED")

    if st.button("RUN AUTO SNIPER NOW"):
        opened = auto_sniper(
            st.session_state.tokens,
            amount,
            max_positions,
            min_score,
            daily_limit
        )

        st.success(
            f"{opened} paper positions opened"
        )

def render_analytics():
    st.markdown("## ANALYTICS")

    metrics = portfolio_metrics()
    stats = trade_statistics()

    cols = st.columns(4)

    cols[0].metric(
        "CLOSED TRADES",
        stats["closed"]
    )

    cols[1].metric(
        "WIN RATE",
        f"{stats['win_rate']:.1f}%"
    )

    cols[2].metric(
        "AVERAGE WIN",
        f"${stats['avg_win']:.2f}"
    )

    cols[3].metric(
        "PROFIT FACTOR",
        f"{stats['profit_factor']:.2f}"
    )

    st.markdown("### EQUITY OVER TIME")

    history = get_equity_history()

    if not history.empty:
        history["timestamp"] = pd.to_datetime(
            history["timestamp"]
        )

        st.line_chart(
            history.set_index("timestamp")["equity"]
        )
    else:
        st.info(
            "Equity history will appear after snapshots are recorded"
        )

    st.markdown("### PERFORMANCE BREAKDOWN")

    st.write({
        "Realized P/L": metrics["realized"],
        "Unrealized P/L": metrics["unrealized"],
        "Total return": metrics["return_pct"],
        "Winning trades": stats["wins"],
        "Losing trades": stats["losses"]
    })

def render_trade_history():
    st.markdown("## TRADE HISTORY")

    history = get_trade_history()

    if history.empty:
        st.info("No trades recorded")
        return

    st.dataframe(
        history,
        use_container_width=True,
        hide_index=True
    )

    csv = history.to_csv(index=False).encode("utf-8")

    st.download_button(
        "DOWNLOAD TRADE HISTORY CSV",
        data=csv,
        file_name="night_run_v7_trades.csv",
        mime="text/csv"
    )

def render_backtesting():
    st.markdown("## BACKTESTING LAB")

    st.info(
        "This is a simplified price-sequence simulation, "
        "not a historical market backtest."
    )

    raw = st.text_area(
        "Enter historical prices separated by commas",
        placeholder="0.0001,0.00011,0.00009,0.00012"
    )

    balance = st.number_input(
        "Initial balance",
        min_value=10.0,
        value=1000.0
    )

    stop = st.slider(
        "Stop loss %",
        1,
        50,
        10
    )

    take = st.slider(
        "Take profit %",
        1,
        200,
        30
    )

    if st.button("RUN SIMULATION"):
        try:
            prices = [
                float(x.strip())
                for x in raw.split(",")
                if x.strip()
            ]

            result = backtest_strategy(
                prices,
                balance,
                stop,
                take
            )

            if result:
                st.metric(
                    "Ending balance",
                    f"${result['ending_balance']:,.2f}",
                    f"{result['return_pct']:+.2f}%"
                )

                st.dataframe(
                    result["trades"],
                    use_container_width=True
                )
            else:
                st.warning("Enter valid prices")

        except ValueError:
            st.error("Invalid price sequence")

def render_risk_center():
    st.markdown("## RISK CENTER")

    metrics = portfolio_metrics()

    positions = get_positions()

    stats = trade_statistics()

    st.metric(
        "DAILY REALIZED P/L",
        f"${get_daily_pnl():+.2f}"
    )

    st.metric(
        "OPEN CAPITAL",
        f"${metrics['invested']:,.2f}"
    )

    st.metric(
        "AVAILABLE CASH",
        f"${metrics['cash']:,.2f}"
    )

    if metrics["cash"] < 100:
        st.warning("Low available virtual cash")

    if len(positions) >= 8:
        st.warning("Portfolio concentration is high")

    if stats["closed"] >= 5 and stats["win_rate"] < 30:
        st.warning(
            "Recent paper trading performance is weak"
        )

    st.markdown("### RISK SETTINGS")

    st.write({
        "Maximum position size": "20% of available cash",
        "Default stop loss": "10%",
        "Default take profit": "30%",
        "Daily realized loss limit": "$50",
        "Maximum open positions": 10
    })

# ============================================================
# MODULE STATUS
# ============================================================

def render_modules():
    modules = [
        ("SCOUT", "Market discovery", "ONLINE"),
        ("RADAR", "Liquidity and volume", "ONLINE"),
        ("FILTER", "Rule-based risk filtering", "ONLINE"),
        ("ANALYST", "Token scoring engine", "ONLINE"),
        ("SNIPER", "Paper execution", "ONLINE"),
        ("GUARD", "Position monitoring", "ONLINE"),
        ("ORACLE", "Historical simulation", "LIMITED"),
        ("ARCHIVE", "Trade database", "ONLINE")
    ]

    st.markdown("## SYSTEM MODULES")

    for name, description, status in modules:
        st.markdown(
            f"""
            <div class="panel" style="padding:12px">
                <b class="neon">{name}</b>
                <span class="muted"> | {description}</span>
                <span style="float:right;color:#48e6a5">
                    ● {status}
                </span>
            </div>
            """,
            unsafe_allow_html=True
        )

# ============================================================
# SETTINGS
# ============================================================

def render_settings():
    st.markdown("## SETTINGS")

    st.write(
        "This application uses virtual funds only."
    )

    st.warning(
        "Do not enter wallet seed phrases or private keys. "
        "This application does not execute real trades."
    )

    if st.button("CREATE PORTFOLIO SNAPSHOT"):
        save_snapshot()
        st.success("Snapshot saved")

    st.markdown("### DATABASE")

    st.write(f"Database: `{DB_FILE}`")

    if st.button("EXPORT DATABASE TABLES"):
        con = db()

        tables = [
            "positions",
            "trades",
            "price_history",
            "snapshots"
        ]

        for table in tables:
            frame = pd.read_sql_query(
                f"SELECT * FROM {table}",
                con
            )

            st.download_button(
                f"Download {table}",
                frame.to_csv(index=False),
                file_name=f"{table}.csv",
                key=f"export_{table}"
            )

        con.close()

    st.markdown("### RESET V7 PORTFOLIO")

    st.error(
        "This permanently deletes the V7 database data."
    )

    confirm = st.checkbox(
        "I understand that V7 paper trading data will be deleted"
    )

    if st.button(
        "RESET V7 DATABASE",
        disabled=not confirm
    ):
        con = db()

        for table in [
            "positions",
            "trades",
            "price_history",
            "snapshots",
            "cooldowns"
        ]:
            con.execute(
                f"DELETE FROM {table}"
            )

        con.execute("""
        UPDATE portfolio
        SET cash=?,starting_balance=?,created_at=?
        WHERE id=1
        """, (
            STARTING_BALANCE,
            STARTING_BALANCE,
            now()
        ))

        con.commit()
        con.close()

        st.success("V7 portfolio reset")
        st.rerun()

# ============================================================
# MAIN APP
# ============================================================

def main():
    init_database()

    render_header()
    render_metrics()
    render_system_status()

    st.divider()

    tabs = st.tabs([
        "MARKET SCANNER",
        "OPEN POSITIONS",
        "AUTO SNIPER",
        "ANALYTICS",
        "BACKTESTING",
        "RISK CENTER",
        "TRADE HISTORY",
        "MODULES",
        "SETTINGS"
    ])

    with tabs[0]:
        render_scanner()

    with tabs[1]:
        render_positions()

    with tabs[2]:
        render_auto_sniper()

    with tabs[3]:
        render_analytics()

    with tabs[4]:
        render_backtesting()

    with tabs[5]:
        render_risk_center()

    with tabs[6]:
        render_trade_history()

    with tabs[7]:
        render_modules()

    with tabs[8]:
        render_settings()

    st.divider()

    st.caption(
        "NIGHT RUN ULTRA V7 PRO | PAPER TRADING ONLY | "
        "Market data by DexScreener | "
        "No wallet connection | No real transactions"
    )

if __name__ == "__main__":
    main()
