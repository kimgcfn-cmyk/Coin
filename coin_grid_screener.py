"""
코인 그리드 전략 스크리너 — 어떤 종목에 coin_grid_bot.py 를 돌려도 되는지 골라준다 (2026-10-25)
================================================================================
Bitget USDT-M 선물 전체를 훑어서 '이 전략이 맞는 모양'(변동이 크고 위아래로 왕복하거나 상승, 하락 추세 아님, 유동성 충분)인 종목을 점수로 정렬한다.
근거: 2026-10-24~25 모의(54종목·10시간 윈도우 165개) — 10시간 고저폭 12% 이상에서 수익이 나고, 하락 추세(평균 -11)·잔잔한 횡보(≈0)에서는 안 맞았다.

사용:
  python coin_grid_screener.py                  # 후보 표만 출력 (주문·알림 없음)
  python coin_grid_screener.py --backtest 5     # 상위 5개를 coin_grid_backtest.py 로 최근 10시간 모의까지 붙여서 출력
  python coin_grid_screener.py --notify         # 상위 후보를 디스코드로 알림(coin_grid_bot.send_discord 사용)
  python coin_grid_screener.py --min-volume 5000000 --top 15      # 최소 24시간 거래대금(USDT), 표시 개수
  python coin_grid_screener.py --symbol IRYS    # 특정 종목만 평가(통과/제외 사유 포함)
주의: 후보는 '시작해도 되는 모양'일 뿐 수익을 보장하지 않는다(최근 10시간 지표). 이미 크게 급등한 뒤인 종목은 고점 근처 위험이 있다. 투자 조언이 아니다.
"""
import sys, time, json, statistics as st
import requests

B = "https://api.bitget.com/api/v2/mix/market"
EXCLUDE = {"TQQQUSDT", "PLTRUSDT"}     # 주식 파생 등 제외할 종목
WIN = 120                               # 5분봉 120개 = 10시간

def tickers():
    return requests.get(f"{B}/tickers", params={"productType": "USDT-FUTURES"}, timeout=20).json()["data"]

def bars_5m(sym, n=200):
    for _ in range(3):
        try:
            r = requests.get(f"{B}/candles", params={"symbol": sym, "productType": "USDT-FUTURES", "granularity": "5m", "limit": str(n)}, timeout=15).json()
            d = r.get("data") or []
            if d:
                rows = sorted((int(x[0]), float(x[2]), float(x[3]), float(x[4]), float(x[6])) for x in d)   # ts, high, low, close, quoteVol
                return rows
        except Exception:
            pass
        time.sleep(0.6)
    return []

def evaluate(t, rows):
    """t: 티커 dict, rows: 5분봉. 반환: dict(지표, 판정, 점수, 사유)"""
    out = {"symbol": t["symbol"]}
    if len(rows) < WIN + 5:
        out.update(ok=False, reasons=["데이터 부족"], score=0); return out
    w = rows[-WIN:]; closes = [r[3] for r in w]; hi = max(r[1] for r in w); lo = min(r[2] for r in w)
    p0, p1 = closes[0], closes[-1]
    rng = (hi - lo) / p0 * 100; net = (p1 / p0 - 1) * 100; from_high = (p1 / hi - 1) * 100
    last2h = (p1 / closes[-24] - 1) * 100 if len(closes) >= 24 else 0.0
    to_peak = (hi / p0 - 1) * 100
    vol24 = float(t["usdtVolume"]); bid = float(t.get("bidPr") or 0); ask = float(t.get("askPr") or 0)
    spread = (ask / bid - 1) * 100 if bid > 0 and ask > 0 else 9.9
    fund = float(t.get("fundingRate") or 0) * 100
    # 거래대금 급증 여부(최근 1시간 vs 직전 9시간 평균 시간당)
    qv = [r[4] for r in w]; recent = sum(qv[-12:]); base = sum(qv[:-12]) / 9 if sum(qv[:-12]) > 0 else 0
    vol_ratio = recent / base if base > 0 else 0.0
    # 유형
    if to_peak >= 10 and from_high <= -10: kind = "급등 후 하락"
    elif net <= -8: kind = "하락 추세"
    elif net >= 8 and from_high > -6: kind = "꾸준한 상승"
    elif net >= 8: kind = "상승(되돌림 큼)"
    elif rng >= 12 and abs(net) < 8: kind = "큰 폭 왕복"
    else: kind = "잔잔한 횡보"
    reasons = []
    if vol24 < 3_000_000: reasons.append(f"거래대금 부족({vol24/1e6:.1f}M<3M)")
    if spread > 0.2: reasons.append(f"스프레드 넓음({spread:.2f}%)")
    if rng < 10: reasons.append(f"변동 작음(10h 고저폭 {rng:.1f}%<10%)")
    if net <= -8: reasons.append(f"하락 추세(10h {net:+.1f}%)")
    if from_high <= -12: reasons.append(f"고점 대비 {from_high:.1f}% 하락 중")
    if last2h <= -6: reasons.append(f"최근 2시간 {last2h:+.1f}% 급락")
    ok = not reasons
    # 점수(통과한 종목끼리 비교용 0~100)
    s = 0.0
    s += min(40, max(0, (rng - 10) / 30 * 40))                       # 변동: 10%→0, 40%→40점
    s += 25 if net >= -3 else (10 if net > -8 else 0)                # 추세
    s += 15 if from_high >= -5 else (8 if from_high >= -10 else 0)   # 고점 근처 여부(너무 높으면 아래 경고)
    s += 10 if vol24 >= 10_000_000 else (6 if vol24 >= 3_000_000 else 0)
    s += 10 if spread <= 0.05 else (5 if spread <= 0.1 else 0)
    warn = []
    if rng > 80: warn.append("변동 극단(80%+) — 한도 작게")
    if from_high >= -1 and to_peak >= 20: warn.append("급등 직후 고점 근처 — 되돌림 위험")
    if fund > 0.05: warn.append(f"펀딩 {fund:+.3f}%(롱이 냄)")
    out.update(ok=ok, reasons=reasons, score=round(s, 1), kind=kind, rng=rng, net=net, from_high=from_high, last2h=last2h, vol24=vol24, spread=spread, fund=fund, vol_ratio=vol_ratio, price=p1, warn=warn)
    return out

def fmt_row(o):
    return (f"{o['symbol']:<12}{o['score']:>5.0f} {o['kind']:<10} 10h고저폭 {o['rng']:>6.1f}% 10h {o['net']:>+7.1f}% 고점대비 {o['from_high']:>+6.1f}% "
            f"2h {o['last2h']:>+6.1f}% 거래대금 {o['vol24']/1e6:>7.1f}M 스프레드 {o['spread']:.3f}% 거래량(1h/평균) {o['vol_ratio']:>4.1f}x"
            + (f"  ⚠️ {' / '.join(o['warn'])}" if o['warn'] else ""))

def main():
    a = sys.argv[1:]
    def val(f, d=None): return a[a.index(f) + 1] if f in a and a.index(f) + 1 < len(a) else d
    min_vol = float(val("--min-volume", 3_000_000)); top = int(val("--top", 10)); nbt = int(val("--backtest", 0)); notify = "--notify" in a
    tk = tickers()
    if "--symbol" in a:
        s = val("--symbol").upper(); s = s if s.endswith("USDT") else s + "USDT"
        tk = [x for x in tk if x["symbol"] == s]
        if not tk: print("종목을 찾지 못했습니다"); return
    else:
        tk = [x for x in tk if float(x["usdtVolume"]) >= min_vol and x["symbol"] not in EXCLUDE]
    print(f"스캔 대상 {len(tk)}개 (24h 거래대금 ≥ {min_vol/1e6:.1f}M USDT) — 최근 10시간 5분봉 기준\n")
    res = []
    for x in tk:
        res.append(evaluate(x, bars_5m(x["symbol"]))); time.sleep(0.1)
    ok = sorted([o for o in res if o["ok"]], key=lambda o: -o["score"]); bad = [o for o in res if not o["ok"]]
    print(f"✅ 통과 {len(ok)}개 / ❌ 제외 {len(bad)}개")
    if "--symbol" in a:
        for o in res:
            print(("✅ " if o["ok"] else "❌ ") + (fmt_row(o) if "kind" in o else o["symbol"]))
            if o["reasons"]: print("   제외 사유:", " / ".join(o["reasons"]))
        return
    print("\n=== 후보 (점수 높은 순) ===")
    for o in ok[:top]: print(fmt_row(o))
    if not ok: print("  통과한 종목이 없습니다 — 지금은 이 전략이 맞는 장이 아닐 수 있습니다.")
    from collections import Counter
    import re as _re
    c = Counter(_re.sub(r"\s*[-+]?\d+(\.\d+)?%?", "", r.split("(")[0]).strip() for o in bad for r in o["reasons"])
    print("\n제외 사유 분포:", dict(c.most_common()))
    kinds = Counter(o["kind"] for o in res if "kind" in o); print("전체 유형 분포:", dict(kinds.most_common()))
    if nbt and ok:
        try:
            from coin_grid_backtest import candles, sim, CFG
            L0 = (3.0, 4.5, 6.0); LO = dict(confirm=3, max_per_min=1); CR = dict(win=5, pct=8.0, mode="pause", rebound=2.0, quiet=30)
            cur = dict(ladder=L0, lad_opts=LO, win=1, surge_pct=3.0, crash=CR)
            print(f"\n=== 상위 {nbt}개 참고 모의 (최근 10시간 1분봉, 현재 전략, 물량 0 시작) ===")
            for o in ok[:nbt]:
                ts, px, vol = candles(o["symbol"], 10)
                if not px: continue
                r = sim(px, vol, {**CFG, **cur}, 0.0)
                print(f"{o['symbol']:<12} 합계 {r['total']:>+7.2f} (실현 {r['realized']:+.2f} 평가 {r['unreal']:+.2f}) 매수 {r['buys']} 매도 {len(r['sells'])} 급등 {r['surge']}회 | 그냥 30USDT 보유 {30*(px[-1]/px[0]-1):+.2f}")
        except Exception as e:
            print("모의 실행 실패:", e)
    if notify:
        import coin_grid_bot as g
        lines = [f"{o['symbol']} 점수 {o['score']:.0f} · {o['kind']} · 10h 고저폭 {o['rng']:.0f}% · 고점대비 {o['from_high']:+.0f}%" + (" ⚠️" if o["warn"] else "") for o in ok[:top]]
        g.send_discord("🔎 <b>그리드 전략 후보 종목</b> (최근 10시간 기준)\n" + ("\n".join(lines) if lines else "통과한 종목 없음") + "\n※ 고저폭 12%+ · 하락 추세 아님 · 거래대금 3M+ 기준. 수익 보장 아님.")
        print("\n디스코드 알림 전송 완료")

if __name__ == "__main__":
    main()
