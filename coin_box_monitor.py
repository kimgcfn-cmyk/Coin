"""
박스 전략 실전 단타 모니터 (신호 감지 + 보유 자동 추적)
============================================================
Box Strategy(1시간봉 박스 + 15분봉 확정)를 실전에서 자동 감시한다.
실제 주문은 내지 않고, 매수/익절/손절/브레이크이븐 타이밍을
텔레그램으로 알린다. 보유·대기 상태는 봇이 JSON으로 자동 추적한다.

[전략 — 상태 머신]
  1) 대기: 1시간봉 박스(스윙H/L) 계산, 15분봉이 박스 하단 매수존에
     닿고 반전 양봉이 나오면 → "돌파 대기" 등록 (신호봉 고점 기록)
  2) 돌파 대기: 이후 6봉(90분) 내에 신호봉 고점 돌파 → 매수 신호
     (신호봉 저점 이탈 시 무효화)
  3) 보유: 목표(박스 상단) / 손절(신호봉 저점) 추적
     +1R 도달 시 → "손절을 진입가로 올리세요" 브레이크이븐 알림

[리스크 안내]
  손익비 1.5 미만이면 진입 신호를 내지 않음 (원문 원칙)

실행:
  python coin_box_monitor.py           # 1회 점검 (cron 15분마다)
  python coin_box_monitor.py --check   # 상태만 출력 (발송 안 함)
  python coin_box_monitor.py --reset   # 상태 초기화

cron 등록 (15분마다):
  */15 * * * * cd /home/ec2-user/trading-bot && venv/bin/python coin_box_monitor.py >> logs/coin_box.log 2>&1
"""

import sys, json, os, time
from datetime import datetime, timezone
from typing import Optional

try:
    import ccxt
    HAS_CCXT = True
except ImportError:
    HAS_CCXT = False

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
#  설정 (백테스트 파라미터와 동일 기준)
# ══════════════════════════════════════════════════════
CFG = {
    "symbols"        : ["BTC/USDT", "ETH/USDT", "SOL/USDT"],  # 감시 코인
    "swing_lookback" : 5,     # 스윙 하이/로우: 좌우 5봉
    "box_min_pct"    : 1.5,   # 박스 최소 높이
    "box_max_pct"    : 25.0,
    "buy_zone_pct"   : 25.0,  # 박스 하단 25% = 매수존
    "confirm_bars"   : 6,     # 반전 양봉 후 6봉(90분) 내 돌파해야
    "sl_buffer_pct"  : 0.2,
    "min_rr"         : 1.5,   # 손익비 1.5 미만이면 신호 안 냄
    "breakeven_at_r" : 1.0,
    "max_hold_bars"  : 96,    # 24시간 시간청산 안내
    "leverage"       : 2,     # 안내용
    "state_file"     : "coin_box_state.json",
    "h1_limit"       : 200,   # 1시간봉 200개 (박스 계산용)
    "m15_limit"      : 50,    # 15분봉 50개 (확정 신호용)
}

# ══════════════════════════════════════════════════════
#  텔레그램 / 상태 저장
# ══════════════════════════════════════════════════════
def send_telegram(text: str) -> bool:
    try:
        if not telegram_configured("coin"):
            print("  (텔레그램 미설정 — 콘솔 출력)")
            print(text)
            return False
    except TypeError:
        if not telegram_configured():
            print(text); return False
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": TG_CHAT, "text": text,
                                     "parse_mode": "HTML"}, timeout=10)
        return r.json().get("ok", False)
    except Exception as e:
        print(f"  텔레그램 오류: {e}")
        return False

def load_state() -> dict:
    if os.path.exists(CFG["state_file"]):
        try:
            with open(CFG["state_file"], encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_state(s: dict):
    with open(CFG["state_file"], "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)

# ══════════════════════════════════════════════════════
#  시세 (바이낸스 — 백테스트와 동일 소스, 실매매는 Bitget)
# ══════════════════════════════════════════════════════
def fetch(symbol: str, timeframe: str, limit: int) -> list:
    if not HAS_CCXT:
        return []
    try:
        return ccxt.binance().fetch_ohlcv(symbol, timeframe, limit=limit)
    except Exception as e:
        print(f"  {symbol} {timeframe} 조회 오류: {e}")
        return []

# ══════════════════════════════════════════════════════
#  박스 계산 (백테스트와 동일 로직)
# ══════════════════════════════════════════════════════
def current_box(h1: list) -> tuple:
    """
    현재 유효한 박스 반환.
    가장 최근 스윙로우를 기준으로, 그보다 '높은' 가장 최근 스윙하이를
    짝지어 박스를 만든다. (상승 중 최근 로우가 옛 하이보다 높아져
    박스가 사라지는 문제 방지)
    """
    lb = CFG["swing_lookback"]
    n = len(h1)
    if n < lb * 2 + 5:
        return (None, None)
    highs = [c[2] for c in h1]
    lows  = [c[3] for c in h1]
    swing_highs, swing_lows = [], []
    for i in range(lb, n - lb):
        if highs[i] == max(highs[i-lb:i+lb+1]):
            swing_highs.append(highs[i])
        if lows[i] == min(lows[i-lb:i+lb+1]):
            swing_lows.append(lows[i])
    if not swing_highs or not swing_lows:
        return (None, None)
    cur_low = swing_lows[-1]
    # 현재 로우보다 높은 가장 최근 스윙하이 탐색
    cur_high = None
    for hv in reversed(swing_highs):
        if hv > cur_low:
            cur_high = hv
            break
    if cur_high:
        height = (cur_high - cur_low) / cur_low * 100
        if CFG["box_min_pct"] <= height <= CFG["box_max_pct"]:
            return (cur_low, cur_high)
    return (None, None)

# ══════════════════════════════════════════════════════
#  코인별 점검 (상태 머신)
# ══════════════════════════════════════════════════════
def check_coin(symbol: str, state: dict) -> Optional[str]:
    """상태에 따라 점검하고, 알림 메시지(또는 None) 반환"""
    label = symbol.replace("/USDT", "")
    st = state.get(symbol, {"phase": "idle"})
    phase = st.get("phase", "idle")

    m15 = fetch(symbol, "15m", CFG["m15_limit"])
    if len(m15) < 10:
        print(f"  {label}: 데이터 부족")
        return None
    # 마지막 봉은 진행 중 → 확정 판단엔 직전 마감봉 사용
    last_closed = m15[-2]
    cur_price = m15[-1][4]
    lo, lc = last_closed[3], last_closed[4]

    # ── 보유 중: 목표/손절/브레이크이븐 추적 ──────────
    if phase == "holding":
        entry = st["entry"]
        sl = st["sl"]
        target = st["target"]
        be_done = st.get("be_done", False)
        risk = entry - st["orig_sl"]

        if cur_price <= sl:
            word = "본전청산" if be_done else "손절"
            state[symbol] = {"phase": "idle"}
            return (f"🔴 <b>{label} {word} 신호!</b>\n"
                    f"진입 ${entry:,.2f} → 현재 ${cur_price:,.2f}\n"
                    f"손절선 ${sl:,.2f} 도달 — 매도(청산)하세요")
        if cur_price >= target:
            pct = (target-entry)/entry*100
            state[symbol] = {"phase": "idle"}
            return (f"🎯 <b>{label} 목표 도달!</b> (박스 상단)\n"
                    f"진입 ${entry:,.2f} → 목표 ${target:,.2f} ({pct:+.1f}%)\n"
                    f"{CFG['leverage']}배 증거금 기준 {pct*CFG['leverage']:+.1f}%\n"
                    f"매도(익절)하세요")
        if not be_done and cur_price >= entry + risk * CFG["breakeven_at_r"]:
            st["sl"] = entry
            st["be_done"] = True
            state[symbol] = st
            return (f"🛡 <b>{label} 브레이크이븐!</b> (+1R 도달)\n"
                    f"손절 주문을 진입가 ${entry:,.2f}로 올리세요\n"
                    f"→ 이제 이 거래는 잃지 않습니다")
        chg = (cur_price-entry)/entry*100
        print(f"  ⏳ {label}: 보유 중 (진입 ${entry:,.2f}, {chg:+.1f}%, "
              f"목표 ${target:,.2f}, 손절 ${st['sl']:,.2f})")
        state[symbol] = st
        return None

    # ── 박스 계산 (idle / waiting 공통) ────────────────
    h1 = fetch(symbol, "1h", CFG["h1_limit"])
    box_low, box_high = current_box(h1)
    if not box_low:
        print(f"  ⚪ {label}: 유효한 박스 없음")
        state[symbol] = {"phase": "idle"}
        return None

    # ── 돌파 대기: 신호봉 고점 돌파 감시 ───────────────
    if phase == "waiting":
        sig_high = st["sig_high"]
        sig_low = st["sig_low"]
        st["bars_waited"] = st.get("bars_waited", 0) + 1

        if cur_price < sig_low:   # 신호봉 저점 이탈 → 무효
            state[symbol] = {"phase": "idle"}
            print(f"  ⚪ {label}: 돌파 대기 무효화 (신호봉 저점 이탈)")
            return None
        if st["bars_waited"] > CFG["confirm_bars"]:
            state[symbol] = {"phase": "idle"}
            print(f"  ⚪ {label}: 돌파 대기 시간 초과 (리셋)")
            return None
        if cur_price >= sig_high:   # 돌파! → 매수 신호
            entry = sig_high
            sl = sig_low * (1 - CFG["sl_buffer_pct"]/100)
            target = box_high
            risk_d = entry - sl
            rr = (target - entry) / risk_d if risk_d > 0 else 0
            if rr < CFG["min_rr"]:
                state[symbol] = {"phase": "idle"}
                print(f"  ⚪ {label}: 돌파했으나 손익비 {rr:.1f} 미달 — 포기")
                return None
            state[symbol] = {"phase": "holding", "entry": entry,
                             "sl": sl, "orig_sl": sl, "target": target,
                             "be_done": False,
                             "entry_time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}
            lev = CFG["leverage"]
            risk_pct = risk_d/entry*100
            return (f"🟢 <b>{label} 매수 신호!</b> (박스전략 확정)\n"
                    f"신호봉 고점 ${sig_high:,.2f} 돌파\n"
                    f"진입가: ${entry:,.2f}\n\n"
                    f"🎯 목표(박스상단): ${target:,.2f} (+{(target-entry)/entry*100:.1f}%)\n"
                    f"📍 손절(신호봉저점): ${sl:,.2f} (-{risk_pct:.1f}%)\n"
                    f"손익비 1:{rr:.1f}\n\n"
                    f"💡 {lev}배 기준 손절 시 증거금 -{risk_pct*lev:.1f}%\n"
                    f"📌 예약주문(지정가+OCO) 권장, +1R 도달 시 브레이크이븐 알림 예정")
        print(f"  ⏳ {label}: 돌파 대기 중 ({st['bars_waited']}/{CFG['confirm_bars']}봉, "
              f"돌파선 ${sig_high:,.2f}, 현재 ${cur_price:,.2f})")
        state[symbol] = st
        return None

    # ── 대기: 매수존 + 반전 양봉 탐지 ──────────────────
    box_h = box_high - box_low
    buy_zone_top = box_low + box_h * CFG["buy_zone_pct"] / 100
    in_zone = lo <= buy_zone_top
    is_bull = lc > last_closed[1]   # 마감봉이 양봉

    if in_zone and is_bull:
        state[symbol] = {"phase": "waiting",
                         "sig_high": last_closed[2],
                         "sig_low": last_closed[3],
                         "bars_waited": 0,
                         "box_low": box_low, "box_high": box_high}
        return (f"👀 <b>{label} 박스 하단 반전 양봉 감지</b>\n"
                f"박스: ${box_low:,.2f} ~ ${box_high:,.2f}\n"
                f"신호봉 고점 ${last_closed[2]:,.2f} 돌파 시 매수 신호 발송\n"
                f"(향후 {CFG['confirm_bars']}봉/90분 내)")
    zone_pos = (cur_price - box_low) / box_h * 100 if box_h > 0 else 0
    print(f"  ⚪ {label}: 대기 (박스 ${box_low:,.0f}~${box_high:,.0f}, "
          f"현재 위치 {zone_pos:.0f}%)")
    state[symbol] = {"phase": "idle"}
    return None

# ══════════════════════════════════════════════════════
#  메인
# ══════════════════════════════════════════════════════
def run_check(send: bool = True):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{'='*56}")
    print(f"  박스전략 단타 모니터 — {now}")
    print(f"{'='*56}")
    if not HAS_CCXT:
        print("  ccxt 필요: pip install ccxt")
        return
    state = load_state()
    alerts = []
    for sym in CFG["symbols"]:
        msg = check_coin(sym, state)
        if msg:
            alerts.append(msg)
        time.sleep(0.3)
    save_state(state)
    if alerts and send:
        for m in alerts:
            send_telegram(m)
        print(f"\n  신호 {len(alerts)}건 발송")
    elif alerts:
        print(f"\n  신호 {len(alerts)}건 (발송 생략 --check)")
        for m in alerts:
            print("  ---"); print("  " + m.replace("<b>","").replace("</b>",""))
    else:
        print(f"\n  신호 없음")

if __name__ == "__main__":
    args = sys.argv[1:]
    if "--reset" in args:
        if os.path.exists(CFG["state_file"]):
            os.remove(CFG["state_file"])
        print("  상태 초기화 완료")
        sys.exit(0)
    run_check(send=("--check" not in args))
