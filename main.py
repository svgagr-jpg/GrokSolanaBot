
import streamlit as st
import requests
import sqlite3
import pandas as pd
import time
from datetime import datetime, timezone, timedelta

# =========================================================
# NIGHT RUN PRO V5
# SOLANA PAPER TRADING ONLY
# Keeps existing V4 database and trade history
# =========================================================

st.set_page_config(
    page_title="NIGHT RUN PRO V5",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

DB = "bot.db"
REFRESH = 5000
SCAN_INTERVAL = 60
TRADE_CHECK = 30
STARTING_CASH = 1000.0

PROFILES_URL = "https://api.dexscreener.com/token-profiles/latest/v1"
PAIRS_URL = "https://api.dexscreener.com/token-pairs/v1/solana/{}"

# ---------------- NIGHT RUN DESIGN ----------------

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&family=Space+Mono:wght@400;700&display=swap');

:root {
    --bg: #07090f;
    --panel: #101521;
    --panel2: #141c2b;
    --border: #222d40;
    --green: #42f5a1;
    --purple: #a78bfa;
    --blue: #60a5fa;
    --red: #fb7185;
    --muted: #8491a5;
}

html, body, [class*="css"] {
    font-family: 'Inter', sans-serif;
}

.stApp {
    background:
      radial-gradient(ellipse at 15% 0%, #17233a 0%, transparent 35%),
      radial-gradient(ellipse at 90% 10%, #24153c 0%, transparent 30%),
      var(--bg);
    color: #edf2fa;
}

[data-testid="stSidebar"] {
    background: #0b101a;
    border-right: 1px solid var(--border);
}

.block-container {
    padding-top: 1.5rem;
    padding-bottom: 3rem;
    max-width: 1600px;
}

h1, h2, h3 {
    color: #f1f5ff;
    letter-spacing: -0.5px;
}

div[data-testid="stMetric"] {
    background: linear-gradient(145deg, #141d2c, #0d131e);
    border: 1px solid var(--border);
    padding: 17px;
    border-radius: 14px;
}

div[data-testid="stMetricValue"] {
    font-family: 'Space Mono', monospace;
    color: var(--green);
}

div[data-testid="stMetricLabel"] {
    color: #91a0b5;
}

.stButton > button {
    background: linear-gradient(100deg, #42f5a1, #26c98c);
    color: #07130e;
    border: 0;
    border-radius: 9px;
    font-weight: 900;
    min-height: 42px;
    transition: 0.2s;
}

.stButton > button:hover {
    background: #8affc5;
    color: #06120d;
    border: 0;
    transform: translateY(-1px);
}

.stTabs [data-baseweb="tab-list"] {
    gap: 8px;
    background: #0d131e;
    border: 1px solid var(--border);
    padding: 7px;
    border-radius: 12px;
}

.stTabs [data-baseweb="tab"] {
    background: transparent;
    border-radius: 8px;
    color: #9aa8bc;
    font-weight: 700;
}

.stTabs [aria-selected="true"] {
    background: #1b2b35 !important;
    color: var(--green) !important;
}

[data-testid="stDataFrame"] {
    border: 1px solid var(--border);
    border-radius: 12px;
}

hr {
    border-color: var(--border);
}

.terminal {
    font-family: 'Space Mono', monospace;
    color: var(--green);
    font-size: 12px;
    letter-spacing: 1px;
}

.muted {
    color: var(--muted);
    font-size: 12px;
}

.hero {
    background: linear-gradient(115deg, #111b2b, #17142b 70%, #10251f);
    border: 1px solid #29364b;
    border-radius: 18px;
    padding: 25px;
    margin-bottom: 18px;
    box-shadow: 0 12px 45px #0005;
}

.hero-title {
    font-size: 32px;
    font-weight: 900;
    letter-spacing: 2px;
    color: #f5f7ff;
}

.hero-sub {
    color: #94a3b8;
    font-family: 'Space Mono', monospace;
    font-size: 11px;
    letter-spacing: 2px;
}

.agent {
    background: linear-gradient(145deg, #141d2b, #0d131d);
    border: 1px solid #283448;
    border-radius: 13px;
    padding: 15px;
    min-height: 115px;
    margin-bottom: 8px;
}

.agent-name {
    font-size: 13px;
    font-weight: 900;
    color: #f0f5ff;
    letter-spacing: 1px;
}

.agent-desc {
    color: #8291a6;
    font-size: 11px;
    margin-top: 6px;
}

.agent-status {
    color: #42f5a1;
    font-family: 'Space Mono', monospace;
    font-size: 10px;
    margin-top: 12px;
}

.panel {
    background: #101724;
    border: 1px solid #253247;
    border-radius: 14px;
    padding: 17px;
    margin-bottom: 12px;
}

.panel-title {
    color: #aebbd0;
    font-size: 11px;
    font-weight: 800;
    letter-spacing: 1.5px;
    margin-bottom: 10px;
}

.big-number {
    font-family: 'Space Mono', monospace;
    font-size: 24px;
    color: #42f5a1;
    font-weight: 700;
}

.tag {
    display: inline-block;
    padding: 5px 9px;
    border-radius: 6px;
    background: #173327;
    color: #42f5a1;
    font-family: 'Space Mono', monospace;
    font-size: 10px;
    font-weight: 700;
}

.tag-purple {
    background: #282044;
    color: #c4b5fd;
}

.tag-red {
    background: #3a1d29;
    color: #fb7185;
}

div[data-testid="stAlert"] {
    border-radius: 10px;
}

footer {
    visibility: hidden;
}
</style>
""", unsafe_allow_html=True)


# ---------------- DATABASE ----------------

def connect():
    return sqlite3.connect(DB, timeout=20)


def init_db():
    with connect() as con:
        con.execute("""
        CREATE TABLE IF NOT EXISTS sniper_trades_v3 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_address TEXT,
            symbol TEXT,
            name TEXT,
            entry_price REAL,
            current_price REAL,
            amount REAL,
            quantity REAL,
            pnl REAL DEFAULT 0,
            pnl_pct REAL DEFAULT 0,
            status TEXT DEFAULT 'OPEN',
            entry_time TEXT,
            close_time TEXT,
            close_reason TEXT,
            auto_trade INTEGER DEFAULT 0
        )
        """)

        con.execute("""
        CREATE TABLE IF NOT EXISTS sniper_snapshots_v3 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            portfolio REAL,
            cash REAL,
            pnl REAL
        )
        """)

        con.execute("""
        CREATE TABLE IF NOT EXISTS v4_price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            address TEXT,
            symbol TEXT,
            timestamp TEXT,
            price REAL,
            volume REAL
        )
        """)

        con.execute("""
        CREATE TABLE IF NOT EXISTS v4_cooldowns (
            address TEXT PRIMARY KEY,
            last_trade REAL
        )
        """)
        con.commit()


init_db()


# ---------------- SESSION ----------------

defaults = {
    "tokens": [],
    "last_scan": 0,
    "last_trade_check": 0,
    "last_snapshot": 0,
    "auto_enabled": False,
    "scan_message": "Waiting for scan",
    "last_auto_message": "Auto Sniper waiting",
    "scan_number": 0,
    "last_error": ""
}

for key, value in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = value


# ---------------- HELPERS ----------------

def number(value, default=0):
    try:
        return float(value or 0)
    except (ValueError, TypeError):
        return default


def now_utc():
    return datetime.now(timezone.utc)


def money(value):
    return f"${number(value):,.2f}"


def get_profiles():
    try:
        response = requests.get(PROFILES_URL, timeout=15)
        response.raise_for_status()
        data = response.json()
        return [
            item for item in data
            if item.get("chainId") == "solana"
            and item.get("tokenAddress")
        ]
    except Exception as error:
        st.session_state.last_error = str(error)
        return []


def get_pairs(address):
    try:
        response = requests.get(
            PAIRS_URL.format(address),
            timeout=15
        )
        response.raise_for_status()
        data = response.json()

        if isinstance(data, list):
            return [
                pair for pair in data
                if pair.get("chainId") == "solana"
            ]
    except Exception:
        pass

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


# ---------------- PRICE HISTORY ----------------

def save_price(token):
    with connect() as con:
        con.execute("""
        INSERT INTO v4_price_history
        (address, symbol, timestamp, price, volume)
        VALUES (?, ?, ?, ?, ?)
        """, (
            token["address"],
            token["symbol"],
            now_utc().isoformat(),
            token["price"],
            token["volume"]
        ))

        con.execute("""
        DELETE FROM v4_price_history
        WHERE timestamp < ?
        """, (
            (now_utc() - timedelta(days=3)).isoformat(),
        ))
        con.commit()


def get_price_history(address):
    with connect() as con:
        return pd.read_sql_query("""
        SELECT timestamp, price
        FROM v4_price_history
        WHERE address = ?
        ORDER BY id ASC
        """, con, params=(address,))


# ---------------- COOLDOWN ----------------

def on_cooldown(address, hours):
    with connect() as con:
        row = con.execute("""
        SELECT last_trade FROM v4_cooldowns
        WHERE address = ?
        """, (address,)).fetchone()

    if not row:
        return False

    return time.time() - number(row[0]) < hours * 3600


def set_cooldown(address):
    with connect() as con:
        con.execute("""
        INSERT INTO v4_cooldowns(address, last_trade)
        VALUES (?, ?)
        ON CONFLICT(address)
        DO UPDATE SET last_trade = excluded.last_trade
        """, (address, time.time()))
        con.commit()


# ---------------- TOKEN ANALYSIS ----------------

def analyze(pair, old_tokens):
    base = pair.get("baseToken") or {}

    address = base.get("address", "")
    symbol = base.get("symbol", "UNKNOWN")
    name = base.get("name", symbol)

    price = number(pair.get("priceUsd"))
    liquidity = number(
        (pair.get("liquidity") or {}).get("usd")
    )
    volume = number(
        (pair.get("volume") or {}).get("h24")
    )
    change = number(
        (pair.get("priceChange") or {}).get("h24")
    )

    txns = pair.get("txns") or {}
    h24 = txns.get("h24") or {}

    buys = number(h24.get("buys"))
    sells = number(h24.get("sells"))
    total = buys + sells

    created = number(pair.get("pairCreatedAt"))
    age_hours = 9999

    if created:
        age_hours = max(
            0,
            (time.time() * 1000 - created) / 3600000
        )

    flags = []

    if liquidity < 5000:
        flags.append("LOW LIQUIDITY")
    if liquidity < 1500:
        flags.append("VERY LOW LIQUIDITY")
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
    if age_hours < 1:
        flags.append("VERY NEW PAIR")

    old = next(
        (
            token for token in old_tokens
            if token["address"] == address
        ),
        None
    )

    spike = False

    if old and old["volume"] > 0:
        spike = volume >= old["volume"] * 1.5

    if spike:
        flags.append("VOLUME SPIKE")

    liquidity_score = min(liquidity / 2500, 35)
    volume_score = (
        min((volume / max(liquidity, 1)) / 1.5, 1) * 25
    )

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

    score = int(min(100, max(
        0,
        liquidity_score
        + volume_score
        + momentum_score
        + activity_score
    )))

    severe = (
        price <= 0
        or liquidity < 1500
        or change < -60
        or "NO RECORDED SELLS" in flags
    )

    if severe or score < 30:
        signal = "AVOID"
    elif (
        score >= 70
        and liquidity >= 5000
        and volume >= 5000
        and total >= 10
        and change < 100
        and not on_cooldown(address, 1)
    ):
        signal = "BUY"
    else:
        signal = "WATCH"

    return {
        "name": name,
        "symbol": symbol,
        "address": address,
        "price": price,
        "liquidity": liquidity,
        "volume": volume,
        "change": change,
        "buys": int(buys),
        "sells": int(sells),
        "age_hours": age_hours,
        "score": score,
        "signal": signal,
        "flags": flags,
        "spike": spike,
        "url": pair.get("url", "")
    }


# ---------------- SCANNER ----------------

def scan():
    profiles = get_profiles()
    results = []
    seen = set()
    previous = st.session_state.tokens

    for profile in profiles[:35]:
        address = profile.get("tokenAddress")

        if not address or address in seen:
            continue

        seen.add(address)

        pair = best_pair(get_pairs(address))

        if not pair:
            continue

        token = analyze(pair, previous)

        if token["price"] <= 0:
            continue

        save_price(token)
        results.append(token)

    results.sort(
        key=lambda token: (
            token["score"],
            token["volume"]
        ),
        reverse=True
    )

    return results[:20]


# ---------------- PORTFOLIO ----------------

def open_positions():
    with connect() as con:
        return pd.read_sql_query("""
        SELECT * FROM sniper_trades_v3
        WHERE status = 'OPEN'
        ORDER BY id DESC
        """, con)


def cash_balance():
    with connect() as con:
        rows = con.execute("""
        SELECT amount, pnl, status
        FROM sniper_trades_v3
        """).fetchall()

    invested = sum(
        number(row[0])
        for row in rows
        if row[2] == "OPEN"
    )

    realized = sum(
        number(row[1])
        for row in rows
        if row[2] == "CLOSED"
    )

    return STARTING_CASH - invested + realized


def realized_total():
    with connect() as con:
        value = con.execute("""
        SELECT COALESCE(SUM(pnl), 0)
        FROM sniper_trades_v3
        WHERE status = 'CLOSED'
        """).fetchone()[0]

    return number(value)


def realized_today():
    today = now_utc().date().isoformat()

    with connect() as con:
        rows = con.execute("""
        SELECT pnl, close_time
        FROM sniper_trades_v3
        WHERE status = 'CLOSED'
        """).fetchall()

    total = 0

    for pnl, close_time in rows:
        if close_time and str(close_time).startswith(today):
            total += number(pnl)

    return total


def open_trade(token, amount, auto=False, cooldown_hours=1):
    if token["price"] <= 0 or amount <= 0:
        return False

    if amount > cash_balance():
        return False

    if on_cooldown(token["address"], cooldown_hours):
        return False

    with connect() as con:
        existing = con.execute("""
        SELECT id FROM sniper_trades_v3
        WHERE token_address = ? AND status = 'OPEN'
        """, (token["address"],)).fetchone()

        if existing:
            return False

        con.execute("""
        INSERT INTO sniper_trades_v3 (
            token_address, symbol, name, entry_price,
            current_price, amount, quantity, pnl, pnl_pct,
            status, entry_time, auto_trade
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 'OPEN', ?, ?)
        """, (
            token["address"],
            token["symbol"],
            token["name"],
            token["price"],
            token["price"],
            amount,
            amount / token["price"],
            now_utc().isoformat(),
            1 if auto else 0
        ))
        con.commit()

    set_cooldown(token["address"])
    return True


def update_position(trade_id, price):
    with connect() as con:
        row = con.execute("""
        SELECT entry_price, quantity
        FROM sniper_trades_v3
        WHERE id = ? AND status = 'OPEN'
        """, (trade_id,)).fetchone()

        if not row or price <= 0:
            return

        entry, quantity = row

        pnl = (price - entry) * quantity
        pct = ((price / entry) - 1) * 100 if entry else 0

        con.execute("""
        UPDATE sniper_trades_v3
        SET current_price = ?, pnl = ?, pnl_pct = ?
        WHERE id = ?
        """, (price, pnl, pct, trade_id))
        con.commit()


def close_trade(trade_id, price, reason="MANUAL"):
    with connect() as con:
        row = con.execute("""
        SELECT entry_price, quantity
        FROM sniper_trades_v3
        WHERE id = ? AND status = 'OPEN'
        """, (trade_id,)).fetchone()

        if not row or price <= 0:
            return

        entry, quantity = row

        pnl = (price - entry) * quantity
        pct = ((price / entry) - 1) * 100 if entry else 0

        con.execute("""
        UPDATE sniper_trades_v3
        SET current_price = ?, pnl = ?, pnl_pct = ?,
            status = 'CLOSED', close_time = ?,
            close_reason = ?
        WHERE id = ?
        """, (
            price,
            pnl,
            pct,
            now_utc().isoformat(),
            reason,
            trade_id
        ))
        con.commit()


# ---------------- AUTO SNIPER ----------------

def daily_limit_hit(limit):
    return realized_today() <= -abs(limit)


def run_auto(amount, max_positions, min_score,
             daily_limit, cooldown_hours):

    if daily_limit_hit(daily_limit):
        st.session_state.auto_enabled = False
        return "DAILY LOSS LIMIT HIT"

    positions = open_positions()

    if len(positions) >= max_positions:
        return "MAXIMUM OPEN POSITIONS REACHED"

    candidates = [
        token for token in st.session_state.tokens
        if token["signal"] == "BUY"
        and token["score"] >= min_score
        and token["liquidity"] >= 5000
        and token["volume"] >= 5000
        and token["change"] < 100
        and "NO RECORDED SELLS" not in token["flags"]
        and not on_cooldown(
            token["address"],
            cooldown_hours
        )
    ]

    for token in candidates:
        if len(open_positions()) >= max_positions:
            break

        if open_trade(
            token,
            amount,
            True,
            cooldown_hours
        ):
            return "PAPER TRADE OPENED: " + token["symbol"]

    return "NO TOKEN PASSED SMART ENTRY"


# ---------------- MONITOR ----------------

def monitor(stop_loss, take_profit):
    positions = open_positions()

    for _, trade in positions.iterrows():
        pair = best_pair(
            get_pairs(trade["token_address"])
        )

        if not pair:
            continue

        price = number(pair.get("priceUsd"))

        if price <= 0:
            continue

        update_position(int(trade["id"]), price)

        entry = number(trade["entry_price"])
        pct = (
            ((price / entry) - 1) * 100
            if entry else 0
        )

        if pct <= -stop_loss:
            close_trade(
                int(trade["id"]),
                price,
                "STOP LOSS"
            )

        elif pct >= take_profit:
            close_trade(
                int(trade["id"]),
                price,
                "TAKE PROFIT"
            )


# ---------------- SNAPSHOTS ----------------

def snapshot():
    if time.time() - st.session_state.last_snapshot < 60:
        return

    positions = open_positions()

    unrealized = (
        sum(positions["pnl"])
        if not positions.empty else 0
    )

    cash = cash_balance()

    with connect() as con:
        closed = con.execute("""
        SELECT COALESCE(SUM(pnl), 0)
        FROM sniper_trades_v3
        WHERE status = 'CLOSED'
        """).fetchone()[0]

        con.execute("""
        INSERT INTO sniper_snapshots_v3
        (timestamp, portfolio, cash, pnl)
        VALUES (?, ?, ?, ?)
        """, (
            now_utc().isoformat(),
            cash + unrealized,
            cash,
            number(closed) + unrealized
        ))
        con.commit()

    st.session_state.last_snapshot = time.time()


# ---------------- SIDEBAR SETTINGS ----------------

st.sidebar.markdown("## ⚡ NIGHT RUN")
st.sidebar.caption("V5 / SOLANA PAPER TERMINAL")
st.sidebar.divider()

amount = st.sidebar.selectbox(
    "Paper trade amount",
    [10, 25, 50, 100],
    index=1
)

stop_loss = st.sidebar.slider(
    "Stop loss %",
    5, 50, 10
)

take_profit = st.sidebar.slider(
    "Take profit %",
    10, 200, 30
)

max_positions = st.sidebar.slider(
    "Max open positions",
    1, 10, 3
)

min_score = st.sidebar.slider(
    "Minimum Smart Entry score",
    30, 95, 70
)

daily_limit = st.sidebar.number_input(
    "Daily loss limit ($)",
    min_value=5,
    max_value=500,
    value=50,
    step=5
)

cooldown_hours = st.sidebar.slider(
    "Token cooldown (hours)",
    1, 24, 3
)

st.sidebar.divider()
st.sidebar.markdown("### AUTO SNIPER")

st.session_state.auto_enabled = st.sidebar.toggle(
    "Enable Auto Paper Trading",
    value=st.session_state.auto_enabled
)

st.sidebar.caption(
    "Virtual trades only. No wallet connection."
)

st.sidebar.divider()
st.sidebar.markdown("### SYSTEM SETTINGS")
st.sidebar.write("Market scan: 60 sec")
st.sidebar.write("Dashboard refresh: 5 sec")
st.sidebar.write("Position monitor: 30 sec")
st.sidebar.write("AI API: Not connected")
st.sidebar.write("Database: Local SQLite")


# ---------------- AUTO REFRESH ----------------

try:
    from streamlit_autorefresh import st_autorefresh
    st_autorefresh(
        interval=REFRESH,
        key="night_run_refresh"
    )
except Exception:
    pass


# ---------------- HEADER ----------------

st.markdown("""
<div class="hero">
  <div class="hero-sub">SOLANA INTELLIGENCE / PAPER TRADING SYSTEM</div>
  <div class="hero-title">
    NIGHT RUN <span style="color:#42f5a1">PRO</span>
    <span style="color:#a78bfa">V5</span>
  </div>
  <div class="hero-sub">
    MARKET RADAR · SMART ENTRY · AUTOMATED PAPER EXECUTION
  </div>
</div>
""", unsafe_allow_html=True)


# ---------------- SCAN ----------------

left, right = st.columns([1, 3])

with left:
    scan_clicked = st.button(
        "⚡ SCAN SOLANA",
        use_container_width=True
    )

if (
    scan_clicked
    or time.time() - st.session_state.last_scan >= SCAN_INTERVAL
):
    with st.spinner("Scanning Solana markets..."):
        st.session_state.tokens = scan()
        st.session_state.last_scan = time.time()
        st.session_state.scan_number += 1

        st.session_state.scan_message = (
            f"Scan complete: {len(st.session_state.tokens)} tokens"
        )

with right:
    st.markdown(
        f"<p class='terminal'>● SYSTEM ONLINE</p>"
        f"<p class='muted'>{st.session_state.scan_message}</p>",
        unsafe_allow_html=True
    )

if st.session_state.last_error:
    st.caption(
        "Last scanner connection issue: "
        + st.session_state.last_error[:180]
    )


# ---------------- TRADE MONITOR ----------------

if (
    time.time() - st.session_state.last_trade_check
    >= TRADE_CHECK
):
    monitor(stop_loss, take_profit)
    st.session_state.last_trade_check = time.time()


# ---------------- AUTO EXECUTION ----------------

if st.session_state.auto_enabled:
    if daily_limit_hit(daily_limit):
        st.session_state.auto_enabled = False
        st.session_state.last_auto_message = (
            "Daily loss limit reached. Auto stopped."
        )

    elif (
        st.session_state.scan_number
        > st.session_state.get("last_auto_scan", 0)
    ):
        st.session_state.last_auto_message = run_auto(
            amount,
            max_positions,
            min_score,
            daily_limit,
            cooldown_hours
        )

        st.session_state.last_auto_scan = (
            st.session_state.scan_number
        )

st.sidebar.caption(
    st.session_state.last_auto_message
)

snapshot()


# ---------------- PORTFOLIO METRICS ----------------

positions = open_positions()
cash = cash_balance()

unrealized = (
    sum(positions["pnl"])
    if not positions.empty else 0
)

realized = realized_total()
total_pnl = realized + unrealized
portfolio = cash + unrealized
today_pnl = realized_today()

closed_count = 0
with connect() as con:
    closed_count = con.execute("""
    SELECT COUNT(*) FROM sniper_trades_v3
    WHERE status = 'CLOSED'
    """).fetchone()[0]

st.markdown("### COMMAND CENTER")

a, b, c, d, e = st.columns(5)

a.metric("PORTFOLIO", money(portfolio))
b.metric("AVAILABLE CASH", money(cash))
c.metric("TOTAL P/L", money(total_pnl))
d.metric("TODAY P/L", money(today_pnl))
e.metric("OPEN POSITIONS", len(positions))

st.divider()


# ---------------- AGENT STATUS ----------------

st.markdown("### NIGHT RUN AGENTS")
st.caption(
    "These are rule-based dashboard modules, not independent AI agents."
)

tokens = st.session_state.tokens
buy_count = sum(
    1 for token in tokens
    if token["signal"] == "BUY"
)
watch_count = sum(
    1 for token in tokens
    if token["signal"] == "WATCH"
)
avoid_count = sum(
    1 for token in tokens
    if token["signal"] == "AVOID"
)

agents = [
    (
        "01 / SCOUT",
        "Discovers new Solana token profiles",
        "ACTIVE" if tokens else "WAITING"
    ),
    (
        "02 / RADAR",
        "Checks liquidity and 24h volume",
        "ACTIVE" if tokens else "WAITING"
    ),
    (
        "03 / FILTER",
        "Checks warning signals and activity",
        "ACTIVE" if tokens else "WAITING"
    ),
    (
        "04 / ANALYST",
        "Calculates rule-based market scores",
        f"{len(tokens)} ANALYZED"
    ),
    (
        "05 / SNIPER",
        "Finds candidates passing entry rules",
        f"{buy_count} CANDIDATES"
    ),
    (
        "06 / GUARD",
        "Monitors paper positions and risk limits",
        f"{len(positions)} POSITIONS"
    )
]

agent_cols = st.columns(3)

for index, agent in enumerate(agents):
    name, desc, status = agent

    with agent_cols[index % 3]:
        st.markdown(
            f"""
            <div class="agent">
              <div class="agent-name">{name}</div>
              <div class="agent-desc">{desc}</div>
              <div class="agent-status">● {status}</div>
            </div>
            """,
            unsafe_allow_html=True
        )

st.divider()


# ---------------- MAIN TABS ----------------

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "◈ MARKET RADAR",
    "▣ OPEN POSITIONS",
    "⚡ AUTO SNIPER",
    "⌁ ANALYTICS",
    "▤ TRADE HISTORY"
])


# ---------------- MARKET RADAR ----------------

with tab1:
    st.subheader("Solana Market Radar")

    x1, x2, x3, x4 = st.columns(4)

    x1.metric("TOKENS SCANNED", len(tokens))
    x2.metric("BUY SIGNALS", buy_count)
    x3.metric("WATCH", watch_count)
    x4.metric("AVOID", avoid_count)

    if tokens:
        data = []

        for token in tokens:
            data.append({
                "TOKEN": token["symbol"],
                "PRICE": token["price"],
                "LIQUIDITY": token["liquidity"],
                "VOLUME 24H": token["volume"],
                "CHANGE %": token["change"],
                "SCORE": token["score"],
                "SIGNAL": token["signal"],
                "SPIKE": "YES" if token["spike"] else "NO",
                "RISK FLAGS": ", ".join(token["flags"]) or "None"
            })

        st.dataframe(
            pd.DataFrame(data),
            use_container_width=True,
            hide_index=True,
            column_config={
                "PRICE": st.column_config.NumberColumn(
                    format="$%.10f"
                ),
                "LIQUIDITY": st.column_config.NumberColumn(
                    format="$%.0f"
                ),
                "VOLUME 24H": st.column_config.NumberColumn(
                    format="$%.0f"
                ),
                "CHANGE %": st.column_config.NumberColumn(
                    format="%.2f%%"
                ),
                "SCORE": st.column_config.ProgressColumn(
                    min_value=0,
                    max_value=100
                )
            }
        )

        labels = [
            f"{token['symbol']} | {token['address'][:8]}"
            for token in tokens
        ]

        selected_label = st.selectbox(
            "Select token for details",
            labels
        )

        selected = tokens[labels.index(selected_label)]

        st.markdown("### Selected token")

        p1, p2, p3, p4 = st.columns(4)

        p1.metric("SCORE", f"{selected['score']}/100")
        p2.metric("SIGNAL", selected["signal"])
        p3.metric("LIQUIDITY", money(selected["liquidity"]))
        p4.metric("24H VOLUME", money(selected["volume"]))

        st.write(
            "**Risk warnings:**",
            ", ".join(selected["flags"]) or "No detected warnings"
        )

        st.code(selected["address"])

        history = get_price_history(selected["address"])

        if not history.empty:
            st.markdown("### Price movement")
            st.line_chart(
                history.set_index("timestamp")["price"]
            )

        if selected["url"]:
            st.link_button(
                "OPEN DEXSCREENER",
                selected["url"]
            )

        if st.button(
            "OPEN PAPER TRADE",
            key="night_run_manual_open"
        ):
            if selected["signal"] != "BUY":
                st.warning(
                    "This token does not currently have a BUY signal."
                )

            elif open_trade(
                selected,
                amount,
                False,
                cooldown_hours
            ):
                st.success("Paper trade opened.")

            else:
                st.error(
                    "Could not open trade. Check cash, cooldown "
                    "or existing position."
                )

    else:
        st.info("No tokens found. Press SCAN SOLANA.")


# ---------------- OPEN POSITIONS ----------------

with tab2:
    st.subheader("Active Paper Positions")

    positions = open_positions()

    if positions.empty:
        st.info("No open paper positions.")

    else:
        for _, trade in positions.iterrows():
            pnl = number(trade["pnl"])
            pct = number(trade["pnl_pct"])

            with st.container(border=True):
                l, m, r = st.columns([3, 2, 1])

                l.markdown(
                    f"### {trade['symbol']}"
                )

                l.caption(
                    f"Trade #{trade['id']} · "
                    f"{'AUTO' if trade['auto_trade'] else 'MANUAL'}"
                )

                l.write(
                    f"Entry: ${number(trade['entry_price']):.10f}"
                )

                l.write(
                    f"Current: ${number(trade['current_price']):.10f}"
                )

                m.metric(
                    "POSITION P/L",
                    money(pnl),
                    f"{pct:.2f}%"
                )

                r.write("")

                if r.button(
                    "CLOSE",
                    key=f"night_close_{trade['id']}"
                ):
                    close_trade(
                        int(trade["id"]),
                        number(trade["current_price"]),
                        "MANUAL"
                    )
                    st.rerun()

        st.markdown("### Position overview")

        st.dataframe(
            positions[[
                "id",
                "symbol",
                "entry_price",
                "current_price",
                "amount",
                "pnl",
                "pnl_pct",
                "entry_time",
                "auto_trade"
            ]],
            use_container_width=True,
            hide_index=True
        )


# ---------------- AUTO SNIPER ----------------

with tab3:
    st.subheader("Auto Sniper Control")

    if st.session_state.auto_enabled:
        st.success("AUTO PAPER TRADING ENABLED")
    else:
        st.warning("AUTO PAPER TRADING DISABLED")

    st.markdown("### Execution status")

    st.code(
        st.session_state.last_auto_message,
        language="text"
    )

    st.markdown("### Smart Entry configuration")

    q1, q2, q3 = st.columns(3)

    q1.metric("MINIMUM SCORE", f"{min_score}/100")
    q2.metric("MAX POSITIONS", max_positions)
    q3.metric("TRADE SIZE", money(amount))

    st.markdown("### Entry rules")

    st.write("- Minimum liquidity: $5,000")
    st.write("- Minimum 24h volume: $5,000")
    st.write("- Minimum activity: 10 transactions")
    st.write("- Avoids extreme price moves")
    st.write(f"- Stop loss: {stop_loss}%")
    st.write(f"- Take profit: {take_profit}%")
    st.write(f"- Token cooldown: {cooldown_hours} hours")
    st.write(f"- Daily realized loss limit: {money(daily_limit)}")

    if daily_limit_hit(daily_limit):
        st.error(
            "Daily realized loss limit reached. "
            "Automatic trading is stopped."
        )

    st.markdown("### BUY candidates")

    candidates = [
        token for token in tokens
        if token["signal"] == "BUY"
    ]

    if candidates:
        for token in candidates[:8]:
            st.markdown(
                f"""
                <div class="panel">
                  <span class="tag">{token['score']}/100</span>
                  <span class="tag-purple">BUY</span>
                  <b style="margin-left:10px">{token['symbol']}</b>
                  <div class="muted">
                    Liquidity {money(token['liquidity'])}
                    · Volume {money(token['volume'])}
                  </div>
                </div>
                """,
                unsafe_allow_html=True
            )

    else:
        st.info("No tokens currently pass Smart Entry filters.")

    st.caption(
        "Risk flags are basic heuristics. They cannot prove "
        "a token is safe or detect every scam or honeypot."
    )


# ---------------- ANALYTICS ----------------

with tab4:
    st.subheader("Performance Analytics")

    with connect() as con:
        history = pd.read_sql_query("""
        SELECT * FROM sniper_trades_v3
        WHERE status = 'CLOSED'
        ORDER BY id ASC
        """, con)

        snapshots = pd.read_sql_query("""
        SELECT * FROM sniper_snapshots_v3
        ORDER BY id ASC
        """, con)

    if not history.empty:
        wins = int((history["pnl"] > 0).sum())
        losses = int((history["pnl"] <= 0).sum())
        winrate = wins / len(history) * 100

        p1, p2, p3 = st.columns(3)

        p1.metric("CLOSED TRADES", len(history))
        p2.metric("WIN RATE", f"{winrate:.1f}%")
        p3.metric("WINS / LOSSES", f"{wins} / {losses}")

        st.markdown("### Profit and loss per trade")

        st.bar_chart(
            history.set_index("id")["pnl"]
        )

        history["cumulative_pnl"] = history["pnl"].cumsum()

        st.markdown("### Cumulative performance")

        st.line_chart(
            history.set_index("id")["cumulative_pnl"]
        )

    else:
        st.info("Close some paper trades to see analytics.")

    if not snapshots.empty:
        st.markdown("### Portfolio history")

        st.line_chart(
            snapshots.set_index("timestamp")["portfolio"]
        )


# ---------------- TRADE HISTORY ----------------

with tab5:
    st.subheader("Trade History")

    with connect() as con:
        all_trades = pd.read_sql_query("""
        SELECT * FROM sniper_trades_v3
        ORDER BY id DESC
        """, con)

    if all_trades.empty:
        st.info("No trade history yet.")

    else:
        st.metric("TOTAL RECORDED TRADES", len(all_trades))

        st.dataframe(
            all_trades[[
                "id",
                "symbol",
                "amount",
                "entry_price",
                "current_price",
                "pnl",
                "pnl_pct",
                "status",
                "close_reason",
                "auto_trade"
            ]],
            use_container_width=True,
            hide_index=True
        )


# ---------------- FOOTER ----------------

st.divider()

st.markdown("""
<div style="text-align:center;padding:15px">
  <div class="terminal">NIGHT RUN PRO V5</div>
  <div class="muted">
    SOLANA PAPER TRADING · LOCAL SQLITE · RULE-BASED SIGNALS
  </div>
  <div class="muted">
    No wallet connection · No real purchases · Not financial advice
  </div>
</div>
""", unsafe_allow_html=True)
