import streamlit as st
import requests
import sqlite3
from datetime import datetime

st.set_page_config(page_title="Grok Solana Bot", page_icon="🚀")

DB = "bot.db"
STOP_LOSS = -15
TAKE_PROFIT = 30

def setup():
    with sqlite3.connect(DB) as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT,
            address TEXT,
            entry REAL,
            amount REAL,
            status TEXT,
            date TEXT
        )
        """)
        columns = [
            row[1] for row in conn.execute("PRAGMA table_info(trades)")
        ]
        if "exit_price" not in columns:
            conn.execute("ALTER TABLE trades ADD COLUMN exit_price REAL")
        if "pnl" not in columns:
            conn.execute("ALTER TABLE trades ADD COLUMN pnl REAL")

def get_price(address):
    try:
        r = requests.get(
            f"https://api.dexscreener.com/token-pairs/v1/solana/{address}",
            timeout=15
        )
        r.raise_for_status()
        pairs = r.json()
        if not pairs:
            return None
        pair = max(
            pairs,
            key=lambda p: float(p.get("liquidity", {}).get("usd") or 0)
        )
        return float(pair.get("priceUsd") or 0)
    except Exception:
        return None

def check_trades():
    closed = []
    with sqlite3.connect(DB) as conn:
        trades = conn.execute(
            "SELECT id, token, address, entry, amount FROM trades WHERE status='OPEN'"
        ).fetchall()

        for trade in trades:
            trade_id, name, address, entry, amount = trade
            price = get_price(address)

            if not price or not entry:
                continue

            change = ((price - entry) / entry) * 100

            if change <= STOP_LOSS or change >= TAKE_PROFIT:
                pnl = amount * change / 100
                conn.execute(
                    """UPDATE trades
                    SET status='CLOSED', exit_price=?, pnl=?
                    WHERE id=?""",
                    (price, pnl, trade_id)
                )
                closed.append((name, change, pnl))

    return closed

def scan():
    try:
        r = requests.get(
            "https://api.dexscreener.com/token-profiles/latest/v1",
            timeout=15
        )
        r.raise_for_status()
        tokens = []

        for item in r.json():
            if item.get("chainId") != "solana":
                continue

            address = item.get("tokenAddress")
            if not address:
                continue

            pairs = requests.get(
                f"https://api.dexscreener.com/token-pairs/v1/solana/{address}",
                timeout=15
            ).json()

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

            score = 0
            if liquidity >= 10000:
                score += 40
            if volume >= 50000:
                score += 30
            if volume >= liquidity:
                score += 30

            tokens.append({
                "Name": pair.get("baseToken", {}).get("name", "Unknown"),
                "Symbol": pair.get("baseToken", {}).get("symbol", "?"),
                "Price": price,
                "Liquidity": liquidity,
                "Volume 24h": volume,
                "Score": score,
                "Address": address
            })

        return sorted(tokens, key=lambda x: x["Score"], reverse=True)

    except Exception as e:
        st.error(f"Scan error: {e}")
        return []

setup()

st.title("🚀 Grok Solana Meme Coin Bot")
st.caption("Paper trading only. No real wallet or transactions.")

if "tokens" not in st.session_state:
    st.session_state.tokens = []

if "balance" not in st.session_state:
    st.session_state.balance = 1000.0

st.metric("Virtual balance", f"${st.session_state.balance:.2f}")

if st.button("🔎 Scan Solana"):
    with st.spinner("Scanning tokens and checking open trades..."):
        closed = check_trades()
        st.session_state.tokens = scan()

    for name, change, pnl in closed:
        st.success(
            f"{name} closed at {change:+.2f}% "
            f"(paper P/L: ${pnl:+.2f})"
        )

if st.session_state.tokens:
    st.subheader("Token analysis")
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

    st.write("Address:", token["Address"])
    st.write(f"Stop loss: {STOP_LOSS}%")
    st.write(f"Take profit: +{TAKE_PROFIT}%")

    if token["Liquidity"] < 10000:
        st.warning("Low liquidity")
    else:
        st.success("Liquidity above basic threshold")

    amount = st.number_input(
        "Paper amount ($)", min_value=1.0,
        max_value=100.0, value=25.0
    )

    if st.button("Buy PAPER ONLY"):
        if amount > st.session_state.balance:
            st.error("Not enough virtual balance")
        else:
            with sqlite3.connect(DB) as conn:
                conn.execute(
                    """INSERT INTO trades
                    (token,address,entry,amount,status,date)
                    VALUES (?,?,?,?,?,?)""",
                    (
                        token["Name"], token["Address"],
                        token["Price"], amount, "OPEN",
                        datetime.now().isoformat()
                    )
                )
            st.session_state.balance -= amount
            st.success("Paper trade opened")

st.divider()
st.subheader("Paper trades")

with sqlite3.connect(DB) as conn:
    trades = conn.execute(
        """SELECT token, entry, amount, status, exit_price, pnl
        FROM trades ORDER BY id DESC"""
    ).fetchall()

if trades:
    for t in trades:
        st.write(
            f"{t[0]} | Entry: ${t[1]:.8f} | "
            f"${t[2]:.2f} | {t[3]}"
        )
        if t[3] == "CLOSED":
            st.write(f"Exit: ${t[4]:.8f} | P/L: ${t[5]:+.2f}")
else:
    st.info("No paper trades yet")

st.caption(
    "Simulation only. Stop loss and take profit are checked "
    "only when you press Scan. Not financial advice."
)
