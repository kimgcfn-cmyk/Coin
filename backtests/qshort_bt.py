import sys, json
sys.path.insert(0, "/Users/Muita/Desktop/Coin"); sys.path.insert(0, ".")
import coin_avg_ladder_bot as b, coin_grid_bot as g
from cap_pair_bt import candles
base = json.load(open("/Users/Muita/Desktop/Coin/coin_grid_presets/hd_cap_long.json"))["cfg"]
def run(px, keep=100.0, trend=False, link=False, rebound=0.0, pb=1.5, tight=0.0, cap=1000.0, relax=0, rkeep=0.0, derisk=0.0, dpct=1.0, uw=0.0, blk=0, erm=0, mix=0, mesL=0, mesS=0, capv=1000.0, seed=None, hedge=0.0, stops=(8.0, 10.0, 12.0), gap=0, gfast=0.0):
    b.CFG.update(base); b.CFG.update(paper=True, paper_slip=0.0015, symbol="X/USDT:USDT", deep_rebound_pct=rebound, deep_from_stage=3, trend_hold=trend, trend_pullback_pct=pb, trend_tight_gain_pct=tight, trend_tight_pullback_pct=1.5, max_total_usdt=capv, entry_base_pct=1.0, relax_opp_stage=relax, relax_keep_usdt=rkeep, derisk_hold_usdt=derisk, derisk_pct=dpct, derisk_usdt=100.0, derisk_underwater_pct=uw, derisk_block_min=blk, entry_ref_min=erm, entry_ref_mix=mix, hedge_follow_usdt=hedge, hedge_opp_stage=3, hedge_max_usdt=500.0, hedge_step_pct=1.0, stops=list(stops), entry_min_gap_min=gap, entry_gap_fast_pct=gfast)
    g.CFG.update(paper=True, paper_slip=0.0015, leverage=1)
    f = lambda sd, usdt, p, ro, qty: g.place_order("X/USDT:USDT", sd, usdt, p, reduce_only=ro, dry_run=False, qty_override=qty)
    peakS = 0.0; mddS = 0.0; S = {"long": {"ref_hist": [[1_000_000 - (len(seed) - k) * 60, v] for k, v in enumerate(seed)]} if seed else {}, "short": {"ref_hist": [[1_000_000 - (len(seed) - k) * 60, v] for k, v in enumerate(seed)]} if seed else {}}; eq_min = 0.0; peak = 0.0; mdd = 0.0; maxnet = 0.0; maxhold = 0.0
    for i, p in enumerate(px):
        for side, opp in (("long", "short"), ("short", "long")):
            b.CFG["side"] = side; b.CFG["max_entry_stage"] = mesL if side == "long" else mesS
            b.CFG["keep_usdt"] = max(keep, S[opp].get("hold", 0.0)) if link else keep; b.CFG["opp_bs"] = S[opp].get("bs", 0)
            b.step(S[side], p, 1_000_000 + i * 60, f, lambda *a: None)
        def un(st, L):
            return (st.get("qty", 0) * ((p*(1-0.0015)) - st["avg"]) if L else st.get("qty", 0) * (st["avg"] - p*1.0015)) - st.get("qty", 0) * p * 0.0004 if st.get("qty", 0) > 0 else 0.0
        eqS = S["short"].get("cum_pnl", 0) + un(S["short"], False); eqL = S["long"].get("cum_pnl", 0) + un(S["long"], True); eq = eqL + eqS; peakS = max(peakS, eqS); mddS = max(mddS, peakS - eqS)
        eq_min = min(eq_min, eq); peak = max(peak, eq); mdd = max(mdd, peak - eq); maxnet = max(maxnet, abs(S["long"].get("hold", 0) - S["short"].get("hold", 0))); maxhold = max(maxhold, S["long"].get("hold", 0) + S["short"].get("hold", 0))
    return dict(eqS=eqS, eqL=eqL, mddS=mddS, mdd=mdd, tot=eq, real=S["long"].get("cum_pnl", 0) + S["short"].get("cum_pnl", 0), lo=S["long"].get("cum_pnl", 0), sh=S["short"].get("cum_pnl", 0), dd=eq_min, net=maxnet, gross=maxhold,
                hl=S["long"].get("hold", 0), hs=S["short"].get("hold", 0), trends=S["long"].get("n_trend", 0), stop=S["long"].get("n_stop", 0) + S["short"].get("n_stop", 0), outs=S["long"].get("n_out", 0) + S["short"].get("n_out", 0))
base = dict(keep=300.0, trend=True, pb=2.5, relax=2, rkeep=100.0, derisk=300.0, dpct=1.0, uw=3.0, blk=30, erm=60)
base = dict(keep=300.0, trend=True, pb=2.5, relax=2, rkeep=100.0, derisk=300.0, dpct=1.0, uw=3.0, blk=30, erm=0)
V = (("평균단가 기준(Q 현재 권장)", dict(base)), ("60분 평균 기준", dict(base, erm=60)))
ts, px = candles("QUSDT", 50*60+10)
for label, n in (("직전 2시간", 120), ("5시간", 300), ("10시간", 600), ("24시간", 1440), ("48시간", 2880)):
    lo = len(px) - n; w = px[lo:]; seed = px[lo - 70:lo]
    print(f"\n[Q {label}] {w[0]:.5f}→{w[-1]:.5f} ({(w[-1]/w[0]-1)*100:+.1f}%) 고 {max(w):.5f} 저 {min(w):.5f}")
    for nm, kw in V:
        r = run(w, seed=seed, **kw)
        print(f"   {nm:<24} 숏 단독 {r['eqS']/200*100:+7.1f}% ({r['eqS']:+7.1f}) MDD {r['mddS']/200*100:5.1f}% | 롱+숏 {r['tot']/400*100:+7.1f}% MDD {r['mdd']/400*100:5.1f}% | 롱 {r['eqL']:+7.1f}")
