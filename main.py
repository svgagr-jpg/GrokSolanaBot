
import streamlit as st
import requests
import sqlite3
import pandas as pd
from datetime import datetime
from streamlit_autorefresh import st_autorefresh

st.set_page_config(page_title="Solana Paper Bot", page_icon="🚀", layout="wide")

DB = "bot.db"
START_BALANCE = 1000.0
STOP_LOSS = -10
TAKE_PROFIT = 30

st_autorefresh(interval=10000, key="refresh")

def setup():
    with sqlite3.connect(DB) as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT, address TEXT, entry REAL,
            amount REAL, status TEXT, date TEXT
        )
        """)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(trades)")]
        if "exit_price" not in cols:
            conn.execute("ALTER TABLE trades ADD COLUMN exit_price REAL")
        if "pnl" not in cols:
            conn.execute("ALTER TABLE trades ADD COLUMN pnl REAL")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS snapshots (
            date TEXT, equity REAL, pnl REAL
        )
        """)

def get_price(address):
    try:
        r = requests.get(
            f"https://api.dexscreener.com/token-pairs/v1/solana/{address}",
            timeout=10
        )
        r.raise_for_status()
        pairs = r.json()
        if not pairs:
            return None
        pair = max(
            pairs,
            key=lambda p: float(p.get("liquidity", {}).get("usd") or 0)
        )
        price = float(pair.get("priceUsd") or 0)
        return price if price > 0 else None
    except Exception:
        return None

def get_trades():
    with sqlite3.connect(DB) as conn:
        return conn.execute("""
        SELECT id, token, address, entry, amount, status, exit_price, pnl
        FROM trades ORDER BY id DESC
        """).fetchall()

def check_trades():
    closed = []
    with sqlite3.connect(DB) as conn:
        open_trades = conn.execute("""
        SELECT id, token, address, entry, amount
        FROM trades WHERE status='OPEN'
        """).fetchall()

        for trade_id, name, address, entry, amount in open_trades:
            price = get_price(address)
            if price is None or not entry:
                continue

            change = (price / entry - 1) * 100
            if change <= STOP_LOSS or change >= TAKE_PROFIT:
                pnl = amount * change / 100
                conn.execute("""
                UPDATE trades
                SET status='CLOSED', exit_price=?, pnl=?
                WHERE id=?
                """, (price, pnl, trade_id))
                closed.append((name, change, pnl))
    return closed

def scan():
    tokens = []
    try:
        r = requests.get(
            "https://api.dexscreener.com/token-profiles/latest/v1",
            timeout=15
        )
        r.raise_for_status()

        for item in r.json():
            if item.get("chainId") != "solana":
                continue

            address = item.get("tokenAddress")
            if not address:
                continue

            response = requests.get(
                f"https://api.dexscreener.com/token-pairs/v1/solana/{address}",
                timeout=10
            )
            response.raise_for_status()
            pairs = response.json()
            if not pairs:
                continue

            pair = max(
                pairs,
                key=lambda p: float(p.get("liquidity", {}).get("usd") or 0)
            )
            price = float(pair.get("priceUsd") or 0)
            liquidity = float(pair.get("liquidity", {}).get("usd") or 0)
            volume = float(pair.get("volume", {}).get("h24") or 0)

            if price <= 0:
                continue

            score = (
                (40 if liquidity >= 10000 else 0) +
                (30 if volume >= 50000 else 0) +
                (30 if volume >= liquidity else 0)
            )

            tokens.append({
                "Name": pair.get("baseToken", {}).get("name", "Unknown"),
                "Symbol": pair.get("baseToken", {}).get("symbol", "?"),
                "Price": price,
                "Liquidity": liquidity,
                "Volume 24h": volume,
                "Score": score,
                "Address": address
            })
    except Exception as e:
        st.error(f"Scan error: {e}")

    return sorted(tokens, key=lambda x: x["Score"], reverse=True)

setup()

st.title("🚀 Solana Paper Trading Dashboard")
st.caption("Virtual funds only • Refresh every 10 seconds")

closed = check_trades()
for name, change, pnl in closed:
    st.warning(f"{name} closed at {change:+.2f}% | P/L ${pnl:+.2f}")

trades = get_trades()
opened = [t for t in trades if t[5] == "OPEN"]
finished = [t for t in trades if t[5] == "CLOSED"]

reserved = sum(t[4] for t in opened)
realized = sum((t[7] or 0) for t in finished)
cash = START_BALANCE - reserved + sum(
    t[4] + (t[7] or 0) for t in finished
)

unrealized = 0
open_value = 0
positions = []

for t in opened:
    price = get_price(t[2])
    if price is None or not t[3]:
        continue

    value = t[4] * price / t[3]
    pnl = value - t[4]
    change = (price / t[3] - 1) * 100
    open_value += value
    unrealized += pnl

    positions.append({
        "Coin": t[1],
        "Entry ($)": t[3],
        "Current ($)": price,
        "P/L ($)": round(pnl, 2),
        "Change (%)": round(change, 2)
    })

equity = cash + open_value
total_pnl = equity - START_BALANCE

with sqlite3.connect(DB) as conn:
    conn.execute(
        "INSERT INTO snapshots VALUES (?, ?, ?)",
        (datetime.now().isoformat(), equity, total_pnl)
    )
    history = pd.read_sql_query(
        "SELECT date, pnl FROM snapshots ORDER BY date",
        conn
    )

st.subheader("Portfolio overview")
a, b, c, d = st.columns(4)
a.metric("Portfolio value", f"${equity:.2f}")
b.metric("Available cash", f"${cash:.2f}")
c.metric("Total P/L", f"${total_pnl:.2f}")
d.metric("Open trades", len(opened))

st.divider()
st.subheader("📈 Profit / Loss graph")

if not history.empty:
    history["date"] = pd.to_datetime(history["date"])
    history = history.drop_duplicates(subset=["date"])
    history = history.set_index("date")
    st.line_chart(history["pnl"], height=300)
else:
    st.info("Graph will appear after first refresh")

st.subheader("Open positions")
if positions:
    st.dataframe(positions, use_container_width=True)
else:
    st.info("No open positions")

st.subheader("Closed trades")
if finished:
    st.dataframe([
        {
            "Coin": t[1],
            "Entry": t[3],
            "Amount": t[4],
            "Exit": t[6],
            "P/L": t[7]
        }
        for t in finished
    ], use_container_width=True)
else:
    st.info("No closed trades yet")

st.divider()
st.subheader("Solana scanner")

if "tokens" not in st.session_state:
    st.session_state.tokens = []

if st.button("Scan Solana"):
    with st.spinner("Scanning..."):
        st.session_state.tokens = scan()

if st.session_state.tokens:
    st.dataframe([
        {k: v for k, v in t.items() if k != "Address"}
        for t in st.session_state.tokens
    ], use_container_width=True)

    choices = [
        f"{t['Symbol']} | Score {t['Score']}/100"
        for t in st.session_state.tokens
    ]
    selected = st.selectbox(
        "Choose token", range(len(choices)),
        format_func=lambda i: choices[i]
    )
    token = st.session_state.tokens[selected]

    amount = st.number_input(
        "Paper trade amount ($)", 1.0, 100.0, 25.0
    )

    if st.button("Buy PAPER ONLY"):
        if amount > cash:
            st.error("Not enough virtual cash")
        else:
            with sqlite3.connect(DB) as conn:
                conn.execute("""
                INSERT INTO trades
                (token,address,entry,amount,status,date)
                VALUES (?,?,?,?,?,?)
                """, (
                    token["Name"], token["Address"],
                    token["Price"], amount, "OPEN",
                    datetime.now().isoformat()
                ))
            st.success("Paper trade opened")
            st.rerun()

st.caption(
    "Simulation only. Refresh and price data can be delayed. "
    "Stops are not guaranteed execution prices."
)
