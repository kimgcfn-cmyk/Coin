"""
수익곡선 필터 실전 컨트롤러 — 롱·숏 각각 판정 (2026-10-28, 사용자 지시: TAG부터 적용, 롱과 숏 각각의 수익곡선으로 판단)
================================================================================
사용:  python3 coin_cf_run.py --sym tag [--live] [--check] [--status] [--reset] [--mode side|pair]     (cron 1분, 롱·숏 봇 점검 뒤에 실행)
동작:  종목(tag) 롱·숏 봇의 장부(coin_avg_hd_<sym>_{long,short}_state.json)를 읽어 봇마다 따로 수익곡선을 판정한다(기본 --mode side).
       · 그 봇의 곡선이 고점 대비 기준 자본(한도÷5)의 30% 하락 → 그 봇만 정지(장부 cf_halt) + 보유량 100 USDT만 남기고 시장가 reduce-only 정리(--live일 때만 실주문)
       · 정지 중에는 그 봇의 그림자(모의)가 같은 전략을 돌고, 그림자 곡선이 저점 +10% 오르고 최소 60분이 지나면 그 봇만 재가동. 정지·재가동 모두 1분 뒤 다시 확인해 같을 때만 실행
       (--mode pair 는 롱+숏 합산 곡선으로 둘을 같이 판정하는 방식 — 참고용)
       두 봇의 flock 잠금(/tmp/coin_hd_<sym>_{long,short}.lock)을 잡은 채로 장부를 수정하므로 봇 프로세스와 겹치지 않는다.
       --check: 판정만 하고 주문·저장 없음. --status: 컨트롤러 상태 출력. --reset: 컨트롤러 상태 초기화(봇 장부는 그대로, cf_halt도 해제).
상태: coin_cf_curve_<sym>.json (필터별 판정 상태·그림자 장부·이벤트). 로그: logs/coin_cf_run_<sym>.log. 과거 결과일 뿐 투자 조언이 아니다.
       비중 조절(10/28 12시대 사용자 지시, 모든 봇): 최근 6시간 롱·숏 곡선 변화 차이가 기준 자본의 30% 이상이면 이기는 쪽 단계 크기 150%·지는 쪽 50%,
       15% 이하로 줄면 같은 비중으로 복귀(최소 60분 유지, 필터 정지 중엔 같은 비중). 결과는 장부 wt_mult로 봇에 전달(coin_cf_filter.weight_update).
"""
import sys, os, json, time, fcntl
from datetime import datetime, timezone
import coin_avg_ladder_bot as b
import coin_grid_bot as g
from coin_cf_filter import make_filters, weight_update

HERE = os.path.dirname(os.path.abspath(__file__))
DROP_PCT, RISE_PCT, MIN_STOP_MIN, KEEP, CONFIRM_MIN = 30.0, 10.0, 60, 100.0, 1      # 10/28 12시 사용자 지시: 정지 −30%·재가동 +10%(기준 자본 대비), 1분 확인 + 최소 정지 60분


def preset(name):
    return json.load(open(os.path.join(HERE, "coin_grid_presets", name + ".json"), encoding="utf-8"))


def atomic_write(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f: json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def lock(path, wait=30):
    f = open(path, "w"); t0 = time.time()
    while True:
        try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB); return f
        except OSError:
            if time.time() - t0 > wait: raise RuntimeError(f"잠금 대기 초과: {path}")
            time.sleep(0.5)


def main():
    args = sys.argv[1:]
    sym = args[args.index("--sym") + 1] if "--sym" in args else "tag"
    suf = args[args.index("--suffix") + 1] if "--suffix" in args else ""         # 모의 복사본(예: _e) — 프리셋 hd_<종목>_<방향><suf>, 장부·잠금·판정 상태도 접미사로 분리
    paper = "--paper" in args                                                   # 모의: 주문은 모의 체결(거래소 호출·디스코드 없음), 장부·판정 상태는 저장
    send = ("--live" in args or paper) and "--check" not in args
    cfile = os.path.join(HERE, f"coin_cf_curve_{sym}{suf}.json"); mode = args[args.index("--mode") + 1] if "--mode" in args else "side"
    files = {sd: os.path.join(HERE, f"coin_avg_hd_{sym}_{sd}{suf}_state.json") for sd in ("long", "short")}
    if "--status" in args:
        d = json.load(open(cfile)) if os.path.exists(cfile) else {}
        print(json.dumps({n: {k: v for k, v in x.items() if k not in ("SH", "hist")} for n, x in d.items()}, ensure_ascii=False, indent=1)); return
    if "--reset" in args:
        locks = [lock(f"/tmp/coin_hd_{sym}_{sd}{suf}.lock") for sd in ("long", "short")]
        for sd in files:
            if os.path.exists(files[sd]):
                st = json.load(open(files[sd], encoding="utf-8")); st["cf_halt"] = False; st["wt_mult"] = 1.0; atomic_write(files[sd], st)
        if os.path.exists(cfile): os.remove(cfile)
        print("  ✅ 컨트롤러 상태 초기화 (봇 장부·실제 포지션은 그대로, 정지 플래그 해제)"); return
    now = time.time()
    print(f"\n{'=' * 56}\n  수익곡선 필터 컨트롤러 — {sym.upper()}{suf} 롱·숏 각각 — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}{' [모의]' if paper else '' if send else ' [판정만]'}\n{'=' * 56}")
    locks = [lock(f"/tmp/coin_hd_{sym}_{sd}{suf}.lock") for sd in ("long", "short")]       # 두 봇 장부를 만지는 동안 봇 프로세스를 막는다
    pres = {sd: preset(f"hd_{sym}_{sd}{suf}") for sd in ("long", "short")}
    symbol = g._normalize_symbol(pres["long"]["symbol"].split("/")[0]) if pres["long"].get("symbol") else None
    live_cfg = {sd: dict(pres[sd]["cfg"], symbol=pres[sd].get("symbol") or symbol, side=sd, auto_trade=not paper, paper=paper, state_file=files[sd]) for sd in pres}
    shadow_cfg = {sd: dict(pres[sd]["cfg"], symbol=live_cfg[sd]["symbol"], side=sd, opp_state="") for sd in pres}
    S = {}
    for sd in files:
        try: S[sd] = json.load(open(files[sd], encoding="utf-8"))
        except Exception: print(f"  ❌ {sd} 장부를 읽지 못함 — 이번 판정 건너뜀"); return
    price = g.fetch_price(live_cfg["long"]["symbol"])
    if not price or price <= 0: print("  ❌ 시세 조회 실패"); return
    print(f"  현재가: {price:,.6g} USDT | 롱 {S['long'].get('qty', 0):,.6g}개 숏 {S['short'].get('qty', 0):,.6g}개")

    def prepare(sd):                        # 실주문 직전: 해당 봇의 실전 CFG를 적용
        b.CFG.clear(); b.CFG.update(live_cfg[sd])
    order_fn = lambda side, usdt, p, ro, qty: g.place_order(b.CFG["symbol"], side, usdt, p, reduce_only=ro, dry_run=not send, qty_override=qty)
    notify = (lambda m: g.send_discord(m + b.balance_line())) if (send and not paper) else None
    fl = make_filters(shadow_cfg, S, order_fn, mode=mode, drop_pct=DROP_PCT, rise_pct=RISE_PCT, min_stop_min=MIN_STOP_MIN, keep=KEEP, confirm_min=CONFIRM_MIN, external=True,
                      prepare=prepare, notify=notify, log=print, name=sym.upper() + suf)
    saved = json.load(open(cfile, encoding="utf-8")) if os.path.exists(cfile) else {}
    keys = [("pair" if len(f.sides) == 2 else f.sides[0]) for f in fl]
    for k_, f in zip(keys, fl):
        if k_ in saved: f.load(saved[k_])
    before = json.dumps(S, sort_keys=True)
    for f in fl: f.step(price, now)
    capw = float(live_cfg["long"]["max_total_usdt"]) / 5.0                     # 봇 기준 자본(한도÷5) — 비중 조절 판정은 이 값 대비 %
    wt = weight_update(saved.get("wt") or {}, S, any(f.mode == "STOP" for f in fl), price, now, capw, notify=notify, log=print, name=sym.upper() + suf)
    if send:
        if json.dumps(S, sort_keys=True) != before:
            for sd in files: atomic_write(files[sd], S[sd])
        atomic_write(cfile, dict({k_: f.dump() for k_, f in zip(keys, fl)}, wt=wt))

if __name__ == "__main__":
    main()
