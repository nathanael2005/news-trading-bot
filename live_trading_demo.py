#!/usr/bin/env python3
"""
Live News Trading Bot — Demo Script
Simulates a full cycle: news fetch → Gemini AI analysis → trade execution.
Uses hardcoded news and a simulated Gemini response for offline demonstration.

In production, replace get_gemini_analysis() with a real API call.
"""

from datetime import datetime, timezone


# ============================================================
# SAMPLE NEWS DATA (replace with NewsAPI in production)
# ============================================================
LATEST_NEWS = [
    {
        "title": "Bitcoin Breaks New All-Time High at $112,000",
        "description": "Bitcoin reached a new ATH. Ethereum up 7%. Strong institutional buying continues.",
        "source": "CryptoNews",
    },
    {
        "title": "Bitcoin Whale Moves $9 Billion BTC",
        "description": (
            "One of the biggest Bitcoin whales cashed out $9B. "
            "Galaxy Digital facilitated the sale. Strategy and institutional firms absorbed the coins."
        ),
        "source": "Yahoo Finance",
    },
    {
        "title": "Bitcoin Bollinger Bands Squeeze — Bullish Breakout Expected",
        "description": (
            "BTC trading in its tightest range in over a year. "
            "Analysts predict a 70%+ rally similar to early 2024. Target: $150K–$190K."
        ),
        "source": "Crypto.news",
    },
]


# ============================================================
# SIMULATED GEMINI AI RESPONSE
# ============================================================
def get_gemini_analysis(news_articles: list[dict]) -> dict:
    """
    Simulates a Gemini AI response for the given news articles.
    In production, this makes a real call to the Gemini API.
    """
    _ = news_articles  # used in production
    return {
        "sentiment":          "bullish",
        "confidence":         0.82,
        "reasoning":          (
            "BTC at new ATH with ETH up 7%. Whale sale absorbed by institutions signals "
            "demand strength. Bollinger squeeze historically precedes 70%+ rallies."
        ),
        "action":             "buy",
        "urgency":            "high",
        "recommended_entry":  112_000,
        "stop_loss":          107_500,
        "take_profit":        125_000,
        "risk_reward":        2.78,
    }


# ============================================================
# NEWS ANALYZER
# ============================================================
class NewsAnalyzer:
    def __init__(self, news_data: list[dict]):
        self.news   = news_data
        self.signal = None

    def analyze(self) -> dict:
        """Display news headlines and fetch AI trade signal."""
        print("\n" + "=" * 60)
        print("📰  ANALYZING LATEST CRYPTO NEWS")
        print("=" * 60)

        for i, article in enumerate(self.news, 1):
            desc = article["description"]
            print(f"\n  {i}. {article['title']}")
            print(f"     {desc[:110]}{'...' if len(desc) > 110 else ''}")
            print(f"     Source: {article['source']}")

        print("\n⏳  Sending to Gemini AI for analysis...")
        self.signal = get_gemini_analysis(self.news)
        return self.signal


# ============================================================
# TRADING ENGINE
# ============================================================
class TradingEngine:
    def __init__(self, initial_balance: float = 10_000.0):
        self.balance        = initial_balance
        self.initial_balance = initial_balance
        self.position       = 0.0   # BTC held
        self.entry_price    = 0.0
        self.trades: list[dict] = []

    # ----------------------------------------------------------
    def execute_buy(self, signal: dict) -> dict:
        """Size position using 2% risk rule, then buy at entry price."""
        entry_price = signal["recommended_entry"]
        stop_loss   = signal["stop_loss"]

        risk_amount     = self.balance * 0.02
        price_diff      = abs(entry_price - stop_loss) or entry_price * 0.01
        quantity        = risk_amount / price_diff

        # Cap size to available balance
        cost = quantity * entry_price
        if cost > self.balance:
            quantity = self.balance / entry_price
            cost     = self.balance

        order = {
            "id":          f"BUY-{datetime.now(timezone.utc).strftime('%H%M%S')}",
            "type":        "BUY",
            "quantity":    round(quantity, 6),
            "entry_price": entry_price,
            "stop_loss":   stop_loss,
            "take_profit": signal["take_profit"],
            "cost":        round(cost, 2),
            "timestamp":   datetime.now(timezone.utc).isoformat(),
        }

        self.balance    -= cost
        self.position    = order["quantity"]
        self.entry_price = entry_price
        self.trades.append(order)
        return order

    # ----------------------------------------------------------
    def execute_sell(self, exit_price: float | None = None) -> dict | None:
        """Close the open position at exit_price."""
        if self.position == 0:
            return None

        if exit_price is None:
            # Default: simulate TP hit
            exit_price = self.entry_price * (1 + 0.05) if self.entry_price else 112_000

        proceeds = self.position * exit_price
        profit   = proceeds - (self.position * self.entry_price)

        order = {
            "id":         f"SELL-{datetime.now(timezone.utc).strftime('%H%M%S')}",
            "type":       "SELL",
            "quantity":   round(self.position, 6),
            "exit_price": exit_price,
            "profit":     round(profit, 2),
            "timestamp":  datetime.now(timezone.utc).isoformat(),
        }

        self.balance    += proceeds
        self.position    = 0.0
        self.entry_price = 0.0
        self.trades.append(order)
        return order

    # ----------------------------------------------------------
    @property
    def equity(self) -> float:
        return self.balance + (self.position * self.entry_price)

    @property
    def pnl(self) -> float:
        return self.equity - self.initial_balance


# ============================================================
# MAIN
# ============================================================
def main():
    print("\n" + "=" * 60)
    print("🚀  NEWS-BASED CRYPTO TRADING BOT — DEMO")
    print("=" * 60)
    print(f"🕐  Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("📊  Mode: Paper Trading (Simulation)")
    print("=" * 60)

    engine   = TradingEngine(initial_balance=10_000.0)
    analyzer = NewsAnalyzer(LATEST_NEWS)
    signal   = analyzer.analyze()

    # ---- Display AI Signal ----
    print("\n" + "-" * 60)
    print("🤖  GEMINI AI TRADE SIGNAL")
    print("-" * 60)
    print(f"   📈 Sentiment:   {signal['sentiment'].upper()}")
    print(f"   🎯 Confidence:  {signal['confidence'] * 100:.0f}%")
    print(f"   💡 Reasoning:   {signal['reasoning']}")
    print(f"   ⚡ Action:      {signal['action'].upper()}")
    print(f"   🚨 Urgency:     {signal['urgency']}")
    print(f"   💰 Entry Price: ${signal['recommended_entry']:,}")
    print(f"   🛡️ Stop Loss:   ${signal['stop_loss']:,}")
    print(f"   🎯 Take Profit: ${signal['take_profit']:,}")
    print(f"   📊 Risk/Reward: {signal['risk_reward']:.2f}R")

    # ---- Execute Trade ----
    print("\n" + "-" * 60)
    print("💱  TRADE EXECUTION")
    print("-" * 60)

    action     = signal["action"].lower()
    confidence = signal["confidence"]

    if action == "buy" and confidence >= 0.70 and engine.position == 0:
        order = engine.execute_buy(signal)
        print(f"\n✅  BUY ORDER PLACED")
        print(f"    Order ID:    {order['id']}")
        print(f"    Quantity:    {order['quantity']:.6f} BTC")
        print(f"    Cost:        ${order['cost']:,.2f}")
        print(f"    Entry:       ${order['entry_price']:,}")
        print(f"    Stop Loss:   ${order['stop_loss']:,}")
        print(f"    Take Profit: ${order['take_profit']:,}")

        print(f"\n📊  PORTFOLIO AFTER BUY")
        print(f"    Cash Balance:   ${engine.balance:,.2f}")
        print(f"    BTC Position:   {engine.position:.6f} BTC")
        print(f"    Position Value: ${engine.position * signal['recommended_entry']:,.2f}")
        print(f"    Total Equity:   ${engine.equity:,.2f}")

    elif action == "sell" and engine.position > 0:
        order = engine.execute_sell(exit_price=signal["recommended_entry"])
        print(f"\n✅  SELL ORDER PLACED")
        print(f"    Order ID:  {order['id']}")
        print(f"    Sold:      {order['quantity']:.6f} BTC @ ${order['exit_price']:,}")
        print(f"    Profit:    ${order['profit']:,.2f}")
        print(f"    Balance:   ${engine.balance:,.2f}")

    else:
        reason = (
            "Confidence too low" if confidence < 0.70
            else "Already in a position" if engine.position > 0
            else "No trade signal"
        )
        print(f"\n⏸️  HOLD — {reason}")

    # ---- Trade Log ----
    print("\n" + "=" * 60)
    print("📋  TRADE LOG")
    print("=" * 60)
    print(f"    Total Trades: {len(engine.trades)}")
    for trade in engine.trades:
        price = trade.get("entry_price") or trade.get("exit_price")
        print(f"    [{trade['type']}] {trade['quantity']:.6f} BTC @ ${price:,}  |  {trade['timestamp']}")

    print("\n" + "=" * 60)
    print("💼  FINAL PORTFOLIO SUMMARY")
    print("=" * 60)
    print(f"    Starting Balance: ${engine.initial_balance:,.2f}")
    print(f"    Current Equity:   ${engine.equity:,.2f}")
    pnl_icon = "🟢" if engine.pnl >= 0 else "🔴"
    print(f"    Net P&L:          {pnl_icon} ${engine.pnl:,.2f}")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()