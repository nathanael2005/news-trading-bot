#!/usr/bin/env python3
"""
Gold (XAUUSD) News-Based Paper Trading Bot
Fetches live gold prices from free public APIs (no broker needed).
Uses Gemini AI to analyze gold/commodity news and generate trade signals.
Tracks portfolio state in gold_state.json and sends Telegram alerts.

Usage:
  python gold_trading_bot.py        # Run one cycle
  python gold_trading_bot.py --test # Force a BUY signal for testing
"""

import os
import sys
import json
import time
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv

# Force UTF-8 on Windows terminals
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
if sys.stderr.encoding and sys.stderr.encoding.lower() != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8')

load_dotenv()

# ============================================================
# CONFIGURATION
# ============================================================
GEMINI_MODEL      = 'gemini-3.1-flash-lite-preview'
GEMINI_API_KEY    = os.getenv('GEMINI_API_KEY', '')
TELEGRAM_TOKEN    = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID  = os.getenv('TELEGRAM_CHAT_ID', '')
NEWS_API_KEY      = os.getenv('NEWS_API_KEY', '')

STOP_LOSS_PCT     = float(os.getenv('GOLD_STOP_LOSS_PCT',    '1.5')) / 100   # Gold moves slower — tighter SL
TAKE_PROFIT_PCT   = float(os.getenv('GOLD_TAKE_PROFIT_PCT',  '3.0')) / 100
MIN_CONFIDENCE    = float(os.getenv('GOLD_MIN_CONFIDENCE',   '0.65'))        # Slightly lower threshold for gold
MAX_SPEND         = float(os.getenv('GOLD_MAX_SPEND',        '5000'))        # Gold is ~$3300/oz, spend more per trade
INITIAL_BALANCE   = float(os.getenv('GOLD_INITIAL_BALANCE',  '100000'))

NEWS_QUERY = (
    '"gold price" OR "XAUUSD" OR "Federal Reserve" OR "FOMC" OR '
    '"Fed rate" OR "interest rates" OR "US CPI" OR "inflation data" OR '
    '"non-farm payrolls" OR "NFP" OR "Treasury yields" OR '
    '"US dollar index" OR "DXY" OR "safe haven" OR '
    '"geopolitical risk" OR "recession" OR "gold demand"'
)
STATE_FILE        = 'gold_state.json'
TEST_MODE         = '--test' in sys.argv

# ============================================================
# HELPERS
# ============================================================
def now_utc() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')

def send_telegram(message: str):
    """Print to console and deliver to Telegram."""
    print(message)
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID):
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    for attempt in range(3):
        try:
            resp = requests.post(url, json=payload, timeout=5)
            if resp.status_code == 200:
                return
        except Exception as e:
            if attempt == 2:
                print(f"[Telegram] Failed after 3 attempts: {e}")
            time.sleep(1)

# ============================================================
# GOLD PRICE FETCHING
# ============================================================
def get_gold_price() -> float | None:
    """
    Try multiple free public sources for XAUUSD spot price.
    Returns price in USD per troy ounce.
    """
    # Source 1: metals.live — free, no auth required
    try:
        resp = requests.get('https://metals.live/api/latest', timeout=10)
        resp.raise_for_status()
        data = resp.json()
        # Returns a list; find gold entry
        if isinstance(data, list):
            for item in data:
                if item.get('metal', '').lower() in ('gold', 'xau'):
                    price = float(item.get('price', 0))
                    if price > 0:
                        print(f"[Price] metals.live → ${price:,.2f}/oz")
                        return price
        elif isinstance(data, dict):
            price = float(data.get('gold', data.get('XAU', 0)))
            if price > 0:
                print(f"[Price] metals.live → ${price:,.2f}/oz")
                return price
    except Exception as e:
        print(f"[Price] metals.live failed: {e}")

    # Source 2: Yahoo Finance — GC=F (Gold Futures, very close to spot)
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        resp = requests.get(
            'https://query1.finance.yahoo.com/v8/finance/chart/GC%3DF',
            headers=headers, timeout=10
        )
        resp.raise_for_status()
        data  = resp.json()
        price = float(data['chart']['result'][0]['meta']['regularMarketPrice'])
        if price > 0:
            print(f"[Price] Yahoo Finance (GC=F) → ${price:,.2f}/oz")
            return price
    except Exception as e:
        print(f"[Price] Yahoo Finance failed: {e}")

    # Source 3: Gold-API.com (free tier, 100 req/month — no key needed for basic)
    try:
        resp = requests.get(
            'https://api.gold-api.com/price/XAU',
            timeout=10
        )
        resp.raise_for_status()
        data  = resp.json()
        price = float(data.get('price', 0))
        if price > 0:
            print(f"[Price] gold-api.com → ${price:,.2f}/oz")
            return price
    except Exception as e:
        print(f"[Price] gold-api.com failed: {e}")

    # Source 4: Frankfurter (currency-based gold approximation — last resort)
    try:
        resp = requests.get(
            'https://api.frankfurter.app/latest?from=XAU&to=USD',
            timeout=10
        )
        resp.raise_for_status()
        price = float(resp.json()['rates']['USD'])
        if price > 500:   # sanity check — gold should be > $500
            print(f"[Price] Frankfurter → ${price:,.2f}/oz")
            return price
    except Exception as e:
        print(f"[Price] Frankfurter failed: {e}")

    return None

# ============================================================
# NEWS FETCHING
# ============================================================
def get_gold_news() -> list[tuple[str, str]]:
    """Fetch latest gold & macro news from NewsAPI."""
    if not NEWS_API_KEY:
        print("[News] NEWS_API_KEY not set — skipping.")
        return []
    try:
        resp = requests.get(
            'https://newsapi.org/v2/everything',
            params={
                'q':        NEWS_QUERY,
                'language': 'en',
                'sortBy':   'publishedAt',
                'pageSize': 5,
                'apiKey':   NEWS_API_KEY
            },
            timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get('status') == 'ok':
            articles = data.get('articles', [])
            result = [(a['title'], a.get('description') or '') for a in articles if a.get('title')]
            print(f"[News] Fetched {len(result)} articles.")
            return result
        else:
            print(f"[News] API error: {data.get('message')}")
    except Exception as e:
        print(f"[News] Failed: {e}")
    return []

# ============================================================
# GEMINI AI ANALYSIS
# ============================================================
def analyze_gold_news(articles: list[tuple[str, str]]) -> dict | None:
    """Send gold/macro news to Gemini and get a structured trade signal."""
    if not GEMINI_API_KEY:
        print("[Gemini] No API key.")
        return None
    if not articles:
        print("[Gemini] No articles to analyze.")
        return None

    news_text = "\n".join([f"- {t}: {d}" for t, d in articles])
    prompt = f"""You are a professional gold (XAUUSD) trading analyst with deep knowledge of macro drivers.

Analyze these recent news headlines:
{news_text}

GOLD PRICE DRIVER RULES (apply these strictly):
- BULLISH (buy): Fed rate cuts, dovish Fed/FOMC, high CPI/inflation, weak USD/DXY falling,
  geopolitical wars or crises, recession fears, falling Treasury yields, strong safe-haven demand,
  China/India gold buying, sanctions, banking crises
- BEARISH (sell): Fed rate hikes, hawkish Fed, strong NFP jobs data, rising USD/DXY,
  rising Treasury yields, risk-on market sentiment, low inflation, economic optimism
- NEUTRAL (hold): Mixed signals, unrelated news, unclear macro direction

Based ONLY on the news above, respond with ONLY this exact JSON (no markdown, no extra text):
{{
    "sentiment": "bullish" | "bearish" | "neutral",
    "confidence": <float 0.0-1.0>,
    "reasoning": "<one concise sentence explaining the key driver>",
    "action": "buy" | "sell" | "hold",
    "key_driver": "<main macro factor: e.g. Fed_dovish | CPI_high | USD_weak | Geopolitical | Yields_falling | Risk_off>"
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
            print(f"[Gemini] Error: {result['error'].get('message')}")
            return None

        text = result['candidates'][0]['content']['parts'][0]['text']
        text = text.replace("```json", "").replace("```", "").strip()
        signal = json.loads(text)

        required = {'sentiment', 'confidence', 'reasoning', 'action'}
        if not required.issubset(signal.keys()):
            print(f"[Gemini] Incomplete signal: {signal}")
            return None

        return signal
    except json.JSONDecodeError as e:
        print(f"[Gemini] JSON parse error: {e}")
    except Exception as e:
        print(f"[Gemini] Request failed: {e}")
    return None

# ============================================================
# STATE MANAGEMENT
# ============================================================
def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"[State] Load failed: {e}. Starting fresh.")
    return {
        'balance':        INITIAL_BALANCE,
        'position_oz':    0.0,      # Troy ounces of gold held
        'entry_price':    0.0,
        'stop_loss':      0.0,
        'take_profit':    0.0,
        'total_trades':   0,
        'winning_trades': 0,
        'losing_trades':  0,
        'total_pnl':      0.0
    }

def save_state(state: dict):
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=4)
    except Exception as e:
        print(f"[State] Save failed: {e}")

def format_portfolio(state: dict, current_price: float) -> str:
    position_value = state['position_oz'] * current_price
    total_equity   = state['balance'] + position_value
    net_pnl        = total_equity - INITIAL_BALANCE
    pnl_icon       = '🟢' if net_pnl >= 0 else '🔴'
    win_rate       = (
        f"{state['winning_trades'] / state['total_trades']:.0%}"
        if state['total_trades'] > 0 else "N/A"
    )
    return (
        f"💰 <b>GOLD PAPER PORTFOLIO</b>\n"
        f"Available USD:   ${state['balance']:,.2f}\n"
        f"Gold Position:   {state['position_oz']:.4f} oz\n"
        f"Position Value:  ${position_value:,.2f}\n"
        f"Total Equity:    ${total_equity:,.2f}\n"
        f"Net PNL: {pnl_icon}  ${net_pnl:,.2f}\n"
        f"Trades: {state['total_trades']} | W: {state['winning_trades']} | L: {state['losing_trades']} | WR: {win_rate}"
    )

# ============================================================
# MAIN BOT LOGIC
# ============================================================
def run():
    print(
        f"🏅 <b>GOLD PAPER TRADING BOT</b>\n"
        f"Time: {now_utc()}\n"
        f"Asset: XAUUSD (Gold Spot)\n"
        f"SL: {STOP_LOSS_PCT:.1%} | TP: {TAKE_PROFIT_PCT:.1%} | Min Confidence: {MIN_CONFIDENCE:.0%}"
    )

    # --- Fetch live gold price ---
    current_price = get_gold_price()
    if not current_price:
        send_telegram("❌ Could not fetch live gold price. Check your internet connection.")
        return

    print(f"📊 <b>Live Gold Price:</b> ${current_price:,.2f} / oz")

    state = load_state()

    # -------------------------
    # CHECK EXISTING POSITION
    # -------------------------
    if state['position_oz'] > 0:
        hit_tp = current_price >= state['take_profit']
        hit_sl = current_price <= state['stop_loss']

        if hit_tp or hit_sl:
            reason   = "🎯 TAKE PROFIT" if hit_tp else "🛑 STOP LOSS"
            proceeds = state['position_oz'] * current_price
            cost     = state['position_oz'] * state['entry_price']
            profit   = proceeds - cost
            pnl_icon = '🟢' if profit >= 0 else '🔴'

            state['balance']      += proceeds
            state['total_trades'] += 1
            state['total_pnl']    += profit
            if profit > 0:
                state['winning_trades'] += 1
            else:
                state['losing_trades'] += 1

            state['position_oz'] = 0.0
            state['entry_price'] = 0.0
            state['stop_loss']   = 0.0
            state['take_profit'] = 0.0

            send_telegram(
                f"{reason} HIT!\n"
                f"Sold {state.get('_last_qty', 0):.4f} oz gold at ${current_price:,.2f}\n"
                f"P&L: {pnl_icon} ${profit:,.2f}\n"
                f"Cumulative PNL: ${state['total_pnl']:,.2f}\n\n"
                f"{format_portfolio(state, current_price)}"
            )
            save_state(state)
            return

        # Still holding
        unrealized = (current_price - state['entry_price']) * state['position_oz']
        unr_icon   = '🟢' if unrealized >= 0 else '🔴'
        print(
            f"⏳ <b>Holding Gold Position</b>\n"
            f"Entry:       ${state['entry_price']:,.2f}\n"
            f"Current:     ${current_price:,.2f}\n"
            f"Take Profit: ${state['take_profit']:,.2f}\n"
            f"Stop Loss:   ${state['stop_loss']:,.2f}\n"
            f"Unrealized:  {unr_icon} ${unrealized:,.2f}\n\n"
            f"{format_portfolio(state, current_price)}"
        )
        return

    # -------------------------
    # FETCH NEWS + GET SIGNAL
    # -------------------------
    if TEST_MODE:
        # Force a BUY signal so you can verify the full trade flow
        print("[TEST MODE] Forcing bullish BUY signal...")
        signal = {
            "sentiment":  "bullish",
            "confidence": 0.90,
            "reasoning":  "TEST MODE — forced BUY signal to verify trade execution.",
            "action":     "buy",
            "key_driver": "Test"
        }
    else:
        articles = get_gold_news()
        if not articles:
            send_telegram("⚠️ No gold news articles found. Skipping this cycle.")
            return
        signal = analyze_gold_news(articles)
        if not signal:
            send_telegram("⚠️ No valid signal from Gemini. Skipping this cycle.")
            return

    action     = signal.get('action', 'hold').lower()
    confidence = float(signal.get('confidence', 0))
    key_driver = signal.get('key_driver', 'N/A')

    print(
        f"📻 <b>AI GOLD SIGNAL</b>\n"
        f"Sentiment:   {signal.get('sentiment', 'N/A').upper()}\n"
        f"Action:      {action.upper()}\n"
        f"Confidence:  {confidence:.0%}\n"
        f"Key Driver:  {key_driver}\n"
        f"Reasoning:   {signal.get('reasoning', 'N/A')}"
    )

    # -------------------------
    # EXECUTE BUY
    # -------------------------
    if action == 'buy' and confidence >= MIN_CONFIDENCE:
        spend    = min(state['balance'], MAX_SPEND)
        if spend < 100:
            send_telegram("❌ Insufficient paper balance to trade gold.")
            return

        qty_oz          = spend / current_price
        sl_price        = round(current_price * (1 - STOP_LOSS_PCT), 2)
        tp_price        = round(current_price * (1 + TAKE_PROFIT_PCT), 2)
        risk_usd        = qty_oz * (current_price - sl_price)
        reward_usd      = qty_oz * (tp_price - current_price)
        risk_reward     = reward_usd / risk_usd if risk_usd else 0

        state['balance']     -= spend
        state['position_oz']  = qty_oz
        state['entry_price']  = current_price
        state['stop_loss']    = sl_price
        state['take_profit']  = tp_price
        state['_last_qty']    = qty_oz

        send_telegram(
            f"✅ <b>GOLD PAPER BUY EXECUTED</b>\n"
            f"Bought {qty_oz:.4f} oz at ${current_price:,.2f}\n"
            f"Total Cost: ${spend:,.2f}\n\n"
            f"🛡️ <b>Risk Management</b>\n"
            f"Stop Loss:   ${sl_price:,.2f}  (-{STOP_LOSS_PCT:.1%})\n"
            f"Take Profit: ${tp_price:,.2f}  (+{TAKE_PROFIT_PCT:.1%})\n"
            f"Risk/Reward: {risk_reward:.2f}R\n"
            f"Max Risk:    ${risk_usd:,.2f}\n"
            f"Max Reward:  ${reward_usd:,.2f}\n\n"
            f"{format_portfolio(state, current_price)}"
        )
        save_state(state)

    # -------------------------
    # EXECUTE SELL (if holding — AI-driven exit)
    # -------------------------
    elif action == 'sell' and state['position_oz'] > 0:
        proceeds = state['position_oz'] * current_price
        profit   = proceeds - (state['position_oz'] * state['entry_price'])
        pnl_icon = '🟢' if profit >= 0 else '🔴'

        state['balance']      += proceeds
        state['total_trades'] += 1
        state['total_pnl']    += profit
        if profit > 0:
            state['winning_trades'] += 1
        else:
            state['losing_trades'] += 1

        qty = state['position_oz']
        state['position_oz'] = 0.0
        state['entry_price'] = 0.0
        state['stop_loss']   = 0.0
        state['take_profit'] = 0.0

        send_telegram(
            f"📤 <b>GOLD PAPER SELL (AI Signal)</b>\n"
            f"Sold {qty:.4f} oz at ${current_price:,.2f}\n"
            f"P&L: {pnl_icon} ${profit:,.2f}\n\n"
            f"{format_portfolio(state, current_price)}"
        )
        save_state(state)

    # -------------------------
    # HOLD
    # -------------------------
    else:
        reason = (
            "Confidence too low" if confidence < MIN_CONFIDENCE
            else f"Signal is {action.upper()} but no open position to sell"
            if action == 'sell' else "Market is neutral"
        )
        print(
            f"⏸️ <b>HOLD</b> — {reason}\n"
            f"Action: {action.upper()} | Confidence: {confidence:.0%}\n\n"
            f"{format_portfolio(state, current_price)}"
        )


# ============================================================
# ENTRY POINT
# ============================================================
if __name__ == "__main__":
    run()
