"""
수익곡선 필터 관찰 모드 — 최근 24시간 1분봉으로 즉시 백테스트 (2026-10-28)
================================================================================
사용:  python3 coin_cf_observe.py [--hours 24] [--notify] [--symbols cap,q,tag]
동작:  Bitget 공개 1분봉(최근 N시간 + 시드 70분)으로 CAP·Q·TAG 롱숏합동 실전 프리셋을 봇 엔진(coin_avg_ladder_bot.cf_step) 그대로 재생해
       '수익곡선 필터 없음' vs '필터 적용'을 비교한다. 실제 주문·상태 변경 없음(전부 모의, 거래소는 시세 조회만).
필터(롱+숏 합산 곡선 기준, 기준 자본 합 = 한도÷5×2 = 200 USDT):  하락 −5%(10 USDT)면 롱·숏 둘 다 정지 + 각 잔량 100 USDT만 남김 → 그림자 한 쌍의 합산 곡선이 저점 +8%(16 USDT) 오르고 최소 정지 시간(기본 120분)이 지나면 둘 다 재가동.
       정지 중에는 반대쪽 봇에 사다리 단계 0으로 보인다. 수동 편입·정정이 없는 순수 시세 재생이다(슬리피지 0.30%·수수료 0.04% 가정, 실제보다 보수적).
주의:  과거 재생 결과일 뿐 투자 조언이 아니다. 필터를 실전 봇에 켜는 것은 별도 지시가 있을 때만(프리셋 cfg "curve_filter": "live").
"""
import sys, os, json, time, requests
import datetime as dt
import coin_avg_ladder_bot as b
import coin_grid_bot as g
from coin_cf_pair import PairFilter

KST = dt.timezone(dt.timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))
SYM = {"cap": "CAPUSDT", "q": "QUSDT", "tag": "TAGUSDT"}
SLIP = 0.0030
SEED_MIN = 70


def candles(sym, minutes):
    """Bitget 공개 1분봉 종가 (시각 오름차순). 반환: [(ts_sec, close)]"""
    end = int(time.time() * 1000); start = end - minutes * 60 * 1000; rows = {}
    while end > start:
        r = requests.get("https://api.bitget.com/api/v2/mix/market/history-candles", params={"symbol": sym, "productType": "USDT-FUTURES", "granularity": "1m", "endTime": str(end), "limit": "200"}, timeout=15).json().get("data") or []
        if not r: break
        for x in r: rows[int(x[0])] = float(x[4])
        end = min(int(x[0]) for x in r) - 1; time.sleep(0.1)
    return [(t / 1000, rows[t]) for t in sorted(rows) if t >= start]


def preset(name):
    return json.load(open(os.path.join(HERE, "coin_grid_presets", f"{name}.json"), encoding="utf-8"))["cfg"]


def replay(key, rows, filt, min_stop=120):
    """rows: 시드+본 구간 [(ts, price)]. filt=True면 롱+숏 합산 수익곡선 필터(PairFilter) 적용. 반환: dict."""
    seed = rows[:SEED_MIN]; body = rows[SEED_MIN:]
    g.CFG.update(paper=True, paper_slip=SLIP, leverage=1)
    cfgs = {sd: dict(preset(f"hd_{key}_{sd}"), paper=True, paper_slip=SLIP, symbol="X/USDT:USDT", opp_state="", side=sd) for sd in ("long", "short")}
    S = {sd: {"ref_hist": [[t, p] for t, p in seed]} for sd in ("long", "short")}
    order_fn = lambda sd, usdt, p, ro, qty: g.place_order("X/USDT:USDT", sd, usdt, p, reduce_only=ro, dry_run=False, qty_override=qty)
    pf = PairFilter(cfgs, S, order_fn, min_stop_min=min_stop) if filt else None
    peak = mdd = 0.0; eq = 0.0
    for t, p in body:
        if pf: eq = pf.step(p, t)
        else:
            for sd, opp in (("long", "short"), ("short", "long")):
                b.CFG.clear(); b.CFG.update(cfgs[sd]); b.CFG["opp_bs"] = S[opp].get("bs", 0); b.step(S[sd], p, t, order_fn, lambda *a: None)
            eq = sum(b.cf_equity(S[sd], p, sd == "long") for sd in S)
        peak = max(peak, eq); mdd = max(mdd, peak - eq)
    last = body[-1][1]
    return dict(tot=eq, mdd=mdd, long=b.cf_equity(S["long"], last, True), short=b.cf_equity(S["short"], last, False),
                events=[(t, w, e) for t, w, e in (pf.events if pf else [])], stop_pct=(100.0 * pf.stop_ticks / len(body)) if pf else 0.0,
                stops=pf.stops if pf else 0, restarts=pf.restarts if pf else 0, mode=pf.mode if pf else "RUN",
                p0=body[0][1], p1=last, hi=max(p for _, p in body), lo=min(p for _, p in body))


def main():
    hours = 24; syms = list(SYM); min_stop = 120
    if "--hours" in sys.argv: hours = int(sys.argv[sys.argv.index("--hours") + 1])
    if "--min-stop" in sys.argv: min_stop = int(sys.argv[sys.argv.index("--min-stop") + 1])
    if "--symbols" in sys.argv: syms = sys.argv[sys.argv.index("--symbols") + 1].split(",")
    now = dt.datetime.now(KST)
    lines = [f"🔎 수익곡선 필터 관찰 모드 — 최근 {hours}시간 재생 ({now:%m-%d %H:%M} KST 기준)",
             f"기준: 롱+숏 합산 곡선이 기준 자본(합 200)의 5% 하락 → 둘 다 정지·잔량 100 USDT만 남김 / 그림자 합산 곡선 저점 +8% 회복 + 최소 {min_stop}분 → 재가동 / 정지 중 반대쪽 단계 0"]
    tb = tf = mb = mf = 0.0; out = {}
    for k in syms:
        rows = candles(SYM[k], hours * 60 + SEED_MIN + 5)
        if len(rows) < SEED_MIN + hours * 50:
            lines.append(f"[{k.upper()}] 시세 {len(rows)}개뿐이라 건너뜀"); continue
        rows = rows[-(hours * 60 + SEED_MIN):]
        base = replay(k, rows, False); filt = replay(k, rows, True, min_stop); out[k] = dict(base=base, filt=filt)
        lines.append(f"\n[{k.upper()}] {base['p0']:.6g}→{base['p1']:.6g} ({(base['p1'] / base['p0'] - 1) * 100:+.1f}%) 고 {base['hi']:.6g} 저 {base['lo']:.6g}")
        lines.append(f"  필터 없음: 합계 {base['tot']:+7.2f} (롱 {base['long']:+7.2f} 숏 {base['short']:+7.2f}) · MDD {base['mdd']:.2f}")
        lines.append(f"  필터 적용: 합계 {filt['tot']:+7.2f} (롱 {filt['long']:+7.2f} 숏 {filt['short']:+7.2f}) · MDD {filt['mdd']:.2f} · 정지 {filt['stops']}회 재가동 {filt['restarts']}회 · 정지 비중 {filt['stop_pct']:.0f}%")
        ev = filt["events"][-6:]
        if ev: lines.append("  정지·재가동(롱+숏 동시): " + ", ".join(f"{dt.datetime.fromtimestamp(t, KST):%H:%M} {w}" for t, w, _ in ev))
        tb += base["tot"]; tf += filt["tot"]; mb += base["mdd"]; mf += filt["mdd"]
    if out:
        lines.append(f"\n[합] 필터 없음 {tb:+.2f} / MDD합 {mb:.1f}  →  필터 적용 {tf:+.2f} / MDD합 {mf:.1f}  (차이 {tf - tb:+.2f}, MDD {((mf / mb - 1) * 100) if mb else 0:+.0f}%)")
    lines.append("※ 실제 주문 없는 과거 재생이며 수동 편입·정정은 반영되지 않습니다. 과거 결과일 뿐 투자 조언이 아닙니다.")
    text = "\n".join(lines); print(text)
    json.dump(dict(t=now.isoformat(), hours=hours, result={k: {n: {kk: vv for kk, vv in v.items() if kk != "events"} | {"events": [(dt.datetime.fromtimestamp(t, KST).strftime("%m-%d %H:%M"), w) for t, w, _ in v["events"]]} for n, v in d.items()} for k, d in out.items()}),
              open(os.path.join(HERE, "coin_cf_observe.json"), "w", encoding="utf-8"), ensure_ascii=False)
    if "--notify" in sys.argv:
        try: g.send_discord("```\n" + text[:1800] + "\n```")
        except Exception as e: print("디스코드 전송 실패:", type(e).__name__)


if __name__ == "__main__":
    main()
