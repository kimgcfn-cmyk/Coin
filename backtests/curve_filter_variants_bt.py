"""수익곡선 필터 변형 비교(잔량 손절 여부·최소 정지시간) — curve_filter_bt.py 와 함께 사용 (2026-10-28)"""
import sys; sys.path.insert(0,"/Users/Muita/Desktop/Coin/backtests")
import curve_filter_bt as c
from cap_pair_bt import candles
data={k:candles(s,2880+80)[1] for k,s in c.SYM.items()}
W=(("12h",720),("24h",1440),("48h",2880))
def tot(**kw):
    out=[]; 
    for k in c.SYM:
        px=data[k]
        for wn,n in W:
            if len(px)<n+70: continue
            lo=len(px)-n; out.append(((k,wn),c.run(k,px[lo:],px[lo-70:lo],**kw)))
    return out
base=tot()
bt=sum(r["tot"] for _,r in base); bm=sum(r["mdd"] for _,r in base)
print(f"필터 없음: 합계 {bt:+.1f} MDD합 {bm:.1f} (구간 {len(base)})")
for cut in (True,False):
  for D in (10.0,15.0):
    for U in (5.0,8.0):
      for ms in (0,60,180):
        r=tot(D=D,U=U,cut=cut,min_stop=ms)
        t=sum(x["tot"] for _,x in r); m=sum(x["mdd"] for _,x in r); st=sum(x["stops"] for _,x in r)
        wins=sum(1 for (a,x),(b,y) in zip(r,base) if x["tot"]>y["tot"])
        bysym={k:sum(x["tot"] for (kk,w),x in r if kk==k)-sum(y["tot"] for (kk,w),y in base if kk==k) for k in c.SYM}
        print(f"잔량손절 {'O' if cut else 'X'} D={D:>4.0f} U={U:>2.0f} 최소정지 {ms:>3}분 | 합계 {t:+7.1f} ({t-bt:+6.1f}) MDD합 {m:6.1f} 개선 {wins}/{len(r)} 정지 {st:3}회 | 종목별 차이 CAP {bysym['cap']:+6.1f} Q {bysym['q']:+6.1f} TAG {bysym['tag']:+6.1f}")
