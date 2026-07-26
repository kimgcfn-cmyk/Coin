"""
코인 범위매매 실전 모니터링 (신호 감지 + 보유 자동 추적)
============================================================
백테스트로 검증된 범위매매 전략(PF 1.81, 전 국면 수익)을
실전에서 자동 감시한다. 실제 주문은 내지 않고, 매수/익절/손절
'타이밍'을 텔레그램으로 알린다. 보유 상태는 봇이 자동 추적한다.

[검증된 파라미터 — 코인별 최적값]
  BTC: RSI≤30 매수 / +4% 익절 / -4% 손절
  SOL: RSI≤35 매수 / +2% 익절 / -4% 손절
  (레버리지 2배 기준 정보 함께 안내)

[동작]
  매시간 실행(cron) 시:
    1) 보유 없는 코인: RSI 체크 → 과매도면 "매수 신호" + 진입가 기록
    2) 보유 중인 코인: 현재가로 익절/손절 도달 확인 → "매도 신호" + 기록삭제
  → 밤사이엔 거래소 예약주문(지정가+OCO)으로 대응 권장

[중요]
  이 시스템은 알림 전용이다. 실제 주문은 사용자가 직접(또는
  거래소 예약주문으로) 실행한다. 봇은 자산을 만지지 않는다.

실행:
  python coin_range_monitor.py          # 1회 점검 (cron용)
  python coin_range_monitor.py --check  # 상태만 출력
  python coin_range_monitor.py --reset  # 보유기록 초기화
"""

import sys, json, os, time
from datetime import datetime, timezone

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
    print("⚠️  config.py 없음 — 텔레그램 설정 확인 필요")
    TG_TOKEN = TG_CHAT = ""
    telegram_configured = lambda *a: False

import requests

# ══════════════════════════════════════════════════════
#  설정 — 백테스트 검증 최적 파라미터
# ══════════════════════════════════════════════════════
COINS = {
    "BTC/USDT": {
        "rsi_buy"     : 30,      # RSI 30 이하 매수
        "target_pct"  : 4.0,     # +4% 익절
        "stop_pct"    : 4.0,     # -4% 손절
        "label"       : "BTC",
    },
    "SOL/USDT": {
        "rsi_buy"     : 35,      # RSI 35 이하 매수
        "target_pct"  : 2.0,     # +2% 익절
        "stop_pct"    : 4.0,     # -4% 손절
        "label"       : "SOL",
    },
}

CFG = {
    "timeframe"    : "4h",       # 4시간봉 (백테스트와 동일)
    "rsi_period"   : 14,
    "ma_period"    : 50,         # 하락장 필터용
    "down_guard"   : 8.0,        # MA -8% 이탈 시 매수 보류
    "leverage"     : 2,          # 안내용 (2배)
    "state_file"   : "coin_range_holdings.json",
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
            print(text); return False
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
#  보유 상태 저장/로드 (봇이 자동 추적)
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
#  지표
# ══════════════════════════════════════════════════════
def calc_rsi(closes, period=14):
    n = len(closes)
    if n < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, n):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0)); losses.append(max(-d, 0))
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for i in range(period, n-1):
        avg_g = (avg_g*(period-1) + gains[i]) / period
        avg_l = (avg_l*(period-1) + losses[i]) / period
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return 100 - (100 / (1 + rs))

def calc_sma(values, period):
    if len(values) < period:
        return None
    return sum(values[-period:]) / period

# ══════════════════════════════════════════════════════
#  시세 조회 (Binance — 백테스트와 동일 소스)
# ══════════════════════════════════════════════════════
def fetch_recent(symbol: str, timeframe: str, limit: int = 120):
    if not HAS_CCXT:
        return []
    ex = ccxt.binance()
    try:
        return ex.fetch_ohlcv(symbol, timeframe, limit=limit)
    except Exception as e:
        print(f"  {symbol} 시세 조회 오류: {e}")
        return []

# ══════════════════════════════════════════════════════
#  핵심: 코인별 점검
# ══════════════════════════════════════════════════════
def check_coin(symbol: str, params: dict, holdings: dict) -> dict:
    """
    한 코인을 점검하고, 필요 시 신호를 반환.
    반환: {"signal": "buy"|"sell_target"|"sell_stop"|None, ...}
    """
    ohlcv = fetch_recent(symbol, CFG["timeframe"], 120)
    if len(ohlcv) < CFG["ma_period"] + 2:
        return {"signal": None, "error": "데이터 부족"}

    closes = [c[4] for c in ohlcv]
    cur_price = closes[-1]
    rsi = calc_rsi(closes, CFG["rsi_period"])
    ma = calc_sma(closes, CFG["ma_period"])
    label = params["label"]

    held = holdings.get(symbol)

    # ── 보유 중: 익절/손절 확인 ────────────────────────
    if held:
        entry = held["entry_price"]
        change_pct = (cur_price - entry) / entry * 100
        target = params["target_pct"]
        stop = params["stop_pct"]

        if change_pct >= target:
            return {"signal": "sell_target", "price": cur_price,
                    "entry": entry, "change": change_pct, "label": label}
        if change_pct <= -stop:
            return {"signal": "sell_stop", "price": cur_price,
                    "entry": entry, "change": change_pct, "label": label}
        # 보유 유지
        return {"signal": None, "held": True, "price": cur_price,
                "entry": entry, "change": change_pct, "rsi": rsi, "label": label}

    # ── 미보유: 매수 신호 확인 ─────────────────────────
    if rsi is None:
        return {"signal": None, "label": label}

    # 하락장 필터 (칼 안 잡기)
    down_market = False
    if ma and ma > 0 and (cur_price - ma) / ma * 100 < -CFG["down_guard"]:
        down_market = True

    if rsi <= params["rsi_buy"] and not down_market:
        return {"signal": "buy", "price": cur_price, "rsi": rsi,
                "target_pct": params["target_pct"], "stop_pct": params["stop_pct"],
                "label": label}

    return {"signal": None, "price": cur_price, "rsi": rsi,
            "down_market": down_market, "label": label}

# ══════════════════════════════════════════════════════
#  알림 메시지 생성
# ══════════════════════════════════════════════════════
def msg_buy(r: dict, symbol: str) -> str:
    price = r["price"]
    tgt = price * (1 + r["target_pct"]/100)
    stp = price * (1 - r["stop_pct"]/100)
    lev = CFG["leverage"]
    return (
        f"🟢 <b>{r['label']} 매수 신호!</b>\n"
        f"RSI {r['rsi']:.1f} (과매도)\n"
        f"현재가: ${price:,.2f}\n\n"
        f"목표: +{r['target_pct']:.0f}% → ${tgt:,.2f}\n"
        f"손절: -{r['stop_pct']:.0f}% → ${stp:,.2f}\n\n"
        f"💡 {lev}배 레버리지 기준\n"
        f"  익절 시 증거금 +{r['target_pct']*lev:.0f}%\n"
        f"  손절 시 증거금 -{r['stop_pct']*lev:.0f}%\n\n"
        f"📌 <b>예약주문 권장</b> (밤 대비):\n"
        f"  지정가 매수 + OCO(익절/손절) 걸어두기\n"
        f"⚠️ 손절 반드시 설정하세요"
    )

def msg_sell(r: dict, symbol: str, reason: str) -> str:
    icon = "🎯" if reason == "target" else "🔴"
    word = "익절" if reason == "target" else "손절"
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
    print(f"\n{'='*56}")
    print(f"  코인 범위매매 모니터 — {now}")
    print(f"{'='*56}")

    if not HAS_CCXT:
        print("  ❌ ccxt 필요: pip install ccxt")
        return

    holdings = load_holdings()
    alerts = []

    for symbol, params in COINS.items():
        r = check_coin(symbol, params, holdings)
        label = params["label"]

        if r["signal"] == "buy":
            # 매수 신호 → 보유 기록 추가 (봇이 자동 추적 시작)
            holdings[symbol] = {
                "entry_price": r["price"],
                "entry_time": now,
                "target_pct": params["target_pct"],
                "stop_pct": params["stop_pct"],
            }
            alerts.append(msg_buy(r, symbol))
            print(f"  🟢 {label}: 매수 신호 (RSI {r['rsi']:.1f}, ${r['price']:,.2f})")

        elif r["signal"] == "sell_target":
            alerts.append(msg_sell(r, symbol, "target"))
            holdings.pop(symbol, None)  # 청산 → 기록 삭제
            print(f"  🎯 {label}: 익절 신호 ({r['change']:+.1f}%)")

        elif r["signal"] == "sell_stop":
            alerts.append(msg_sell(r, symbol, "stop"))
            holdings.pop(symbol, None)
            print(f"  🔴 {label}: 손절 신호 ({r['change']:+.1f}%)")

        elif r.get("held"):
            print(f"  ⏳ {label}: 보유 중 (진입 ${r['entry']:,.2f}, "
                  f"현재 {r['change']:+.1f}%, RSI {r.get('rsi') or 0:.1f})")

        else:
            rsi_str = f"RSI {r.get('rsi'):.1f}" if r.get('rsi') else "데이터부족"
            dm = " [하락장 보류]" if r.get("down_market") else ""
            print(f"  ⚪ {label}: 대기 ({rsi_str}){dm}")

    save_holdings(holdings)

    # 신호가 있으면 텔레그램 발송
    if alerts and send:
        for msg in alerts:
            send_telegram(msg)
        print(f"\n  ✅ 신호 {len(alerts)}건 텔레그램 발송")
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
        print("  ✅ 보유 기록 초기화 완료")
        sys.exit(0)

    if "--check" in args:
        # 상태만 출력, 텔레그램 발송 안 함
        run_check(send=False)
        sys.exit(0)

    # 기본: 점검 + 신호 발송
    run_check(send=True)
