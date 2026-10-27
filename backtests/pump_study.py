import requests, time, json, sys, concurrent.futures as cf
BASE = "https://api.bitget.com/api/v2/mix/market"
def tickers():
    r = requests.get(f"{BASE}/tickers", params={"productType": "USDT-FUTURES"}, timeout=20).json()["data"]
    return [x for x in r if float(x.get("usdtVolume") or 0) > 0]
def hist(sym, bars=1100):
    """1시간봉 최대 bars개 (과거로 거슬러). 반환: [(ts,o,h,l,c,volUSDT)] 오름차순"""
    end = int(time.time() * 1000); rows = {}
    while len(rows) < bars:
        try:
            r = requests.get(f"{BASE}/history-candles", params={"symbol": sym, "productType": "USDT-FUTURES", "granularity": "1H", "endTime": str(end), "limit": "200"}, timeout=20).json().get("data") or []
        except Exception: break
        if not r: break
        for x in r: rows[int(x[0])] = (int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[6]))
        end = min(int(x[0]) for x in r) - 1
        if len(r) < 200: break
        time.sleep(0.05)
    return [rows[k] for k in sorted(rows)]
def main():
    tk = tickers(); syms = [x["symbol"] for x in tk]
    print("심볼", len(syms)); data = {}
    def job(s): return s, hist(s)
    with cf.ThreadPoolExecutor(8) as ex:
        for s, h in ex.map(job, syms):
            if len(h) > 100: data[s] = h
    json.dump(data, open("pump_hist.json", "w"))
    print("저장", len(data), "종목, 평균 봉수", sum(len(v) for v in data.values()) // max(1, len(data)))
if __name__ == "__main__": main()
