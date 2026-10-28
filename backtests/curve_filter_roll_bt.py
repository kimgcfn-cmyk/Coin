"""수익곡선 필터(정지·잔량 손절·그림자 재가동) 롤링 윈도우 백테스트 — CAP·Q·TAG 실전 프리셋, 최근 약 7일 (2026-10-28)
24시간 창을 6시간씩 밀며 여러 장세(상승·하락·급등락)에서 필터 없음과 D·U·최소정지 조합을 비교한다. 과거 재생일 뿐 투자 조언이 아니다."""
import sys, json, itertools
sys.path.insert(0, "/Users/Muita/Desktop/Coin/backtests"); sys.path.insert(0, ".")
import curve_filter_bt as c
from cap_pair_bt import candles

DAYS = 7; WIN = 1440; STEP = 360
GRID = [(D, U, ms) for D in (5.0, 10.0, 15.0, 20.0) for U in (3.0, 5.0, 8.0) for ms in (0, 180)]

if __name__ == "__main__":
    data = {}
    for k, s in c.SYM.items():
        ts, px = candles(s, DAYS * 1440 + 100); data[k] = px; print(k, "1분봉", len(px), "개 =", round(len(px) / 1440, 2), "일", flush=True)
    wins = []
    for k, px in data.items():
        lo = 70
        while lo + WIN <= len(px):
            wins.append((k, lo)); lo += STEP
    print("창", len(wins), "개", flush=True)
    base = {}; res = {g_: {} for g_ in GRID}
    for (k, lo) in wins:
        px = data[k]; w = px[lo:lo + WIN]; seed = px[lo - 70:lo]
        base[(k, lo)] = c.run(k, w, seed)
        for g_ in GRID:
            res[g_][(k, lo)] = c.run(k, w, seed, g_[0], g_[1], cut=True, min_stop=g_[2])
    out = dict(base={f"{k}|{lo}": v for (k, lo), v in base.items()}, res={f"{g_}": {f"{k}|{lo}": v for (k, lo), v in d.items()} for g_, d in res.items()})
    json.dump(out, open("/tmp/curve_filter_roll.json", "w"), ensure_ascii=False)
    keys = list(base)
    bt = sum(base[x]["tot"] for x in keys); bm = sum(base[x]["mdd"] for x in keys); bw = min(base[x]["tot"] for x in keys)
    bbad = sum(1 for x in keys if base[x]["tot"] < 0)
    print(f"\n필터 없음: 창 {len(keys)}개 평균 손익 {bt/len(keys):+.2f} 평균 MDD {bm/len(keys):.1f} 최악 {bw:+.1f} 손실 창 {bbad}개")
    print("D U 최소정지 | 평균손익(차이) | 평균MDD | 최악창 | 손실창 | 개선창 | 정지횟수/창 | 종목별 평균 손익 차이 CAP/Q/TAG")
    for g_ in GRID:
        r = res[g_]
        t = sum(r[x]["tot"] for x in keys); m = sum(r[x]["mdd"] for x in keys); w = min(r[x]["tot"] for x in keys)
        bad = sum(1 for x in keys if r[x]["tot"] < 0); imp = sum(1 for x in keys if r[x]["tot"] > base[x]["tot"])
        st = sum(r[x]["stops"] for x in keys) / len(keys)
        per = {s: sum(r[x]["tot"] - base[x]["tot"] for x in keys if x[0] == s) / max(1, sum(1 for x in keys if x[0] == s)) for s in c.SYM}
        print(f"D={g_[0]:>3.0f} U={g_[1]:>2.0f} {g_[2]:>3}분 | {t/len(keys):+7.2f} ({(t-bt)/len(keys):+6.2f}) | {m/len(keys):6.1f} | {w:+7.1f} | {bad:2}개 | {imp}/{len(keys)} | {st:5.1f} | {per['cap']:+6.2f} {per['q']:+6.2f} {per['tag']:+6.2f}")
