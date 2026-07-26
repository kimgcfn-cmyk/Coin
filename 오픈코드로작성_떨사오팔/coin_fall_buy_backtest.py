"""
떨사오픔 전략 백테스트 v2 — Falling Buy, Rising Sell
====================================================
[전략 개요]
  가격이 하락 후 반등 신호가 확인되면 매수, 목표 수익 또는 과매수 시 매도.

[매수 조건]
  1) RSI 과매도 이력: 최근 N개 캔들 내 RSI가 과매도 구간 진입 이력
  2) RSI 반등 중: 현재 RSI가 최근 저점 대비 상승 중
  3) 단기 EMA 확인: 가격이 단기 EMA(10) 위로 복귀
  4) 하락장 필터: 가격이 장기 EMA(50) 대비 일정 수준 이상 이탈 시 보류

[매도 조건]
  1) 목표 수익률 도달 (익절)
  2) 추적 손절 (trailing stop): 진입 후 최고가 대비 하락 시
  3) RSI 과매수: RSI 70 초과 시 추가 상승 제한적 → 익절

[파라미터]
  BTC: RSI≤30 매수 / +4.5% 익절 / -3% 손절 / trailing 2%
  ETH: RSI≤32 매수 / +4% 익절 / -3% 손절 / trailing 2%
  SOL: RSI≤35 매수 / +4% 익절 / -3% 손절 / trailing 2.5%

[실행]
  python coin_fall_buy_backtest.py
"""

import ccxt
import time
from datetime import datetime, timezone
from typing import List, Dict, Optional

# ══════════════════════════════════════════════════════
#  설정
# ══════════════════════════════════════════════════════
COINS = {
    "BTC/USDT": {
        "rsi_buy": 30,
        "rsi_sell": 62,
        "target_pct": 3.5,
        "stop_pct": 4.5,
        "trailing_pct": 1.0,
        "lookback": 2,
        "label": "BTC",
    },
    "ETH/USDT": {
        "rsi_buy": 32,
        "rsi_sell": 68,
        "target_pct": 4.0,
        "stop_pct": 4.5,
        "trailing_pct": 1.5,
        "lookback": 4,
        "label": "ETH",
    },
    "SOL/USDT": {
        "rsi_buy": 35,
        "rsi_sell": 70,
        "target_pct": 4.0,
        "stop_pct": 4.0,
        "trailing_pct": 2.0,
        "lookback": 3,
        "label": "SOL",
    },
}

CFG = {
    "timeframe": "1h",
    "rsi_period": 14,
    "ema_fast": 10,
    "ema_slow": 50,
    "down_guard": 7.0,
    "leverage": 2,
    "initial_capital": 10000,
    "position_size_pct": 10.0,
    "cooldown_bars": 3,
    "data_limit": 2000,
}

# ══════════════════════════════════════════════════════
#  지표 계산
# ══════════════════════════════════════════════════════
def calc_rsi(closes: List[float], period: int = 14) -> Optional[float]:
    n = len(closes)
    if n < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, n):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for i in range(period, n - 1):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return 100 - (100 / (1 + rs))


def calc_ema(values: List[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    ema = sum(values[:period]) / period
    for v in values[period:]:
        ema = v * k + ema * (1 - k)
    return ema

# ══════════════════════════════════════════════════════
#  시세 조회
# ══════════════════════════════════════════════════════
def fetch_ohlcv(symbol: str, timeframe: str, limit: int) -> List:
    ex = ccxt.binance()
    try:
        return ex.fetch_ohlcv(symbol, timeframe, limit=limit)
    except Exception as e:
        print(f"  {symbol} 시세 조회 오류: {e}")
        return []

# ══════════════════════════════════════════════════════
#  백테스트 엔진
# ══════════════════════════════════════════════════════
def backtest(symbol: str, params: dict) -> Dict:
    ohlcv = fetch_ohlcv(symbol, CFG["timeframe"], CFG["data_limit"])
    if len(ohlcv) < CFG["ema_slow"] + 20:
        return {"error": "데이터 부족"}

    closes = [c[4] for c in ohlcv]
    opens = [c[1] for c in ohlcv]
    capital = CFG["initial_capital"]
    position = 0.0
    entry_price = 0.0
    highest_price = 0.0
    cooldown = 0
    trades = []

    for i in range(CFG["ema_slow"], len(ohlcv)):
        price = closes[i]
        op = opens[i]

        if cooldown > 0:
            cooldown -= 1

        rsi = calc_rsi(closes[: i + 1], CFG["rsi_period"])
        ema_fast = calc_ema(closes[: i + 1], CFG["ema_fast"])
        ema_slow = calc_ema(closes[: i + 1], CFG["ema_slow"])
        if rsi is None or ema_fast is None or ema_slow is None:
            continue

        down_market = (price - ema_slow) / ema_slow * 100 < -CFG["down_guard"]

        # ── 매수 조건 ──
        if position == 0 and cooldown == 0:
            lb = params.get("lookback", 3)
            recent_rsi = []
            for j in range(max(CFG["ema_slow"] + 1, i - lb), i):
                ri = calc_rsi(closes[: j + 1], CFG["rsi_period"])
                if ri is not None:
                    recent_rsi.append(ri)

            rsi_was_oversold = any(r <= params["rsi_buy"] for r in recent_rsi)
            rsi_rising = len(recent_rsi) >= 2 and rsi > recent_rsi[-1]
            price_above_fast = price > ema_fast
            bullish_candle = price > op

            if rsi_was_oversold and rsi_rising and price_above_fast and not down_market:
                position_size = capital * CFG["position_size_pct"] / 100
                entry_price = price
                highest_price = price
                position = position_size / price
                capital -= position_size
                trades.append({
                    "type": "buy",
                    "price": price,
                    "rsi": rsi,
                    "time": i,
                })

        # ── 보유 중 매도 판단 ──
        elif position > 0:
            change_pct = (price - entry_price) / entry_price * 100
            highest_price = max(highest_price, price)
            drawdown_from_high = (price - highest_price) / highest_price * 100
            trailing_trigger = -params["trailing_pct"]

            sell = False
            reason = ""

            if change_pct >= params["target_pct"]:
                sell, reason = True, "target"
            elif rsi >= params["rsi_sell"] and change_pct > 0:
                sell, reason = True, "rsi_overbought"
            elif change_pct <= -params["stop_pct"]:
                sell, reason = True, "stop"
            elif (
                highest_price > entry_price * 1.01
                and drawdown_from_high <= trailing_trigger
            ):
                sell, reason = True, "trailing"

            if sell:
                capital += position * price
                trades.append({
                    "type": "sell",
                    "price": price,
                    "change": change_pct,
                    "reason": reason,
                    "time": i,
                })
                if reason == "stop":
                    cooldown = CFG["cooldown_bars"]
                position = 0.0
                entry_price = 0.0
                highest_price = 0.0

    final_price = closes[-1]
    total_value = capital + (position * final_price if position > 0 else 0)
    total_return = (total_value - CFG["initial_capital"]) / CFG["initial_capital"] * 100

    buy_trades = [t for t in trades if t["type"] == "buy"]
    sell_trades = [t for t in trades if t["type"] == "sell"]
    win_trades = [t for t in sell_trades if t.get("change", 0) > 0]

    return {
        "symbol": symbol,
        "total_return": total_return,
        "total_trades": len(buy_trades),
        "win_rate": len(win_trades) / len(sell_trades) * 100 if sell_trades else 0,
        "final_value": total_value,
        "trades": trades,
    }

# ══════════════════════════════════════════════════════
#  메인
# ══════════════════════════════════════════════════════
if __name__ == "__main__":
    print("\n" + "=" * 56)
    print("  떨사오픔 전략 백테스트 v2")
    print("=" * 56)
    print("  개선: RSI 반등 확인 + EMA 크로스 + trailing stop")
    print("        + 쿨다운 + RSI 과매수 출구")

    results = []
    for symbol, params in COINS.items():
        print(f"\n  {symbol} 백테스트 중...")
        r = backtest(symbol, params)
        results.append(r)
        time.sleep(0.5)

    print("\n" + "=" * 56)
    print("  백테스트 결과")
    print("=" * 56)
    for r in results:
        if "error" in r:
            print(f"  {r['symbol']}: {r['error']}")
        else:
            sells = [t for t in r["trades"] if t["type"] == "sell"]
            reasons = {}
            for s in sells:
                reasons[s["reason"]] = reasons.get(s["reason"], 0) + 1
            reason_str = " | ".join(f"{k}:{v}" for k, v in reasons.items())
            print(
                f"  {r['symbol']}: 총수익 {r['total_return']:+.2f}% | "
                f"승률 {r['win_rate']:.0f}% | 거래 {r['total_trades']}회 | "
                f"출구: {reason_str}"
            )
