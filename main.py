import streamlit as st
import requests
import sqlite3
from datetime import datetime

st.set_page_config(page_title="Grok Solana Bot", page_icon="🚀", layout="wide")

DB = "bot.db"

def setup():
    conn = sqlite3.connect(DB)
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
    conn.commit()
    conn.close()

def get_tokens():
    url = "https://api.dexscreener.com/token-profiles/latest/v1"
    try:
        data = requests.get(url, timeout=15).json()
        tokens = []

        for item in data:
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

            liquidity = float(pair.get("liquidity", {}).get("usd") or 0)
            volume = float(pair.get("volume", {}).get("h24") or 0)
            price = float(pair.get("priceUsd") or 0)

            if price <= 0:
                continue

            tokens.append({
                "Name": pair.get("baseToken", {}).get("name", "Unknown"),
                "Symbol": pair.get("baseToken", {}).get("symbol", "?"),
                "Price ($)": price,
                "Liquidity ($)": liquidity,
                "Volume 24h ($)": volume,
                "Address": address
            })

        return sorted(tokens, key=lambda x: x["Volume 24h ($)"], reverse=True)

    except Exception as e:
        st.error(f"Napaka pri iskanju: {e}")
        return []

setup()

st.title("🚀 Grok Solana Meme Coin Bot")
st.caption("Paper trading only • No real transactions • No wallet connection")

if "tokens" not in st.session_state:
    st.session_state.tokens = []

if "balance" not in st.session_state:
    st.session_state.balance = 1000.0

st.metric("Paper balance", f"${st.session_state.balance:.2f}")

if st.button("🔎 Scan Solana tokens"):
    with st.spinner("Iščem tokene..."):
        st.session_state.tokens = get_tokens()

if st.session_state.tokens:
    st.subheader("Najdeni tokeni")
    st.dataframe(
        [{k: v for k, v in t.items() if k != "Address"}
         for t in st.session_state.tokens],
        use_container_width=True
    )

    names = [
        f"{t['Symbol']} | {t['Name']} | ${t['Price ($)']:.8f}"
        for t in st.session_state.tokens
    ]

    selected = st.selectbox("Izberi token", range(len(names)),
                            format_func=lambda i: names[i])
    token = st.session_state.tokens[selected]

    st.write("Token address:", token["Address"])

    amount = st.number_input(
        "Paper trade amount ($)",
        min_value=1.0,
        max_value=100.0,
        value=25.0
    )

    if st.button("🟢 Buy (PAPER ONLY)"):
        if amount > st.session_state.balance:
            st.error("Premalo sredstev")
        else:
            conn = sqlite3.connect(DB)
            conn.execute(
                "INSERT INTO trades (token,address,entry,amount,status,date) VALUES (?,?,?,?,?,?)",
                (token["Name"], token["Address"], token["Price ($)"],
                 amount, "OPEN", datetime.now().isoformat())
            )
            conn.commit()
            conn.close()
            st.session_state.balance -= amount
            st.success("Paper trade odprt")

st.divider()
st.subheader("📊 Paper trades")

conn = sqlite3.connect(DB)
trades = conn.execute(
    "SELECT id, token, entry, amount, status, date FROM trades ORDER BY id DESC"
).fetchall()
conn.close()

if trades:
    for trade in trades:
        st.write(
            f"**{trade[1]}** | Entry: ${trade[2]:.8f} | "
            f"Amount: ${trade[3]:.2f} | {trade[4]}"
        )
else:
    st.info("Še nimaš odprtih paper tradov")

st.caption("⚠️ To je testni bot. Cene in podatki so lahko napačni ali zastareli. Ne kupuje ali prodaja pravih coinov.")
