"""
수익곡선 필터(그림자 봇 재가동) 재생 백테스트 — CAP·Q·TAG 롱숏합동 실전 프리셋 그대로 (2026-10-28)
================================================================================
규칙(사용자 지시 2026-10-28):
  ① 롱·숏 각 봇의 수익곡선(누적 순손익 = 실현 + 평가, 평가는 반대 슬리피지·수수료 반영)이 직전 고점 대비 D USDT 하락하면
     → 그 봇을 정지하고 잔량을 100 USDT만 남기고 시장가로 손절한다(잔량 100은 그대로 보유).
  ② 정지 시점부터 '그림자 봇'(정지 직전 장부 그대로 모의로 계속 가동)을 돌리며 그 수익곡선을 추적한다.
  ③ 그림자 곡선이 자기 저점 대비 U USDT 오르면 실거래 봇을 재가동한다(잔량 100 USDT + 최신 시세 기록으로 사다리를 처음부터).
비교: 필터 없음(원래 봇) vs D·U 조합. 슬리피지 0.30%·수수료 0.04% 가정(실제보다 보수적). 과거 재생일 뿐 투자 조언이 아니다.
"""
import sys, json, copy
sys.path.insert(0, "/Users/Muita/Desktop/Coin"); sys.path.insert(0, ".")
import coin_avg_ladder_bot as b, coin_grid_bot as g
from cap_pair_bt import candles

SLIP = 0.0030; FEE = 0.0004; KEEP = 100.0
PRE = {}


def preset(name):
    if name not in PRE: PRE[name] = json.load(open(f"/Users/Muita/Desktop/Coin/coin_grid_presets/{name}.json"))["cfg"]
    return PRE[name]


def un(st, L, p):
    q = st.get("qty", 0)
    return ((q * ((p * (1 - SLIP)) - st["avg"]) if L else q * (st["avg"] - p * (1 + SLIP))) - q * p * FEE) if q > 0 else 0.0


def order_fn(sd, usdt, p, ro, qty):
    return g.place_order("X/USDT:USDT", sd, usdt, p, reduce_only=ro, dry_run=False, qty_override=qty)


def cut_to_keep(st, L, p):
    """잔량을 KEEP USDT만 남기고 시장가 정리하고 장부(실현손익)를 갱신한다. 반환: 정리한 수량."""
    q = st.get("qty", 0.0); hold = st.get("hold", 0.0)
    if q <= 0 or hold <= KEEP: return 0.0
    qq = q * (1 - KEEP / hold)
    ok, detail, qf, info = order_fn("sell" if L else "buy", 0.0, p, True, qq)
    if not ok: return 0.0
    aq = info.get("filled_qty", qf) or qq; fp = info.get("avg_price", p); fee = info.get("fee") or fp * aq * FEE
    net = aq * ((fp - st["avg"]) if L else (st["avg"] - fp)) - fee
    st["cum_pnl"] += net; st["cum_fees"] = st.get("cum_fees", 0.0) + fee
    st["hold"] = st["hold"] * (st["qty"] - aq) / st["qty"] if st["qty"] > 0 else 0.0; st["qty"] -= aq
    st["n_out"] = st.get("n_out", 0) + 1
    return aq


def run(sym_key, px, seed, D=None, U=None, win_note="", cut=True, min_stop=0, opp_mode="shadow", confirm=0, confirm_restart=None):
    """sym_key: 'cap'|'q'|'tag'. D=None이면 필터 없음. 반환: 합계 손익·MDD·정지 횟수 등."""
    g.CFG.update(paper=True, paper_slip=SLIP, leverage=1)
    cfgs = {"long": dict(preset(f"hd_{sym_key}_long"), paper=True, paper_slip=SLIP, symbol="X/USDT:USDT", opp_state="", side="long"),
            "short": dict(preset(f"hd_{sym_key}_short"), paper=True, paper_slip=SLIP, symbol="X/USDT:USDT", opp_state="", side="short")}
    mk = lambda: {"ref_hist": [[1_000_000 - (len(seed) - k) * 60, v] for k, v in enumerate(seed)]}
    S = {sd: dict(real=mk(), shadow=None, mode="RUN", peak=0.0, trough=0.0, stops=0, restarts=0, stop_min=0) for sd in ("long", "short")}
    peak = 0.0; mdd = 0.0; curve = []

    def call(state, sd, p, now, opp_bs):
        b.CFG.clear(); b.CFG.update(cfgs[sd]); b.CFG["opp_bs"] = opp_bs
        b.step(state, p, now, order_fn, lambda *a: None)

    def strat_state(sd):         # 반대쪽 봇의 사다리 단계 — 정지 중이면 그림자(원래 전략이 하고 있을 상태)를 본다
        o = S[sd]
        if o["mode"] == "RUN": return o["real"].get("bs", 0)
        return {"shadow": o["shadow"], "real": o["real"]}.get(opp_mode, {}).get("bs", 0)      # shadow: 원래 전략의 단계 / real: 정지된 실봇 단계 / zero: 0

    for i, p in enumerate(px):
        now = 1_000_000 + i * 60
        for sd in ("long", "short"):
            L = sd == "long"; o = S[sd]; opp = "short" if L else "long"
            opp_bs = strat_state(opp)
            if o["mode"] == "RUN":
                call(o["real"], sd, p, now, opp_bs)
            else:
                call(o["shadow"], sd, p, now, opp_bs); o["stop_min"] += 1
            er = o["real"].get("cum_pnl", 0.0) + un(o["real"], L, p)
            if D is not None:
                cr = confirm if confirm_restart is None else confirm_restart
                if o["mode"] == "RUN":
                    o["peak"] = max(o["peak"], er)
                    hit = o["peak"] - er >= D
                    if confirm and hit:                            # 확인 대기: 처음 조건을 만족한 뒤 confirm분 뒤에도 만족해야 실행
                        if o.get("pend") is None: o["pend"] = i; hit = False
                        elif i - o["pend"] < confirm: hit = False
                    elif confirm and not hit and o.get("pend") is not None and i - o["pend"] >= confirm: o["pend"] = None
                    if hit: o["pend"] = None
                    if hit:                       # ① 수익곡선 하락 → 정지 + 잔량 손절
                        o["shadow"] = copy.deepcopy(o["real"]); o["since"] = i
                        if cut: cut_to_keep(o["real"], L, p)
                        o["mode"] = "STOP"; o["stops"] += 1; o.setdefault("ev", []).append((i, "정지"))
                        es = o["shadow"].get("cum_pnl", 0.0) + un(o["shadow"], L, p); o["trough"] = es
                else:
                    sh = o["shadow"]; es = sh.get("cum_pnl", 0.0) + un(sh, L, p)
                    o["trough"] = min(o["trough"], es)
                    rhit = es - o["trough"] >= U and i - o["since"] >= min_stop
                    if cr and rhit:
                        if o.get("rpend") is None: o["rpend"] = i; rhit = False
                        elif i - o["rpend"] < cr: rhit = False
                    elif cr and not rhit and o.get("rpend") is not None and i - o["rpend"] >= cr: o["rpend"] = None
                    if rhit: o["rpend"] = None
                    if rhit:                       # ③ 그림자 곡선 상승 → 실거래 재가동
                        r = o["real"]; r["ref_hist"] = copy.deepcopy(sh["ref_hist"]); r.update(bs=0, ss=0, full=False, stopped=0, started=True)
                        r.pop("trend", None); r.pop("t_peak", None); r.pop("rest_until", None)      # 실전 엔진과 같게: 재가동 때 손절 후 휴식도 해제
                        o["mode"] = "RUN"; o["restarts"] += 1; o.setdefault("ev", []).append((i, "재가동"))
                        o["peak"] = o["real"].get("cum_pnl", 0.0) + un(o["real"], L, p)
        eq = sum(S[sd]["real"].get("cum_pnl", 0.0) + un(S[sd]["real"], sd == "long", p) for sd in ("long", "short"))
        peak = max(peak, eq); mdd = max(mdd, peak - eq); curve.append(eq)
    return dict(tot=eq, mdd=mdd, stops=sum(S[sd]["stops"] for sd in S), restarts=sum(S[sd]["restarts"] for sd in S),
                stop_pct=100.0 * sum(S[sd]["stop_min"] for sd in S) / (2 * len(px)),
                long=S["long"]["real"].get("cum_pnl", 0.0) + un(S["long"]["real"], True, px[-1]), short=S["short"]["real"].get("cum_pnl", 0.0) + un(S["short"]["real"], False, px[-1]),
                ev={sd: S[sd].get("ev", []) for sd in S})


SYM = {"cap": "CAPUSDT", "q": "QUSDT", "tag": "TAGUSDT"}
WINDOWS = (("직전 12시간", 720), ("직전 24시간", 1440), ("직전 48시간", 2880), ("직전 72시간", 4320))
GRID_D = (5.0, 8.0, 10.0, 15.0)
GRID_U = (3.0, 5.0, 8.0)

if __name__ == "__main__":
    data = {}
    for k, s in SYM.items():
        ts, px = candles(s, 4320 + 80); data[k] = px
    rows = []          # (sym, window, D, U, result)
    for k in SYM:
        px = data[k]
        for wname, n in WINDOWS:
            if len(px) < n + 70: continue
            lo = len(px) - n; w = px[lo:]; seed = px[lo - 70:lo]
            base = run(k, w, seed)
            print(f"\n[{k.upper()} {wname}] {w[0]:.6g}→{w[-1]:.6g} ({(w[-1]/w[0]-1)*100:+.1f}%) 고 {max(w):.6g} 저 {min(w):.6g}")
            print(f"   필터 없음            합계 {base['tot']:+8.2f} (롱 {base['long']:+7.2f} 숏 {base['short']:+7.2f}) MDD {base['mdd']:6.2f}")
            rows.append((k, wname, None, None, base))
            for D in GRID_D:
                for U in GRID_U:
                    r = run(k, w, seed, D, U); rows.append((k, wname, D, U, r))
                    print(f"   D={D:>4.0f} U={U:>2.0f}          합계 {r['tot']:+8.2f} (롱 {r['long']:+7.2f} 숏 {r['short']:+7.2f}) MDD {r['mdd']:6.2f} | 정지 {r['stops']}회 재가동 {r['restarts']}회 정지시간 {r['stop_pct']:4.0f}%")
    json.dump([(k, w, D, U, r) for k, w, D, U, r in rows], open("curve_filter_bt_result.json", "w"), ensure_ascii=False)
    # 종합: D·U 조합별 12개 구간(3종목×4구간) 합계
    print("\n[종합] D·U별 12개 구간 합계 (필터 없음 대비)")
    base_tot = sum(r["tot"] for k, w, D, U, r in rows if D is None); base_mdd = sum(r["mdd"] for k, w, D, U, r in rows if D is None)
    print(f"   필터 없음      합계 {base_tot:+8.1f}  MDD합 {base_mdd:6.1f}")
    for D in GRID_D:
        for U in GRID_U:
            sel = [r for k, w, d_, u_, r in rows if d_ == D and u_ == U]
            wins = sum(1 for (k, w, d_, u_, r), (k2, w2, d2, u2, r2) in zip([x for x in rows if x[2] == D and x[3] == U], [x for x in rows if x[2] is None]) if r["tot"] > r2["tot"])
            print(f"   D={D:>4.0f} U={U:>2.0f}   합계 {sum(r['tot'] for r in sel):+8.1f} ({sum(r['tot'] for r in sel) - base_tot:+7.1f})  MDD합 {sum(r['mdd'] for r in sel):6.1f}  개선 구간 {wins}/{len(sel)}  정지 {sum(r['stops'] for r in sel)}회")


def run_pair(sym_key, px, seed, D, U, min_stop=0):
    """롱+숏 합산 곡선 기준 필터: 합산 수익곡선이 고점 대비 D 하락하면 롱·숏 둘 다 정지+잔량 100 손절, 그림자 한 쌍이 돌다가 합산 그림자 곡선이 저점 +U 오르면 둘 다 재가동."""
    g.CFG.update(paper=True, paper_slip=SLIP, leverage=1)
    cfgs = {"long": dict(preset(f"hd_{sym_key}_long"), paper=True, paper_slip=SLIP, symbol="X/USDT:USDT", opp_state="", side="long"),
            "short": dict(preset(f"hd_{sym_key}_short"), paper=True, paper_slip=SLIP, symbol="X/USDT:USDT", opp_state="", side="short")}
    mk = lambda: {"ref_hist": [[1_000_000 - (len(seed) - k) * 60, v] for k, v in enumerate(seed)]}
    R = {"long": mk(), "short": mk()}; SH = None; mode = "RUN"; peak = 0.0; trough = 0.0; since = 0; stops = restarts = stop_min = 0; mpeak = mdd = 0.0

    def call(state, sd, p, now, opp_bs):
        b.CFG.clear(); b.CFG.update(cfgs[sd]); b.CFG["opp_bs"] = opp_bs; b.step(state, p, now, order_fn, lambda *a: None)
    for i, p in enumerate(px):
        now = 1_000_000 + i * 60
        if mode == "RUN":
            for sd, opp in (("long", "short"), ("short", "long")): call(R[sd], sd, p, now, R[opp].get("bs", 0))
            e = sum(R[sd].get("cum_pnl", 0.0) + un(R[sd], sd == "long", p) for sd in R); peak = max(peak, e)
            if peak - e >= D:
                SH = {sd: copy.deepcopy(R[sd]) for sd in R}
                for sd in R: cut_to_keep(R[sd], sd == "long", p); R[sd].update(bs=0, ss=0)
                mode = "STOP"; since = i; stops += 1; trough = sum(SH[sd].get("cum_pnl", 0.0) + un(SH[sd], sd == "long", p) for sd in SH)
        else:
            stop_min += 1
            for sd, opp in (("long", "short"), ("short", "long")): call(SH[sd], sd, p, now, SH[opp].get("bs", 0))
            es = sum(SH[sd].get("cum_pnl", 0.0) + un(SH[sd], sd == "long", p) for sd in SH); trough = min(trough, es)
            if es - trough >= U and i - since >= min_stop:
                for sd in R:
                    R[sd]["ref_hist"] = copy.deepcopy(SH[sd]["ref_hist"]); R[sd].update(bs=0, ss=0, full=False, stopped=0, started=True); R[sd].pop("trend", None); R[sd].pop("t_peak", None)
                mode = "RUN"; restarts += 1; SH = None; peak = sum(R[sd].get("cum_pnl", 0.0) + un(R[sd], sd == "long", p) for sd in R)
        eq = sum(R[sd].get("cum_pnl", 0.0) + un(R[sd], sd == "long", p) for sd in R); mpeak = max(mpeak, eq); mdd = max(mdd, mpeak - eq)
    return dict(tot=eq, mdd=mdd, stops=stops, restarts=restarts, stop_pct=100.0 * stop_min / len(px),
                long=R["long"].get("cum_pnl", 0.0) + un(R["long"], True, px[-1]), short=R["short"].get("cum_pnl", 0.0) + un(R["short"], False, px[-1]))
