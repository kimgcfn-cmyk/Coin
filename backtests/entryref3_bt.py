import sys, json
sys.path.insert(0, "/Users/Muita/Desktop/Coin"); sys.path.insert(0, ".")
import coin_avg_ladder_bot as b, coin_grid_bot as g
from cap_pair_bt import candles
base = json.load(open("/Users/Muita/Desktop/Coin/coin_grid_presets/hd_cap_long.json"))["cfg"]
def run(px, keep=100.0, trend=False, link=False, rebound=0.0, pb=1.5, tight=0.0, cap=1000.0, relax=0, rkeep=0.0, derisk=0.0, dpct=1.0, uw=0.0, blk=0, erm=0, mix=0):
    b.CFG.update(base); b.CFG.update(paper=True, paper_slip=0.0015, symbol="X/USDT:USDT", deep_rebound_pct=rebound, deep_from_stage=3, trend_hold=trend, trend_pullback_pct=pb, trend_tight_gain_pct=tight, trend_tight_pullback_pct=1.5, max_total_usdt=cap, entry_base_pct=1.0, relax_opp_stage=relax, relax_keep_usdt=rkeep, derisk_hold_usdt=derisk, derisk_pct=dpct, derisk_usdt=100.0, derisk_underwater_pct=uw, derisk_block_min=blk, entry_ref_min=erm, entry_ref_mix=mix)
    g.CFG.update(paper=True, paper_slip=0.0015, leverage=1)
    f = lambda sd, usdt, p, ro, qty: g.place_order("X/USDT:USDT", sd, usdt, p, reduce_only=ro, dry_run=False, qty_override=qty)
    S = {"long": {}, "short": {}}; eq_min = 0.0; maxnet = 0.0; maxhold = 0.0
    for i, p in enumerate(px):
        for side, opp in (("long", "short"), ("short", "long")):
            b.CFG["side"] = side
            b.CFG["keep_usdt"] = max(keep, S[opp].get("hold", 0.0)) if link else keep; b.CFG["opp_bs"] = S[opp].get("bs", 0)
            b.step(S[side], p, 1_000_000 + i * 60, f, lambda *a: None)
        def un(st, L):
            return (st.get("qty", 0) * ((p*(1-0.0015)) - st["avg"]) if L else st.get("qty", 0) * (st["avg"] - p*1.0015)) - st.get("qty", 0) * p * 0.0004 if st.get("qty", 0) > 0 else 0.0
        eq = S["long"].get("cum_pnl", 0) + S["short"].get("cum_pnl", 0) + un(S["long"], True) + un(S["short"], False)
        eq_min = min(eq_min, eq); maxnet = max(maxnet, abs(S["long"].get("hold", 0) - S["short"].get("hold", 0))); maxhold = max(maxhold, S["long"].get("hold", 0) + S["short"].get("hold", 0))
    return dict(tot=eq, real=S["long"].get("cum_pnl", 0) + S["short"].get("cum_pnl", 0), lo=S["long"].get("cum_pnl", 0), sh=S["short"].get("cum_pnl", 0), dd=eq_min, net=maxnet, gross=maxhold,
                hl=S["long"].get("hold", 0), hs=S["short"].get("hold", 0), trends=S["long"].get("n_trend", 0), stop=S["long"].get("n_stop", 0) + S["short"].get("n_stop", 0), outs=S["long"].get("n_out", 0) + S["short"].get("n_out", 0))
base = dict(keep=300.0, trend=True, pb=2.5, relax=2, rkeep=100.0, derisk=300.0, dpct=1.0, uw=3.0, blk=30)
V = (("A 평균단가(현재)", dict(base)), ("D 60분 평균", dict(base, erm=60)), ("M1 섞기-보수(더 낮은 쪽)", dict(base, erm=60, mix=1)), ("M2 섞기-공격(더 높은 쪽)", dict(base, erm=60, mix=2)),
     ("M3 섞기-보수 30분", dict(base, erm=30, mix=1)), ("M4 섞기-보수 120분", dict(base, erm=120, mix=1)))
for sym in ("CAPUSDT", "QUSDT"):
    ts, px = candles(sym, 48*60+10)
    for hn, h in (("5h", 301), ("10h", 601), ("24h", 1441), ("48h", 2881)):
        w = px[-h:]; print(f"\n[{sym} {hn}] {w[0]:.5f}→{w[-1]:.5f} ({(w[-1]/w[0]-1)*100:+.1f}%) 고 {max(w):.5f} 저 {min(w):.5f}")
        for nm, kw in V:
            r = run(w, **kw)
            print(f"   {nm:<24} 합 {r['tot']:+8.2f} (실현 {r['real']:+7.2f}) 낙폭 {r['dd']:+7.2f} | 순노출 {r['net']:>4.0f} 총노출 {r['gross']:>4.0f} | 끝 롱{r['hl']:.0f}/숏{r['hs']:.0f} 손절 {r['stop']}")
