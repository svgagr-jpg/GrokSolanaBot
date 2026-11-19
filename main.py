
import streamlit as st
import streamlit.components.v1 as components
import requests
import sqlite3
import pandas as pd
import time
from datetime import datetime, timezone

# =========================================================
# GROKSOLANABOT V3 PRO
# Paper trading only. No wallet. No real transactions.
# =========================================================

st.set_page_config(
    page_title="GROK SOLANA SNIPER PRO",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

DB = "bot.db"
REFRESH_SECONDS = 5
SCAN_INTERVAL = 60
TRADE_CHECK_INTERVAL = 30

API_PROFILES = "https://api.dexscreener.com/token-profiles/latest/v1"
API_PAIRS = "https://api.dexscreener.com/token-pairs/v1/solana/{}"

# ---------------- DESIGN ----------------

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=Space+Mono:wght@400;700&display=swap');

html, body, [class*="css"] {
    font-family: Inter, sans-serif;
}
.stApp {
    background: #080b10;
    color: #e7edf5;
}
[data-testid="stSidebar"] {
    background: #0c1119;
    border-right: 1px solid #1b2935;
}
h1, h2, h3 {
    color: #eaf2f8;
}
div[data-testid="stMetric"] {
    background: #101923;
    border: 1px solid #1c303b;
    padding: 16px;
    border-radius: 12px;
}
div[data-testid="stMetricValue"] {
    color: #35e6a1;
    font-family: 'Space Mono', monospace;
}
.stButton > button {
    background: #14c98a;
    color: #06120e;
    border: 0;
    border-radius: 8px;
    font-weight: 800;
    min-height: 42px;
}
.stButton > button:hover {
    background: #35e6a1;
    color: #06120e;
}
section[data-testid="stMain"] {
    background: #080b10;
}
hr {
    border-color: #1b2935;
}
.small-muted {
    color: #8293a5;
    font-size: 12px;
}
.terminal {
    font-family: 'Space Mono', monospace;
    color: #35e6a1;
}
</style>
""", unsafe_allow_html=True)

# ---------------- DATABASE ----------------

def db():
    return sqlite3.connect(DB, timeout=20)

def init_db():
    with db() as con:
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
        CREATE TABLE IF NOT EXISTS sniper_volume_history_v3 (
            token_address TEXT,
            timestamp REAL,
            volume REAL
        )
        """)
        con.execute("""
        CREATE TABLE IF NOT EXISTS sniper_cooldowns_v3 (
            token_address TEXT PRIMARY KEY,
            timestamp REAL
        )
        """)
        con.commit()

init_db()

# ---------------- SESSION STATE ----------------

defaults = {
    "tokens": [],
    "last_scan": 0,
    "last_trade_check": 0,
    "last_snapshot": 0,
    "scan_message": "Waiting for scan",
    "auto_enabled": False,
    "starting_cash": 1000.0
}

for key, value in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = value

# ---------------- API HELPERS ----------------

def safe_float(value, default=0):
    try:
        return float(value or 0)
    except:
        return default

def get_profiles():
    try:
        r = requests.get(API_PROFILES, timeout=12)
        r.raise_for_status()
        data = r.json()
        return [
            x for x in data
            if x.get("chainId") == "solana" and x.get("tokenAddress")
        ]
    except Exception:
        return []

def get_pairs(address):
    try:
        r = requests.get(API_PAIRS.format(address), timeout=12)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, list):
            return []
        return [
            p for p in data
            if p.get("chainId") == "solana"
        ]
    except Exception:
        return []

def choose_pair(pairs):
    if not pairs:
        return None
    return max(
        pairs,
        key=lambda p: safe_float(
            (p.get("liquidity") or {}).get("usd")
        )
    )

# ---------------- VOLUME HISTORY ----------------

def record_volume(address, volume):
    now = time.time()
    with db() as con:
        con.execute("""
        INSERT INTO sniper_volume_history_v3
        (token_address, timestamp, volume)
        VALUES (?, ?, ?)
        """, (address, now, volume))

        con.execute("""
        DELETE FROM sniper_volume_history_v3
        WHERE timestamp < ?
        """, (now - 86400,))

        con.commit()

def volume_spike(address, current_volume):
    with db() as con:
        rows = con.execute("""
        SELECT volume FROM sniper_volume_history_v3
        WHERE token_address = ?
        AND timestamp > ?
        ORDER BY timestamp DESC
        LIMIT 10
        """, (address, time.time() - 3600)).fetchall()

    if len(rows) < 2:
        return False

    previous = [safe_float(x[0]) for x in rows]
    baseline = sum(previous) / len(previous)

    return baseline > 0 and current_volume >= baseline * 2

# ---------------- TOKEN ANALYSIS ----------------

def analyze_token(pair):
    base = pair.get("baseToken") or {}
    address = base.get("address", "")
    symbol = base.get("symbol", "UNKNOWN")
    name = base.get("name", symbol)

    price = safe_float(pair.get("priceUsd"))
    liquidity = safe_float((pair.get("liquidity") or {}).get("usd"))
    volume = safe_float((pair.get("volume") or {}).get("h24"))
    change = safe_float((pair.get("priceChange") or {}).get("h24"))

    txns = pair.get("txns") or {}
    h24 = txns.get("h24") or {}
    buys = safe_float(h24.get("buys"))
    sells = safe_float(h24.get("sells"))
    total_txns = buys + sells

    age_hours = 9999
    created = safe_float(pair.get("pairCreatedAt"))
    if created > 0:
        age_hours = max(0, (time.time() * 1000 - created) / 3600000)

    flags = []

    if liquidity < 5000:
        flags.append("LOW LIQUIDITY")

    if volume < 1000:
        flags.append("LOW VOLUME")

    if total_txns < 10:
        flags.append("LOW ACTIVITY")

    if change < -50:
        flags.append("LARGE PRICE DROP")

    if change > 150:
        flags.append("EXTREME PRICE MOVE")

    if sells == 0 and buys > 10:
        flags.append("NO RECORDED SELLS")

    # Rule-based score, not an AI prediction
    liquidity_score = min(liquidity / 2500, 40)

    volume_ratio = volume / max(liquidity, 1)
    volume_score = min(volume_ratio / 1.5, 1) * 30

    if 0 < change <= 50:
        momentum_score = 20
    elif 50 < change <= 100:
        momentum_score = 12
    elif -10 <= change <= 0:
        momentum_score = 10
    elif change > 100:
        momentum_score = 5
    else:
        momentum_score = 0

    activity_score = min(total_txns / 10, 10)

    score = int(min(100, max(
        0,
        liquidity_score + volume_score +
        momentum_score + activity_score
    )))

    spike = volume_spike(address, volume)

    if spike:
        flags.append("VOLUME SPIKE")

    severe = (
        liquidity < 1000 or
        price <= 0 or
        change < -70 or
        "NO RECORDED SELLS" in flags
    )

    if severe or score < 30:
        signal = "AVOID"
    elif score >= 65 and liquidity >= 5000 and total_txns >= 10:
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

def scan_tokens():
    profiles = get_profiles()
    results = []
    seen = set()

    for profile in profiles[:35]:
        address = profile.get("tokenAddress")

        if not address or address in seen:
            continue

        seen.add(address)

        pairs = get_pairs(address)
        pair = choose_pair(pairs)

        if not pair:
            continue

        token = analyze_token(pair)

        if token["price"] <= 0:
            continue

        record_volume(address, token["volume"])
        results.append(token)

    results.sort(
        key=lambda x: (x["score"], x["volume"]),
        reverse=True
    )

    return results[:20]

# ---------------- PAPER PORTFOLIO ----------------

def get_cash():
    with db() as con:
        rows = con.execute("""
        SELECT amount, pnl, status
        FROM sniper_trades_v3
        """).fetchall()

    used = sum(
        safe_float(row[0])
        for row in rows if row[2] == "OPEN"
    )

    realized = sum(
        safe_float(row[1])
        for row in rows if row[2] == "CLOSED"
    )

    return st.session_state.starting_cash - used + realized

def get_open_trades():
    with db() as con:
        return pd.read_sql_query("""
        SELECT * FROM sniper_trades_v3
        WHERE status = 'OPEN'
        ORDER BY id DESC
        """, con)

def open_trade(token, amount, auto=False):
    if token["price"] <= 0 or amount <= 0:
        return False

    if amount > get_cash():
        return False

    with db() as con:
        existing = con.execute("""
        SELECT id FROM sniper_trades_v3
        WHERE token_address = ? AND status = 'OPEN'
        """, (token["address"],)).fetchone()

        if existing:
            return False

        con.execute("""
        INSERT INTO sniper_trades_v3 (
            token_address, symbol, name, entry_price,
            current_price, amount, quantity, entry_time,
            auto_trade
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            token["address"],
            token["symbol"],
            token["name"],
            token["price"],
            token["price"],
            amount,
            amount / token["price"],
            datetime.now(timezone.utc).isoformat(),
            1 if auto else 0
        ))
        con.commit()

    return True

def close_trade(trade_id, price, reason="MANUAL"):
    with db() as con:
        row = con.execute("""
        SELECT entry_price, amount, quantity
        FROM sniper_trades_v3
        WHERE id = ? AND status = 'OPEN'
        """, (trade_id,)).fetchone()

        if not row:
            return

        entry, amount, quantity = row
        pnl = (price - entry) * quantity
        pnl_pct = ((price / entry) - 1) * 100 if entry else 0

        con.execute("""
        UPDATE sniper_trades_v3
        SET current_price = ?, pnl = ?, pnl_pct = ?,
            status = 'CLOSED', close_time = ?,
            close_reason = ?
        WHERE id = ?
        """, (
            price, pnl, pnl_pct,
            datetime.now(timezone.utc).isoformat(),
            reason, trade_id
        ))
        con.commit()

def update_trade_price(trade_id, price):
    with db() as con:
        row = con.execute("""
        SELECT entry_price, quantity
        FROM sniper_trades_v3
        WHERE id = ? AND status = 'OPEN'
        """, (trade_id,)).fetchone()

        if not row or price <= 0:
            return

        entry, quantity = row
        pnl = (price - entry) * quantity
        pnl_pct = ((price / entry) - 1) * 100 if entry else 0

        con.execute("""
        UPDATE sniper_trades_v3
        SET current_price = ?, pnl = ?, pnl_pct = ?
        WHERE id = ?
        """, (price, pnl, pnl_pct, trade_id))
        con.commit()

# ---------------- AUTO SNIPER ----------------

def auto_sniper(tokens, amount, max_positions, min_score):
    if not tokens:
        return "No tokens available"

    opened = get_open_trades()

    if len(opened) >= max_positions:
        return "Maximum open positions reached"

    candidates = [
        t for t in tokens
        if t["signal"] == "BUY"
        and t["score"] >= min_score
        and t["liquidity"] >= 5000
        and t["price"] > 0
        and "LOW ACTIVITY" not in t["flags"]
        and "LARGE PRICE DROP" not in t["flags"]
        and "EXTREME PRICE MOVE" not in t["flags"]
    ]

    for token in candidates:
        if len(opened) >= max_positions:
            break

        if open_trade(token, amount, auto=True):
            return "AUTO PAPER TRADE OPENED: " + token["symbol"]

    return "No token passed the Auto Sniper filters"

# ---------------- MONITOR POSITIONS ----------------

def monitor_trades(stop_loss, take_profit):
    trades = get_open_trades()

    for _, trade in trades.iterrows():
        pairs = get_pairs(trade["token_address"])
        pair = choose_pair(pairs)

        if not pair:
            continue

        price = safe_float(pair.get("priceUsd"))
        if price <= 0:
            continue

        update_trade_price(int(trade["id"]), price)

        entry = safe_float(trade["entry_price"])
        pnl_pct = ((price / entry) - 1) * 100 if entry else 0

        if pnl_pct <= -stop_loss:
            close_trade(int(trade["id"]), price, "STOP LOSS")

        elif pnl_pct >= take_profit:
            close_trade(int(trade["id"]), price, "TAKE PROFIT")

# ---------------- PORTFOLIO SNAPSHOTS ----------------

def save_snapshot():
    if time.time() - st.session_state.last_snapshot < 60:
        return

    trades = get_open_trades()
    cash = get_cash()
    unrealized = sum(
        safe_float(x) for x in trades["pnl"]
    ) if not trades.empty else 0

    with db() as con:
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
            datetime.now(timezone.utc).isoformat(),
            cash + unrealized,
            cash,
            closed + unrealized
        ))
        con.commit()

    st.session_state.last_snapshot = time.time()

# ---------------- SIDEBAR ----------------

st.sidebar.markdown("## ⚡ GROK SNIPER PRO")
st.sidebar.caption("SOLANA PAPER TRADING TERMINAL")
st.sidebar.divider()

st.sidebar.markdown("### RISK MANAGEMENT")

trade_amount = st.sidebar.selectbox(
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
    "Maximum open positions",
    1, 10, 3
)

min_score = st.sidebar.slider(
    "Minimum Auto Sniper score",
    30, 95, 65
)

st.sidebar.divider()
st.sidebar.markdown("### AUTO SNIPER")

auto_enabled = st.sidebar.toggle(
    "Enable Auto Paper Trading",
    value=st.session_state.auto_enabled
)
st.session_state.auto_enabled = auto_enabled

st.sidebar.caption(
    "Only virtual trades. Auto mode does not buy real tokens."
)

st.sidebar.divider()
st.sidebar.markdown("### SYSTEM")
st.sidebar.write("Scanner interval: 60 seconds")
st.sidebar.write("UI refresh: 5 seconds")
st.sidebar.write("Trade checks: 30 seconds")
st.sidebar.write("AI provider: Not required")
st.sidebar.write("Network: Solana")

# ---------------- HEADER ----------------

st.markdown(
    "<h1>⚡ GROK SOLANA SNIPER <span style='color:#35e6a1'>PRO</span></h1>",
    unsafe_allow_html=True
)
st.caption("SMART TOKEN SCANNER  •  RISK FILTERS  •  AUTO PAPER EXECUTION")

# ---------------- REFRESH ----------------

try:
    from streamlit_autorefresh import st_autorefresh
    st_autorefresh(
        interval=REFRESH_SECONDS * 1000,
        key="sniper_refresh"
    )
except Exception:
    pass

# ---------------- SCAN CONTROL ----------------

col_scan, col_status = st.columns([1, 3])

with col_scan:
    scan_clicked = st.button("⚡ SCAN SOLANA", use_container_width=True)

should_scan = (
    scan_clicked or
    time.time() - st.session_state.last_scan >= SCAN_INTERVAL
)

if should_scan:
    with st.spinner("Scanning Solana tokens..."):
        st.session_state.tokens = scan_tokens()
        st.session_state.last_scan = time.time()
        st.session_state.scan_message = (
            f"Scan complete: {len(st.session_state.tokens)} tokens found"
        )

with col_status:
    st.markdown(
        f"<p class='terminal'>● SYSTEM ONLINE</p>"
        f"<p class='small-muted'>{st.session_state.scan_message}</p>",
        unsafe_allow_html=True
    )

# ---------------- TRADE MONITOR ----------------

if time.time() - st.session_state.last_trade_check >= TRADE_CHECK_INTERVAL:
    monitor_trades(stop_loss, take_profit)
    st.session_state.last_trade_check = time.time()

# ---------------- AUTO EXECUTION ----------------

if st.session_state.auto_enabled:
    auto_message = auto_sniper(
        st.session_state.tokens,
        trade_amount,
        max_positions,
        min_score
    )
    st.sidebar.caption(auto_message)

save_snapshot()

# ---------------- METRICS ----------------

open_trades = get_open_trades()
cash = get_cash()

unrealized = (
    sum(open_trades["pnl"])
    if not open_trades.empty else 0
)

with db() as con:
    realized = con.execute("""
    SELECT COALESCE(SUM(pnl), 0)
    FROM sniper_trades_v3
    WHERE status = 'CLOSED'
    """).fetchone()[0]

total_pnl = realized + unrealized
portfolio = cash + unrealized

m1, m2, m3, m4 = st.columns(4)
m1.metric("PORTFOLIO VALUE", f"${portfolio:,.2f}")
m2.metric("AVAILABLE CASH", f"${cash:,.2f}")
m3.metric("TOTAL P/L", f"${total_pnl:,.2f}")
m4.metric("OPEN POSITIONS", len(open_trades))

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
    st.subheader("Live Token Scanner")

    tokens = st.session_state.tokens

    if tokens:
        rows = []

        for t in tokens:
            rows.append({
                "TOKEN": t["symbol"],
                "PRICE": t["price"],
                "LIQUIDITY": t["liquidity"],
                "24H VOLUME": t["volume"],
                "24H CHANGE %": t["change"],
                "SCORE": t["score"],
                "SIGNAL": t["signal"],
                "VOLUME SPIKE": "YES" if t["spike"] else "NO",
                "RISK FLAGS": ", ".join(t["flags"]) or "None"
            })

        frame = pd.DataFrame(rows)

        st.dataframe(
            frame,
            use_container_width=True,
            hide_index=True,
            column_config={
                "PRICE": st.column_config.NumberColumn(format="$%.10f"),
                "LIQUIDITY": st.column_config.NumberColumn(format="$%.0f"),
                "24H VOLUME": st.column_config.NumberColumn(format="$%.0f"),
                "24H CHANGE %": st.column_config.NumberColumn(format="%.2f%%"),
                "SCORE": st.column_config.ProgressColumn(
                    min_value=0, max_value=100
                )
            }
        )

        st.divider()
        st.subheader("Token Details")

        selected_symbol = st.selectbox(
            "Select token",
            [t["symbol"] + " | " + t["address"][:8] for t in tokens]
        )

        selected = tokens[
            [t["symbol"] + " | " + t["address"][:8] for t in tokens]
            .index(selected_symbol)
        ]

        a, b, c, d = st.columns(4)
        a.metric("MARKET SCORE", f"{selected['score']}/100")
        b.metric("SIGNAL", selected["signal"])
        c.metric("LIQUIDITY", f"${selected['liquidity']:,.0f}")
        d.metric("24H VOLUME", f"${selected['volume']:,.0f}")

        st.write("**Risk flags:**", ", ".join(selected["flags"]) or "No detected flags")
        st.write("**Token address:**", selected["address"])

        if selected["url"]:
            st.link_button("OPEN DEXSCREENER", selected["url"])

        if st.button("OPEN PAPER TRADE", key="manual_open"):
            if selected["signal"] != "BUY":
                st.warning("This token is not currently rated BUY. You can still review it.")
            else:
                if open_trade(selected, trade_amount):
                    st.success("Paper trade opened!")
                else:
                    st.error("Trade could not be opened. Check available cash or open positions.")

    else:
        st.info("No tokens found yet. Press SCAN SOLANA.")

# ---------------- OPEN POSITIONS ----------------

with tab2:
    st.subheader("Open Paper Positions")

    open_trades = get_open_trades()

    if open_trades.empty:
        st.info("No open positions.")
    else:
        st.dataframe(
            open_trades[[
                "id", "symbol", "entry_price",
                "current_price", "amount", "pnl", "pnl_pct",
                "entry_time", "auto_trade"
            ]],
            use_container_width=True,
            hide_index=True
        )

        for _, trade in open_trades.iterrows():
            with st.container(border=True):
                left, right = st.columns([3, 1])
                left.markdown(f"**{trade['symbol']}**  ·  Trade #{trade['id']}")
                left.write(
                    f"Entry ${trade['entry_price']:.10f}  |  "
                    f"Current ${trade['current_price']:.10f}"
                )
                left.write(
                    f"P/L: ${trade['pnl']:.2f} ({trade['pnl_pct']:.2f}%)"
                )

                if right.button("CLOSE TRADE", key=f"close_{trade['id']}"):
                    close_trade(
                        int(trade["id"]),
                        safe_float(trade["current_price"]),
                        "MANUAL"
                    )
                    st.rerun()

# ---------------- AUTO SNIPER ----------------

with tab3:
    st.subheader("Smart Auto Sniper")

    if st.session_state.auto_enabled:
        st.success("AUTO PAPER TRADING ENABLED")
    else:
        st.warning("Auto Paper Trading is disabled")

    st.write(
        "The system scans tokens and checks their local market score, "
        "liquidity, trading activity and risk flags."
    )

    st.markdown("### Execution rules")
    st.write(f"- Minimum score: {min_score}/100")
    st.write(f"- Paper amount: ${trade_amount}")
    st.write(f"- Maximum open positions: {max_positions}")
    st.write(f"- Stop loss: -{stop_loss}%")
    st.write(f"- Take profit: +{take_profit}%")

    st.markdown("### Current candidates")

    candidates = [
        t for t in st.session_state.tokens
        if t["signal"] == "BUY"
    ]

    if candidates:
        for t in candidates[:5]:
            st.write(
                f"**{t['symbol']}**  |  Score {t['score']}  |  "
                f"Liquidity ${t['liquidity']:,.0f}"
            )
    else:
        st.info("No tokens currently pass the BUY filters.")

    st.caption(
        "Important: risk flags are basic warning signals, not a verified "
        "honeypot or rug-pull detector. A BUY signal is not a prediction."
    )

# ---------------- ANALYTICS ----------------

with tab4:
    st.subheader("Performance Analytics")

    with db() as con:
        history = pd.read_sql_query("""
        SELECT * FROM sniper_trades_v3
        WHERE status = 'CLOSED'
        ORDER BY id ASC
        """, con)

        snapshots = pd.read_sql_query("""
        SELECT * FROM sniper_snapshots_v3
        ORDER BY id ASC
        """, con)

    x1, x2, x3 = st.columns(3)

    if not history.empty:
        wins = int((history["pnl"] > 0).sum())
        losses = int((history["pnl"] <= 0).sum())
        winrate = wins / len(history) * 100

        x1.metric("CLOSED TRADES", len(history))
        x2.metric("WIN RATE", f"{winrate:.1f}%")
        x3.metric("WINS / LOSSES", f"{wins} / {losses}")

        st.markdown("### Closed trade P/L")
        st.bar_chart(history.set_index("id")["pnl"])
    else:
        x1.metric("CLOSED TRADES", 0)
        x2.metric("WIN RATE", "0%")
        x3.metric("WINS / LOSSES", "0 / 0")
        st.info("Analytics will appear after trades are closed.")

    if not snapshots.empty:
        st.markdown("### Portfolio history")
        st.line_chart(snapshots.set_index("timestamp")["portfolio"])

# ---------------- TRADE HISTORY ----------------

with tab5:
    st.subheader("Trade History")

    with db() as con:
        all_trades = pd.read_sql_query("""
        SELECT * FROM sniper_trades_v3
        ORDER BY id DESC
        """, con)

    if all_trades.empty:
        st.info("No trades yet.")
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
    "<p class='small-muted'>GROK SOLANA SNIPER PRO V3 · "
    "PAPER TRADING ONLY · NOT FINANCIAL ADVICE</p>",
    unsafe_allow_html=True
)
