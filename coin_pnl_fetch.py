"""
최근 24시간 코인 투자 손익 분석용 데이터 수집 (서버에서 실행, 읽기 전용) — coin_pnl_report.py 가 ssh로 호출한다 (2026-10-26)
체결(실현손익 포함)·계정 원장(잔고 추이, 펀딩)·봇 주문 ID·현재 자산·포지션·봇 장부를 JSON 한 덩어리로 stdout에 출력한다.
"""
import json, re, glob, gzip, time, sys
import coin_grid_bot as g
ex=g.get_exchange(); ex.load_markets()
now=int(time.time()*1000); start=now-24*3600*1000
def pages(meth, params, key="list", maxpages=40):
    out=[]; cursor=None
    for _ in range(maxpages):
        p=dict(params, limit="100")
        if cursor: p["cursor"]=cursor
        r=getattr(ex,meth)(p); d=r.get("data") or {}
        lst=d.get(key,[]) if isinstance(d,dict) else d
        if not lst: break
        out+=lst
        oldest=min(int(x.get("createdTime") or x.get("ts") or 0) for x in lst)
        cursor=d.get("cursor") if isinstance(d,dict) else None
        if oldest<start or not cursor or len(lst)<100: break
        time.sleep(0.2)
    return out
fills=[x for x in pages("privateUtaGetV3TradeFills",{"category":"USDT-FUTURES","startTime":str(start-3600*1000),"endTime":str(now)}) if int(x["createdTime"])>=start]
recs=pages("privateUtaGetV3AccountFinancialRecords",{"category":"USDT-FUTURES","coin":"USDT","startTime":str(start-3600*1000),"endTime":str(now)})
recs=[x for x in recs if int(x["ts"])>=start-1]
# 봇 주문 ID (실거래 봇 로그)
ids=set()
for name in ("coin_grid_short_order","coin_grid_short_t","coin_grid_cap","coin_grid_short_cap","coin_grid_aria"):
    for p in glob.glob(f"logs/{name}.log*"):
        op=gzip.open if p.endswith(".gz") else open
        try:
            for l in op(p,"rt",encoding="utf-8"):
                for m in re.finditer(r"주문ID (\d{12,})",l): ids.add(m.group(1))
        except Exception: pass
a=(ex.privateUtaGetV3AccountAssets({}).get("data") or {})
pos=[]
for p in ex.fetch_positions(None,params=g.UTA):
    if float(p["contracts"] or 0)>0:
        pos.append(dict(symbol=p["symbol"].split("/")[0]+"USDT",side=p["side"],qty=float(p["contracts"]),avg=float(p["entryPrice"]),upl=float(p["unrealizedPnl"] or 0),notional=float(p["notional"] or 0),lev=p.get("leverage"),mark=float(p.get("markPrice") or 0)))
states={}
for nm,f in (("ORDER","coin_grid_short_order_state.json"),("T","coin_grid_short_t_state.json"),("CAP","coin_grid_cap_state.json"),("CAP_S","coin_grid_short_cap_state.json"),("ARIA","coin_grid_aria_state.json")):
    try: s=json.load(open(f,encoding="utf-8")); states[nm]={k:s.get(k) for k in ("qty","avg_cost","cum_pnl","cum_fees")}
    except Exception: pass
import subprocess
try:
    cron=[l for l in subprocess.run(["crontab","-l"],capture_output=True,text=True).stdout.splitlines() if l.strip() and not l.strip().startswith("#")]
except Exception: cron=[]
mk={"ORDER 숏 봇":"coin_grid_short_order.lock","T 숏 봇":"coin_grid_short_t.lock","CAP 롱 봇":"coin_grid_cap.lock","CAP 숏 봇":"coin_grid_short_cap.lock","ARIA 롱 봇":"coin_grid_aria.lock"}
active={n:any(m in l for l in cron) for n,m in mk.items()}
print(json.dumps(dict(now=now,start=start,fills=fills,recs=recs,bot_ids=sorted(ids),active=active,assets={k:a.get(k) for k in ("accountEquity","usdtEquity","unrealisedPnl","imr","mmr","effEquity")},positions=pos,states=states),ensure_ascii=False))
