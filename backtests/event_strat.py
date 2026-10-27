import sys, json, statistics as st, datetime as dt
sys.argv = [sys.argv[0], "QUSDT", "300"]; sys.path.insert(0, ".")
import counter_bt as cb
ev = json.load(open("pump_1m.json"))
K = dt.timezone(dt.timedelta(hours=9))
C1 = dict(max_total_usdt=300.0, keep_usdt=100.0, derisk_hold_usdt=150.0)
def pair(**x): d = dict(preset="hd_cap_long", entry_ref_min=60, entry_min_gap_min=5, **C1); d.update(x); return d
def short_only(**x): d = dict(preset="hd_cap_long", sides=("short",), opp_state="", entry_ref_min=60, entry_min_gap_min=5, relax_opp_stage=0, trend_hold=False, **C1); d.update(x); return d
WIDE = dict(entry_base_pct=3.0, ladder_base_pct=6.0, ladder_step_pct=3.0)
V = (("P 합동 C1 (현행 설정)", pair(), 120.0),
     ("S 숏 단독 C1", short_only(), 60.0),
     ("W 숏 단독 넓은 사다리(3/6/3)", short_only(**WIDE), 60.0),
     ("PW 합동 넓은 사다리(3/6/3)", pair(**WIDE), 120.0))
def summarize(rows, capital):
    r = [x["tot"] / capital * 100 for x in rows]; m = [x["mdd"] / capital * 100 for x in rows]; h = [x["hold_h"] for x in rows]
    w = [v for v in r if v > 0]
    tot = sum(x["tot"] for x in rows)
    return dict(n=len(r), win=len(w) / len(r) * 100, mean=st.mean(r), med=st.median(r), worst=min(r), best=max(r), mdd_mean=st.mean(m), mdd_max=max(m), usdt=tot, hold=st.mean(h), stops=sum(x["stops"] for x in rows))
if __name__ == "__main__":
    res = {k: [] for k, _, _ in V}; per = []
    for e in ev:
        line = [e["sym"], dt.datetime.fromtimestamp(e["ts"] / 1000, K).strftime("%m-%d %H시"), e["pump"]]
        for nm, ov, cap in V:
            r = cb.run(e["px"], e["seed"], **ov); r["tot_pct"] = r["tot"] / cap * 100
            res[nm].append(r); line.append(round(r["tot"] / cap * 100, 1))
        per.append(line)
    json.dump(per, open("event_strat_per.json", "w"), ensure_ascii=False)
    print(f"이벤트 {len(ev)}건 · 신호 시점부터 24시간 재생 · 기준 자본 숏 단독 60, 합동 120(한도 300)")
    print(f"{'설정':<32}{'승률':>6}{'평균':>8}{'중앙':>8}{'최악':>9}{'최고':>8}{'평균MDD':>8}{'최대MDD':>8}{'합계USDT':>9}{'보유h':>6}{'손절':>5}")
    for nm, ov, cap in V:
        s = summarize(res[nm], cap)
        print(f"{nm:<30}{s['win']:5.0f}%{s['mean']:+7.1f}%{s['med']:+7.1f}%{s['worst']:+8.1f}%{s['best']:+7.1f}%{s['mdd_mean']:7.1f}%{s['mdd_max']:7.1f}%{s['usdt']:+9.1f}{s['hold']:6.1f}{s['stops']:5d}")
    print("\n이벤트별(수익률 %, 순서: P / S / W / PW) 최악 8건(합동 P 기준)")
    for l in sorted(per, key=lambda x: x[3])[:8]: print("  ", l)
    print("이벤트별 최고 8건(합동 P 기준)")
    for l in sorted(per, key=lambda x: -x[3])[:8]: print("  ", l)
