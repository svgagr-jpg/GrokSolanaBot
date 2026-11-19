
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

con = sqlite3.connect(DB, check_same_thread=False)
con.execute("""
CREATE TABLE IF NOT EXISTS paper_trades_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token TEXT, address TEXT, entry REAL, amount REAL,
    status TEXT, exit REAL, pnl REAL, opened TEXT, closed TEXT
)
""")
con.execute("""
CREATE TABLE IF NOT EXISTS portfolio_snapshots_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    time TEXT, value REAL
)
""")
con.commit()

def get_key():
    try:
        return st.secrets["GEMINI_API_KEY"]
    except Exception:
        return ""

def ask_gemini(prompt):
    key = get_key()
    if not key:
        raise Exception("GEMINI_API_KEY manjka v Streamlit Secrets.")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
    r = requests.post(
        url,
        headers={"Content-Type": "application/json", "X-goog-api-key": key},
        json={"contents": [{"parts": [{"text": prompt}]}]},
        timeout=45
    )
    if r.status_code != 200:
        raise Exception(f"Gemini napaka {r.status_code}: {r.text[:400]}")
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]

def get_tokens():
    r = requests.get(
        "https://api.dexscreener.com/token-profiles/latest/v1",
        timeout=20
    )
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
            url = f"https://api.dexscreener.com/token-pairs/v1/solana/{address}"
            pr = requests.get(url, timeout=15)
            pr.raise_for_status()
            pairs = pr.json()
            if not pairs:
                continue

            pair = max(
                pairs,
                key=lambda x: float((x.get("liquidity") or {}).get("usd") or 0)
            )

            price = float(pair.get("priceUsd") or 0)
            if price <= 0:
                continue

            tokens.append({
                "Name": (pair.get("baseToken") or {}).get("name", "Unknown"),
                "Symbol": (pair.get("baseToken") or {}).get("symbol", "?"),
                "Address": address,
                "Price ($)": price,
                "Liquidity ($)": float((pair.get("liquidity") or {}).get("usd") or 0),
                "Volume 24h ($)": float((pair.get("volume") or {}).get("h24") or 0),
                "Change 24h (%)": float((pair.get("priceChange") or {}).get("h24") or 0),
                "Gemini score": 0,
                "Reason": "Čaka na Gemini analizo",
                "URL": pair.get("url", "")
            })
        except Exception:
            continue

        if len(tokens) >= 12:
            break

    return tokens

def analyze_tokens(tokens):
    compact = [
        {
            "symbol": t["Symbol"],
            "liquidity_usd": t["Liquidity ($)"],
            "volume_24h_usd": t["Volume 24h ($)"],
            "change_24h_percent": t["Change 24h (%)"]
        }
        for t in tokens
    ]

    prompt = """
Analyze these Solana tokens using only the supplied numbers.
Give each token a risk score from 0 to 100.
Return ONLY JSON:
{"scores":[{"symbol":"TOKEN","score":50,"reason":"Short reason"}]}
Tokens:
""" + json.dumps(compact)

    raw = ask_gemini(prompt).replace("```json", "").replace("```", "").strip()
    results = json.loads(raw).get("scores", [])
    scores = {x["symbol"]: x for x in results}

    for token in tokens:
        result = scores.get(token["Symbol"], {})
        token["Gemini score"] = result.get("score", 0)
        token["Reason"] = result.get("reason", "Brez ocene")

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

def positions():
    return pd.read_sql_query(
        "SELECT * FROM paper_trades_v2 ORDER BY id DESC", con
    )

def cash_left():
    open_trades = positions()
    open_trades = open_trades[open_trades["status"] == "OPEN"]
    return STARTING_CASH - open_trades["amount"].sum()

def check_trades():
    for _, trade in positions().query("status == 'OPEN'").iterrows():
        try:
            current = price_for(trade["address"])
            if not current or not trade["entry"]:
                continue
            change = current / trade["entry"] - 1

            if change <= -0.10 or change >= 0.30:
                pnl = trade["amount"] * change
                con.execute("""
                UPDATE paper_trades_v2
                SET status='CLOSED', exit=?, pnl=?, closed=?
                WHERE id=?
                """, (
                    current, pnl, datetime.now().isoformat(), int(trade["id"])
                ))
        except Exception:
            pass
    con.commit()

def portfolio_value():
    value = cash_left()
    for _, trade in positions().query("status == 'OPEN'").iterrows():
        try:
            current = price_for(trade["address"])
            value += trade["amount"] * current / trade["entry"] if current else trade["amount"]
        except Exception:
            value += trade["amount"]
    return value

st_autorefresh(interval=10000, key="refresh")

st.subheader("Gemini connection")
if st.button("Test Gemini connection"):
    try:
        answer = ask_gemini("Reply with exactly OK")
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

    with st.spinner("Finding Solana tokens..."):
        try:
            found = get_tokens()
            if not found:
                raise Exception("DexScreener ni našel tokenov.")
            st.session_state.tokens = found

            try:
                analyze_tokens(st.session_state.tokens)
            except Exception as e:
                st.session_state.scan_error = "Gemini analiza ni uspela: " + str(e)
        except Exception as e:
            st.session_state.scan_error = str(e)

if st.session_state.scan_error:
    st.warning(st.session_state.scan_error)

st.subheader("Solana token scanner")

if st.session_state.tokens:
    table_data = pd.DataFrame(st.session_state.tokens)
    st.dataframe(
        table_data[
            ["Name", "Symbol", "Price ($)", "Liquidity ($)",
             "Volume 24h ($)", "Change 24h (%)", "Gemini score"]
        ],
        use_container_width=True,
        hide_index=True
    )

    st.caption("Izberi token spodaj za paper nakup.")
    choices = [
        f"{i + 1}. {t['Symbol']} | {t['Name']}"
        for i, t in enumerate(st.session_state.tokens)
    ]
    selected = st.selectbox("Izberi token", choices)
    index = choices.index(selected)
    token = st.session_state.tokens[index]

    st.write("**Gemini razlog:**", token["Reason"])
    if token["URL"]:
        st.markdown(f"[Odpri na DexScreener]({token['URL']})")

    if st.button("Paper buy $25"):
        if cash_left() < 25:
            st.error("Ni dovolj paper denarja.")
        else:
            con.execute("""
            INSERT INTO paper_trades_v2
            (token,address,entry,amount,status,opened)
            VALUES (?,?,?,?,?,?)
            """, (
                token["Symbol"], token["Address"], token["Price ($)"],
                25, "OPEN", datetime.now().isoformat()
            ))
            con.commit()
            st.success("Paper trade odprt!")
            st.rerun()
else:
    st.info("Pritisni Scan Solana with Gemini AI za skeniranje.")

st.divider()
check_trades()
value = portfolio_value()
cash = cash_left()
pnl = value - STARTING_CASH

st.subheader("Paper trading dashboard")
a, b, c = st.columns(3)
a.metric("Portfolio value", f"${value:.2f}")
b.metric("Available cash", f"${cash:.2f}")
c.metric("Total P/L", f"${pnl:.2f}")

all_positions = positions()
opened = all_positions[all_positions["status"] == "OPEN"]
closed = all_positions[all_positions["status"] == "CLOSED"]

st.subheader("Open positions")
if not opened.empty:
    st.dataframe(
        opened[["token", "entry", "amount", "opened"]],
        use_container_width=True,
        hide_index=True
    )
else:
    st.info("Ni odprtih pozicij.")

st.subheader("Portfolio history")
con.execute(
    "INSERT INTO portfolio_snapshots_v2 (time,value) VALUES (?,?)",
    (datetime.now().strftime("%H:%M:%S"), value)
)
con.commit()

history = pd.read_sql_query(
    "SELECT time,value FROM portfolio_snapshots_v2 ORDER BY id", con
)
if not history.empty:
    st.line_chart(history.set_index("time")["value"])

st.subheader("Closed trades")
if not closed.empty:
    st.dataframe(
        closed[["token", "entry", "exit", "amount", "pnl", "closed"]],
        use_container_width=True,
        hide_index=True
    )
else:
    st.info("Ni zaprtih poslov.")

st.caption(
    "Paper trading only. Stop loss -10% and take profit +30% "
    "are checked every refresh. Execution price is not guaranteed."
)
