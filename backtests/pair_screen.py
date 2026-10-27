import sys, time, requests, inspect, pickle
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, "/private/tmp/claude-502/-Users-Muita-Desktop-Stock/5cb679a8-9796-4ca5-bb28-70c45593713f/scratchpad")
import cap_trend_bt as ct
from cap_pair_bt import mdd
src = inspect.getsource(ct.sim).replace("def sim(px, side,", "def sim_sp(px, side,", 1).replace("guard=True, trend=None,", "guard=True, spacing=None, trend=None,", 1)
src = src.replace("(last is None or p <= last*(1-step/100))", '(spacing == "off" or last is None or p <= last*(1-step/100))', 1)
src = src.replace("(last is None or p >= last*(1+step/100))", '(spacing == "off" or last is None or p >= last*(1+step/100))', 1)
ns = dict(ct.__dict__); exec(src, ns); sim_sp = ns["sim_sp"]
B = "https://api.bitget.com"
T = requests.get(B + "/api/v2/mix/market/tickers", params={"productType": "USDT-FUTURES"}, timeout=20).json()["data"]
rows = []
for x in T:
    s = x["symbol"]
    if not s.endswith("USDT") or "STOCK" in s: continue
    try: vol = float(x["usdtVolume"]); bid = float(x["bidPr"]); ask = float(x["askPr"]); chg = float(x["change24h"]) * 100
    except Exception: continue
    rows.append((s, vol, (ask / bid - 1) * 100, chg))
rows = [r for r in rows if r[1] >= 8e6 and r[2] <= 0.15]; rows.sort(key=lambda r: -r[1])
print("1차(24h 거래대금 8M↑, 스프레드 0.15%↓):", len(rows))
def candles(s, minutes=42 * 60):
    end = int(time.time() * 1000); start = end - minutes * 60000; out = {}
    while end > start:
        r = []
        for k in range(6):
            try:
                r = requests.get(B + "/api/v2/mix/market/history-candles", params={"symbol": s, "productType": "USDT-FUTURES", "granularity": "1m", "endTime": str(end), "limit": "200"}, timeout=15).json().get("data") or []
                if r: break
            except Exception: pass
            time.sleep(0.6 * (k + 1))
        if not r: break
        for z in r: out[int(z[0])] = (float(z[4]), float(z[6]))
        end = min(int(z[0]) for z in r) - 1; time.sleep(0.08)
    ks = sorted(k for k in out if k >= start); return [out[k] for k in ks]
with ThreadPoolExecutor(3) as ex: data = list(ex.map(lambda r: candles(r[0]), rows))
pickle.dump((rows, data), open("/tmp/pair_screen.pkl", "wb"))
print("수신 완료", sum(1 for d in data if len(d) > 2000))
