
import streamlit as st
import requests
import sqlite3
import pandas as pd
import json
from datetime import datetime
from streamlit_autorefresh import st_autorefresh

st.set_page_config(page_title="Solana AI Paper Bot", layout="wide")
st.title("Solana Gemini AI Paper Bot")

MODEL = "gemini-3.8-flash"
DB = "bot.db"
STARTING_CASH = 1000.0

def db():
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
    return con

con = db()

def gemini_key():
    try:
        return st.secrets["GEMINI_API_KEY"]
    except Exception:
        return ""

def ask_gemini(prompt):
    key = gemini_key()
    if not key:
        raise Exception("GEMINI_API_KEY manjka v Streamlit Secrets.")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
    response = requests.post(
        url,
        headers={
            "Content-Type": "application/json",
            "X-goog-api-key": key
        },
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2}
        },
        timeout=45
    )

    if response.status_code != 200:
        raise Exception(f"Gemini napaka {response.status_code}: {response.text[:500]}")

    data = response.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]

def get_tokens():
    url = "https://api.dexscreener.com/token-profiles/latest/v1"
    r = requests.get(url, timeout=20)
    r.raise_for_status()

    profiles = r.json()
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
            pair_url = f"https://api.dexscreener.com/token-pairs/v1/solana/{address}"
            pr = requests.get(pair_url, timeout=15)
            pr.raise_for_status()
            pairs = pr.json()

            if not pairs:
                continue

            pair = max(
                pairs,
                key=lambda x: float((x.get("liquidity") or {}).get("usd") or 0)
            )

            liquidity = float((pair.get("liquidity") or {}).get("usd") or 0)
            volume = float((pair.get("volume") or {}).get("h24") or 0)
            price = float(pair.get("priceUsd") or 0)
            change = float((pair.get("priceChange") or {}).get("h24") or 0)

            if price <= 0:
                continue

            tokens.append({
                "name": (pair.get("baseToken") or {}).get("name", "Unknown"),
                "symbol": (pair.get("baseToken") or {}).get("symbol", "?"),
                "address": address,
                "price": price,
                "liquidity": liquidity,
                "volume": volume,
                "change": change,
                "url": pair.get("url", "")
            })

        except Exception:
            continue

        if len(tokens) >= 12:
            break

    return tokens

def ai_scores(tokens):
    compact = []
    for t in tokens:
        compact.append({
            "symbol": t["symbol"],
            "liquidity_usd": t["liquidity"],
            "volume_24h_usd": t["volume"],
            "change_24h_percent": t["change"]
        })

    prompt = """
Analyze these Solana meme coins using ONLY the supplied numbers.
This is experimental paper-trading analysis, not financial advice.
Give each token a risk score from 0 to 100.
Do not invent information.
Return ONLY valid JSON in this format:
{"scores":[{"symbol":"TOKEN","score":50,"reason":"Short reason"}]}

Tokens:
""" + json.dumps(compact)

    raw = ask_gemini(prompt)
    raw = raw.replace("```json", "").replace("```", "").strip()
    result = json.loads(raw)
    return {
        x["symbol"]: x
        for x in result.get("scores", [])
    }

def price_for(address):
    url = f"https://api.dexscreener.com/token-pairs/v1/solana/{address}"
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    pairs = r.json()
    if not pairs:
        return None
    pair = max(
        pairs,
        key=lambda x: float((x.get("liquidity") or {}).get("usd") or 0)
    )
    return float(pair.get("priceUsd") or 0)

def get_cash():
    trades = pd.read_sql_query(
        "SELECT amount FROM paper_trades_v2 WHERE status='OPEN'", con
    )
    return STARTING_CASH - trades["amount"].sum()

def get_positions():
    return pd.read_sql_query(
        "SELECT * FROM paper_trades_v2 ORDER BY id DESC", con
    )

def snapshot():
    positions = get_positions()
    cash = get_cash()
    total = cash

    for _, trade in positions[positions["status"] == "OPEN"].iterrows():
        try:
            current = price_for(trade["address"])
            if current:
                total += trade["amount"] * current / trade["entry"]
            else:
                total += trade["amount"]
        except Exception:
            total += trade["amount"]

    con.execute(
        "INSERT INTO portfolio_snapshots_v2 (time,value) VALUES (?,?)",
        (datetime.now().strftime("%H:%M:%S"), total)
    )
    con.commit()

def check_trades():
    positions = get_positions()
    for _, trade in positions[positions["status"] == "OPEN"].iterrows():
        try:
            current = price_for(trade["address"])
            if not current or not trade["entry"]:
                continue

            change = (current / trade["entry"]) - 1

            if change <= -0.10 or change >= 0.30:
                pnl = trade["amount"] * change
                con.execute("""
                    UPDATE paper_trades_v2
                    SET status='CLOSED', exit=?, pnl=?, closed=?
                    WHERE id=?
                """, (
                    current, pnl,
                    datetime.now().isoformat(),
                    int(trade["id"])
                ))
        except Exception:
            pass
    con.commit()

st_autorefresh(interval=15000, key="refresh")

st.subheader("Gemini connection")

if st.button("Test Gemini connection"):
    try:
        answer = ask_gemini('Reply with exactly the word OK')
        st.success("Gemini works: " + answer[:100])
    except Exception as e:
        st.error(str(e))

st.divider()

if "tokens" not in st.session_state:
    st.session_state.tokens = []

if "scan_error" not in st.session_state:
    st.session_state.scan_error = ""

if st.button("Scan Solana with Gemini AI", type="primary"):
    st.session_state.tokens = []
    st.session_state.scan_error = ""

    with st.spinner("Fetching Solana tokens..."):
        try:
            found = get_tokens()
            if not found:
                raise Exception("DexScreener ni vrnil tokenov. Poskusi znova kasneje.")

            st.session_state.tokens = found

            try:
                scores = ai_scores(found)
                for token in st.session_state.tokens:
                    result = scores.get(token["symbol"], {})
                    token["score"] = int(result.get("score", 0))
                    token["reason"] = result.get("reason", "Gemini ni ocenil tokena.")
            except Exception as e:
                st.session_state.scan_error = "Gemini analiza ni uspela: " + str(e)
                for token in st.session_state.tokens:
                    token["score"] = 0
                    token["reason"] = "Gemini analiza ni uspela."

        except Exception as e:
            st.session_state.scan_error = str(e)

if st.session_state.scan_error:
    st.warning(st.session_state.scan_error)

st.subheader("Solana token scanner")

if st.session_state.tokens:
    for i, token in enumerate(st.session_state.tokens):
        with st.container(border=True):
            st.write(f"**{token['name']} ({token['symbol']})**")
            a, b, c = st.columns(3)
            a.metric("Price", f"${token['price']:.8f}")
            b.metric("Liquidity", f"${token['liquidity']:,.0f}")
            c.metric("Gemini score", f"{token.get('score', 0)}/100")

            st.write(
                f"24h volume: ${token['volume']:,.0f} | "
                f"24h change: {token['change']:.2f}%"
            )
            st.caption(token.get("reason", ""))

            if token.get("url"):
                st.markdown(f"[Open DexScreener]({token['url']})")

            if st.button("Paper buy $25", key=f"buy_{i}"):
                cash = get_cash()
                if cash < 25:
                    st.error("Not enough paper cash.")
                else:
                    con.execute("""
                        INSERT INTO paper_trades_v2
                        (token,address,entry,amount,status,opened)
                        VALUES (?,?,?,?,?,?)
                    """, (
                        token["symbol"], token["address"],
                        token["price"], 25, "OPEN",
                        datetime.now().isoformat()
                    ))
                    con.commit()
                    st.success("Paper trade opened!")
                    st.rerun()
else:
    st.info("Press Scan Solana with Gemini AI to find tokens.")

st.divider()
check_trades()
snapshot()

positions = get_positions()
cash = get_cash()
open_positions = positions[positions["status"] == "OPEN"]
closed_positions = positions[positions["status"] == "CLOSED"]

portfolio_value = cash
for _, trade in open_positions.iterrows():
    try:
        current = price_for(trade["address"])
        if current:
            portfolio_value += trade["amount"] * current / trade["entry"]
        else:
            portfolio_value += trade["amount"]
    except Exception:
        portfolio_value += trade["amount"]

total_pnl = portfolio_value - STARTING_CASH

st.subheader("Paper trading dashboard")
a, b, c = st.columns(3)
a.metric("Portfolio value", f"${portfolio_value:.2f}")
b.metric("Available cash", f"${cash:.2f}")
c.metric("Total P/L", f"${total_pnl:.2f}")

st.subheader("Open positions")
if not open_positions.empty:
    st.dataframe(
        open_positions[["token", "entry", "amount", "opened"]],
        use_container_width=True
    )
else:
    st.info("No open positions.")

st.subheader("Portfolio history")
history = pd.read_sql_query(
    "SELECT time,value FROM portfolio_snapshots_v2 ORDER BY id", con
)
if not history.empty:
    st.line_chart(history.set_index("time")["value"])
else:
    st.info("Graph will appear after snapshots are collected.")

st.subheader("Closed trades")
if not closed_positions.empty:
    st.dataframe(
        closed_positions[["token", "entry", "exit", "amount", "pnl", "closed"]],
        use_container_width=True
    )
else:
    st.info("No closed trades yet.")

st.caption(
    "Paper trading only. Stop loss and take profit are checked on app refresh. "
    "They are not guaranteed execution prices. Gemini scores are experimental."
)
