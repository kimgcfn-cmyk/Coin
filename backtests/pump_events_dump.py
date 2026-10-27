import json, datetime as dt
data = json.load(open("pump_hist.json"))
def events(h, pump=0.30, look=24, roll=0.04, cool=24):
    out = []; last = -999
    for i in range(look + 2, len(h) - 1):
        w = h[i - look:i + 1]
        hi_idx = max(range(len(w)), key=lambda k: w[k][2]); hi = w[hi_idx][2]
        if hi_idx >= len(w) - 1: continue
        lo = min(x[3] for x in w[:hi_idx + 1])
        if hi / lo - 1 < pump or h[i][4] > hi * (1 - roll) or i - last < cool: continue
        out.append(dict(sym="", ts=h[i][0], entry=h[i][4], pump=round((hi / lo - 1) * 100, 1), hi=hi)); last = i
    return out
ev = []
for s, h in data.items():
    for e in events(h):
        e["sym"] = s
        # 신호 이후 24시간이 이미 지나 있어야 재생 가능
        if e["ts"] + 25 * 3600 * 1000 <= h[-1][0]: ev.append(e)
json.dump(ev, open("pump_events.json", "w"))
print(len(ev), "이벤트(신호 후 24시간 이상 지난 것)")
K = dt.timezone(dt.timedelta(hours=9))
for e in sorted(ev, key=lambda x: x["ts"])[:5]: print(e["sym"], dt.datetime.fromtimestamp(e["ts"]/1000, K).strftime("%m-%d %H시"), e["pump"])
