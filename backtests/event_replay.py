import sys, json, time, requests, datetime as dt
sys.argv = [sys.argv[0], "QUSDT", "300"]
sys.path.insert(0, "."); 
import counter_bt as cb
BASE = "https://api.bitget.com/api/v2/mix/market/history-candles"
def fetch_1m(sym, t0_ms, t1_ms):
    rows = {}; end = t1_ms
    while end > t0_ms:
        try: r = requests.get(BASE, params={"symbol": sym, "productType": "USDT-FUTURES", "granularity": "1m", "endTime": str(end), "limit": "200"}, timeout=20).json().get("data") or []
        except Exception: break
        if not r: break
        for x in r: rows[int(x[0])] = float(x[4])
        end = min(int(x[0]) for x in r) - 1; time.sleep(0.06)
    ts = sorted(t for t in rows if t0_ms <= t <= t1_ms)
    return [rows[t] for t in ts], ts
def main():
    ev = json.load(open("pump_events.json")); out = []
    for k, e in enumerate(sorted(ev, key=lambda x: x["ts"])):
        px, ts = fetch_1m(e["sym"], e["ts"] - 75 * 60000, e["ts"] + 24 * 3600000)
        if len(px) < 1400: print("건너뜀(데이터 부족)", e["sym"], len(px)); continue
        seed = px[:70]; w = px[70:]
        out.append(dict(sym=e["sym"], ts=e["ts"], pump=e["pump"], seed=seed, px=w))
        print(k + 1, "/", len(ev), e["sym"], len(w), "분", flush=True)
    json.dump(out, open("pump_1m.json", "w"))
    print("저장", len(out), "이벤트")
if __name__ == "__main__": main()
