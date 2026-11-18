
import streamlit as st
import requests
import sqlite3
import pandas as pd
import json
from datetime import datetime
from google import genai
from streamlit_autorefresh import st_autorefresh

st.set_page_config(page_title="Solana AI Paper Bot", page_icon="🚀", layout="wide")

DB = "bot.db"
START_BALANCE = 1000.0
STOP_LOSS = -10
TAKE_PROFIT = 30
MODEL = "gemini-3.8-flash"

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

        count = conn.execute(
            "SELECT COUNT(*) FROM snapshots"
        ).fetchone()[0]

        if count == 0:
            conn.execute(
                "INSERT INTO snapshots VALUES (?, ?, ?)",
                (datetime.now().isoformat(), START_BALANCE, 0.0)
            )


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


def gemini_analyze(tokens):
    api_key = st.secrets.get("GEMINI_API_KEY", "")

    if not api_key:
        raise ValueError("Gemini API key is missing from Streamlit Secrets")

    client = genai.Client(api_key=api_key)

    data = []

    for i, token in enumerate(tokens):
        data.append({
            "id": i,
            "name": token["Name"],
            "symbol": token["Symbol"],
            "price_usd": token["Price"],
            "liquidity_usd": token["Liquidity"],
            "volume_24h_usd": token["Volume 24h"]
        })

    prompt = f"""
You are an analytical assistant evaluating Solana meme coin market data.

Analyze only the supplied data.
Do not invent information about holders, contract safety, developers,
social media, future prices or market events.

Give each token a score from 0 to 100 based on:
- Liquidity
- 24 hour trading volume
- Relationship between volume and liquidity
- General market activity visible in the supplied metrics

A high score does NOT mean the token is safe or guaranteed to rise.

Return valid JSON only in this exact structure:
{{
  "results": [
    {{
      "id": 0,
      "score": 50,
      "reason": "Short explanation"
    }}
  ]
}}

Token data:
{json.dumps(data)}
"""

    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config={
            "response_mime_type": "application/json",
            "temperature": 0.2
        }
    )

    result = json.loads(response.text)

    scores = {}

    for item in result.get("results", []):
        try:
            index = int(item["id"])
            score = max(0, min(100, int(item["score"])))

            scores[index] = {
                "score": score,
                "reason": str(item.get("reason", "No explanation"))
            }
        except (ValueError, TypeError, KeyError):
            continue

    return scores


def scan():
    tokens = []

    try:
        r = requests.get(
            "https://api.dexscreener.com/token-profiles/latest/v1",
            timeout=15
        )
        r.raise_for_status()

        profiles = r.json()

        for item in profiles:
            if item.get("chainId") != "solana":
                continue

            address = item.get("tokenAddress")

            if not address:
                continue

            try:
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
                    key=lambda p: float(
                        p.get("liquidity", {}).get("usd") or 0
                    )
                )

                price = float(pair.get("priceUsd") or 0)
                liquidity = float(pair.get("liquidity", {}).get("usd") or 0)
                volume = float(pair.get("volume", {}).get("h24") or 0)

                if price <= 0:
                    continue

                tokens.append({
                    "Name": pair.get("baseToken", {}).get("name", "Unknown"),
                    "Symbol": pair.get("baseToken", {}).get("symbol", "?"),
                    "Price": price,
                    "Liquidity": liquidity,
                    "Volume 24h": volume,
                    "Score": 0,
                    "AI reason": "Waiting for Gemini analysis",
                    "Address": address
                })

            except Exception:
                continue

            if len(tokens) >= 15:
                break

    except Exception as e:
        st.error(f"Scanner error: {e}")
        return []

    if not tokens:
        return []

    try:
        scores = gemini_analyze(tokens)

        for i, token in enumerate(tokens):
            if i in scores:
                token["Score"] = scores[i]["score"]
                token["AI reason"] = scores[i]["reason"]
            else:
                token["AI reason"] = "Gemini did not return a score"

    except Exception as e:
        st.error(f"Gemini AI error: {e}")
        st.warning("AI scoring failed. Scores remain unavailable.")
        
    return sorted(tokens, key=lambda x: x["Score"], reverse=True)


setup()

st.title("🚀 Solana Gemini AI Paper Bot")
st.caption("Gemini AI scoring • Virtual funds only • Refresh every 10 seconds")

closed = check_trades()

for name, change, pnl in closed:
    st.warning(
        f"{name} closed at {change:+.2f}% | P/L ${pnl:+.2f}"
    )

trades = get_trades()
opened = [t for t in trades if t[5] == "OPEN"]
finished = [t for t in trades if t[5] == "CLOSED"]

reserved = sum(t[4] for t in opened)

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
    history = history.set_index("date")
    st.line_chart(history["pnl"], height=300)
else:
    st.info("Waiting for graph data")

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
st.subheader("🧠 Gemini AI Solana scanner")

if st.button("Scan Solana with Gemini AI"):
    with st.spinner("Gemini is analyzing tokens..."):
        st.session_state.tokens = scan()

if "tokens" in st.session_state and st.session_state.tokens:
    display_tokens = [
        {k: v for k, v in t.items() if k != "Address"}
        for t in st.session_state.tokens
    ]

    st.dataframe(display_tokens, use_container_width=True)

    choices = [
        f"{t['Symbol']} | AI Score {t['Score']}/100"
        for t in st.session_state.tokens
    ]

    selected = st.selectbox(
        "Choose token",
        range(len(choices)),
        format_func=lambda i: choices[i]
    )

    token = st.session_state.tokens[selected]

    st.write("**Gemini analysis:**", token["AI reason"])

    amount = st.number_input(
        "Paper trade amount ($)",
        min_value=1.0,
        max_value=100.0,
        value=25.0
    )

    if st.button("Buy PAPER ONLY"):
        if amount > cash:
            st.error("Not enough virtual cash")
        elif token["Score"] == 0:
            st.error("No valid AI score. Cannot open trade.")
        else:
            with sqlite3.connect(DB) as conn:
                conn.execute("""
                INSERT INTO trades
                (token,address,entry,amount,status,date)
                VALUES (?,?,?,?,?,?)
                """, (
                    token["Name"],
                    token["Address"],
                    token["Price"],
                    amount,
                    "OPEN",
                    datetime.now().isoformat()
                ))

            st.success("Paper trade opened!")
            st.rerun()

st.caption(
    "Paper trading only. AI scores are experimental, "
    "not financial advice. Stop losses are not guaranteed execution prices."
)
