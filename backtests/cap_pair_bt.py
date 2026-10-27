import sys, time, requests
FEE = 0.0004
def candles(sym, minutes):
    end = int(time.time()*1000); start = end - minutes*60*1000; rows = {}
    while end > start:
        r = requests.get("https://api.bitget.com/api/v2/mix/market/history-candles", params={"symbol": sym, "productType": "USDT-FUTURES", "granularity": "1m", "endTime": str(end), "limit": "200"}, timeout=15).json().get("data") or []
        if not r: break
        for x in r: rows[int(x[0])] = float(x[4])
        end = min(int(x[0]) for x in r) - 1; time.sleep(0.1)
    ts = sorted(t for t in rows if t >= start); return ts, [rows[t] for t in ts]

def sim(px, side, step, sstep, buy_u, sell_u, cap, slip, n=5, trail=10.0, rest=120, stop=10.0, crash=8.0):
    """side='long'|'short'. 봇 로직 재현: 직전 n분 평균가 기준, 진입 간격(직전 진입가에서 step% 더), 한도, 롱 고점 -trail% 전량청산+rest분 휴식, 숏 평균+stop% 강제손절·정지, 5분 급락(롱)/급등(숏) 시 신규 중단."""
    L = side == "long"; q = 0.0; avg = 0.0; hold = 0.0; cash = 0.0; fees = 0.0; last = None; peak = None
    realized = 0.0; ent = 0; ex = 0; liq = 0; halted = False; rest_until = -1; eqs = []; maxexp = 0.0
    def open_(u, p):
        nonlocal q, avg, hold, cash, fees
        fp = p*(1+slip) if L else p*(1-slip); qq = u/fp; fee = u*FEE
        eff = fp*(1+FEE) if L else fp*(1-FEE)
        avg = (q*avg + qq*eff)/(q+qq); q += qq; hold += u; fees += fee; cash -= fee
        if L: cash -= u
        else: cash += u
        return qq
    def close_(qq, p):
        nonlocal q, hold, cash, fees, realized
        qq = min(qq, q); fp = p*(1-slip) if L else p*(1+slip); val = qq*fp; fee = val*FEE
        pnl = qq*(fp-avg)-fee if L else qq*(avg-fp)-fee
        realized += pnl; fees += fee; cash += (val-fee) if L else -(val+fee); q -= qq
        hold = hold*(q/(q+qq)) if q > 1e-12 else 0.0
        if q <= 1e-12: q = 0.0; hold = 0.0
    for i in range(len(px)):
        p = px[i]
        mark = (q*p*(1-slip)*(1-FEE)) if L else (-q*p*(1+slip)*(1+FEE))
        eqs.append(cash + mark); maxexp = max(maxexp, q*p)
        if i < n or halted or i < rest_until: continue
        ref = sum(px[i-n:i])/n
        if L:
            if q > 0: peak = p if peak is None else max(peak, p)
            else: peak = None
            if q > 0 and trail and p <= peak*(1-trail/100): close_(q, p); liq += 1; rest_until = i+rest; last = None; continue
            hi5 = max(px[max(0, i-5):i+1]); paused = p <= hi5*(1-crash/100)
            if p <= ref*(1-step/100) and not paused and hold+buy_u <= cap and (last is None or p <= last*(1-step/100)): open_(buy_u, p); last = p; ent += 1
            elif p >= ref*(1+sstep/100) and q > 0:
                u = sell_u; qq = u/p
                if q-qq < qq*0.5: qq = q
                close_(qq, p); ex += 1
        else:
            if q > 0 and stop and p >= avg*(1+stop/100): close_(q, p); halted = True; liq += 1; continue
            lo5 = min(px[max(0, i-5):i+1]); paused = p >= lo5*(1+crash/100)
            if p >= ref*(1+step/100) and not paused and hold+buy_u <= cap and (last is None or p >= last*(1+step/100)): open_(buy_u, p); last = p; ent += 1
            elif p <= ref*(1-sstep/100) and q > 0:
                qq = sell_u/p
                if q-qq < qq*0.5: qq = q
                close_(qq, p); ex += 1
        if q == 0: last = None
    p = px[-1]; mark = (q*p*(1-slip)*(1-FEE)) if L else (-q*p*(1+slip)*(1+FEE))
    tot = cash + mark; unreal = tot - realized + fees*0  # 참고
    return dict(total=tot, realized=realized, fees=fees, ent=ent, ex=ex, liq=liq, end_qty=q, end_hold=hold, eqs=eqs, maxexp=maxexp, halted=halted)

def mdd(e):
    pk = -1e9; m = 0.0
    for x in e: pk = max(pk, x); m = min(m, x-pk)
    return m

if __name__ == "__main__":
    ts, px = candles("CAPUSDT", 20*60+30)
    print(f"CAP 1분봉 {len(px)}개, 최근 20.5h: {px[0]:.5f} → {px[-1]:.5f}")
    wins = {"최근 10시간": px[-610:], "그 직전 10시간": px[-1210:-600]}
    var = [("A 대칭 0.5% / 100USDT", 0.5, 0.5, 100, 100, 600, 800), ("B 대칭 0.5% / 10USDT", 0.5, 0.5, 10, 10, 600, 800), ("C 기존 CAP식 −0.5%매수100 · +1%매도200", 0.5, 1.0, 100, 200, 1000, 1000)]
    for slip in (0.0028, 0.0010):
        print(f"\n########## 슬리피지 편도 {slip*100:.2f}% · 수수료 0.04% · 평균가 5분 · 진입간격 · 롱 고점−10%청산 · 숏 +10% 손절 · 시작 물량 0 ##########")
        for wn, w in wins.items():
            print(f"[{wn}] {w[0]:.5f} → {w[-1]:.5f} ({(w[-1]/w[0]-1)*100:+.1f}%) 고 {max(w):.5f} 저 {min(w):.5f}")
            for nm, bs, ss, bu, su, capL, capS in var:
                a = sim(w, "long", bs, ss, bu, su, capL, slip); b = sim(w, "short", bs, ss, bu, su, capS, slip)
                comb = [x+y for x, y in zip(a["eqs"], b["eqs"])]
                print(f"  {nm:<34} 롱 {a['total']:+7.2f}(실현{a['realized']:+6.2f} 진입{a['ent']:>3}/청산{a['ex']:>3} 말보유{a['end_hold']:>4.0f}) | 숏 {b['total']:+7.2f}(실현{b['realized']:+6.2f} 진입{b['ent']:>3}/환매{b['ex']:>3} 말보유{b['end_hold']:>4.0f}{' 손절' if b['halted'] else ''}) | 합계 {comb[-1]:+7.2f} 낙폭 {mdd(comb):+6.1f} 수수료 {a['fees']+b['fees']:.2f} 최대노출 {a['maxexp']+b['maxexp']:.0f}")
