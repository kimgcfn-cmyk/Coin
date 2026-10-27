import json, statistics as st, datetime as dt
data = json.load(open("pump_hist.json"))
K = dt.timezone(dt.timedelta(hours=9))
SLIP = 0.003; FEE = 0.0004
def events(h, pump=0.40, look=24, roll=0.04, cool=24):
    """급등 후 하락 시작 신호: 직전 look봉 안에서 저점→고점 상승률 ≥ pump, 그 고점 이후 종가가 고점 대비 roll 이상 내려오면 신호(종가 진입)."""
    out = []; last = -999
    n = len(h)
    for i in range(look + 2, n - 1):
        w = h[i - look:i + 1]
        hi_idx = max(range(len(w)), key=lambda k: w[k][2]); hi = w[hi_idx][2]
        if hi_idx >= len(w) - 1: continue                     # 고점이 현재 봉이면 아직 상승 중
        lo = min(x[3] for x in w[:hi_idx + 1])
        if hi / lo - 1 < pump: continue
        if h[i][4] > hi * (1 - roll): continue                # 고점 대비 roll 이상 내려와야
        if i - last < cool: continue
        out.append(i); last = i
    return out
def sim(h, i, stop, tp, hold=24):
    """종가 i에서 숏 진입(슬리피지), 이후 봉의 고/저로 손절·익절, 시간 청산. 같은 봉이면 손절 우선. 수익률(%)과 사유."""
    e = h[i][4] * (1 - SLIP); sl = e * (1 + stop); tpp = e * (1 - tp)
    for j in range(i + 1, min(len(h), i + 1 + hold)):
        if h[j][2] >= sl: return -(stop + SLIP + 2 * FEE) * 100, "손절"
        if h[j][3] <= tpp: return (tp - SLIP - 2 * FEE) * 100, "익절"
    j = min(len(h) - 1, i + hold); x = h[j][4] * (1 + SLIP)
    return ((e - x) / e - 2 * FEE) * 100, "시간"
def run(pump, roll, stop, tp, hold=24):
    res = []
    for s, h in data.items():
        for i in events(h, pump=pump, roll=roll):
            if i + 2 >= len(h): continue
            r, why = sim(h, i, stop, tp, hold); res.append((s, h[i][0], r, why))
    return res
def summ(res):
    if not res: return "표본 없음"
    r = [x[2] for x in res]; w = [x for x in r if x > 0]; l = [x for x in r if x <= 0]
    pf = (sum(w) / -sum(l)) if l and sum(l) < 0 else float("inf")
    return f"n={len(r):3d} 승률 {len(w)/len(r)*100:4.0f}% 평균 {st.mean(r):+5.1f}% 중앙 {st.median(r):+5.1f}% 최악 {min(r):+6.1f}% 손익비 {pf:4.2f} | 익절 {sum(1 for x in res if x[3]=='익절')} 손절 {sum(1 for x in res if x[3]=='손절')} 시간 {sum(1 for x in res if x[3]=='시간')}"
print("종목", len(data), "총 봉", sum(len(v) for v in data.values()))
for pump in (0.30, 0.40, 0.60):
    print(f"\n[급등 기준 {pump*100:.0f}% 이상(24시간 내 저→고), 고점 대비 4% 하락 확인 후 숏]")
    for stop, tp in ((0.06, 0.10), (0.08, 0.15), (0.10, 0.20), (0.10, 0.30), (0.15, 0.30)):
        print(f"   손절 +{stop*100:.0f}% / 익절 −{tp*100:.0f}%: " + summ(run(pump, 0.04, stop, tp)))
print("\n[확인 강도 비교: 급등 40%, 손절 10% / 익절 20%]")
for roll in (0.02, 0.04, 0.07, 0.10):
    print(f"   고점 대비 {roll*100:.0f}% 하락 확인: " + summ(run(0.40, roll, 0.10, 0.20)))
res = run(0.40, 0.04, 0.10, 0.20)
print("\n표본 최근 사례(급등 40%, 4% 확인, 손절10/익절20):")
for s, t, r, why in sorted(res, key=lambda x: -x[1])[:12]: print("  ", s, dt.datetime.fromtimestamp(t/1000, K).strftime("%m-%d %H시"), f"{r:+.1f}%", why)

print("\n==== 신호 이후 가격 경로(손절·익절 없이) — 진입가 대비 ====")
import statistics as st
for pump in (0.30, 0.40, 0.60):
    rows = []
    for s, h in data.items():
        for i in events(h, pump=pump, roll=0.04):
            if i + 13 >= len(h): continue
            e = h[i][4]
            f12 = (h[min(len(h)-1, i+12)][4] / e - 1) * 100; f24 = (h[min(len(h)-1, i+24)][4] / e - 1) * 100
            mae = (max(x[2] for x in h[i+1:i+25]) / e - 1) * 100            # 최대 불리(상승) 폭
            mfe = (min(x[3] for x in h[i+1:i+25]) / e - 1) * 100            # 최대 유리(하락) 폭
            rows.append((f12, f24, mae, mfe))
    if not rows: continue
    n = len(rows)
    print(f"급등 {pump*100:.0f}%↑ n={n}: 12시간 후 가격 변화 중앙 {st.median(r[0] for r in rows):+.1f}% (하락 비율 {sum(1 for r in rows if r[0]<0)/n*100:.0f}%) / 24시간 후 중앙 {st.median(r[1] for r in rows):+.1f}% (하락 {sum(1 for r in rows if r[1]<0)/n*100:.0f}%)"
          f" | 24시간 내 최대 상승(불리) 중앙 {st.median(r[2] for r in rows):+.1f}% · 75분위 {sorted(r[2] for r in rows)[int(n*0.75)]:+.1f}% | 최대 하락(유리) 중앙 {st.median(r[3] for r in rows):+.1f}%")
