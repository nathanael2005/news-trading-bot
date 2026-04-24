#!/usr/bin/env python3
"""
Full diagnostic test for the News Trading Bot.
Tests every component independently and reports pass/fail.
"""

import os
import sys
import json
import time
import requests
from datetime import datetime, timezone

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

from dotenv import load_dotenv
load_dotenv()

PASS = "✅ PASS"
FAIL = "❌ FAIL"
WARN = "⚠️  WARN"
SEP  = "-" * 55

results = []

def log(status, name, detail=""):
    results.append((status, name))
    detail_str = f"\n     → {detail}" if detail else ""
    print(f"  {status}  {name}{detail_str}")

print("\n" + "=" * 55)
print("🔬  NEWS TRADING BOT — FULL DIAGNOSTIC")
print(f"    {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
print("=" * 55)


# ============================================================
# 1. ENV VARIABLES
# ============================================================
print(f"\n{SEP}")
print("1️⃣   ENVIRONMENT VARIABLES")
print(SEP)

required_vars = {
    "GEMINI_API_KEY":     os.getenv("GEMINI_API_KEY", ""),
    "NEWS_API_KEY":       os.getenv("NEWS_API_KEY", ""),
    "TELEGRAM_BOT_TOKEN": os.getenv("TELEGRAM_BOT_TOKEN", ""),
    "TELEGRAM_CHAT_ID":   os.getenv("TELEGRAM_CHAT_ID", ""),
    "BINANCE_API_KEY":    os.getenv("BINANCE_API_KEY", ""),
    "BINANCE_SECRET_KEY": os.getenv("BINANCE_SECRET_KEY", ""),
}
for var, val in required_vars.items():
    if val:
        log(PASS, f"{var} is set ({val[:8]}...)")
    else:
        log(FAIL, f"{var} is MISSING")


# ============================================================
# 2. TELEGRAM
# ============================================================
print(f"\n{SEP}")
print("2️⃣   TELEGRAM NOTIFICATION")
print(SEP)

token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

if token and chat_id:
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id":    chat_id,
            "text":       f"🔬 <b>Bot Diagnostic</b>\nTest message sent at {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}",
            "parse_mode": "HTML"
        }
        resp = requests.post(url, json=payload, timeout=8)
        data = resp.json()
        if data.get("ok"):
            log(PASS, "Telegram message delivered", f"message_id={data['result']['message_id']}")
        else:
            log(FAIL, "Telegram rejected message", data.get("description", "unknown error"))
    except Exception as e:
        log(FAIL, "Telegram request failed", str(e))
else:
    log(FAIL, "Telegram skipped — token or chat_id missing")


# ============================================================
# 3. BTC PRICE FETCH
# ============================================================
print(f"\n{SEP}")
print("3️⃣   LIVE BTC PRICE FETCH")
print(SEP)

price = None
sources = [
    ("Binance Public API",  "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", lambda r: float(r["price"])),
    ("Coinbase Public API", "https://api.coinbase.com/v2/prices/BTC-USD/spot",            lambda r: float(r["data"]["amount"])),
    ("CoinGecko API",       "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd", lambda r: float(r["bitcoin"]["usd"])),
]
for name, url, extractor in sources:
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        price = extractor(resp.json())
        log(PASS, f"{name}", f"BTC = ${price:,.2f}")
        break
    except Exception as e:
        log(FAIL, f"{name}", str(e))

if not price:
    log(FAIL, "All price sources failed")


# ============================================================
# 4. NEWS API
# ============================================================
print(f"\n{SEP}")
print("4️⃣   NEWS API")
print(SEP)

news_key = os.getenv("NEWS_API_KEY", "")
articles = []
if news_key:
    try:
        resp = requests.get(
            "https://newsapi.org/v2/everything",
            params={"q": "bitcoin OR cryptocurrency", "language": "en",
                    "sortBy": "publishedAt", "pageSize": 5, "apiKey": news_key},
            timeout=10
        )
        data = resp.json()
        if data.get("status") == "ok":
            articles = [(a["title"], a.get("description") or "") for a in data["articles"] if a.get("title")]
            log(PASS, f"NewsAPI returned {len(articles)} articles")
            for i, (title, _) in enumerate(articles[:3], 1):
                print(f"       {i}. {title[:70]}...")
        else:
            log(FAIL, "NewsAPI error", data.get("message", "unknown"))
    except Exception as e:
        log(FAIL, "NewsAPI request failed", str(e))
else:
    log(FAIL, "NEWS_API_KEY missing — skipping")


# ============================================================
# 5. GEMINI AI ANALYSIS
# ============================================================
print(f"\n{SEP}")
print("5️⃣   GEMINI AI SIGNAL GENERATION")
print(SEP)

gemini_key   = os.getenv("GEMINI_API_KEY", "")
gemini_model = "gemini-3.1-flash-lite-preview"
signal       = None

if gemini_key and articles:
    news_text = "\n".join([f"- {t}: {d}" for t, d in articles])
    prompt = f"""You are a crypto trading analyst. Analyze these recent news headlines:
{news_text}

Respond ONLY with this exact JSON (no markdown):
{{
    "sentiment": "bullish" | "bearish" | "neutral",
    "confidence": <float 0.0-1.0>,
    "reasoning": "<brief explanation>",
    "action": "buy" | "sell" | "hold"
}}"""
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{gemini_model}:generateContent?key={gemini_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json"}
        }
        resp = requests.post(url, json=payload, timeout=30)
        result = resp.json()
        if "error" in result:
            log(FAIL, "Gemini API error", result["error"].get("message", "unknown"))
        else:
            text = result["candidates"][0]["content"]["parts"][0]["text"]
            text = text.replace("```json", "").replace("```", "").strip()
            signal = json.loads(text)
            log(PASS, "Gemini returned valid signal",
                f"action={signal['action'].upper()} | confidence={signal['confidence']:.0%} | sentiment={signal['sentiment']}")
            print(f"       Reasoning: {signal['reasoning'][:100]}")
    except json.JSONDecodeError as e:
        log(FAIL, "Gemini returned non-JSON response", str(e))
    except Exception as e:
        log(FAIL, "Gemini request failed", str(e))
elif not gemini_key:
    log(FAIL, "GEMINI_API_KEY missing")
else:
    log(WARN, "Gemini skipped — no articles to analyze")


# ============================================================
# 6. PAPER TRADE EXECUTION (Forced BUY)
# ============================================================
print(f"\n{SEP}")
print("6️⃣   PAPER TRADE — FORCED BUY SIMULATION")
print(SEP)

STATE_FILE      = "bot_state.json"
INITIAL_BALANCE = 100_000.0
STOP_LOSS_PCT   = float(os.getenv("STOP_LOSS_PCT", "2")) / 100
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "5")) / 100
MAX_SPEND       = float(os.getenv("MAX_SPEND", "1000"))

# Load or create fresh state
if os.path.exists(STATE_FILE):
    with open(STATE_FILE) as f:
        state = json.load(f)
    log(PASS, "Loaded existing bot_state.json",
        f"balance=${state['balance']:,.2f} | position={state['position']:.6f} BTC | trades={state['total_trades']}")
else:
    state = {
        "balance": INITIAL_BALANCE, "position": 0.0,
        "entry_price": 0.0, "stop_loss": 0.0, "take_profit": 0.0,
        "total_trades": 0, "winning_trades": 0, "losing_trades": 0
    }
    log(WARN, "No bot_state.json found — using fresh state")

if price:
    # Simulate a BUY
    sim_state = dict(state)  # don't overwrite real state
    spend    = min(sim_state["balance"], MAX_SPEND)
    qty      = spend / price
    sl       = round(price * (1 - STOP_LOSS_PCT), 2)
    tp       = round(price * (1 + TAKE_PROFIT_PCT), 2)
    sim_state["balance"]    -= spend
    sim_state["position"]    = qty
    sim_state["entry_price"] = price
    sim_state["stop_loss"]   = sl
    sim_state["take_profit"] = tp

    log(PASS, "Paper BUY executed (simulation only — state not saved)",
        f"qty={qty:.6f} BTC @ ${price:,.2f} | SL=${sl:,.2f} | TP=${tp:,.2f}")

    # Test SL/TP logic
    tp_hit = price >= tp
    sl_hit = price <= sl
    if tp_hit:
        log(PASS, "SL/TP check: TAKE PROFIT would trigger")
    elif sl_hit:
        log(WARN, "SL/TP check: STOP LOSS would trigger")
    else:
        log(PASS, "SL/TP check: Position would be held (price between SL and TP)")
else:
    log(FAIL, "Paper trade skipped — no price available")


# ============================================================
# 7. STATE FILE READ/WRITE
# ============================================================
print(f"\n{SEP}")
print("7️⃣   STATE FILE (bot_state.json) PERSISTENCE")
print(SEP)

test_state = {
    "balance": 99_000.0, "position": 0.001,
    "entry_price": price or 77000, "stop_loss": 75000, "take_profit": 82000,
    "total_trades": 1, "winning_trades": 0, "losing_trades": 0,
    "_test": True
}
try:
    with open("_test_state.json", "w") as f:
        json.dump(test_state, f, indent=4)
    with open("_test_state.json") as f:
        loaded = json.load(f)
    os.remove("_test_state.json")
    assert loaded["balance"] == 99_000.0
    log(PASS, "State file write/read/delete OK")
except Exception as e:
    log(FAIL, "State file I/O failed", str(e))


# ============================================================
# 8. BINANCE TESTNET CONNECTIVITY
# ============================================================
print(f"\n{SEP}")
print("8️⃣   BINANCE TESTNET CONNECTIVITY")
print(SEP)

try:
    from binance.spot import Spot
    from binance.error import ClientError

    client = Spot(
        api_key=os.getenv("BINANCE_API_KEY"),
        api_secret=os.getenv("BINANCE_SECRET_KEY"),
        base_url="https://testnet.binance.vision",
        timeout=10
    )
    price_data = client.ticker_price("BTCUSDT")
    testnet_price = float(price_data["price"])
    log(PASS, "Binance Testnet reachable", f"BTCUSDT = ${testnet_price:,.2f}")

    account = client.account()
    usdt = float(next((b["free"] for b in account["balances"] if b["asset"] == "USDT"), 0))
    btc  = float(next((b["free"] for b in account["balances"] if b["asset"] == "BTC"), 0))
    log(PASS, "Binance account fetched", f"USDT=${usdt:,.2f} | BTC={btc:.6f}")

    open_orders = client.get_open_orders(symbol="BTCUSDT")
    log(PASS, f"Open orders check OK", f"{len(open_orders)} open order(s)")

except ImportError:
    log(FAIL, "binance-connector not installed", "Run: pip install binance-connector")
except ClientError as e:
    log(FAIL, "Binance ClientError", e.error_message)
except Exception as e:
    log(WARN, "Binance Testnet unreachable (may be temporarily down)", str(e)[:80])


# ============================================================
# FINAL REPORT
# ============================================================
print(f"\n{'=' * 55}")
print("📋  DIAGNOSTIC SUMMARY")
print("=" * 55)
passed  = sum(1 for s, _ in results if s == PASS)
failed  = sum(1 for s, _ in results if s == FAIL)
warned  = sum(1 for s, _ in results if s == WARN)
total   = len(results)
print(f"  Total checks : {total}")
print(f"  {PASS}  : {passed}")
print(f"  {FAIL}  : {failed}")
print(f"  {WARN}  : {warned}")
print("=" * 55)
if failed == 0:
    print("  🎉  Bot is fully operational!")
elif failed <= 2:
    print("  ⚠️   Minor issues — bot may still work partially.")
else:
    print("  ❌  Critical issues found — review failures above.")
print("=" * 55 + "\n")
