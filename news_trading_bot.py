#!/usr/bin/env python3
"""
News-Based Crypto Trading Bot
Supports two modes:
  - Live (default): Connects to Binance Testnet for real order execution
  - Paper (--paper): Simulates trades locally via bot_state.json (no Binance needed)

Usage:
  python news_trading_bot.py          # Live Binance Testnet mode
  python news_trading_bot.py --paper  # Paper trading mode (offline-safe)
"""

import os
import sys
import json
import math
import time
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv

# Force UTF-8 output so emojis don't crash on Windows terminals
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
if sys.stderr.encoding and sys.stderr.encoding.lower() != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8')

load_dotenv()

# ============================================================
# MODE DETECTION
# ============================================================
PAPER_MODE = '--paper' in sys.argv

# ============================================================
# CONFIGURATION
# ============================================================
TRADING_PAIR     = os.getenv('TRADING_PAIR', 'BTCUSDT')
NEWS_QUERY       = 'bitcoin OR cryptocurrency OR crypto market'
STOP_LOSS_PCT    = float(os.getenv('STOP_LOSS_PCT', '2')) / 100
TAKE_PROFIT_PCT  = float(os.getenv('TAKE_PROFIT_PCT', '5')) / 100
MIN_CONFIDENCE   = float(os.getenv('MIN_CONFIDENCE', '0.70'))
INITIAL_BALANCE  = 100_000.0   # Starting paper balance in USD
MAX_SPEND        = float(os.getenv('MAX_SPEND', '1000'))  # Max $ per single trade
STATE_FILE       = 'bot_state.json'

TELEGRAM_TOKEN   = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')

GEMINI_MODEL     = 'gemini-3.1-flash-lite-preview'
GEMINI_API_KEY   = os.getenv('GEMINI_API_KEY', '')

# Only import Binance libraries when NOT in paper mode
if not PAPER_MODE:
    try:
        from binance.spot import Spot
        from binance.error import ClientError
        binance_client = Spot(
            api_key=os.getenv('BINANCE_API_KEY'),
            api_secret=os.getenv('BINANCE_SECRET_KEY'),
            base_url='https://testnet.binance.vision',
            timeout=10
        )
    except ImportError:
        print("ERROR: 'binance-connector' package not installed. Run: pip install binance-connector")
        sys.exit(1)

# ============================================================
# SHARED HELPERS
# ============================================================
def now_utc() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')

def send_telegram(message: str):
    """Print to console and optionally send to Telegram."""
    print(message)
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID):
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    for attempt in range(3):
        try:
            resp = requests.post(url, json=payload, timeout=5)
            if resp.status_code == 200:
                return
        except Exception as e:
            if attempt == 2:
                print(f"[Telegram] Failed after 3 attempts: {e}")
            time.sleep(1)

def get_crypto_news() -> list[tuple[str, str]]:
    """Fetch latest crypto news from NewsAPI."""
    api_key = os.getenv('NEWS_API_KEY', '')
    if not api_key:
        print("[News] NEWS_API_KEY not set — skipping news fetch.")
        return []
    url = 'https://newsapi.org/v2/everything'
    params = {
        'q': NEWS_QUERY,
        'language': 'en',
        'sortBy': 'publishedAt',
        'pageSize': 5,
        'apiKey': api_key
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get('status') == 'ok':
            articles = data.get('articles', [])
            return [(a['title'], a.get('description') or '') for a in articles if a.get('title')]
    except requests.RequestException as e:
        print(f"[News] Request failed: {e}")
    except Exception as e:
        print(f"[News] Unexpected error: {e}")
    return []

def analyze_news_with_gemini(articles: list[tuple[str, str]]) -> dict | None:
    """Send news headlines to Gemini and get a structured trade signal."""
    if not GEMINI_API_KEY:
        print("[Gemini] GEMINI_API_KEY not set.")
        return None
    if not articles:
        print("[Gemini] No articles to analyze.")
        return None

    news_text = "\n".join([f"- {t}: {d}" for t, d in articles])
    prompt = f"""You are an expert crypto trading analyst. Analyze these recent news headlines:

{news_text}

Based ONLY on the news above, respond with ONLY this exact JSON (no markdown):
{{
    "sentiment": "bullish" | "bearish" | "neutral",
    "confidence": <float 0.0-1.0>,
    "reasoning": "<brief one-sentence explanation>",
    "action": "buy" | "sell" | "hold"
}}"""

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"}
    }
    try:
        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        result = resp.json()

        if 'error' in result:
            print(f"[Gemini] API Error: {result['error'].get('message', 'Unknown error')}")
            return None

        candidates = result.get('candidates', [])
        if not candidates:
            print("[Gemini] No candidates returned.")
            return None

        text = candidates[0]['content']['parts'][0]['text']
        text = text.replace("```json", "").replace("```", "").strip()
        signal = json.loads(text)

        # Validate required fields
        required = {'sentiment', 'confidence', 'reasoning', 'action'}
        if not required.issubset(signal.keys()):
            print(f"[Gemini] Incomplete signal: {signal}")
            return None

        return signal
    except json.JSONDecodeError as e:
        print(f"[Gemini] JSON parse error: {e} | Raw: {text!r}")
    except requests.RequestException as e:
        print(f"[Gemini] Request failed: {e}")
    except Exception as e:
        print(f"[Gemini] Unexpected error: {e}")
    return None

# ============================================================
# PAPER TRADING MODE
# ============================================================
def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[State] Could not load state file: {e}. Starting fresh.")
    return {
        'balance': INITIAL_BALANCE,
        'position': 0.0,
        'entry_price': 0.0,
        'stop_loss': 0.0,
        'take_profit': 0.0,
        'total_trades': 0,
        'winning_trades': 0,
        'losing_trades': 0
    }

def save_state(state: dict):
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=4)
    except OSError as e:
        print(f"[State] Could not save state: {e}")

def get_btc_price_from_web() -> float | None:
    """Fetch live BTC price from a public API (no auth needed)."""
    sources = [
        ('https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT',
         lambda r: float(r['price'])),
        ('https://api.coinbase.com/v2/prices/BTC-USD/spot',
         lambda r: float(r['data']['amount'])),
        ('https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd',
         lambda r: float(r['bitcoin']['usd'])),
    ]
    for url, extractor in sources:
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            price = extractor(resp.json())
            if price and price > 0:
                return price
        except Exception:
            continue
    return None

def format_paper_portfolio(state: dict, current_price: float) -> str:
    total_val = state['balance'] + (state['position'] * current_price)
    pnl = total_val - INITIAL_BALANCE
    pnl_icon = '🟢' if pnl >= 0 else '🔴'
    return (
        f"💼 <b>PAPER PORTFOLIO</b>\n"
        f"Available USD: ${state['balance']:,.2f}\n"
        f"BTC Position:  {state['position']:.6f} BTC\n"
        f"Total Equity:  ${total_val:,.2f}\n"
        f"Net PNL: {pnl_icon} ${pnl:,.2f}\n"
        f"Trades: {state['total_trades']} | "
        f"Wins: {state['winning_trades']} | "
        f"Losses: {state['losing_trades']}"
    )

def run_paper_mode():
    send_telegram("📄 <b>PAPER TRADING MODE</b>\nRunning without Binance connection...")

    current_price = get_btc_price_from_web()
    if not current_price:
        send_telegram("❌ Could not fetch live BTC price. Check your internet connection.")
        return

    state = load_state()

    print(
        f"🤖 <b>Bot Wake Up</b>\n"
        f"Time: {now_utc()}\n"
        f"Mode: PAPER TRADING\n"
        f"BTC Price: ${current_price:,.2f}"
    )

    # --- Check SL/TP on existing position ---
    if state['position'] > 0:
        hit_tp = current_price >= state['take_profit']
        hit_sl = current_price <= state['stop_loss']

        if hit_tp or hit_sl:
            reason = "🎯 TAKE PROFIT" if hit_tp else "🛑 STOP LOSS"
            proceeds = state['position'] * current_price
            profit = proceeds - (state['position'] * state['entry_price'])

            state['balance'] += proceeds
            state['total_trades'] += 1
            if profit > 0:
                state['winning_trades'] += 1
            else:
                state['losing_trades'] += 1

            # Reset position
            state['position'] = 0.0
            state['entry_price'] = 0.0
            state['stop_loss'] = 0.0
            state['take_profit'] = 0.0

            send_telegram(
                f"{reason} HIT!\n"
                f"Sold BTC at ${current_price:,.2f}\n"
                f"P&L: {'🟢' if profit >= 0 else '🔴'} ${profit:,.2f}\n\n"
                f"{format_paper_portfolio(state, current_price)}"
            )
            save_state(state)
            return

        # Still holding — no new trade
        print(
            f"⏳ <b>Holding Position</b>\n"
            f"Current:     ${current_price:,.2f}\n"
            f"Take Profit: ${state['take_profit']:,.2f}\n"
            f"Stop Loss:   ${state['stop_loss']:,.2f}\n\n"
            f"{format_paper_portfolio(state, current_price)}"
        )
        return

    # --- No position: fetch news and get AI signal ---
    articles = get_crypto_news()
    if not articles:
        send_telegram("⚠️ No news articles found. Skipping this cycle.")
        return

    signal = analyze_news_with_gemini(articles)
    if not signal:
        send_telegram("⚠️ No valid signal from Gemini. Skipping this cycle.")
        return

    action     = signal.get('action', 'hold').lower()
    confidence = float(signal.get('confidence', 0))

    print(
        f"📻 <b>AI SIGNAL</b>\n"
        f"Sentiment:  {signal.get('sentiment', 'N/A').upper()}\n"
        f"Action:     {action.upper()}\n"
        f"Confidence: {confidence:.0%}\n"
        f"Reasoning:  {signal.get('reasoning', 'N/A')}"
    )

    if action == 'buy' and confidence >= MIN_CONFIDENCE:
        spend = min(state['balance'], MAX_SPEND)
        if spend < 10:
            send_telegram("❌ Insufficient paper balance to trade.")
            return

        quantity = spend / current_price
        state['balance']    -= spend
        state['position']    = quantity
        state['entry_price'] = current_price
        state['stop_loss']   = round(current_price * (1 - STOP_LOSS_PCT), 2)
        state['take_profit'] = round(current_price * (1 + TAKE_PROFIT_PCT), 2)

        send_telegram(
            f"✅ <b>PAPER BUY EXECUTED</b>\n"
            f"Bought {quantity:.6f} BTC at ${current_price:,.2f}\n\n"
            f"🛡️ <b>OCO Protection Set</b>\n"
            f"Stop Loss:   ${state['stop_loss']:,.2f} (-{STOP_LOSS_PCT:.0%})\n"
            f"Take Profit: ${state['take_profit']:,.2f} (+{TAKE_PROFIT_PCT:.0%})\n\n"
            f"{format_paper_portfolio(state, current_price)}"
        )
        save_state(state)

    elif action == 'sell' and state['position'] > 0:
        proceeds = state['position'] * current_price
        profit   = proceeds - (state['position'] * state['entry_price'])
        state['balance'] += proceeds
        state['total_trades'] += 1
        if profit > 0:
            state['winning_trades'] += 1
        else:
            state['losing_trades'] += 1
        state['position']    = 0.0
        state['entry_price'] = 0.0
        state['stop_loss']   = 0.0
        state['take_profit'] = 0.0
        send_telegram(
            f"📤 <b>PAPER SELL EXECUTED (AI Signal)</b>\n"
            f"Sold at ${current_price:,.2f}\n"
            f"P&L: {'🟢' if profit >= 0 else '🔴'} ${profit:,.2f}\n\n"
            f"{format_paper_portfolio(state, current_price)}"
        )
        save_state(state)

    else:
        print(
            f"⏸️ <b>HOLD</b> — No trade taken.\n"
            f"Action: {action.upper()} | Confidence: {confidence:.0%}\n\n"
            f"{format_paper_portfolio(state, current_price)}"
        )

# ============================================================
# LIVE BINANCE TESTNET MODE
# ============================================================
def format_live_portfolio(current_price: float) -> str:
    try:
        account = binance_client.account()
        usdt = float(next((b['free'] for b in account['balances'] if b['asset'] == 'USDT'), 0))
        btc  = float(next((b['free'] for b in account['balances'] if b['asset'] == 'BTC'), 0))
        total = usdt + (btc * current_price)
        return (
            f"💼 <b>BINANCE TESTNET STATUS</b>\n"
            f"Available USDT: ${usdt:,.2f}\n"
            f"BTC Holdings:   {btc:.6f}\n"
            f"Total Equity:   ${total:,.2f}"
        )
    except ClientError as e:
        return f"Error fetching portfolio: {e.error_message}"
    except Exception as e:
        return f"Error fetching portfolio: {e}"

def run_live_mode():
    try:
        price_data    = binance_client.ticker_price(TRADING_PAIR)
        current_price = float(price_data['price'])
    except ClientError as e:
        send_telegram(f"❌ Binance API Error: {e.error_message}")
        return
    except Exception as e:
        send_telegram(
            f"❌ <b>Cannot reach Binance Testnet</b>\n"
            f"Server may be temporarily down. Will retry next run.\n"
            f"Error: {type(e).__name__}: {e}"
        )
        return

    print(
        f"🤖 <b>Bot Wake Up</b>\n"
        f"Time: {now_utc()}\n"
        f"Mode: LIVE TESTNET\n"
        f"BTC Price: ${current_price:,.2f}"
    )

    # Check for open orders — don't double-trade
    try:
        open_orders = binance_client.get_open_orders(symbol=TRADING_PAIR)
        if open_orders:
            print(
                f"⏳ {len(open_orders)} open OCO order(s) still active. Holding.\n\n"
                f"{format_live_portfolio(current_price)}"
            )
            return
    except ClientError:
        pass  # Non-fatal — proceed to signal check

    articles = get_crypto_news()
    if not articles:
        send_telegram("⚠️ No news articles found. Skipping this cycle.")
        return

    signal = analyze_news_with_gemini(articles)
    if not signal:
        send_telegram("⚠️ No valid signal from Gemini. Skipping this cycle.")
        return

    action     = signal.get('action', 'hold').lower()
    confidence = float(signal.get('confidence', 0))

    print(
        f"📻 <b>AI SIGNAL</b>\n"
        f"Sentiment:  {signal.get('sentiment', 'N/A').upper()}\n"
        f"Action:     {action.upper()}\n"
        f"Confidence: {confidence:.0%}\n"
        f"Reasoning:  {signal.get('reasoning', 'N/A')}"
    )

    if action == 'buy' and confidence >= MIN_CONFIDENCE:
        try:
            account = binance_client.account()
            usdt    = float(next((b['free'] for b in account['balances'] if b['asset'] == 'USDT'), 0))
            spend   = min(usdt, MAX_SPEND)

            if spend < 10:
                send_telegram("❌ Not enough USDT on testnet.")
                return

            buy_qty   = round(spend / current_price, 5)
            buy_order = binance_client.new_order(
                symbol=TRADING_PAIR, side='BUY', type='MARKET', quantity=buy_qty
            )
            executed_qty = float(buy_order.get('executedQty', buy_qty))

            sl_price = math.floor(current_price * (1 - STOP_LOSS_PCT) * 100) / 100
            tp_price = math.ceil(current_price  * (1 + TAKE_PROFIT_PCT) * 100) / 100

            binance_client.new_oco_order(
                symbol=TRADING_PAIR,
                side='SELL',
                quantity=executed_qty,
                price=str(tp_price),
                stopPrice=str(sl_price),
                stopLimitPrice=str(sl_price),
                stopLimitTimeInForce='GTC'
            )

            send_telegram(
                f"✅ <b>TESTNET BUY EXECUTED</b>\n"
                f"Bought {executed_qty} BTC at ~${current_price:,.2f}\n\n"
                f"🛡️ <b>Live OCO Placed on Binance</b>\n"
                f"Stop Loss:   ${sl_price:,.2f} (-{STOP_LOSS_PCT:.0%})\n"
                f"Take Profit: ${tp_price:,.2f} (+{TAKE_PROFIT_PCT:.0%})\n\n"
                f"{format_live_portfolio(current_price)}"
            )
        except ClientError as e:
            send_telegram(f"❌ Order failed: {e.error_message}")
        except Exception as e:
            send_telegram(f"❌ Unexpected error executing order: {e}")

    elif action == 'hold' or confidence < MIN_CONFIDENCE:
        print(
            f"⏸️ <b>HOLD</b> — No trade taken.\n"
            f"Action: {action.upper()} | Confidence: {confidence:.0%}\n\n"
            f"{format_live_portfolio(current_price)}"
        )

# ============================================================
# ENTRY POINT
# ============================================================
if __name__ == "__main__":
    if PAPER_MODE:
        run_paper_mode()
    else:
        run_live_mode()