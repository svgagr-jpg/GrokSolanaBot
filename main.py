
import streamlit as st
import requests
import sqlite3
import pandas as pd
import json
import time
from datetime import datetime
from streamlit_autorefresh import st_autorefresh

# =========================
# CONFIG
# =========================

st.set_page_config(
    page_title="SOLANA TERMINAL",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)

DB = "bot.db"
MODEL = "gemini-3.8-flash"
STARTING_CASH = 1000.0
REFRESH_MS = 5000

# =========================
# PREMIUM DESIGN
# =========================

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

html, body, [class*="css"] {
    font-family: 'Inter', sans-serif;
}

.stApp {
    background:
        radial-gradient(ellipse at 20% 0%, #10251d 0%, transparent 40%),
        #080d12;
    color: #e8f2ee;
}

header[data-testid="stHeader"] {
    background: rgba(8,13,18,0.9);
}

section[data-testid="stSidebar"] {
    background: #0c1518;
    border-right: 1px solid #1b3930;
}

.block-container {
    padding-top: 2rem;
    max-width: 1500px;
}

h1, h2, h3 {
    color: #eafff4 !important;
    font-weight: 800 !important;
}

p, label, span {
    color: #c5d5ce;
}

div[data-testid="stMetric"] {
    background: linear-gradient(145deg,#12231e,#0d1717);
    border: 1px solid #244b3b;
    padding: 19px;
    border-radius: 15px;
}

div[data-testid="stMetricValue"] {
    color: #55f2a0 !important;
    font-weight: 800;
}

div[data-testid="stMetricLabel"] {
    color: #9bb7a9 !important;
}

.stButton > button {
    background: linear-gradient(90deg,#00bd76,#37ed9b);
    color: #06140e !important;
    font-weight: 800;
    border: none;
    border-radius: 10px;
    min-height: 42px;
    transition: 0.2s;
}

.stButton > button:hover {
    background: #72ffb6;
    border: none;
    color: #06140e !important;
    transform: scale(1.01);
}

div[data-testid="stDataFrame"] {
    border: 1px solid #234536;
    border-radius: 12px;
    overflow: hidden;
}

div[data-testid="stTabs"] button {
    color: #9fb8aa;
    font-weight: 700;
}

div[data-testid="stTabs"] button[aria-selected="true"] {
    color: #53f5a0;
}

hr {
    border-color: #254638;
}

.terminal-header {
    background: linear-gradient(110deg,#10271e,#10201f,#101820);
    border: 1px solid #286044;
    padding: 25px;
    border-radius: 18px;
    margin-bottom: 20px;
}

.terminal-label {
    color: #5df5a5;
    font-size: 12px;
    letter-spacing: 3px;
    font-weight: 800;
}

.terminal-title {
    color: #effff5;
    font-size: 32px;
    font-weight: 800;
    margin-top: 8px;
}

.terminal-subtitle {
    color: #91ad9e;
    font-size: 13px;
}

.status-dot {
    color: #52f59e;
    font-weight: 800;
}
</style>
""", unsafe_allow_html=True)

# =========================
# DATABASE
# =========================

con = sqlite3.connect(DB, check_same_thread=False)

con.execute("""
CREATE TABLE IF NOT EXISTS paper_trades_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token TEXT,
    address TEXT,
    entry REAL,
    amount REAL,
    status TEXT,
    exit REAL,
    pnl REAL,
    opened TEXT,
    closed TEXT
)
""")

con.execute("""
CREATE TABLE IF NOT EXISTS portfolio_snapshots_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    time TEXT,
    value REAL
)
""")

con.commit()

# =========================
# HELPERS
# =========================

def get_positions():
    return pd.read_sql_query(
        "SELECT * FROM paper_trades_v2 ORDER BY id DESC",
        con
    )

def get_cash():
    df = get_positions()
    opened = df[df["status"] == "OPEN"]
    return STARTING_CASH - opened["amount"].sum()

def get_price(address):
    url = f"https://api.dexscreener.com/token-pairs/v1/solana/{address}"
    response = requests.get(url, timeout=15)
    response.raise_for_status()

    pairs = response.json()
    if not pairs:
        return None

    pair = max(
        pairs,
        key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0)
    )

    return float(pair.get("priceUsd") or 0)

# =========================
# GEMINI AI
# =========================

def gemini(prompt):
    try:
        key = st.secrets["GEMINI_API_KEY"]
    except Exception:
        raise Exception("GEMINI_API_KEY ni nastavljen v Secrets.")

    url = (
        "https://generativelanguage.googleapis.com/"
        f"v1beta/models/{MODEL}:generateContent"
    )

    response = requests.post(
        url,
        headers={
            "Content-Type": "application/json",
            "X-goog-api-key": key
        },
        json={
            "contents": [
                {"parts": [{"text": prompt}]}
            ],
            "generationConfig": {
                "temperature": 0.2
            }
        },
        timeout=45
    )

    if response.status_code != 200:
        raise Exception(
            f"Gemini {response.status_code}: {response.text[:350]}"
        )

    return response.json()["candidates"][0]["content"]["parts"][0]["text"]

def test_gemini():
    return gemini("Reply with exactly OK")

def analyze_tokens(tokens):
    compact = [
        {
            "symbol": t["symbol"],
            "liquidity": t["liquidity"],
            "volume_24h": t["volume"],
            "change_24h": t["change"]
        }
        for t in tokens
    ]

    prompt = """
You are an experimental Solana token analysis assistant.

Analyze ONLY the supplied metrics.
Do not invent facts.
Score each token from 0 to 100.
Explain the score briefly.
This is not financial advice.

Return ONLY JSON:
{"scores":[
{"symbol":"ABC","score":50,"reason":"Short explanation"}
]}

DATA:
""" + json.dumps(compact)

    raw = gemini(prompt)
    raw = raw.replace("```json", "").replace("```", "").strip()

    data = json.loads(raw)
    return {
        x["symbol"]: x
        for x in data.get("scores", [])
    }

# =========================
# SOLANA SCANNER
# =========================

def scan_solana():
    url = "https://api.dexscreener.com/token-profiles/latest/v1"
    response = requests.get(url, timeout=20)
    response.raise_for_status()

    profiles = response.json()
    tokens = []
    seen = set()

    for profile in profiles:
        if profile.get("chainId") != "solana":
            continue

        address = profile.get("tokenAddress")

        if not address or address in seen:
            continue

        seen.add(address)

        try:
            pair_url = (
                f"https://api.dexscreener.com/token-pairs/v1/solana/{address}"
            )
            r = requests.get(pair_url, timeout=15)
            r.raise_for_status()

            pairs = r.json()
            if not pairs:
                continue

            pair = max(
                pairs,
                key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0)
            )

            price = float(pair.get("priceUsd") or 0)

            if price <= 0:
                continue

            tokens.append({
                "name": (pair.get("baseToken") or {}).get("name", "Unknown"),
                "symbol": (pair.get("baseToken") or {}).get("symbol", "?"),
                "address": address,
                "price": price,
                "liquidity": float(
                    (pair.get("liquidity") or {}).get("usd") or 0
                ),
                "volume": float(
                    (pair.get("volume") or {}).get("h24") or 0
                ),
                "change": float(
                    (pair.get("priceChange") or {}).get("h24") or 0
                ),
                "score": None,
                "reason": "Waiting for Gemini",
                "url": pair.get("url", "")
            })

        except Exception:
            continue

        if len(tokens) >= 15:
            break

    return tokens

# =========================
# PAPER TRADING
# =========================

def open_trade(token, amount):
    if amount <= 0:
        return False, "Invalid amount"

    if get_cash() < amount:
        return False, "Not enough paper balance"

    con.execute("""
        INSERT INTO paper_trades_v2
        (token,address,entry,amount,status,opened)
        VALUES (?,?,?,?,?,?)
    """, (
        token["symbol"],
        token["address"],
        token["price"],
        amount,
        "OPEN",
        datetime.now().isoformat()
    ))

    con.commit()
    return True, "Paper trade opened"

def close_trade(trade_id):
    df = get_positions()
    trade = df[df["id"] == trade_id]

    if trade.empty:
        return

    trade = trade.iloc[0]

    try:
        current = get_price(trade["address"])
        if not current:
            return

        pnl = trade["amount"] * (
            current / trade["entry"] - 1
        )

        con.execute("""
            UPDATE paper_trades_v2
            SET status='CLOSED', exit=?, pnl=?, closed=?
            WHERE id=?
        """, (
            current,
            pnl,
            datetime.now().isoformat(),
            trade_id
        ))

        con.commit()

    except Exception:
        pass

def monitor_trades():
    df = get_positions()
    opened = df[df["status"] == "OPEN"]

    for _, trade in opened.iterrows():
        try:
            current = get_price(trade["address"])

            if not current or not trade["entry"]:
                continue

            change = current / trade["entry"] - 1

            if change <= -0.10 or change >= 0.30:
                close_trade(int(trade["id"]))

        except Exception:
            continue

def calculate_portfolio():
    df = get_positions()
    opened = df[df["status"] == "OPEN"]

    total = get_cash()

    for _, trade in opened.iterrows():
        try:
            current = get_price(trade["address"])
            if current:
                total += trade["amount"] * current / trade["entry"]
            else:
                total += trade["amount"]
        except Exception:
            total += trade["amount"]

    return total

# =========================
# AUTO REFRESH
# =========================

st_autorefresh(
    interval=REFRESH_MS,
    key="terminal_refresh"
)

monitor_trades()

# =========================
# HEADER
# =========================

st.markdown("""
<div class="terminal-header">
    <div class="terminal-label">SOLANA NETWORK / PAPER TRADING</div>
    <div class="terminal-title">SOLANA TERMINAL</div>
    <div class="terminal-subtitle">
        AI MARKET INTELLIGENCE • TOKEN SCANNER • PORTFOLIO ANALYTICS
    </div>
    <br>
    <span class="status-dot">● SYSTEM ONLINE</span>
</div>
""", unsafe_allow_html=True)

# =========================
# SIDEBAR
# =========================

with st.sidebar:
    st.markdown("## SOLANA TERMINAL")
    st.caption("PAPER TRADING SYSTEM")

    st.divider()

    st.markdown("### Risk settings")
    st.metric("Stop loss", "-10%")
    st.metric("Take profit", "+30%")

    st.divider()

    st.markdown("### System")
    st.write("Refresh: 10 seconds")
    st.write("Network: Solana")
    st.write("Mode: Paper only")
    st.write("AI: Gemini")

    st.divider()

    if st.button("TEST GEMINI"):
        with st.spinner("Testing AI..."):
            try:
                answer = test_gemini()
                st.success(answer)
            except Exception as e:
                st.error(str(e))

# =========================
# PORTFOLIO METRICS
# =========================

portfolio = calculate_portfolio()
cash = get_cash()
profit = portfolio - STARTING_CASH

df = get_positions()
opened = df[df["status"] == "OPEN"]
closed = df[df["status"] == "CLOSED"]

st.markdown("### PORTFOLIO OVERVIEW")

m1, m2, m3, m4 = st.columns(4)

m1.metric("Portfolio value", f"${portfolio:,.2f}")
m2.metric("Available cash", f"${cash:,.2f}")
m3.metric("Total P/L", f"${profit:,.2f}")
m4.metric("Open trades", str(len(opened)))

st.divider()

# =========================
# TABS
# =========================

tab1, tab2, tab3, tab4 = st.tabs([
    "MARKET SCANNER",
    "OPEN POSITIONS",
    "ANALYTICS",
    "TRADE HISTORY"
])

# =========================
# MARKET SCANNER
# =========================

with tab1:
    st.markdown("### LIVE SOLANA TOKEN SCANNER")
    st.caption("Latest token profiles from DexScreener")

    if "tokens" not in st.session_state:
        st.session_state.tokens = []

    if "scan_error" not in st.session_state:
        st.session_state.scan_error = ""

    col1, col2 = st.columns([1, 3])

    with col1:
        scan_button = st.button(
            "SCAN SOLANA",
            type="primary",
            use_container_width=True
        )

    with col2:
        st.caption("Fetch tokens and request Gemini analysis")

    if scan_button:
        st.session_state.tokens = []
        st.session_state.scan_error = ""

        with st.spinner("Scanning Solana..."):
            try:
                found = scan_solana()

                if not found:
                    raise Exception("No Solana tokens found.")

                st.session_state.tokens = found

                try:
                    scores = analyze_tokens(found)

                    for token in st.session_state.tokens:
                        result = scores.get(token["symbol"], {})
                        token["score"] = result.get("score")
                        token["reason"] = result.get(
                            "reason", "No Gemini explanation"
                        )

                except Exception as e:
                    st.session_state.scan_error = (
                        "Gemini analysis unavailable: " + str(e)
                    )

            except Exception as e:
                st.session_state.scan_error = str(e)

    if st.session_state.scan_error:
        st.warning(st.session_state.scan_error)

    tokens = st.session_state.tokens

    if tokens:
        rows = []

        for token in tokens:
            rows.append({
                "Token": token["name"],
                "Symbol": token["symbol"],
                "Price": token["price"],
                "Liquidity": token["liquidity"],
                "Volume 24h": token["volume"],
                "Change 24h %": token["change"],
                "Gemini score": (
                    token["score"]
                    if token["score"] is not None
                    else "Waiting"
                )
            })

        st.dataframe(
            pd.DataFrame(rows),
            use_container_width=True,
            hide_index=True
        )

        st.markdown("### SELECT TOKEN")

        choices = [
            f"{i+1}. {t['symbol']} — {t['name']}"
            for i, t in enumerate(tokens)
        ]

        selected = st.selectbox("Choose a token", choices)
        selected_index = choices.index(selected)
        token = tokens[selected_index]

        c1, c2, c3 = st.columns(3)

        c1.metric("Price", f"${token['price']:.8f}")
        c2.metric("Liquidity", f"${token['liquidity']:,.2f}")
        c3.metric(
            "Gemini score",
            str(token["score"]) if token["score"] is not None else "Waiting"
        )

        st.info(token["reason"])

        if token["url"]:
            st.markdown(f"[OPEN DEXSCREENER]({token['url']})")

        amount = st.selectbox(
            "Paper trade amount",
            [10, 25, 50, 100],
            index=1
        )

        if st.button("OPEN PAPER TRADE", type="primary"):
            success, message = open_trade(token, amount)

            if success:
                st.success(message)
                st.rerun()
            else:
                st.error(message)

    else:
        st.info("Press SCAN SOLANA to load tokens.")

# =========================
# OPEN POSITIONS
# =========================

with tab2:
    st.markdown("### ACTIVE PAPER POSITIONS")

    if not opened.empty:
        display = []

        for _, trade in opened.iterrows():
            try:
                current = get_price(trade["address"])
                if not current:
                    continue

                change = (current / trade["entry"] - 1) * 100
                pnl = trade["amount"] * change / 100

                display.append({
                    "ID": int(trade["id"]),
                    "Token": trade["token"],
                    "Entry": trade["entry"],
                    "Current": current,
                    "P/L %": round(change, 2),
                    "P/L $": round(pnl, 2),
                    "Invested": trade["amount"]
                })

            except Exception:
                continue

        if display:
            st.dataframe(
                pd.DataFrame(display),
                use_container_width=True,
                hide_index=True
            )

        trade_ids = opened["id"].astype(int).tolist()

        selected_id = st.selectbox(
            "Select position to close manually",
            trade_ids
        )

        if st.button("CLOSE SELECTED PAPER TRADE"):
            close_trade(int(selected_id))
            st.success("Paper position closed.")
            st.rerun()

    else:
        st.info("No open positions.")

# =========================
# ANALYTICS
# =========================

with tab3:
    st.markdown("### PORTFOLIO ANALYTICS")

    df = get_positions()
    opened = df[df["status"] == "OPEN"]
    closed = df[df["status"] == "CLOSED"]

    a, b, c = st.columns(3)

    a.metric("Total trades", len(df))
    b.metric("Open trades", len(opened))
    c.metric("Closed trades", len(closed))

    if not closed.empty:
        wins = len(closed[closed["pnl"] > 0])
        losses = len(closed[closed["pnl"] <= 0])
        win_rate = wins / len(closed) * 100

        a, b, c = st.columns(3)
        a.metric("Winning trades", wins)
        b.metric("Losing trades", losses)
        c.metric("Win rate", f"{win_rate:.1f}%")

        st.markdown("### CLOSED TRADE P/L")

        chart_data = closed[["token", "pnl"]].copy()
        chart_data = chart_data.set_index("token")
        st.bar_chart(chart_data)

    else:
        st.info("Your analytics will appear after closing trades.")

    st.markdown("### PORTFOLIO VALUE HISTORY")

    history = pd.read_sql_query(
        "SELECT time,value FROM portfolio_snapshots_v2 ORDER BY id",
        con
    )

    if not history.empty:
        st.line_chart(history.set_index("time")["value"])
    else:
        st.info("Waiting for portfolio history.")

# =========================
# TRADE HISTORY
# =========================

with tab4:
    st.markdown("### COMPLETE TRADE HISTORY")

    history = get_positions()

    if not history.empty:
        st.dataframe(
            history[
                [
                    "id", "token", "entry", "exit",
                    "amount", "status", "pnl", "opened", "closed"
                ]
            ],
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("No trade history yet.")

# =========================
# SNAPSHOT
# =========================

con.execute(
    "INSERT INTO portfolio_snapshots_v2 (time,value) VALUES (?,?)",
    (datetime.now().strftime("%H:%M:%S"), portfolio)
)
con.commit()

# =========================
# FOOTER
# =========================

st.divider()

st.caption(
    "SOLANA TERMINAL • PAPER TRADING ONLY • "
    "Gemini scores are experimental. "
    "Stop loss and take profit are checked on refresh and are not guaranteed execution prices."
)
