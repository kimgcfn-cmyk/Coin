"""
떨사오픔 전략 실전 모니터 v2 — Falling Buy, Rising Sell
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

[동작]
  4시간마다 실행(cron) 시:
    1) 보유 없는 코인: 조건 충족 시 "매수 신호" + 진입가 기록
    2) 보유 중인 코인: 익절/손절/trailing 확인 → "매도 신호" + 기록삭제
  → 밤사이엔 거래소 예약주문(지정가+OCO)으로 대응 권장

[중요]
  이 시스템은 알림 전용이다. 실제 주문은 사용자가 직접(또는
  거래소 예약주문으로) 실행한다. 봇은 자산을 만지지 않는다.

[실행]
  python coin_fall_buy_monitor.py          # 1회 점검 (cron용)
  python coin_fall_buy_monitor.py --check  # 상태만 출력
  python coin_fall_buy_monitor.py --reset  # 보유기록 초기화
"""

import sys, json, os, time
from datetime import datetime, timezone
from typing import List, Dict, Optional

try:
    import ccxt
    HAS_CCXT = True
except ImportError:
    HAS_CCXT = False

# ── config.py의 텔레그램 헬퍼 (코인 봇) ────────────────
try:
    from config import (
        TELEGRAM_TOKEN_COIN as TG_TOKEN,
        TELEGRAM_CHAT_ID_COIN as TG_CHAT,
        telegram_configured,
    )
except ImportError:
    print("config.py 없음 — 텔레그램 설정 확인 필요")
    TG_TOKEN = TG_CHAT = ""
    telegram_configured = lambda *a: False

import requests

# ══════════════════════════════════════════════════════
#  설정 — 백테스트 검증 최적 파라미터
# ══════════════════════════════════════════════════════
COINS = {
    "BTC/USDT": {
        "rsi_buy": 30,
        "rsi_sell": 62,
        "target_pct": 3.5,
        "stop_pct": 4.5,
        "trailing_pct": 1.0,
        "label": "BTC",
    },
    "ETH/USDT": {
        "rsi_buy": 32,
        "rsi_sell": 68,
        "target_pct": 4.0,
        "stop_pct": 4.5,
        "trailing_pct": 1.5,
        "label": "ETH",
    },
    "SOL/USDT": {
        "rsi_buy": 35,
        "rsi_sell": 70,
        "target_pct": 4.0,
        "stop_pct": 4.0,
        "trailing_pct": 2.0,
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
    "state_file": "coin_fall_buy_holdings.json",
}

# ══════════════════════════════════════════════════════
#  텔레그램
# ══════════════════════════════════════════════════════
def send_telegram(text: str) -> bool:
    try:
        if not telegram_configured("coin"):
            print("  (텔레그램 미설정 — 콘솔 출력만)")
            print(text)
            return False
    except TypeError:
        if not telegram_configured():
            print(text)
            return False
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id": TG_CHAT, "text": text, "parse_mode": "HTML",
        }, timeout=10)
        return r.json().get("ok", False)
    except Exception as e:
        print(f"  텔레그램 오류: {e}")
        return False

# ══════════════════════════════════════════════════════
#  보유 상태 저장/로드
# ══════════════════════════════════════════════════════
def load_holdings() -> dict:
    path = CFG["state_file"]
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_holdings(h: dict):
    with open(CFG["state_file"], "w", encoding="utf-8") as f:
        json.dump(h, f, ensure_ascii=False, indent=2)

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
def fetch_recent(symbol: str, timeframe: str, limit: int = 120) -> List:
    if not HAS_CCXT:
        return []
    ex = ccxt.binance()
    try:
        return ex.fetch_ohlcv(symbol, timeframe, limit=limit)
    except Exception as e:
        print(f"  {symbol} 시세 조회 오류: {e}")
        return []

# ══════════════════════════════════════════════════════
#  코인별 점검
# ══════════════════════════════════════════════════════
def check_coin(symbol: str, params: dict, holdings: dict) -> Dict:
    ohlcv = fetch_recent(symbol, CFG["timeframe"], 120)
    if len(ohlcv) < CFG["ema_slow"] + 5:
        return {"signal": None, "error": "데이터 부족"}

    closes = [c[4] for c in ohlcv]
    opens = [c[1] for c in ohlcv]
    cur_price = closes[-1]
    cur_open = opens[-1]
    rsi = calc_rsi(closes, CFG["rsi_period"])
    ema_fast = calc_ema(closes, CFG["ema_fast"])
    ema_slow = calc_ema(closes, CFG["ema_slow"])
    label = params["label"]

    held = holdings.get(symbol)

    # ── 보유 중 → 매도 판단 ──
    if held:
        entry = held["entry_price"]
        highest = max(held.get("highest_price", entry), cur_price)
        change_pct = (cur_price - entry) / entry * 100
        drawdown_from_high = (cur_price - highest) / highest * 100
        target = params["target_pct"]
        stop = params["stop_pct"]
        trailing = params["trailing_pct"]

        # 최고가 갱신 저장
        held["highest_price"] = highest

        if change_pct >= target:
            return {"signal": "sell_target", "price": cur_price,
                    "entry": entry, "change": change_pct, "label": label}

        if rsi and rsi >= params.get("rsi_sell", 70) and change_pct > 0:
            return {"signal": "sell_rsi", "price": cur_price,
                    "entry": entry, "change": change_pct, "rsi": rsi, "label": label}

        if change_pct <= -stop:
            return {"signal": "sell_stop", "price": cur_price,
                    "entry": entry, "change": change_pct, "label": label}

        if highest > entry * 1.01 and drawdown_from_high <= -trailing:
            return {"signal": "sell_trailing", "price": cur_price,
                    "entry": entry, "change": change_pct,
                    "drawdown": drawdown_from_high, "label": label}

        return {"signal": None, "held": True, "price": cur_price,
                "entry": entry, "change": change_pct, "highest": highest,
                "rsi": rsi, "label": label}

    # ── 미보유 → 매수 판단 ──
    if rsi is None or ema_fast is None or ema_slow is None:
        return {"signal": None, "label": label}

    down_market = (cur_price - ema_slow) / ema_slow * 100 < -CFG["down_guard"]

    lb = params.get("lookback", 3)
    recent_rsi = []
    n = len(closes)
    for j in range(max(CFG["ema_slow"] + 1, n - 1 - lb), n - 1):
        ri = calc_rsi(closes[: j + 1], CFG["rsi_period"])
        if ri is not None:
            recent_rsi.append(ri)

    rsi_was_oversold = any(r <= params["rsi_buy"] for r in recent_rsi)
    rsi_rising = len(recent_rsi) >= 2 and rsi > recent_rsi[-1]
    price_above_fast = cur_price > ema_fast
    bullish_candle = cur_price > cur_open

    if rsi_was_oversold and rsi_rising and price_above_fast and not down_market:
        return {"signal": "buy", "price": cur_price, "rsi": rsi,
                "rsi_prev": recent_rsi[-1] if recent_rsi else rsi,
                "target_pct": params["target_pct"],
                "stop_pct": params["stop_pct"],
                "trailing_pct": params["trailing_pct"],
                "label": label}

    return {"signal": None, "price": cur_price, "rsi": rsi,
            "rsi_prev": recent_rsi[-1] if recent_rsi else None,
            "price_above_fast": price_above_fast,
            "bullish_candle": bullish_candle,
            "down_market": down_market, "label": label}

# ══════════════════════════════════════════════════════
#  알림 메시지 생성
# ══════════════════════════════════════════════════════
def msg_buy(r: dict) -> str:
    price = r["price"]
    tgt = price * (1 + r["target_pct"] / 100)
    stp = price * (1 - r["stop_pct"] / 100)
    trail = price * (1 - r["trailing_pct"] / 100)
    lev = CFG["leverage"]
    return (
        f"🟢 <b>{r['label']} 매수 신호!</b>\n"
        f"RSI {r['rsi']:.1f} → 반등 확인 (이전 {r['rsi_prev']:.1f})\n"
        f"현재가: ${price:,.2f}\n\n"
        f"목표: +{r['target_pct']:.0f}% → ${tgt:,.2f}\n"
        f"손절: -{r['stop_pct']:.0f}% → ${stp:,.2f}\n"
        f"추적손절: -{r['trailing_pct']:.0f}% → ${trail:,.2f}\n\n"
        f"💡 {lev}배 레버리지 기준\n"
        f"  익절 시 증거금 +{r['target_pct'] * lev:.0f}%\n"
        f"  손절 시 증거금 -{r['stop_pct'] * lev:.0f}%\n\n"
        f"📌 <b>예약주문 권장</b> (밤 대비):\n"
        f"  지정가 매수 + OCO(익절/손절) 걸어두기\n"
        f"⚠️ 손절 반드시 설정하세요"
    )

def msg_sell(r: dict, reason: str) -> str:
    icons = {"target": "🎯", "stop": "🔴", "trailing": "📉", "rsi": "📊"}
    words = {"target": "익절", "stop": "손절", "trailing": "추적손절", "rsi": "RSI 과매수"}
    icon = icons.get(reason, "🔴")
    word = words.get(reason, "매도")
    lev = CFG["leverage"]
    margin_change = r["change"] * lev
    return (
        f"{icon} <b>{r['label']} {word} 신호!</b>\n"
        f"진입가: ${r['entry']:,.2f}\n"
        f"현재가: ${r['price']:,.2f}\n"
        f"수익률: {r['change']:+.1f}% (가격)\n"
        f"{lev}배 증거금 기준: {margin_change:+.1f}%\n\n"
        f"📌 매도(청산) 권고"
    )

# ══════════════════════════════════════════════════════
#  메인 점검 루프
# ══════════════════════════════════════════════════════
def run_check(send: bool = True):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{'=' * 56}")
    print(f"  떨사오픔 전략 모니터 v2 — {now}")
    print(f"{'=' * 56}")

    if not HAS_CCXT:
        print("  ccxt 필요: pip install ccxt")
        return

    holdings = load_holdings()
    alerts = []

    for symbol, params in COINS.items():
        r = check_coin(symbol, params, holdings)
        label = params["label"]

        if r["signal"] == "buy":
            holdings[symbol] = {
                "entry_price": r["price"],
                "entry_time": now,
                "highest_price": r["price"],
                "target_pct": params["target_pct"],
                "stop_pct": params["stop_pct"],
                "trailing_pct": params["trailing_pct"],
            }
            alerts.append(msg_buy(r))
            print(f"  🟢 {label}: 매수 신호 (RSI {r['rsi']:.1f}, ${r['price']:,.2f})")

        elif r["signal"] == "sell_target":
            alerts.append(msg_sell(r, "target"))
            holdings.pop(symbol, None)
            print(f"  🎯 {label}: 익절 신호 ({r['change']:+.1f}%)")

        elif r["signal"] == "sell_stop":
            alerts.append(msg_sell(r, "stop"))
            holdings.pop(symbol, None)
            print(f"  🔴 {label}: 손절 신호 ({r['change']:+.1f}%)")

        elif r["signal"] == "sell_trailing":
            alerts.append(msg_sell(r, "trailing"))
            holdings.pop(symbol, None)
            print(f"  📉 {label}: 추적손절 신호 ({r['change']:+.1f}%)")

        elif r["signal"] == "sell_rsi":
            alerts.append(msg_sell(r, "rsi"))
            holdings.pop(symbol, None)
            print(f"  📊 {label}: RSI 과매수 출구 ({r['change']:+.1f}%, RSI {r.get('rsi', 0):.1f})")

        elif r.get("held"):
            print(f"  ⏳ {label}: 보유 중 (진입 ${r['entry']:,.2f}, "
                  f"현재 {r['change']:+.1f}%, "
                  f"최고 ${r.get('highest', 0):,.2f}, "
                  f"RSI {r.get('rsi') or 0:.1f})")

        else:
            rsi_str = f"RSI {r.get('rsi'):.1f}" if r.get("rsi") else "데이터부족"
            prev_str = f"(이전 {r.get('rsi_prev', 0):.1f})" if r.get("rsi_prev") else ""
            dm = " [하락장 보류]" if r.get("down_market") else ""
            ema_ok = " ✓" if r.get("price_above_fast") else ""
            bull = " 🟢" if r.get("bullish_candle") else ""
            print(f"  ⚪ {label}: 대기 ({rsi_str}{prev_str}){ema_ok}{bull}{dm}")

    save_holdings(holdings)

    if alerts and send:
        for msg in alerts:
            send_telegram(msg)
        print(f"\n  신호 {len(alerts)}건 발송")
    elif not alerts:
        print(f"\n  신호 없음 (대기)")

    return holdings

# ══════════════════════════════════════════════════════
#  실행
# ══════════════════════════════════════════════════════
if __name__ == "__main__":
    args = sys.argv[1:]

    if "--reset" in args:
        if os.path.exists(CFG["state_file"]):
            os.remove(CFG["state_file"])
        print("  보유 기록 초기화 완료")
        sys.exit(0)

    if "--check" in args:
        run_check(send=False)
        sys.exit(0)

    run_check(send=True)
