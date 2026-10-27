import sys, json
sys.path.insert(0, "/Users/Muita/Desktop/Coin"); sys.path.insert(0, ".")
import coin_avg_ladder_bot as b, coin_grid_bot as g
from cap_pair_bt import candles
PRE = json.load(open("/Users/Muita/Desktop/Coin/coin_grid_presets/hd_cap_long.json"))["cfg"]
def run(px, seed, **ov):
    cfg = dict(PRE); cfg.update(paper=True, paper_slip=0.0030, symbol="X/USDT:USDT", opp_state="", entry_ref_min=60, entry_base_pct=1.0)
    cfg.update(ov); b.CFG.update(cfg); g.CFG.update(paper=True, paper_slip=0.0030, leverage=1)
    ev = {"long": [], "short": []}; cur = [0]
    def f(sd, usdt, p, ro, qty):
        res = g.place_order("X/USDT:USDT", sd, usdt, p, reduce_only=ro, dry_run=False, qty_override=qty)
        if res[0]: ev[b.CFG["side"]].append(("out" if ro else "in", cur[0], res[2]))
        return res
    mk = lambda: {"ref_hist": [[1_000_000 - (len(seed) - k) * 60, v] for k, v in enumerate(seed)]}
    S = {"long": mk(), "short": mk()}; peak = 0.0; mdd = 0.0; eq = 0.0
    def un(st, L, p):
        q = st.get("qty", 0)
        return ((q * ((p * (1 - 0.0030)) - st["avg"]) if L else q * (st["avg"] - p * 1.0030)) - q * p * 0.0004) if q > 0 else 0.0
    for i, p in enumerate(px):
        cur[0] = i
        for side, opp in (("long", "short"), ("short", "long")):
            b.CFG["side"] = side; b.CFG["opp_bs"] = S[opp].get("bs", 0)
            b.step(S[side], p, 1_000_000 + i * 60, f, lambda *a: None)
        eq = sum(S[k].get("cum_pnl", 0) + un(S[k], k == "long", p) for k in ("long", "short"))
        peak = max(peak, eq); mdd = max(mdd, peak - eq)
    # FIFO 평균 보유시간(청산분 + 끝까지 보유한 분의 경과 포함)
    tot = 0.0; wsum = 0.0; n = len(px)
    for side in ("long", "short"):
        lots = []
        for kind, i, q in ev[side]:
            if kind == "in": lots.append([i, q])
            else:
                rem = q
                while rem > 1e-9 and lots:
                    take = min(rem, lots[0][1]); wsum += take * (i - lots[0][0]); tot += take; lots[0][1] -= take; rem -= take
                    if lots[0][1] <= 1e-9: lots.pop(0)
        for i0, q in lots: wsum += q * (n - i0); tot += q
    return dict(tot=eq, mdd=mdd, hold_h=(wsum / tot / 60) if tot else 0.0, stops=S["long"].get("n_stop", 0) + S["short"].get("n_stop", 0), outs=S["long"].get("n_out", 0) + S["short"].get("n_out", 0))
SMALL = dict(max_total_usdt=500.0, ladder_usdt=50.0, first_entry_usdt=50.0, reinvest_usdt=50.0, derisk_usdt=50.0, keep_usdt=150.0, derisk_hold_usdt=150.0, relax_keep_usdt=50.0)
V = (("P1 CAP 표준(한도1000·100씩)", dict(max_total_usdt=1000.0)),
     ("P2 소형(한도500·50씩)", dict(SMALL)),
     ("P3 소형+평균단가 기준", dict(SMALL, entry_ref_min=0)),
     ("P4 소형+넓은 사다리 3/6/3", dict(SMALL, entry_base_pct=3.0, ladder_base_pct=6.0, ladder_step_pct=3.0)),
     ("P5 소형+간격 5분", dict(SMALL, entry_min_gap_min=5)),
     ("P6 소형+진입2%·청산3%", dict(SMALL, entry_base_pct=2.0, ladder_base_pct=3.0)))
if __name__ == "__main__":
    ts, px = candles("HUMAUSDT", 52 * 60 + 10)
    for label, n in (("직전 12시간", 720), ("직전 24시간", 1440), ("직전 48시간", 2880), ("그 앞 12시간(24~12시간 전)", None)):
        if n is None: lo = len(px) - 1440; w = px[lo:lo + 720]
        else: lo = len(px) - n; w = px[lo:]
        seed = px[lo - 70:lo]
        print(f"\n[HUMA {label}] {w[0]:.5f}→{w[-1]:.5f} ({(w[-1]/w[0]-1)*100:+.1f}%) 고 {max(w):.5f} 저 {min(w):.5f}")
        for nm, ov in V:
            r = run(w, seed, **ov); capital = 2 * ov["max_total_usdt"] / 5
            print(f"   {nm:<26} 수익률 {r['tot']/capital*100:+7.1f}% ({r['tot']:+7.1f}) MDD {r['mdd']/capital*100:5.1f}% ({r['mdd']:6.1f}) | 평균 보유 {r['hold_h']:5.1f}시간 | 청산 {r['outs']}건 손절 {r['stops']}")
