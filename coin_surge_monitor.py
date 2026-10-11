"""
급등 코인 탐지 모니터 (알림 전용 — 실제 주문 없음)
============================================================
Bitget USDT-M 선물 전 종목을 주기적으로 훑어서, 단기간에 가격이 급등하면서
거래량까지 터지는 코인을 찾아 디스코드로 알린다. 매수/매도 주문은 내지 않는다.
(API 키 불필요 — Bitget 공개 시세 API만 사용)

[탐지 방식 — 2단계]
  1단계 (가벼움): 티커 1회 호출로 전 종목 현재가를 받아 state에 쌓아두고,
     과거 샘플과 비교해 구간별 상승률을 계산한다.
       5분 +3% / 15분 +5% / 30분 +8%  중 하나라도 넘으면 후보
     (24시간 거래대금이 min_vol_usdt 미만인 종목, exclude 종목은 제외)
  2단계 (후보만): 5분봉을 받아 거래량 배율을 확인한다.
       최근 3개 마감봉 평균 거래대금 / 그 직전 20개 마감봉 평균 >= 2.5배
     → 가격 급등 + 거래량 동반일 때만 알림 (거래량 없는 호가 장난 걸러냄)

[중복 알림 방지]
  같은 종목은 cooldown_min(60분) 동안 재알림하지 않는다.
  단, 직전 알림가보다 rearm_pct(4%) 이상 더 오르면 다시 알린다.
  한 번 실행에 보내는 알림은 max_alerts(5건)까지 (점수 높은 순).

[주의]
  - 최초 실행 후 history가 쌓여야 감지가 시작된다 (5분 구간은 약 5분 뒤부터,
    30분 구간은 약 30분 뒤부터). cron 5분 주기를 전제로 한다.
  - 급등 "이후" 알림이라 추격매수 신호가 아니다. 이미 오른 뒤라는 점을 감안할 것.

[.env 설정 — 이미 있다면 재사용]
  DISCORD_WEBHOOK_URL_SURGE=...  (급등 전용 채널 권장, 없으면 DISCORD_WEBHOOK_URL 사용)

실행:
  python coin_surge_monitor.py           # 1회 점검 + 알림 발송 (cron용)
  python coin_surge_monitor.py --check   # 발송/저장 없이 결과만 출력
  python coin_surge_monitor.py --top     # 지금 24시간 상승률 상위 종목 확인 (진단용)
  python coin_surge_monitor.py --status  # history 축적 현황 / 최근 알림
  python coin_surge_monitor.py --reset   # 상태 초기화

cron 등록 (5분마다):
  */5 * * * * cd /home/ec2-user/trading-bot && venv/bin/python coin_surge_monitor.py >> logs/coin_surge.log 2>&1
"""

import sys, json, os, re, time
from datetime import datetime, timezone

import requests

try:
    import config as _cfg
    def _cget(key):
        return _cfg._get(key) if hasattr(_cfg, "_get") else getattr(_cfg, key, "")
    DISCORD_WEBHOOK_URL = _cget("DISCORD_WEBHOOK_URL_SURGE") or _cget("DISCORD_WEBHOOK_URL")
except ImportError:
    print("⚠️  config.py 없음 — .env 설정 확인 필요")
    DISCORD_WEBHOOK_URL = ""

# ══════════════════════════════════════════════════════
#  설정
# ══════════════════════════════════════════════════════
CFG = {
    "product_type"     : "USDT-FUTURES",
    "min_vol_usdt"     : 1_000_000,                   # 24시간 거래대금 하한 (너무 얇은 종목 제외, 약 140종목)
    "exclude"          : ["TQQQUSDT", "PLTRUSDT"],    # 그리드 봇이 따로 운용 중인 종목
    "windows"          : [(5, 3.0), (15, 5.0), (30, 8.0)],    # (분, 상승률% 기준)
    "history_keep_min" : 45,                          # 가격 샘플 보관 시간 (최대 구간 30분 + 여유)
    "candle_gran"      : "5m",
    "vol_recent_bars"  : 3,                           # 최근 N개 마감봉 평균
    "vol_base_bars"    : 20,                          # 그 직전 N개 마감봉 평균과 비교
    "min_vol_ratio"    : 2.5,                         # 거래량 배율 하한
    "max_candidates"   : 20,                          # 2단계 확인 최대 개수
    "max_alerts"       : 5,                           # 1회 실행당 최대 알림 수
    "cooldown_min"     : 60,                          # 같은 종목 재알림 금지 시간
    "rearm_pct"        : 4.0,                         # 직전 알림가 대비 이만큼 더 오르면 재알림
    "state_file"       : "coin_surge_state.json",
}

BITGET = "https://api.bitget.com"

# ══════════════════════════════════════════════════════
#  디스코드
# ══════════════════════════════════════════════════════
def _html_to_discord_markdown(text: str) -> str:
    text = re.sub(r"<b>(.*?)</b>", r"**\1**", text, flags=re.DOTALL)
    text = re.sub(r"<i>(.*?)</i>", r"*\1*", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", "", text)
    return text

def send_discord(text: str) -> bool:
    if not DISCORD_WEBHOOK_URL:
        print("  (디스코드 웹훅 미설정 — 콘솔 출력만)")
        print(text)
        return False
    content = _html_to_discord_markdown(text)
    if len(content) > 1900:
        content = content[:1900] + "\n…(생략)"
    try:
        r = requests.post(DISCORD_WEBHOOK_URL, json={"content": content}, timeout=10)
        return r.status_code in (200, 204)
    except Exception as e:
        print(f"  디스코드 오류: {e}")
        return False

# ══════════════════════════════════════════════════════
#  상태 저장/로드
# ══════════════════════════════════════════════════════
def load_state() -> dict:
    path = CFG["state_file"]
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_state(s: dict):
    tmp = CFG["state_file"] + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, CFG["state_file"])   # 쓰는 도중 끊겨도 기존 파일이 깨지지 않게

# ══════════════════════════════════════════════════════
#  시세 (Bitget 공개 API)
# ══════════════════════════════════════════════════════
def _api_get(path: str, params: dict, retries: int = 2):
    for _ in range(retries + 1):
        try:
            r = requests.get(BITGET + path, params=params, timeout=10)
            d = r.json()
            if d.get("code") == "00000":
                return d.get("data")
            print(f"  API 오류: {d.get('code')} {d.get('msg')}")
        except Exception as e:
            print(f"  API 조회 오류: {e}")
        time.sleep(1.0)
    return None

def fetch_tickers() -> dict:
    """전 종목 티커. 반환: {symbol: {price, vol, chg24(%), funding(%)}}"""
    data = _api_get("/api/v2/mix/market/tickers", {"productType": CFG["product_type"]})
    out = {}
    for x in data or []:
        try:
            sym = x["symbol"]
            price = float(x["lastPr"])
            vol = float(x.get("usdtVolume") or x.get("quoteVolume") or 0)
            chg24 = float(x.get("change24h") or 0) * 100
            funding = float(x.get("fundingRate") or 0) * 100
        except (KeyError, TypeError, ValueError):
            continue
        if price > 0:
            out[sym] = {"price": price, "vol": vol, "chg24": chg24, "funding": funding}
    return out

def fetch_candles(symbol: str, limit: int = 40) -> list:
    """캔들 [ts, open, high, low, close, baseVol, quoteVol] 오름차순. 마지막 봉은 진행 중."""
    data = _api_get("/api/v2/mix/market/candles", {
        "symbol": symbol, "productType": CFG["product_type"],
        "granularity": CFG["candle_gran"], "limit": str(limit),
    })
    rows = []
    for r in data or []:
        try:
            rows.append([int(r[0])] + [float(v) for v in r[1:7]])
        except (IndexError, TypeError, ValueError):
            continue
    rows.sort(key=lambda r: r[0])
    return rows

# ══════════════════════════════════════════════════════
#  탐지 로직
# ══════════════════════════════════════════════════════
def eligible(sym: str, t: dict) -> bool:
    return (sym.endswith("USDT") and sym not in CFG["exclude"]
            and t["vol"] >= CFG["min_vol_usdt"])

def update_history(state: dict, tickers: dict, now: int):
    """eligible 종목의 현재가 샘플을 쌓고 오래된 샘플/사라진 종목을 정리."""
    hist = state.setdefault("hist", {})
    cutoff = now - CFG["history_keep_min"] * 60
    live = set()
    for sym, t in tickers.items():
        if not eligible(sym, t):
            continue
        live.add(sym)
        arr = [p for p in hist.get(sym, []) if p[0] >= cutoff]
        arr.append([now, t["price"]])
        hist[sym] = arr
    for sym in list(hist):
        if sym not in live:
            del hist[sym]

def change_over(arr: list, now: int, minutes: int, price: float):
    """minutes 전 샘플 대비 상승률(%). 허용 오차 안에 샘플이 없으면 None."""
    target = now - minutes * 60
    tol = max(180, minutes * 60 * 0.25)
    best = None
    for ts, p in arr:
        d = abs(ts - target)
        if d <= tol and (best is None or d < best[0]):
            best = (d, p)
    if not best or best[1] <= 0:
        return None
    return (price / best[1] - 1) * 100

def find_candidates(state: dict, tickers: dict, now: int) -> list:
    """1단계: 구간별 상승률이 기준을 넘은 후보 (점수 높은 순)."""
    cands = []
    for sym, arr in state.get("hist", {}).items():
        t = tickers.get(sym)
        if not t:
            continue
        changes, hits = {}, []
        for minutes, thr in CFG["windows"]:
            c = change_over(arr, now, minutes, t["price"])
            changes[minutes] = c
            if c is not None and c >= thr:
                hits.append(c / thr)
        if hits:
            cands.append({"symbol": sym, "changes": changes, "score": max(hits), **t})
    cands.sort(key=lambda x: -x["score"])
    return cands

def volume_ratio(rows: list):
    """2단계: 최근 마감봉 평균 거래대금 / 직전 구간 평균. 데이터 부족하면 None."""
    closed = rows[:-1]   # 마지막 봉은 진행 중이라 제외
    n_recent, n_base = CFG["vol_recent_bars"], CFG["vol_base_bars"]
    if len(closed) < n_recent + n_base:
        return None
    recent = closed[-n_recent:]
    base = closed[-(n_recent + n_base):-n_recent]
    r_avg = sum(c[6] for c in recent) / n_recent
    b_avg = sum(c[6] for c in base) / n_base
    return r_avg / b_avg if b_avg > 0 else None

def in_cooldown(state: dict, sym: str, price: float, now: int) -> bool:
    a = state.get("alerted", {}).get(sym)
    if not a:
        return False
    if now - a["ts"] >= CFG["cooldown_min"] * 60:
        return False
    return price < a["price"] * (1 + CFG["rearm_pct"] / 100)

# ══════════════════════════════════════════════════════
#  알림 메시지
# ══════════════════════════════════════════════════════
def fmt_price(p: float) -> str:
    if p >= 100:  return f"{p:,.2f}"
    if p >= 1:    return f"{p:,.4f}"
    if p >= 0.01: return f"{p:,.5f}"
    return f"{p:.8f}".rstrip("0")

def fmt_big(v: float) -> str:
    if v >= 1e9: return f"{v/1e9:.2f}B"
    if v >= 1e6: return f"{v/1e6:.1f}M"
    if v >= 1e3: return f"{v/1e3:.0f}K"
    return f"{v:.0f}"

def fmt_window(m: int) -> str:
    return f"{m//60}시간" if m % 60 == 0 else f"{m}분"

def build_message(c: dict, ratio: float) -> str:
    base = c["symbol"][:-4]
    moves = " / ".join(f"{fmt_window(m)} {v:+.1f}%"
                       for m, v in c["changes"].items() if v is not None)
    return (f"🚀 <b>{base} 급등 감지</b> ({c['symbol']})\n"
            f"현재가: ${fmt_price(c['price'])}\n"
            f"📈 {moves}\n"
            f"📊 거래량 ×{ratio:.1f} (5분봉 최근 15분 / 직전 100분 평균)\n"
            f"24시간 {c['chg24']:+.1f}% · 거래대금 {fmt_big(c['vol'])} USDT\n"
            f"펀딩비 {c['funding']:+.4f}%\n"
            f"⚠️ 이미 오른 뒤의 알림입니다 — 추격 진입은 신중히")

# ══════════════════════════════════════════════════════
#  메인 점검
# ══════════════════════════════════════════════════════
def run_check(send: bool = True):
    now = int(time.time())
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{'='*56}")
    print(f"  급등 코인 탐지 — {stamp}")
    print(f"{'='*56}")

    tickers = fetch_tickers()
    if not tickers:
        print("  ❌ 티커 조회 실패 — 이번 점검 건너뜀")
        return

    state = load_state()
    update_history(state, tickers, now)
    n_elig = len(state["hist"])
    depth = max((now - arr[0][0] for arr in state["hist"].values() if arr), default=0) / 60
    print(f"  전체 {len(tickers)}종목 중 감시 대상 {n_elig}종목 "
          f"(24h 거래대금 {fmt_big(CFG['min_vol_usdt'])}+), history {depth:.0f}분 축적")

    cands = find_candidates(state, tickers, now)
    if not cands:
        first = min(m for m, _ in CFG["windows"])
        print("  후보 없음 (급등 구간 기준 미달)" if depth >= first
              else f"  history 축적 중 — 약 {first}분 뒤부터 감지 시작")
    alerts = []
    checked = 0
    for c in cands:
        if len(alerts) >= CFG["max_alerts"] or checked >= CFG["max_candidates"]:
            break
        sym = c["symbol"]
        mv = " / ".join(f"{fmt_window(m)} {v:+.1f}%" for m, v in c["changes"].items() if v is not None)
        if in_cooldown(state, sym, c["price"], now):
            print(f"  ⏸ {sym}: 후보 ({mv}) — 쿨다운 중")
            continue
        checked += 1
        ratio = volume_ratio(fetch_candles(sym))
        time.sleep(0.15)
        if ratio is None:
            print(f"  ⚪ {sym}: 후보 ({mv}) — 캔들 부족, 거래량 확인 불가")
        elif ratio < CFG["min_vol_ratio"]:
            print(f"  ⚪ {sym}: 후보 ({mv}) — 거래량 ×{ratio:.1f} < ×{CFG['min_vol_ratio']} (제외)")
        else:
            print(f"  🚀 {sym}: 급등 확정 ({mv}, 거래량 ×{ratio:.1f})")
            alerts.append((c, ratio))
            state.setdefault("alerted", {})[sym] = {"ts": now, "price": c["price"]}

    # 24시간 지난 알림 기록 정리
    state["alerted"] = {s: a for s, a in state.get("alerted", {}).items()
                        if now - a["ts"] < 86400}

    if send:
        save_state(state)
    else:
        print("  (--check 모드: 상태 저장 생략)")

    if not alerts:
        print("\n  알림 없음")
        return
    for i, (c, ratio) in enumerate(alerts):
        msg = build_message(c, ratio)
        if send:
            send_discord(msg)
            time.sleep(0.5)
        else:
            print("\n  [--check, 발송 생략]")
            print("  " + msg.replace("<b>", "").replace("</b>", "").replace("\n", "\n  "))
    if send:
        print(f"\n  ✅ 알림 {len(alerts)}건 발송")

# ══════════════════════════════════════════════════════
#  진단 도구
# ══════════════════════════════════════════════════════
def show_top(n: int = 15):
    tickers = fetch_tickers()
    rows = [(s, t) for s, t in tickers.items() if eligible(s, t)]
    rows.sort(key=lambda x: -x[1]["chg24"])
    print(f"\n  24시간 상승률 상위 {n} (거래대금 {fmt_big(CFG['min_vol_usdt'])}+ 종목 {len(rows)}개 중)")
    for s, t in rows[:n]:
        print(f"    {s:<16} {t['chg24']:+7.1f}%   ${fmt_price(t['price']):>12}   "
              f"거래대금 {fmt_big(t['vol']):>7}")

def show_status():
    st = load_state()
    now = int(time.time())
    hist = st.get("hist", {})
    if not hist:
        print("  history 없음 (아직 시작 안 함)")
    else:
        depth = max((now - arr[0][0] for arr in hist.values() if arr), default=0) / 60
        print(f"  감시 종목 {len(hist)}개, history {depth:.0f}분 축적")
    alerted = st.get("alerted", {})
    print(f"  최근 24시간 알림 {len(alerted)}건")
    for s, a in sorted(alerted.items(), key=lambda x: -x[1]["ts"]):
        ago = (now - a["ts"]) / 60
        print(f"    {s:<16} {ago:5.0f}분 전  알림가 ${fmt_price(a['price'])}")

# ══════════════════════════════════════════════════════
#  실행
# ══════════════════════════════════════════════════════
if __name__ == "__main__":
    args = sys.argv[1:]

    if "--reset" in args:
        if os.path.exists(CFG["state_file"]):
            os.remove(CFG["state_file"])
        print("  ✅ 급등 탐지 상태 초기화 완료")
        sys.exit(0)

    if "--status" in args:
        show_status()
        sys.exit(0)

    if "--top" in args:
        show_top()
        sys.exit(0)

    run_check(send=("--check" not in args))
