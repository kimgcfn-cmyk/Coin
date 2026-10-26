"""
급등 코인 탐지 모니터 — "펌프 후 되돌림 관찰" 알림 (알림 전용 — 실제 주문 없음)
============================================================
Bitget USDT-M 선물 전 종목을 주기적으로 훑어서, 단기간에 가격이 급등하면서
거래량까지 터지는 코인을 찾아 디스코드로 알린다. 매수/매도 주문은 내지 않는다.
(API 키 불필요 — Bitget 공개 시세 API만 사용)

[탐지 방식 — 2단계]
  1단계 (가벼움): 티커 1회 호출로 전 종목 현재가를 받아 state에 쌓아두고,
     과거 샘플과 비교해 5분 상승률을 계산한다.
       5분 +5% 이상이면 후보
     (24시간 거래대금이 min_vol_usdt 미만인 종목, exclude 종목은 제외)
  2단계 (후보만): 5분봉을 받아 거래량 배율을 확인한다.
       (a) 최근 3개 마감봉 평균 거래대금 / 그 직전 20개 마감봉 평균 >= 5배
       (b) 최근 3개 마감봉 평균 거래대금 / 24시간 평균(5분당) >= 3배
     → 가격 급등 + 거래량 동반일 때만 알림 (거래량 없는 호가 장난 걸러냄)
     (b)는 최근 100분이 유독 한산했던 코인이 평소 수준의 거래만으로 배수가 커지는 경우를 거른다.
     24시간 평균 = 티커의 24시간 거래대금 ÷ 288 (현재 급등분이 포함되어 약간 높게 잡힘)

[알림 내용]
  현재가 · 5분 상승률 · 거래량 배수(100분 대비/24시간 평균 대비) · 24시간 등락/거래대금/펀딩비
  (메시지 레이아웃만 기존 급등 알림과 같고, 방향/손절/목표 같은 매매 규칙은 넣지 않는다)
  12시간마다 '스캔 진행중' 생존 알림을 보낸다 (heartbeat_hours, 0이면 끔)

[중복 알림 방지]
  1분마다 실행되므로 이전 실행이 끝나지 않았으면(lock) 건너뛰고,
  한 번 실행은 run_budget_sec(40초)를 넘기지 않는다.
  같은 종목은 cooldown_min(60분) 동안 재알림하지 않는다.
  단, 직전 알림가보다 rearm_pct(4%) 이상 더 오르면 다시 알린다.
  한 번 실행에 보내는 알림은 max_alerts(5건)까지 (점수 높은 순).

[2026-10-26 개선 — 그리드 봇 전략과 맞추기]
  - 알림 대상: 24시간 거래대금 5M 이상(기존 1M) + 봇 운용 종목(own_symbols: ORDER·CAP·T)은 거래대금과 상관없이 항상 감시, 알림에 ⭐ 표시
  - 알림에 10시간 등락·고점 대비·호가 깊이·스프레드·유동성 판정(봇이 거래할 만한 수준인지)을 함께 표시
  - 메시지 빈도: 새 급등 알림과 "관찰 종료"(알림 후 60분)는 즉시, 관찰 중인 종목이 있으면 10분마다 요약 1건만 (cron 5분 주기 전제)
  - 사후 추적: 알림 1시간·4시간 뒤 가격 변화와 그 사이 최고·최저를 coin_surge_outcomes.json 에 자동 기록 → --outcomes 로 확인,
    12시간 생존 알림에 평균 결과를 같이 보냄, 맥의 coin_excel_export.py 가 엑셀(급등알림_사후추적.xlsx)로 만든다
  - 알림 문구는 "추격 매수 신호 아님 / 펌프 후 되돌림 관찰"로 바꿈 (최근 알림 9건 중 7건이 이후 하락했으나 표본이 적어 보장 아님)

[주의]
  - 최초 실행 후 history가 쌓여야 감지가 시작된다 (약 5분 뒤부터).
    cron 1분 주기를 전제로 한다.
  - 급등 "이후" 알림이라 추격매수 신호가 아니다. 이미 오른 뒤라는 점을 감안할 것.

[.env 설정 — 이미 있다면 재사용]
  DISCORD_WEBHOOK_URL_ALERT=...  (기존 급등 알림 채널, 없으면 DISCORD_WEBHOOK_URL 사용)

실행:
  python coin_surge_monitor.py           # 1회 점검 + 알림 발송 (cron용)
  python coin_surge_monitor.py --check   # 발송/저장 없이 결과만 출력
  python coin_surge_monitor.py --top     # 지금 24시간 상승률 상위 종목 확인 (진단용)
  python coin_surge_monitor.py --status  # history 축적 현황 / 최근 알림
  python coin_surge_monitor.py --outcomes # 알림 1시간·4시간 뒤 결과 표
  python coin_surge_monitor.py --reset   # 상태 초기화

cron 등록 (1분마다):
  * * * * * cd /home/ec2-user/trading-bot && venv/bin/python coin_surge_monitor.py >> logs/coin_surge.log 2>&1
"""

import sys, json, os, re, time, fcntl
from datetime import datetime, timedelta, timezone

import requests

try:
    import config as _cfg
    def _cget(key):
        return _cfg._get(key) if hasattr(_cfg, "_get") else getattr(_cfg, key, "")
    DISCORD_WEBHOOK_URL = _cget("DISCORD_WEBHOOK_URL_ALERT") or _cget("DISCORD_WEBHOOK_URL")
except ImportError:
    print("⚠️  config.py 없음 — .env 설정 확인 필요")
    DISCORD_WEBHOOK_URL = ""

# ══════════════════════════════════════════════════════
#  설정
# ══════════════════════════════════════════════════════
CFG = {
    "product_type"     : "USDT-FUTURES",
    "min_vol_usdt"     : 5_000_000,                   # 24시간 거래대금 하한 (2026-10-26 1M→5M: 봇이 거래할 만한 유동성만)
    "own_symbols"      : ["ORDERUSDT", "CAPUSDT", "TUSDT"],   # 봇 운용 종목 — 거래대금 하한과 무관하게 감시, 알림에 ⭐ 표시
    "min_depth_usdt"   : 1000,                        # 유동성 판정: 호가 상위 5개(매수·매도 중 작은 쪽) 합계
    "max_spread_pct"   : 0.2,                         # 유동성 판정: 스프레드 상한
    "outcome_file"     : "coin_surge_outcomes.json",  # 알림 사후 추적 기록
    "outcome_minutes"  : [60, 240],                   # 알림 후 이 시간(분)이 지난 첫 점검에서 가격 변화를 기록
    "outcome_keep_days": 14,
    "summary_min"      : 10,                          # 관찰 중인 급등 종목 요약 간격(분). 새 알림·관찰 종료는 즉시, 나머지는 이 간격으로만
    "watch_min"        : 60,                          # 알림 후 이 시간(분) 동안을 "관찰 중"으로 본다(= 쿨다운 시간과 같음)
    "exclude"          : ["TQQQUSDT", "PLTRUSDT"],    # 그리드 봇이 따로 운용 중인 종목
    "windows"          : [(5, 5.0)],                          # (분, 상승률% 기준) — 5분 +5% 이상
    "history_keep_min" : 15,                          # 가격 샘플 보관 시간 (구간 5분 + 여유)
    "candle_gran"      : "5m",
    "vol_recent_bars"  : 3,                           # 최근 N개 마감봉 평균
    "vol_base_bars"    : 20,                          # 그 직전 N개 마감봉 평균과 비교
    "min_vol_ratio"    : 5.0,                         # 거래량 배율 하한 (직전 100분 평균 대비)
    "min_vol_ratio_24h": 3.0,                         # 거래량 배율 하한 (24시간 평균 대비, 2026-10-23 최근 알림 10건 기준 3배 시 6건 통과)
    "max_candidates"   : 20,                          # 2단계 확인 최대 개수
    "max_alerts"       : 5,                           # 1회 실행당 최대 알림 수
    "cooldown_min"     : 60,                          # 같은 종목 재알림 금지 시간
    "rearm_pct"        : 4.0,                         # 직전 알림가 대비 이만큼 더 오르면 재알림
    "run_budget_sec"   : 40,                          # 1회 실행 시간 상한 (1분 cron 겹침 방지)
    "heartbeat_hours"  : 12,                          # '스캔 진행중' 생존 알림 간격 (0이면 끔)
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
            bid = float(x.get("bidPr") or 0); ask = float(x.get("askPr") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        if price > 0:
            out[sym] = {"price": price, "vol": vol, "chg24": chg24, "funding": funding, "bid": bid, "ask": ask}
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
            and (t["vol"] >= CFG["min_vol_usdt"] or sym in CFG["own_symbols"]))

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

def volume_ratio_24h(rows: list, vol24: float):
    """최근 3개 마감봉 평균 거래대금 / 24시간 평균(5분당 = 24시간 거래대금 / 288). 계산 불가면 None."""
    closed = rows[:-1]
    n = CFG["vol_recent_bars"]
    if len(closed) < n or vol24 <= 0:
        return None
    recent_avg = sum(c[6] for c in closed[-n:]) / n
    return recent_avg / (vol24 / 288)

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

def kst_now() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S KST")

def book_depth(sym: str):
    """호가 상위 5개 합계(USDT): 매수·매도 중 작은 쪽. 조회 실패 시 None."""
    d = _api_get("/api/v2/mix/market/merge-depth", {"symbol": sym, "productType": CFG["product_type"], "limit": "15"})
    try:
        b = sum(float(x[0]) * float(x[1]) for x in d.get("bids", [])[:5]); a = sum(float(x[0]) * float(x[1]) for x in d.get("asks", [])[:5])
        return min(a, b)
    except Exception:
        return None

def enrich(c: dict, rows: list) -> dict:
    """알림용 추가 지표: 10시간 등락, 고점 대비, 호가 깊이, 스프레드, 유동성 판정."""
    ex = {"net10h": None, "from_high": None, "depth": None, "spread": None, "liquid": False, "own": c["symbol"] in CFG["own_symbols"]}
    if len(rows) >= 121:
        ex["net10h"] = (c["price"] / rows[-121][4] - 1) * 100
        hi = max(r[2] for r in rows[-120:])
        ex["from_high"] = (c["price"] / hi - 1) * 100 if hi > 0 else None
    if c.get("bid", 0) > 0 and c.get("ask", 0) > 0:
        ex["spread"] = (c["ask"] / c["bid"] - 1) * 100
    ex["depth"] = book_depth(c["symbol"])
    ex["liquid"] = bool(c["vol"] >= CFG["min_vol_usdt"] and ex["spread"] is not None and ex["spread"] <= CFG["max_spread_pct"]
                        and ex["depth"] is not None and ex["depth"] >= CFG["min_depth_usdt"])
    return ex

# ── 알림 사후 추적 ──
def load_outcomes() -> dict:
    p = CFG["outcome_file"]
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            return {}
    return {}

def save_outcomes(o: dict):
    tmp = CFG["outcome_file"] + ".tmp"
    json.dump(o, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, CFG["outcome_file"])

def record_alert(o: dict, c: dict, ratio: float, ratio24: float, ex: dict, now: int):
    o.setdefault("alerts", []).append({"sym": c["symbol"], "ts": now, "price": c["price"], "chg24": round(c["chg24"], 2), "vol": round(c["vol"]),
        "ratio": round(ratio, 1), "ratio24": round(ratio24, 1), "net10h": None if ex["net10h"] is None else round(ex["net10h"], 1),
        "from_high": None if ex["from_high"] is None else round(ex["from_high"], 1), "depth": None if ex["depth"] is None else round(ex["depth"]),
        "spread": None if ex["spread"] is None else round(ex["spread"], 3), "liquid": ex["liquid"], "own": ex["own"], "hi": c["price"], "lo": c["price"]})

def update_outcomes(o: dict, tickers: dict, now: int):
    """열려 있는 알림마다: 최고·최저가 갱신, 60분/240분이 지난 첫 점검에서 가격을 기록."""
    keep = now - CFG["outcome_keep_days"] * 86400
    o["alerts"] = [a for a in o.get("alerts", []) if a["ts"] >= keep]
    for a in o["alerts"]:
        t = tickers.get(a["sym"])
        if not t: continue
        age = now - a["ts"]
        if age <= max(CFG["outcome_minutes"]) * 60 + 120:
            a["hi"] = max(a.get("hi", a["price"]), t["price"]); a["lo"] = min(a.get("lo", a["price"]), t["price"])
        for m in CFG["outcome_minutes"]:
            k = f"p{m}"
            if k not in a and age >= m * 60:
                a[k] = t["price"]; a[f"hi{m}"] = a["hi"]; a[f"lo{m}"] = a["lo"]

def outcome_summary(o: dict) -> str:
    """알림 결과 요약(문자열). 완료된 건만 집계."""
    out = []
    for m in CFG["outcome_minutes"]:
        vals = [(a[f"p{m}"] / a["price"] - 1) * 100 for a in o.get("alerts", []) if f"p{m}" in a]
        if vals:
            down = sum(1 for v in vals if v < 0)
            out.append(f"{fmt_window(m)} 후 평균 {sum(vals)/len(vals):+.1f}% (하락 {down}/{len(vals)}건)")
    return " · ".join(out) if out else "아직 결과가 쌓이는 중"

def show_outcomes():
    o = load_outcomes(); now = int(time.time())
    al = sorted(o.get("alerts", []), key=lambda a: -a["ts"])
    print(f"  기록된 알림 {len(al)}건 — " + outcome_summary(o))
    for a in al[:30]:
        kst = (datetime.fromtimestamp(a["ts"], timezone.utc) + timedelta(hours=9)).strftime("%m-%d %H:%M")
        r = lambda m: f"{(a[f'p{m}']/a['price']-1)*100:+6.1f}%" if f"p{m}" in a else "   대기"
        print(f"    {kst} {a['sym']:<12} 알림가 {fmt_price(a['price']):>10}  1h {r(60)}  4h {r(240)}  4h내 최고 {((a.get('hi240', a.get('hi', a['price']))/a['price']-1)*100):+5.1f}% 최저 {((a.get('lo240', a.get('lo', a['price']))/a['price']-1)*100):+5.1f}%  "
              f"{'⭐운용 ' if a.get('own') else ''}{'유동성 OK' if a.get('liquid') else '유동성 얇음'}")

def build_message(c: dict, ratio: float, ratio24: float, ex: dict = None) -> str:
    moves = " / ".join(f"{fmt_window(m)} {v:+.1f}%"
                       for m, v in c["changes"].items() if v is not None)
    sep = "━━━━━━━━━━━━━━━━━━━"
    ex = ex or {}
    star = "⭐ <b>봇 운용 종목</b> — " if ex.get("own") else ""
    lines = [f"🚨 <b>급등 탐지(되돌림 관찰) — {c['symbol']}</b>", f"{star}{kst_now()}", sep,
             f"현재가: {fmt_price(c['price'])}",
             f"상승률: {moves}",
             f"거래량 배수: 직전 100분 대비 {ratio:.1f}x / 24시간 평균 대비 {ratio24:.1f}x",
             f"24시간 {c['chg24']:+.1f}% · 거래대금 {fmt_big(c['vol'])} USDT · 펀딩 {c['funding']:+.4f}%"]
    if ex.get("net10h") is not None:
        lines.append(f"10시간 {ex['net10h']:+.1f}% · 10시간 고점 대비 {ex['from_high']:+.1f}%")
    if ex.get("depth") is not None or ex.get("spread") is not None:
        dp = "-" if ex.get("depth") is None else f"{ex['depth']:,.0f}"; sp = "-" if ex.get("spread") is None else f"{ex['spread']:.3f}%"
        lines.append(f"호가 깊이(상위5) {dp} USDT · 스프레드 {sp} → " + ("✅ 봇이 거래할 만한 유동성" if ex.get("liquid") else "⚠️ 유동성 얇음(봇 거래 비권장)"))
    lines += [sep, "⚠️ 알림 전용 — 자동 주문 없음. 이미 오른 뒤 감지된 신호라 추격 매수 신호가 아니라 '되돌림 관찰'용입니다.",
              "ℹ️ 알림 1시간·4시간 뒤 결과는 자동 기록됩니다 (최근 표본에서는 오른 뒤 내려간 경우가 많았지만 표본이 적어 보장은 아닙니다)."]
    return "\n".join(lines)

def build_watch_messages(state: dict, tickers: dict, now: int, new_syms: list) -> list:
    """관찰 중인 급등 종목(알림 후 watch_min분 이내) 기준으로: 관찰이 끝난 종목은 즉시 1건, 나머지는 summary_min분마다 요약 1건.
    새 알림은 기존 상세 알림이 즉시 나가므로 여기서는 요약 타이머만 초기화한다."""
    watch = CFG["watch_min"] * 60
    active = {s: a for s, a in state.get("alerted", {}).items() if now - a["ts"] < watch}
    msgs = []
    prev = state.get("active_prev")
    removed = [s for s in (prev or []) if s not in active]
    def chg(sym, a):
        t = tickers.get(sym)
        return None if not t else (t["price"] / a["price"] - 1) * 100
    if removed:
        lines = []
        for s in removed:
            a = state.get("alerted", {}).get(s)
            c = chg(s, a) if a else None
            lines.append(f"{s}: 알림가 {fmt_price(a['price'])} → " + (f"현재 {c:+.1f}%" if c is not None else "시세 조회 불가") if a else f"{s}: 기록 없음")
        msgs.append("🏁 <b>급등 관찰 종료</b> (알림 후 " + str(CFG["watch_min"]) + "분 경과)\n" + "\n".join(lines))
    if new_syms:
        state["last_summary_ts"] = now
    elif active and now - state.get("last_summary_ts", 0) >= CFG["summary_min"] * 60 - 30:
        lines = []
        for s, a in sorted(active.items(), key=lambda x: -x[1]["ts"]):
            c = chg(s, a); h = state.get("hist", {}).get(s, []); c5 = change_over(h, now, 5, tickers[s]["price"]) if (h and s in tickers) else None
            lines.append(f"{s}: 알림 {(now - a['ts']) // 60}분 전 · 알림가 {fmt_price(a['price'])} → " + (f"현재 {c:+.1f}%" if c is not None else "조회 불가") + (f" · 최근 5분 {c5:+.1f}%" if c5 is not None else ""))
        msgs.append(f"🔎 <b>급등 관찰 {CFG['summary_min']}분 요약</b> (관찰 중 {len(active)}개 — 새 급등이 없으면 이 요약만 옵니다)\n" + "\n".join(lines))
        state["last_summary_ts"] = now
    state["active_prev"] = list(active)
    return msgs

def _send_watch(msgs: list, send: bool):
    for m in msgs:
        if send:
            send_discord(m); time.sleep(0.5)
        else:
            print("\n  [--check, 발송 생략]\n  " + m.replace("<b>", "").replace("</b>", "").replace("\n", "\n  "))

def build_heartbeat(outcome_text: str = "") -> str:
    conds = " / ".join(f"{fmt_window(m)} {t:.1f}%↑" for m, t in CFG["windows"])
    return (f"🔍 <b>스캔 진행중</b>\n"
            f"{kst_now()}\n"
            f"조건: {conds} & 거래량 {CFG['min_vol_ratio']}x↑(100분 대비) / {CFG['min_vol_ratio_24h']}x↑(24시간 평균 대비)\n"
            f"{('알림 사후 결과: ' + outcome_text + chr(10)) if outcome_text else ''}"
            f"({CFG['heartbeat_hours']}시간 간격으로 알립니다)")

# ══════════════════════════════════════════════════════
#  메인 점검
# ══════════════════════════════════════════════════════
def run_check(send: bool = True):
    t0 = time.time()
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

    hb_hours = CFG["heartbeat_hours"]
    if hb_hours and now - state.get("last_heartbeat", 0) >= hb_hours * 3600:
        if send:
            send_discord(build_heartbeat(outcome_summary(outcomes)))
            state["last_heartbeat"] = now
        else:
            print("  [--check] 생존 알림('스캔 진행중') 발송 시점 — 발송 생략")

    outcomes = load_outcomes()
    if send:
        update_outcomes(outcomes, tickers, now)
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
        if time.time() - t0 > CFG["run_budget_sec"]:
            print(f"  ⏱ 실행 시간 {CFG['run_budget_sec']}초 초과 — 남은 후보는 다음 실행으로")
            break
        sym = c["symbol"]
        mv = " / ".join(f"{fmt_window(m)} {v:+.1f}%" for m, v in c["changes"].items() if v is not None)
        if in_cooldown(state, sym, c["price"], now):
            print(f"  ⏸ {sym}: 후보 ({mv}) — 쿨다운 중")
            continue
        checked += 1
        rows = fetch_candles(sym, 130)
        ratio = volume_ratio(rows)
        ratio24 = volume_ratio_24h(rows, c["vol"])
        time.sleep(0.15)
        if ratio is None:
            print(f"  ⚪ {sym}: 후보 ({mv}) — 캔들 부족, 거래량 확인 불가")
        elif ratio < CFG["min_vol_ratio"]:
            print(f"  ⚪ {sym}: 후보 ({mv}) — 거래량 ×{ratio:.1f} < ×{CFG['min_vol_ratio']} (제외)")
        elif ratio24 is None or ratio24 < CFG["min_vol_ratio_24h"]:
            r24 = "계산 불가" if ratio24 is None else f"×{ratio24:.1f}"
            print(f"  ⚪ {sym}: 후보 ({mv}) — 100분 대비 ×{ratio:.1f} 통과, 24시간 평균 대비 {r24} < ×{CFG['min_vol_ratio_24h']} (제외)")
        else:
            print(f"  🚀 {sym}: 급등 확정 ({mv}, 거래량 100분 대비 ×{ratio:.1f} / 24시간 평균 대비 ×{ratio24:.1f})")
            ex = enrich(c, rows)
            alerts.append((c, ratio, ratio24, ex))
            state.setdefault("alerted", {})[sym] = {"ts": now, "price": c["price"]}
            if send: record_alert(outcomes, c, ratio, ratio24, ex, now)

    watch_msgs = build_watch_messages(state, tickers, now, [a[0]["symbol"] for a in alerts]) if send else []
    # 24시간 지난 알림 기록 정리
    state["alerted"] = {s: a for s, a in state.get("alerted", {}).items()
                        if now - a["ts"] < 86400}

    if send:
        save_state(state); save_outcomes(outcomes)
    else:
        print("  (--check 모드: 상태 저장 생략)")

    if not alerts:
        print("\n  알림 없음" + (f" (관찰 메시지 {len(watch_msgs)}건)" if watch_msgs else ""))
        _send_watch(watch_msgs, send)
        return
    for c, ratio, ratio24, ex in alerts:
        msg = build_message(c, ratio, ratio24, ex)
        if send:
            send_discord(msg)
            time.sleep(0.5)
        else:
            print("\n  [--check, 발송 생략]")
            print("  " + msg.replace("<b>", "").replace("</b>", "").replace("\n", "\n  "))
    _send_watch(watch_msgs, send)
    if send:
        print(f"\n  ✅ 알림 {len(alerts)}건 발송" + (f" + 관찰 메시지 {len(watch_msgs)}건" if watch_msgs else ""))

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
def acquire_lock():
    """이전 실행이 아직 도는 중이면 None. (1분 cron에서 같은 급등을 중복 알림하지 않게)"""
    f = open(CFG["state_file"] + ".lock", "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f

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

    if "--outcomes" in args:
        show_outcomes()
        sys.exit(0)

    if "--top" in args:
        show_top()
        sys.exit(0)

    lock = None
    if "--check" not in args:
        lock = acquire_lock()
        if lock is None:
            print("  ⏭ 이전 실행이 아직 진행 중 — 이번 실행 건너뜀")
            sys.exit(0)
    run_check(send=("--check" not in args))
