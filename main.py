
import streamlit as st
import requests
import sqlite3
import pandas as pd
import time
from datetime import datetime, timezone, timedelta

# =========================================================
# GROK SOLANA SNIPER V4 PRO
# PAPER TRADING ONLY
# =========================================================

st.set_page_config(
    page_title="GROK SOLANA SNIPER V4 PRO",
    page_icon="⚡",
    layout="wide"
)

DB = "bot.db"
REFRESH = 5000
SCAN_INTERVAL = 60
TRADE_CHECK = 30
STARTING_CASH = 1000.0

PROFILES_URL = "https://api.dexscreener.com/token-profiles/latest/v1"
PAIRS_URL = "https://api.dexscreener.com/token-pairs/v1/solana/{}"

# ---------------- DESIGN ----------------

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&family=Space+Mono:wght@400;700&display=swap');
html, body, [class*="css"] {font-family:Inter,sans-serif;}
.stApp {background:#080b10;color:#e7edf5;}
[data-testid="stSidebar"] {background:#0c1119;border-right:1px solid #1b2935;}
div[data-testid="stMetric"] {
    background:#101923;border:1px solid #1c303b;
    padding:15px;border-radius:12px;
}
div[data-testid="stMetricValue"] {
    color:#35e6a1;font-family:'Space Mono',monospace;
}
.stButton>button {
    background:#14c98a;color:#06120e;border:0;
    border-radius:8px;font-weight:800;min-height:42px;
}
.stButton>button:hover {background:#35e6a1;color:#06120e;}
h1,h2,h3 {color:#eaf2f8;}
hr {border-color:#1b2935;}
.terminal {font-family:'Space Mono',monospace;color:#35e6a1;}
.muted {color:#8293a5;font-size:12px;}
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
    "last_auto_scan": 0,
    "auto_enabled": False,
    "scan_message": "Waiting for scan",
    "last_auto_message": "Auto Sniper waiting",
    "scan_number": 0
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
    except Exception:
        return []

def get_pairs(address):
    try:
        response = requests.get(PAIRS_URL.format(address), timeout=15)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list):
            return [p for p in data if p.get("chainId") == "solana"]
    except Exception:
        pass
    return []

def best_pair(pairs):
    if not pairs:
        return None
    return max(
        pairs,
        key=lambda p: number((p.get("liquidity") or {}).get("usd"))
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
        """, ((now_utc() - timedelta(days=3)).isoformat(),))
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

# ---------------- ANTI SCAM + SMART ENTRY ----------------

def analyze(pair, old_tokens):
    base = pair.get("baseToken") or {}
    address = base.get("address", "")
    symbol = base.get("symbol", "UNKNOWN")
    name = base.get("name", symbol)

    price = number(pair.get("priceUsd"))
    liquidity = number((pair.get("liquidity") or {}).get("usd"))
    volume = number((pair.get("volume") or {}).get("h24"))
    change = number((pair.get("priceChange") or {}).get("h24"))

    txns = pair.get("txns") or {}
    h24 = txns.get("h24") or {}
    buys = number(h24.get("buys"))
    sells = number(h24.get("sells"))
    total = buys + sells

    created = number(pair.get("pairCreatedAt"))
    age_hours = 9999
    if created:
        age_hours = max(0, (time.time() * 1000 - created) / 3600000)

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
        (t for t in old_tokens if t["address"] == address),
        None
    )

    spike = False
    if old and old["volume"] > 0:
        spike = volume >= old["volume"] * 1.5

    if spike:
        flags.append("VOLUME SPIKE")

    # Rule-based market score. Not AI.
    liquidity_score = min(liquidity / 2500, 35)
    volume_score = min((volume / max(liquidity, 1)) / 1.5, 1) * 25

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
        liquidity_score + volume_score +
        momentum_score + activity_score
    )))

    # Warning heuristics only. Not a verified scam detector.
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
        key=lambda x: (x["score"], x["volume"]),
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
        number(row[0]) for row in rows if row[2] == "OPEN"
    )
    realized = sum(
        number(row[1]) for row in rows if row[2] == "CLOSED"
    )
    return STARTING_CASH - invested + realized

def realized_today():
    today = now_utc().date().isoformat()
    with connect() as con:
        rows = con.execute("""
        SELECT pnl, close_time FROM sniper_trades_v3
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
        SELECT entry_price, quantity FROM sniper_trades_v3
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
        SELECT entry_price, quantity FROM sniper_trades_v3
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
            price, pnl, pct, now_utc().isoformat(),
            reason, trade_id
        ))
        con.commit()

# ---------------- DAILY LOSS PROTECTION ----------------

def daily_limit_hit(limit):
    return realized_today() <= -abs(limit)

# ---------------- AUTO SNIPER ----------------

def run_auto(amount, max_positions, min_score,
             daily_limit, cooldown_hours):
    if daily_limit_hit(daily_limit):
        st.session_state.auto_enabled = False
        return "DAILY LOSS LIMIT HIT. AUTO STOPPED."

    positions = open_positions()
    if len(positions) >= max_positions:
        return "Maximum open positions reached"

    candidates = [
        t for t in st.session_state.tokens
        if t["signal"] == "BUY"
        and t["score"] >= min_score
        and t["liquidity"] >= 5000
        and t["volume"] >= 5000
        and t["change"] < 100
        and "NO RECORDED SELLS" not in t["flags"]
        and not on_cooldown(t["address"], cooldown_hours)
    ]

    for token in candidates:
        if len(open_positions()) >= max_positions:
            break
        if open_trade(
            token, amount, True, cooldown_hours
        ):
            return "PAPER TRADE OPENED: " + token["symbol"]

    return "No token passed Smart Entry filters"

# ---------------- MONITOR ----------------

def monitor(stop_loss, take_profit):
    positions = open_positions()

    for _, trade in positions.iterrows():
        pair = best_pair(get_pairs(trade["token_address"]))
        if not pair:
            continue

        price = number(pair.get("priceUsd"))
        if price <= 0:
            continue

        update_position(int(trade["id"]), price)

        entry = number(trade["entry_price"])
        pct = ((price / entry) - 1) * 100 if entry else 0

        if pct <= -stop_loss:
            close_trade(int(trade["id"]), price, "STOP LOSS")
        elif pct >= take_profit:
            close_trade(int(trade["id"]), price, "TAKE PROFIT")

# ---------------- SNAPSHOTS ----------------

def snapshot():
    if time.time() - st.session_state.last_snapshot < 60:
        return

    positions = open_positions()
    unrealized = (
        sum(positions["pnl"]) if not positions.empty else 0
    )
    cash = cash_balance()

    with connect() as con:
        closed = con.execute("""
        SELECT COALESCE(SUM(pnl), 0)
        FROM sniper_trades_v3 WHERE status = 'CLOSED'
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

# ---------------- SIDEBAR ----------------

st.sidebar.markdown("## ⚡ GROK SNIPER V4")
st.sidebar.caption("SOLANA PAPER TRADING TERMINAL")
st.sidebar.divider()

amount = st.sidebar.selectbox(
    "Paper trade amount", [10, 25, 50, 100], index=1
)

stop_loss = st.sidebar.slider(
    "Stop loss %", 5, 50, 10
)
take_profit = st.sidebar.slider(
    "Take profit %", 10, 200, 30
)
max_positions = st.sidebar.slider(
    "Max open positions", 1, 10, 3
)
min_score = st.sidebar.slider(
    "Minimum Smart Entry score", 30, 95, 70
)
daily_limit = st.sidebar.number_input(
    "Daily loss limit ($)", min_value=5,
    max_value=500, value=50, step=5
)
cooldown_hours = st.sidebar.slider(
    "Token cooldown (hours)", 1, 24, 3
)

st.sidebar.divider()
st.sidebar.markdown("### AUTO SNIPER")

st.session_state.auto_enabled = st.sidebar.toggle(
    "Enable Auto Paper Trading",
    value=st.session_state.auto_enabled
)

st.sidebar.caption(
    "Virtual trades only. No wallet connection or real purchases."
)

st.sidebar.divider()
st.sidebar.markdown("### SYSTEM")
st.sidebar.write("Scanner: 60 seconds")
st.sidebar.write("Dashboard: 5 seconds")
st.sidebar.write("Trade monitor: 30 seconds")
st.sidebar.write("AI API: Not required")

# ---------------- HEADER ----------------

st.markdown(
    "<h1>⚡ GROK SOLANA SNIPER "
    "<span style='color:#35e6a1'>V4 PRO</span></h1>",
    unsafe_allow_html=True
)
st.caption(
    "ANTI SCAM WARNINGS • SMART ENTRY • VOLUME RADAR "
    "• AUTO PAPER TRADING"
)

try:
    from streamlit_autorefresh import st_autorefresh
    st_autorefresh(interval=REFRESH, key="v4_refresh")
except Exception:
    pass

# ---------------- SCAN ----------------

left, right = st.columns([1, 3])

with left:
    scan_clicked = st.button(
        "⚡ SCAN SOLANA", use_container_width=True
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

# ---------------- TRADE MONITOR ----------------

if time.time() - st.session_state.last_trade_check >= TRADE_CHECK:
    monitor(stop_loss, take_profit)
    st.session_state.last_trade_check = time.time()

# ---------------- AUTO EXECUTION ----------------

if st.session_state.auto_enabled:
    if daily_limit_hit(daily_limit):
        st.session_state.auto_enabled = False
        st.session_state.last_auto_message = (
            "Daily loss limit reached. Auto stopped."
        )
    elif st.session_state.scan_number > st.session_state.last_auto_scan:
        st.session_state.last_auto_message = run_auto(
            amount, max_positions, min_score,
            daily_limit, cooldown_hours
        )
        st.session_state.last_auto_scan = st.session_state.scan_number

st.sidebar.caption(st.session_state.last_auto_message)

snapshot()

# ---------------- METRICS ----------------

positions = open_positions()
cash = cash_balance()
unrealized = sum(positions["pnl"]) if not positions.empty else 0

with connect() as con:
    realized = con.execute("""
    SELECT COALESCE(SUM(pnl), 0)
    FROM sniper_trades_v3 WHERE status = 'CLOSED'
    """).fetchone()[0]

total_pnl = number(realized) + unrealized
portfolio = cash + unrealized
today_pnl = realized_today()

a, b, c, d, e = st.columns(5)
a.metric("PORTFOLIO", f"${portfolio:,.2f}")
b.metric("CASH", f"${cash:,.2f}")
c.metric("TOTAL P/L", f"${total_pnl:,.2f}")
d.metric("TODAY P/L", f"${today_pnl:,.2f}")
e.metric("OPEN POSITIONS", len(positions))

st.divider()

# ---------------- TABS ----------------

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "MARKET SCANNER",
    "OPEN POSITIONS",
    "AUTO SNIPER",
    "ANALYTICS",
    "TRADE HISTORY"
])

# ---------------- MARKET SCANNER ----------------

with tab1:
    st.subheader("Market Scanner")

    tokens = st.session_state.tokens

    if tokens:
        data = []
        for t in tokens:
            data.append({
                "TOKEN": t["symbol"],
                "PRICE": t["price"],
                "LIQUIDITY": t["liquidity"],
                "VOLUME 24H": t["volume"],
                "CHANGE %": t["change"],
                "SCORE": t["score"],
                "SIGNAL": t["signal"],
                "VOLUME SPIKE": "YES" if t["spike"] else "NO",
                "RISK FLAGS": ", ".join(t["flags"]) or "None"
            })

        st.dataframe(
            pd.DataFrame(data),
            use_container_width=True,
            hide_index=True,
            column_config={
                "PRICE": st.column_config.NumberColumn(format="$%.10f"),
                "LIQUIDITY": st.column_config.NumberColumn(format="$%.0f"),
                "VOLUME 24H": st.column_config.NumberColumn(format="$%.0f"),
                "CHANGE %": st.column_config.NumberColumn(format="%.2f%%"),
                "SCORE": st.column_config.ProgressColumn(
                    min_value=0, max_value=100
                )
            }
        )

        labels = [
            f"{t['symbol']} | {t['address'][:8]}"
            for t in tokens
        ]

        selected_label = st.selectbox(
            "Select token", labels
        )
        selected = tokens[labels.index(selected_label)]

        x1, x2, x3, x4 = st.columns(4)
        x1.metric("SCORE", f"{selected['score']}/100")
        x2.metric("SIGNAL", selected["signal"])
        x3.metric("LIQUIDITY", f"${selected['liquidity']:,.0f}")
        x4.metric("VOLUME", f"${selected['volume']:,.0f}")

        st.write(
            "**Risk warnings:**",
            ", ".join(selected["flags"]) or "No detected warnings"
        )
        st.write("**Address:**", selected["address"])

        history = get_price_history(selected["address"])
        if not history.empty:
            st.markdown("### Token price history")
            st.line_chart(
                history.set_index("timestamp")["price"]
            )

        if selected["url"]:
            st.link_button("OPEN DEXSCREENER", selected["url"])

        if st.button("OPEN PAPER TRADE", key="v4_manual_open"):
            if selected["signal"] != "BUY":
                st.warning(
                    "Smart Entry does not currently rate this token BUY."
                )
            elif open_trade(
                selected, amount, False, cooldown_hours
            ):
                st.success("Paper trade opened!")
            else:
                st.error(
                    "Could not open trade. Check cash, cooldown or existing position."
                )
    else:
        st.info("No tokens found. Press SCAN SOLANA.")

# ---------------- OPEN POSITIONS ----------------

with tab2:
    st.subheader("Open Positions")

    positions = open_positions()

    if positions.empty:
        st.info("No open paper positions.")
    else:
        st.dataframe(
            positions[[
                "id", "symbol", "entry_price",
                "current_price", "amount", "pnl",
                "pnl_pct", "entry_time", "auto_trade"
            ]],
            use_container_width=True,
            hide_index=True
        )

        for _, trade in positions.iterrows():
            with st.container(border=True):
                l, r = st.columns([3, 1])
                l.markdown(
                    f"**{trade['symbol']}** · Trade #{trade['id']}"
                )
                l.write(
                    f"Entry: ${trade['entry_price']:.10f} | "
                    f"Current: ${trade['current_price']:.10f}"
                )
                l.write(
                    f"P/L: ${trade['pnl']:.2f} "
                    f"({trade['pnl_pct']:.2f}%)"
                )

                if r.button(
                    "CLOSE TRADE", key=f"v4_close_{trade['id']}"
                ):
                    close_trade(
                        int(trade["id"]),
                        number(trade["current_price"]),
                        "MANUAL"
                    )
                    st.rerun()

# ---------------- AUTO SNIPER ----------------

with tab3:
    st.subheader("Smart Auto Sniper V4")

    if st.session_state.auto_enabled:
        st.success("AUTO PAPER TRADING ENABLED")
    else:
        st.warning("AUTO PAPER TRADING DISABLED")

    st.write("**Latest Auto Sniper status:**")
    st.code(st.session_state.last_auto_message)

    st.markdown("### Smart Entry rules")
    st.write(f"- Minimum score: {min_score}/100")
    st.write("- Minimum liquidity: $5,000")
    st.write("- Minimum 24h volume: $5,000")
    st.write("- Minimum trading activity: 10 transactions")
    st.write("- Avoids extreme price moves")
    st.write(f"- Token cooldown: {cooldown_hours} hours")
    st.write(f"- Daily loss limit: ${daily_limit:.2f}")

    if daily_limit_hit(daily_limit):
        st.error("Daily loss limit reached. Auto trading is stopped.")

    st.markdown("### BUY candidates")
    candidates = [
        t for t in st.session_state.tokens
        if t["signal"] == "BUY"
    ]

    if candidates:
        for token in candidates[:8]:
            st.write(
                f"**{token['symbol']}** | Score {token['score']} "
                f"| Liquidity ${token['liquidity']:,.0f} "
                f"| Volume ${token['volume']:,.0f}"
            )
    else:
        st.info("No tokens currently pass the Smart Entry filters.")

    st.caption(
        "Anti Scam checks are warning heuristics only. "
        "They cannot prove a token is safe or detect every honeypot."
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

        st.markdown("### Profit and loss")
        st.bar_chart(history.set_index("id")["pnl"])

        st.markdown("### Cumulative P/L")
        history["cumulative_pnl"] = history["pnl"].cumsum()
        st.line_chart(
            history.set_index("id")["cumulative_pnl"]
        )
    else:
        st.info("Close some trades to see performance analytics.")

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
        st.dataframe(
            all_trades[[
                "id", "symbol", "amount", "entry_price",
                "current_price", "pnl", "pnl_pct",
                "status", "close_reason", "auto_trade"
            ]],
            use_container_width=True,
            hide_index=True
        )

# ---------------- FOOTER ----------------

st.divider()
st.markdown(
    "<p class='muted'>GROK SOLANA SNIPER V4 PRO · "
    "PAPER TRADING ONLY · NOT FINANCIAL ADVICE</p>",
    unsafe_allow_html=True
)
