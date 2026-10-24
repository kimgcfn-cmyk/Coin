"""
코인 그리드 전략 모의 테스트 (coin_grid_bot.py의 현재 전략을 1분봉으로 재현)
================================================================================
사용:  python coin_grid_backtest.py ABC [시간=20]        (Bitget 공개 시세만 사용, 주문·키 불필요)
출력:  현재 전략 합계/실현/평가, 수수료, 매수·매도 건수와 승률, 급등·급락 횟수, 최대 낙폭·최대 노출,
       비교용 변형(급등 모드 끔, 급락 대응 없음, 추격 매수 없음)과 '그냥 30USDT 보유' 결과.
가정:  1분 종가로만 체결, 시장가 수수료 0.04% + 슬리피지 0.05%(편도), 물량 0에서 시작, 마지막 물량은 종가로 평가.
한계:  분 안의 움직임·호가 깊이는 반영되지 않는다. 표본(최근 N시간)이 짧고 한 번의 장이라 방향만 참고한다. 투자 조언이 아니다.
"""
import requests, time, statistics as st, sys
FEE, SLIP = 0.0004, 0.0005
def candles(sym, hours):
    end = int(time.time() * 1000); rows = {}; start = end - hours * 3600 * 1000
    while end > start:
        r = requests.get("https://api.bitget.com/api/v2/mix/market/history-candles", params={"symbol": sym, "productType": "USDT-FUTURES", "granularity": "1m", "endTime": str(end), "limit": "200"}, timeout=15).json()["data"]
        if not r: break
        for x in r: rows[int(x[0])] = (float(x[4]), float(x[6]))
        end = min(int(x[0]) for x in r) - 1; time.sleep(0.1)
    ts = sorted(t for t in rows if t >= start); return ts, [rows[t][0] for t in ts], [rows[t][1] for t in ts]

CFG = dict(buy_step=1.0, sell_step=1.5, buy_usdt=20.0, sell_usdt=30.0, cap=200.0, pause_pct=15.0,
           surge=True, surge_pct=5.0, win=5, vol_ratio=0.0, chase=30.0, ladder=(3.0, 4.5, 6.0), look=100)

def sim(px, vol, c=CFG, init_usdt=0.0):
    p0 = px[0]
    S = dict(qty=init_usdt / p0, avg=p0, ref=p0, peak=p0, hold=init_usdt, paused=False, surge=False, speak=0, base=0, k=0)
    cash = -init_usdt; fees = 0.0; realized = 0.0
    eqs = []; exps = []
    ev = dict(buys=0, sells=[], chase=0, surge=0, ladder=0, pauses=0, resumes=0, maxexp=0.0); eq_peak = -1e9; mdd = 0.0
    def buy(usdt, p):
        nonlocal cash, fees
        fp = p * (1 + SLIP); q = usdt / fp; fee = usdt * FEE
        S["avg"] = (S["qty"] * S["avg"] + usdt + fee) / (S["qty"] + q) if S["qty"] + q > 0 else fp
        S["qty"] += q; cash -= usdt + fee; fees += fee; S["hold"] += usdt; return q
    def sell(q, p):
        nonlocal cash, fees, realized
        q = min(q, S["qty"]); fp = p * (1 - SLIP); proceeds = q * fp; fee = proceeds * FEE
        pnl = q * (fp - S["avg"]) - fee; realized += pnl; cash += proceeds - fee; fees += fee
        S["qty"] -= q; ev["sells"].append(pnl)
        if S["qty"] <= 1e-12: S["qty"] = 0.0; S["hold"] = 0.0
        return q
    for i in range(1, len(px)):
        p = px[i]; handled = False
        if c["surge"]:
            if not S["surge"]:
                lo = min(px[max(0, i - c["win"]):i + 1])
                if p >= lo * (1 + c["surge_pct"] / 100) and i >= 40:
                    prior = vol[max(0, i - 4 - c["look"]):i - 4]
                    ratio = (sum(vol[i - 4:i + 1]) / (sum(prior) / len(prior) * 5)) if len(prior) >= 30 and sum(prior) > 0 else None
                    if (c["vol_ratio"] == 0 or (ratio is not None and ratio >= c["vol_ratio"])):
                        S.update(surge=True, speak=p, k=0); ev["surge"] += 1
                        if c["chase"] > 0 and S["hold"] + c["chase"] <= c["cap"]:
                            buy(c["chase"], p); ev["chase"] += 1
                        S["base"] = S["qty"]; handled = True
            else:
                S["speak"] = max(S["speak"], p); S["peak"] = max(S["peak"], p); handled = True
                lad = c["ladder"]
                if S["qty"] <= 0:
                    if p <= S["speak"] * (1 - lad[0] / 100):
                        S.update(surge=False, ref=p, peak=p)
                        if S["qty"] <= 0: S["paused"] = False
                else:
                    lo_ = c.get("lad_opts", {}); conf = lo_.get("confirm", 1); gap = lo_.get("gap", 0); mx = lo_.get("max_per_min", 99); done_now = 0
                    if S["k"] < len(lad) and p <= S["speak"] * (1 - lad[S["k"]] / 100): S["below_n"] = S.get("below_n", 0) + 1
                    else: S["below_n"] = 0
                    while S["k"] < len(lad) and p <= S["speak"] * (1 - lad[S["k"]] / 100) and S["qty"] > 0 and S.get("below_n", 0) >= conf and i - S.get("last_i", -999) >= gap and done_now < mx:
                        q = S["qty"] if S["k"] == len(lad) - 1 else min(S["base"] / len(lad), S["qty"])
                        if q * p < 6 or S["qty"] - q < 6 / p: q = S["qty"]
                        sell(q, p); S["k"] += 1; ev["ladder"] += 1; done_now += 1; S["last_i"] = i
                    if S["k"] >= len(lad) or S["qty"] <= 0:
                        S.update(surge=False, ref=p, peak=p)
                        if S["qty"] <= 0: S["paused"] = False
        crash = c.get("crash")
        if crash and not handled:
            if not S.get("crash_on"):
                hi5 = max(px[max(0, i - crash["win"]):i + 1])
                if p <= hi5 * (1 - crash["pct"] / 100):
                    S["crash_on"] = True; S["clow"] = p; S["clow_i"] = i; ev["crash"] = ev.get("crash", 0) + 1
                    if crash["mode"] == "sell" and S["qty"] > 0:
                        sell(S["qty"], p); S["ref"] = p
            else:
                if p < S["clow"]: S["clow"] = p; S["clow_i"] = i
                if p >= S["clow"] * (1 + crash["rebound"] / 100) or i - S["clow_i"] >= crash["quiet"]:
                    S["crash_on"] = False; S["ref"] = p
        if not handled:
            # 신규 매수 중단(pause_buy) 처리
            if S["paused"]:
                if p < S["ref"]: S["ref"] = p
                if S["qty"] <= 0: S.update(paused=False, peak=p, ref=p); ev["resumes"] += 1
            else:
                S["peak"] = p if S["qty"] <= 0 else max(S["peak"], p)
                if S["qty"] > 0 and p <= S["peak"] * (1 - c["pause_pct"] / 100): S["paused"] = True; ev["pauses"] += 1
            if S["qty"] <= 0 and p > S["ref"]: S["ref"] = p
            if p <= S["ref"] * (1 - c["buy_step"] / 100):
                if not S["paused"] and not S.get("crash_on") and S["hold"] < c["cap"]:
                    buy(c["buy_usdt"], p); S["ref"] = p; ev["buys"] += 1
                elif S.get("crash_on"):
                    S["ref"] = min(S["ref"], p)
            elif p >= S["ref"] * (1 + c["sell_step"] / 100) and S["qty"] > 0:
                q = c["sell_usdt"] / p
                if S["qty"] - q < q * 0.5: q = S["qty"]
                before = S["qty"]; sell(q, p); S["hold"] = 0.0 if S["qty"] <= 0 else max(0.0, S["hold"] - c["sell_usdt"]); S["ref"] = p
        eq = cash + S["qty"] * p * (1 - SLIP) * (1 - FEE); eq_peak = max(eq_peak, eq); mdd = min(mdd, eq - eq_peak)
        ev["maxexp"] = max(ev["maxexp"], S["qty"] * p); eqs.append(eq); exps.append(S["qty"] * p)
    pend = S["qty"] * px[-1] * (1 - SLIP) * (1 - FEE) - S["qty"] * S["avg"]
    return dict(eqs=eqs, exps=exps, total=cash + S["qty"] * px[-1] * (1 - SLIP) * (1 - FEE), realized=realized, unreal=pend, fees=fees, mdd=mdd, end_qty=S["qty"], **ev)
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용: python coin_grid_backtest.py ABC [시간=20]"); sys.exit(1)
    base = sys.argv[1].upper().replace("/USDT:USDT", "")
    base = base[:-4] if base.endswith("USDT") else base
    hours = float(sys.argv[2]) if len(sys.argv) > 2 else 20
    L0 = (3.0, 4.5, 6.0); LO = dict(confirm=3, max_per_min=1); CR = dict(win=5, pct=8.0, mode="pause", rebound=2.0, quiet=30)
    cur = dict(ladder=L0, lad_opts=LO, win=1, surge_pct=3.0, crash=CR)
    def mdd(s):
        pk = -1e9; m = 0
        for e in s: pk = max(pk, e); m = min(m, e - pk)
        return m
    ts, px, vol = candles(base + "USDT", hours)
    if not px:
        print("시세를 가져오지 못했습니다 — 심볼을 확인하세요"); sys.exit(1)
    print(f"===== {base}USDT 최근 {hours:g}시간 1분봉 {len(px)}개: {px[0]:.6g} → {px[-1]:.6g} ({(px[-1]/px[0]-1)*100:+.1f}%), 고 {max(px):.6g} 저 {min(px):.6g}")
    for lab, c in (("현재 전략", cur), ("급등 모드 끔", {**cur, "surge": False}), ("급락 대응 없음", {**cur, "crash": None}), ("추격 매수 없음", {**cur, "chase": 0.0})):
        r = sim(px, vol, {**CFG, **c}, 0.0); s = r["sells"]; w = [x for x in s if x > 0]
        print(f"  {lab:<14}| 합계 {r['total']:>+7.2f} (실현 {r['realized']:>+6.2f} 평가 {r['unreal']:>+5.2f}) 수수료 {r['fees']:.2f} | 매수 {r['buys']:>3} 매도 {len(s):>3} 승률 {len(w)/len(s)*100 if s else 0:>3.0f}% | 급등 {r['surge']}회 사다리 {r['ladder']} 급락 {r.get('crash', 0)}회 | 낙폭 {mdd(r['eqs']):>7.2f} 최대노출 {r['maxexp']:.0f}")
    print(f"  (참고) 30 USDT 그냥 보유 {30*(px[-1]/px[0]-1):+.2f}")
