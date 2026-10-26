"""
코인 그리드 전략 스크리너 — 어떤 종목에 coin_grid_bot.py 를 돌려도 되는지 골라준다 (2026-10-25)
================================================================================
Bitget USDT-M 선물 전체를 훑어서 '이 전략이 맞는 모양'(변동이 크고 위아래로 왕복하거나 상승, 하락 추세 아님, 유동성 충분)인 종목을 점수로 정렬한다.
근거: 2026-10-24~25 모의(54종목·10시간 윈도우 165개) — 10시간 고저폭 12% 이상에서 수익이 나고, 하락 추세(평균 -11)·잔잔한 횡보(≈0)에서는 안 맞았다.

사용:
  python coin_grid_screener.py                  # 후보 표만 출력 (주문·알림 없음)
  python coin_grid_screener.py --backtest 5     # 상위 5개를 coin_grid_backtest.py 로 최근 10시간 모의까지 붙여서 출력
  python coin_grid_screener.py --notify         # 후보가 바뀔 때(신규·제외)만 즉시, 안 바뀌면 10분마다 요약을 디스코드 추천 채널(.env DISCORD_WEBHOOK_URL_PICKS, 없으면 기본 채널)로 알림 — 매분 cron 전제
  python coin_grid_screener.py --min-volume 5000000 --top 15      # 최소 24시간 거래대금(USDT), 표시 개수
  --min-hour-volume 300000   최근 1시간 거래대금 하한(USDT, 기본 30만) — 24시간 숫자가 커도 지금 거래가 죽은 종목을 거른다
  --min-depth 1000           호가 상위 5개의 매수·매도 중 작은 쪽 합계 하한(USDT, 기본 1,000) — 20~30USDT 주문이 호가의 3% 이하가 되게
  python coin_grid_screener.py --symbol IRYS    # 특정 종목만 평가(통과/제외 사유 포함)
주의: 후보는 '시작해도 되는 모양'일 뿐 수익을 보장하지 않는다(최근 10시간 지표). 이미 크게 급등한 뒤인 종목은 고점 근처 위험이 있다. 투자 조언이 아니다.
"""
import sys, time, json, statistics as st
import requests

B = "https://api.bitget.com/api/v2/mix/market"
EXCLUDE = {"TQQQUSDT", "PLTRUSDT"}     # 주식 파생 등 제외할 종목
WIN = 120                               # 5분봉 120개 = 10시간

def tickers():
    return requests.get(f"{B}/tickers", params={"productType": "USDT-FUTURES"}, timeout=20).json()["data"]

def bars_5m(sym, n=200):
    for _ in range(3):
        try:
            r = requests.get(f"{B}/candles", params={"symbol": sym, "productType": "USDT-FUTURES", "granularity": "5m", "limit": str(n)}, timeout=15).json()
            d = r.get("data") or []
            if d:
                rows = sorted((int(x[0]), float(x[2]), float(x[3]), float(x[4]), float(x[6])) for x in d)   # ts, high, low, close, quoteVol
                return rows
        except Exception:
            pass
        time.sleep(0.6)
    return []

def book_depth(sym):
    """호가 상위 5개 합계(USDT): (매수, 매도)"""
    try:
        d = requests.get(f"{B}/merge-depth", params={"symbol": sym, "productType": "USDT-FUTURES", "limit": "15"}, timeout=15).json().get("data", {})
        b = sum(float(x[0]) * float(x[1]) for x in d.get("bids", [])[:5]); a = sum(float(x[0]) * float(x[1]) for x in d.get("asks", [])[:5])
        return b, a
    except Exception:
        return 0.0, 0.0

def evaluate(t, rows, min_vol=10_000_000, min_h=300_000):
    """t: 티커 dict, rows: 5분봉. 반환: dict(지표, 판정, 점수, 사유)"""
    out = {"symbol": t["symbol"]}
    if len(rows) < WIN + 5:
        out.update(ok=False, reasons=["데이터 부족"], score=0); return out
    w = rows[-WIN:]; closes = [r[3] for r in w]; hi = max(r[1] for r in w); lo = min(r[2] for r in w)
    p0, p1 = closes[0], closes[-1]
    rng = (hi - lo) / p0 * 100; net = (p1 / p0 - 1) * 100; from_high = (p1 / hi - 1) * 100
    last2h = (p1 / closes[-24] - 1) * 100 if len(closes) >= 24 else 0.0
    to_peak = (hi / p0 - 1) * 100
    vol24 = float(t["usdtVolume"]); bid = float(t.get("bidPr") or 0); ask = float(t.get("askPr") or 0)
    spread = (ask / bid - 1) * 100 if bid > 0 and ask > 0 else 9.9
    fund = float(t.get("fundingRate") or 0) * 100
    # 거래대금 급증 여부(최근 1시간 vs 직전 9시간 평균 시간당)
    qv = [r[4] for r in w]; recent = sum(qv[-12:]); base = sum(qv[:-12]) / 9 if sum(qv[:-12]) > 0 else 0
    hour_vol = recent
    vol_ratio = recent / base if base > 0 else 0.0
    # 유형
    if to_peak >= 10 and from_high <= -10: kind = "급등 후 하락"
    elif net <= -8: kind = "하락 추세"
    elif net >= 8 and from_high > -6: kind = "꾸준한 상승"
    elif net >= 8: kind = "상승(되돌림 큼)"
    elif rng >= 12 and abs(net) < 8: kind = "큰 폭 왕복"
    else: kind = "잔잔한 횡보"
    reasons = []
    if hour_vol < min_h: reasons.append(f"최근 1시간 거래 적음({hour_vol/1e3:.0f}K<{min_h/1e3:.0f}K)")
    if vol24 < min_vol: reasons.append(f"거래대금 부족({vol24/1e6:.1f}M<{min_vol/1e6:.0f}M)")
    if spread > 0.2: reasons.append(f"스프레드 넓음({spread:.2f}%)")
    if rng < 10: reasons.append(f"변동 작음(10h 고저폭 {rng:.1f}%<10%)")
    if net <= -8: reasons.append(f"하락 추세(10h {net:+.1f}%)")
    if from_high <= -12: reasons.append(f"고점 대비 {from_high:.1f}% 하락 중")
    if last2h <= -6: reasons.append(f"최근 2시간 {last2h:+.1f}% 급락")
    ok = not reasons
    # 점수(통과한 종목끼리 비교용 0~100)
    s = 0.0
    s += min(40, max(0, (rng - 10) / 30 * 40))                       # 변동: 10%→0, 40%→40점
    s += 25 if net >= -3 else (10 if net > -8 else 0)                # 추세
    s += 15 if from_high >= -5 else (8 if from_high >= -10 else 0)   # 고점 근처 여부(너무 높으면 아래 경고)
    s += 10 if vol24 >= 10_000_000 else (6 if vol24 >= 3_000_000 else 0)
    s += 10 if spread <= 0.05 else (5 if spread <= 0.1 else 0)
    warn = []
    if rng > 80: warn.append("변동 극단(80%+) — 한도 작게")
    if from_high >= -1 and to_peak >= 20: warn.append("급등 직후 고점 근처 — 되돌림 위험")
    if fund > 0.05: warn.append(f"펀딩 {fund:+.3f}%(롱이 냄)")
    out.update(ok=ok, reasons=reasons, score=round(s, 1), hour_vol=hour_vol, kind=kind, rng=rng, net=net, from_high=from_high, last2h=last2h, vol24=vol24, spread=spread, fund=fund, vol_ratio=vol_ratio, price=p1, warn=warn)
    return out

def fmt_row(o):
    return (f"{o['symbol']:<12}{o['score']:>5.0f} {o['kind']:<10} 10h고저폭 {o['rng']:>6.1f}% 10h {o['net']:>+7.1f}% 고점대비 {o['from_high']:>+6.1f}% "
            f"2h {o['last2h']:>+6.1f}% 거래대금 {o['vol24']/1e6:>7.1f}M 1h {o['hour_vol']/1e3:>6.0f}K 호가깊이 {o.get('depth', 0):>6,.0f} 스프레드 {o['spread']:.3f}% 거래량(1h/평균) {o['vol_ratio']:>4.1f}x"
            + (f"  ⚠️ {' / '.join(o['warn'])}" if o['warn'] else ""))

def send_picks(text: str) -> bool:
    """추천 종목 알림은 별도 디스코드 채널(DISCORD_WEBHOOK_URL_PICKS)로 보낸다. 값이 없거나 전송에 실패하면 기존 채널(coin_grid_bot.send_discord)로 대신 보낸다."""
    import coin_grid_bot as g
    url = ""
    try:
        import config as _cfg
        url = (_cfg._get("DISCORD_WEBHOOK_URL_PICKS") if hasattr(_cfg, "_get") else getattr(_cfg, "DISCORD_WEBHOOK_URL_PICKS", "")) or ""
    except Exception:
        pass
    if url:
        try:
            content = g._html_to_discord_markdown(text)
            if len(content) > 1900: content = content[:1900] + "\n…(생략)"
            r = requests.post(url, json={"content": content}, timeout=10)
            if r.status_code in (200, 204): return True
            print(f"  추천 채널 전송 실패(HTTP {r.status_code}) — 기존 채널로 대신 보냅니다")
        except Exception as e:
            print(f"  추천 채널 전송 오류({type(e).__name__}) — 기존 채널로 대신 보냅니다")
    else:
        print("  (DISCORD_WEBHOOK_URL_PICKS 없음 — 기존 채널로 보냅니다)")
    return g.send_discord(text)

STATE_FILE = "coin_screener_state.json"
STREAK_GAP_MIN = 25     # 직전 점검이 이 분보다 오래됐으면(점검을 건너뛰었으면) 연속 추천으로 보지 않는다

def update_streak(symbols, now=None, path=STATE_FILE):
    """이번에 추천하는 종목마다 '연속 몇 번째 추천인지'를 센다. 직전 점검에도 있었고 점검 간격이 STREAK_GAP_MIN분 이내면 +1, 아니면 1(신규).
    추천에서 빠진 종목은 기록에서 지운다(다시 나오면 신규). 반환: {심볼: (횟수, 최초 추천 시각 datetime[UTC])}"""
    import os, datetime as dt
    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        st = json.load(open(path, encoding="utf-8"))
    except Exception:
        st = {}
    prev = st.get("symbols", {}); last_run = st.get("last_run")
    fresh = False
    if last_run:
        try: fresh = (now - dt.datetime.fromisoformat(last_run)).total_seconds() <= STREAK_GAP_MIN * 60
        except Exception: fresh = False
    out = {}; new_state = {}
    for sym in symbols:
        p = prev.get(sym) if fresh else None
        cnt = (p["count"] + 1) if p else 1
        first = dt.datetime.fromisoformat(p["first"]) if p else now
        out[sym] = (cnt, first); new_state[sym] = {"count": cnt, "first": first.isoformat()}
    tmp = path + ".tmp"
    json.dump({"last_run": now.isoformat(), "symbols": new_state}, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
    os.replace(tmp, path)
    return out

def streak_label(cnt, first, now=None):
    """'🆕 신규' 또는 '🔁 N번째 추천 (최초 HH:MM KST, M분째)'"""
    import datetime as dt
    now = now or dt.datetime.now(dt.timezone.utc)
    if cnt <= 1: return "🆕 신규"
    kst = dt.timezone(dt.timedelta(hours=9))
    mins = int((now - first).total_seconds() // 60)
    return f"🔁 {cnt}번째 추천 (최초 {first.astimezone(kst).strftime('%H:%M')} KST, {mins}분째)"

NOTIFY_FILE = "coin_screener_notify.json"
SUMMARY_MIN = 10        # 후보가 안 바뀌어도 이 분마다 요약을 보낸다

def _row_line(o, streak, now_utc):
    return (f"{o['symbol']} 점수 {o['score']:.0f} · {o['kind']} · 10h 고저폭 {o['rng']:.0f}% · 고점대비 {o['from_high']:+.0f}% · 24h {o['vol24']/1e6:.0f}M · 1h {o['hour_vol']/1e3:.0f}K · 호가 {o.get('depth', 0):,.0f}"
            + (" ⚠️" if o["warn"] else "") + f" · {streak_label(*streak[o['symbol']], now=now_utc)}")

def notify_changes(ok, bad, top, now_utc=None, path=NOTIFY_FILE, send=None):
    """매분 점검 결과를 비교해서 후보가 바뀔 때(신규·제외)만 즉시 알리고, 안 바뀌면 SUMMARY_MIN분마다 요약만 보낸다.
    반환: 보낸 메시지 종류('변경' / '요약' / None)와 본문. send: 테스트용 전송 함수(없으면 send_picks)."""
    import datetime as dt, os
    send = send or send_picks
    now_utc = now_utc or dt.datetime.now(dt.timezone.utc); now_ts = now_utc.timestamp()
    shown = ok[:top]; cur = [o["symbol"] for o in shown]
    try: st = json.load(open(path, encoding="utf-8"))
    except Exception: st = {}
    prev = st.get("symbols", []); last_ts = st.get("last_msg_ts", 0)
    streak = update_streak(cur, now_utc)
    new = [x for x in cur if x not in prev]; gone = [x for x in prev if x not in cur]
    kind = None; body = None
    foot = "※ 고저폭 12%+ · 하락 추세 아님 · 거래대금 10M+ · 최근 1시간 30만+ · 호가 깊이 1천+ 기준. 수익 보장 아님."
    why = {o["symbol"]: " / ".join(o.get("reasons", [])[:2]) or "후보 조건 이탈" for o in bad if "symbol" in o}
    if new or gone:
        kind = "변경"
        parts = ["🔔 <b>그리드 후보 변경</b> (최근 10시간 기준, 매분 점검)"]
        if new:
            parts.append(f"🆕 새로 들어온 후보 {len(new)}개")
            parts += [_row_line(o, streak, now_utc) for o in shown if o["symbol"] in new]
        if gone:
            parts.append(f"❌ 빠진 후보 {len(gone)}개: " + ", ".join(f"{g}({why.get(g, '대상 밖')})" for g in gone))
        stay = [x for x in cur if x not in new]
        if stay: parts.append(f"✔️ 유지 {len(stay)}개: " + ", ".join(f"{x}({streak[x][0]}회째)" for x in stay))
        if not cur: parts.append("현재 후보 없음")
        body = "\n".join(parts + [foot])
    elif cur and now_ts - last_ts >= SUMMARY_MIN * 60 - 20:
        kind = "요약"
        body = "\n".join([f"🔎 <b>그리드 후보 {SUMMARY_MIN}분 요약</b> (변경 없음, 최근 10시간 기준)"] + [_row_line(o, streak, now_utc) for o in shown] + [foot])
    if kind:
        send(body)
        st["last_msg_ts"] = now_ts
    st["symbols"] = cur
    tmp = path + ".tmp"; json.dump(st, open(tmp, "w", encoding="utf-8"), ensure_ascii=False); os.replace(tmp, path)
    print(f"\n디스코드 알림: {kind or '없음(변경 없음, 요약 시간 전)'} (신규 {len(new)} / 제외 {len(gone)} / 현재 {len(cur)}개)")
    return kind, body

def main():
    a = sys.argv[1:]
    def val(f, d=None): return a[a.index(f) + 1] if f in a and a.index(f) + 1 < len(a) else d
    min_vol = float(val("--min-volume", 10_000_000)); min_h = float(val("--min-hour-volume", 300_000)); min_depth = float(val("--min-depth", 1000)); top = int(val("--top", 10)); nbt = int(val("--backtest", 0)); notify = "--notify" in a
    tk = tickers()
    if "--symbol" in a:
        s = val("--symbol").upper(); s = s if s.endswith("USDT") else s + "USDT"
        tk = [x for x in tk if x["symbol"] == s]
        if not tk: print("종목을 찾지 못했습니다"); return
    else:
        tk = [x for x in tk if float(x["usdtVolume"]) >= min_vol and x["symbol"] not in EXCLUDE]
    print(f"스캔 대상 {len(tk)}개 (24h 거래대금 ≥ {min_vol/1e6:.1f}M USDT, 최근 1h ≥ {min_h/1e3:.0f}K, 호가깊이 ≥ {min_depth:,.0f}) — 최근 10시간 5분봉 기준\n")
    res = []
    for x in tk:
        o = evaluate(x, bars_5m(x["symbol"]), min_vol, min_h)
        if o.get("ok"):   # 나머지 조건을 통과한 종목만 호가 깊이를 확인(호출 절약)
            b, a_ = book_depth(x["symbol"]); o["depth"] = min(b, a_)
            if o["depth"] < min_depth:
                o["ok"] = False; o["reasons"].append(f"호가 얇음(상위5 {o['depth']:,.0f}<{min_depth:,.0f} USDT)")
        res.append(o); time.sleep(0.1)
    ok = sorted([o for o in res if o["ok"]], key=lambda o: -o["score"]); bad = [o for o in res if not o["ok"]]
    print(f"✅ 통과 {len(ok)}개 / ❌ 제외 {len(bad)}개")
    if "--symbol" in a:
        for o in res:
            print(("✅ " if o["ok"] else "❌ ") + (fmt_row(o) if "kind" in o else o["symbol"]))
            if o["reasons"]: print("   제외 사유:", " / ".join(o["reasons"]))
        return
    print("\n=== 후보 (점수 높은 순) ===")
    for o in ok[:top]: print(fmt_row(o))
    if not ok: print("  통과한 종목이 없습니다 — 지금은 이 전략이 맞는 장이 아닐 수 있습니다.")
    from collections import Counter
    import re as _re
    c = Counter(_re.sub(r"\s*[-+]?\d+(\.\d+)?%?", "", r.split("(")[0]).strip() for o in bad for r in o["reasons"])
    print("\n제외 사유 분포:", dict(c.most_common()))
    kinds = Counter(o["kind"] for o in res if "kind" in o); print("전체 유형 분포:", dict(kinds.most_common()))
    if nbt and ok:
        try:
            from coin_grid_backtest import candles, sim, CFG
            L0 = (3.0, 4.5, 6.0); LO = dict(confirm=3, max_per_min=1); CR = dict(win=5, pct=8.0, mode="pause", rebound=2.0, quiet=30)
            cur = dict(ladder=L0, lad_opts=LO, win=1, surge_pct=3.0, crash=CR)
            print(f"\n=== 상위 {nbt}개 참고 모의 (최근 10시간 1분봉, 현재 전략, 물량 0 시작) ===")
            for o in ok[:nbt]:
                ts, px, vol = candles(o["symbol"], 10)
                if not px: continue
                r = sim(px, vol, {**CFG, **cur}, 0.0)
                print(f"{o['symbol']:<12} 합계 {r['total']:>+7.2f} (실현 {r['realized']:+.2f} 평가 {r['unreal']:+.2f}) 매수 {r['buys']} 매도 {len(r['sells'])} 급등 {r['surge']}회 | 그냥 30USDT 보유 {30*(px[-1]/px[0]-1):+.2f}")
        except Exception as e:
            print("모의 실행 실패:", e)
    if notify:
        notify_changes(ok, bad, top)

if __name__ == "__main__":
    main()
