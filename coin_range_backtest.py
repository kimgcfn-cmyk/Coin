"""
코인 범위 매매 백테스트 (횡보장 전용, 최적화)
================================================
횡보장에서 RSI 역추세로 수익을 내는 전략.

[배경]
  국면 적응형 테스트 결과, 돌파 매매는 이 시장에서 손실(-$3,490)이었고
  범위 매매는 횡보장(2023)에서 +$965로 유효했다.
  → 손실 주범인 돌파를 제거하고 범위 매매만 남겨 최적화한다.

[전략 — 떨사오팔 원리]
  "떨어지면 사고 오르면 판다"
  - RSI 과매도(기본 35↓) → 매수
  - RSI 과매수(기본 65↑) 또는 목표% 도달 → 매도
  - 박스 이탈(손절%) → 손절

  단, 하락장에서는 진입 안 함 (칼 떨어지는데 잡지 않기):
  - 가격이 장기 MA 아래로 크게(-N%) 벗어나면 관망

[검증된 리스크 관리]
  - 1회 거래당 리스크: 계좌의 1%
  - 일일 손실 한도: 3연속 손절 시 당일 중단
  - 메이저 코인만 (BTC/ETH/SOL)

실행:
  python coin_range_backtest.py              # BTC, 4년, 1배
  python coin_range_backtest.py --optimize   # 파라미터 최적화 탐색
  python coin_range_backtest.py --compare    # 레버리지 1/2/3배
  python coin_range_backtest.py --symbol ETH/USDT
"""

import sys, time
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional

try:
    import ccxt
    HAS_CCXT = True
except ImportError:
    HAS_CCXT = False

# ══════════════════════════════════════════════════════
#  설정
# ══════════════════════════════════════════════════════
CFG = {
    "symbol"          : "BTC/USDT",  # 현물 (백테스트는 Binance, 실매매는 Bitget)
    "timeframe"       : "4h",        # 4시간봉 (무이타 스캐너와 동일)
    "years"           : 4.0,         # 백테스트 기간 (년) — --years로 조절
    "starting_cash"   : 10_000.0,    # 시작 자본 (USDT)

    # ── 하락장 필터 (칼 안 잡기) ───────────────────────
    "ma_period"       : 50,          # 장기 이동평균
    "down_guard_pct"  : 8.0,         # 가격이 MA보다 -8% 아래면 하락장 → 관망

    # ── 범위 매매 (RSI 역추세) ─────────────────────────
    "rsi_period"      : 14,
    "range_buy_rsi"   : 35.0,        # RSI 35 이하 = 과매도 = 매수
    "range_sell_rsi"  : 65.0,        # RSI 65 이상 = 과매수 = 매도
    "range_target_pct": 0.03,        # 또는 +3% 도달 시 익절
    "range_stop_pct"  : 0.03,        # 손절 -3% (박스 하단 이탈)
    "max_hold_bars"   : 20,          # 최대 보유 20봉 (시간 손절)

    # ── 검증된 리스크 관리 ─────────────────────────────
    "risk_per_trade"  : 0.01,        # 1회 거래당 계좌의 1%
    "daily_loss_limit": 3,           # 3연속 손절 시 당일 중단

    # ── 레버리지 ───────────────────────────────────────
    "leverage"        : 1.0,

    # ── 비용 ───────────────────────────────────────────
    "fee_pct"         : 0.06,
    "slippage_pct"    : 0.02,
}

# ══════════════════════════════════════════════════════
#  지표 계산
# ══════════════════════════════════════════════════════
def calc_rsi(closes: list[float], period: int = 14) -> list[Optional[float]]:
    """RSI 계산 (Wilder 방식)"""
    n = len(closes)
    rsi: list[Optional[float]] = [None] * n
    if n < period + 1:
        return rsi

    gains, losses = [], []
    for i in range(1, n):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))

    # 첫 평균
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, n):
        if i > period:
            avg_gain = (avg_gain * (period-1) + gains[i-1]) / period
            avg_loss = (avg_loss * (period-1) + losses[i-1]) / period
        if avg_loss == 0:
            rsi[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi[i] = 100 - (100 / (1 + rs))
    return rsi

def calc_sma(values: list[float], period: int) -> list[Optional[float]]:
    """단순 이동평균"""
    n = len(values)
    sma: list[Optional[float]] = [None] * n
    for i in range(period-1, n):
        sma[i] = sum(values[i-period+1:i+1]) / period
    return sma

# ══════════════════════════════════════════════════════
#  데이터 수집
# ══════════════════════════════════════════════════════
def fetch_ohlcv(symbol: str, timeframe: str, since_date: str) -> list:
    """
    OHLCV 수집 (페이징).
    [데이터 소스] 백테스트 검증용으로 Binance 사용.
      - Bitget 선물은 과거 4시간봉을 200~400개만 제공(페이징 제한)
      - Binance는 2022년~현재 전체(약 9900개)를 안정적으로 제공
      - BTC 가격 흐름은 거래소 간 거의 동일하므로 검증에 문제없음
      - 실제 매매는 무이타님의 Bitget에서 (신호는 동일)
    """
    if not HAS_CCXT:
        print("  ❌ ccxt 미설치 — pip install ccxt")
        return []
    ex = ccxt.binance()   # 과거 데이터가 풍부한 바이낸스로 검증
    # 현물 심볼로 정규화 (BTC/USDT:USDT → BTC/USDT)
    spot_symbol = symbol.split(":")[0]
    all_ohlcv = []
    since = ex.parse8601(since_date)
    limit = 1000
    for _ in range(50):
        try:
            batch = ex.fetch_ohlcv(spot_symbol, timeframe, since=since, limit=limit)
        except Exception as e:
            print(f"  ⚠️ 수집 오류: {e}")
            break
        if not batch:
            break
        all_ohlcv += batch
        since = batch[-1][0] + 1
        if len(batch) < limit:
            break
        if batch[-1][0] >= ex.milliseconds():
            break
        time.sleep(0.3)
    # [ts, open, high, low, close, volume]
    return all_ohlcv

# ══════════════════════════════════════════════════════
#  백테스트 엔진
# ══════════════════════════════════════════════════════
@dataclass
class Trade:
    entry_date: str = ""
    exit_date: str = ""
    entry_price: float = 0.0
    exit_price: float = 0.0
    result: str = ""       # "win" | "loss"
    pnl: float = 0.0       # 손익 (USDT)
    pnl_pct: float = 0.0   # 계좌 대비 %
    strat: str = ""        # "breakout" | "range"

# ══════════════════════════════════════════════════════
#  백테스트 엔진 (범위 매매 전용)
# ══════════════════════════════════════════════════════
@dataclass
class Trade:
    entry_date: str = ""
    exit_date: str = ""
    entry_price: float = 0.0
    exit_price: float = 0.0
    result: str = ""
    pnl: float = 0.0
    exit_reason: str = ""   # "target" | "rsi" | "stop" | "timeout"

def run_backtest(ohlcv: list, leverage: float = 1.0) -> dict:
    """
    범위 매매 백테스트:
      - RSI 과매도 매수 → 과매수/목표% 매도, 박스이탈 손절
      - 하락장(MA -N% 이탈)에서는 진입 안 함
    """
    fee = (CFG["fee_pct"] + CFG["slippage_pct"]) / 100
    n = len(ohlcv)
    if n < CFG["ma_period"] + 2:
        return {"error": "데이터 부족"}

    closes = [c[4] for c in ohlcv]
    rsi = calc_rsi(closes, CFG["rsi_period"])
    sma = calc_sma(closes, CFG["ma_period"])

    cash = CFG["starting_cash"]
    equity_curve = []
    trades: list = []
    peak = cash
    mdd = 0.0
    consecutive_losses = 0
    last_day = ""

    i = CFG["ma_period"]
    while i < n - 1:
        ts, o, h, l, c, v = ohlcv[i]
        date = datetime.fromtimestamp(ts/1000, timezone.utc).strftime("%Y-%m-%d %H:%M")
        day = date[:10]
        if day != last_day:
            consecutive_losses = 0
            last_day = day

        if consecutive_losses >= CFG["daily_loss_limit"]:
            equity_curve.append((date, round(cash, 2)))
            i += 1
            continue

        # ── 하락장 필터: 가격이 MA보다 크게 아래면 관망 ──
        ma = sma[i]
        down_market = False
        if ma is not None and ma > 0:
            if (c - ma) / ma * 100 < -CFG["down_guard_pct"]:
                down_market = True

        cur_rsi = rsi[i]
        entry = None
        if (not down_market) and cur_rsi is not None and cur_rsi <= CFG["range_buy_rsi"]:
            entry = c  # 과매도 종가 매수

        if entry is not None:
            stop = entry * (1 - CFG["range_stop_pct"])
            target = entry * (1 + CFG["range_target_pct"])
            stop_dist = CFG["range_stop_pct"]
            risk_amount = cash * CFG["risk_per_trade"]
            position_value = (risk_amount / stop_dist) * leverage
            position_value = min(position_value, cash * leverage)

            exit_price = None; exit_date = date; result = "open"; reason = ""
            for j in range(i, min(i + CFG["max_hold_bars"], n)):
                _, oj, hj, lj, cj, _ = ohlcv[j]
                ej = datetime.fromtimestamp(ohlcv[j][0]/1000, timezone.utc).strftime("%Y-%m-%d %H:%M")
                if lj <= stop:
                    exit_price = stop; exit_date = ej; result = "loss"; reason = "stop"; break
                if hj >= target:
                    exit_price = target; exit_date = ej; result = "win"; reason = "target"; break
                if rsi[j] is not None and rsi[j] >= CFG["range_sell_rsi"]:
                    exit_price = cj; exit_date = ej
                    result = "win" if cj > entry else "loss"; reason = "rsi"; break
            if exit_price is None:
                lj = min(i + CFG["max_hold_bars"], n-1)
                exit_price = closes[lj]
                exit_date = datetime.fromtimestamp(ohlcv[lj][0]/1000, timezone.utc).strftime("%Y-%m-%d %H:%M")
                result = "win" if exit_price > entry else "loss"; reason = "timeout"

            price_change = (exit_price - entry) / entry
            net = position_value * price_change - position_value * fee * 2
            cash += net
            trades.append(Trade(entry_date=date, exit_date=exit_date,
                                entry_price=entry, exit_price=exit_price,
                                result=result, pnl=net, exit_reason=reason))
            if result == "loss":
                consecutive_losses += 1
            else:
                consecutive_losses = 0

            exit_i = i
            for j in range(i, min(i+CFG["max_hold_bars"], n)):
                ej = datetime.fromtimestamp(ohlcv[j][0]/1000, timezone.utc).strftime("%Y-%m-%d %H:%M")
                if ej == exit_date:
                    exit_i = j; break
            i = max(exit_i, i) + 1
        else:
            i += 1

        equity_curve.append((date, round(cash, 2)))
        if cash > peak: peak = cash
        dd = (peak - cash)/peak*100 if peak > 0 else 0
        mdd = max(mdd, dd)
        if cash <= 0:
            print("  경고: 계좌 소진"); break

    wins = [t for t in trades if t.result == "win"]
    losses = [t for t in trades if t.result == "loss"]
    win_rate = len(wins)/len(trades)*100 if trades else 0
    total_return = (cash/CFG["starting_cash"]-1)*100
    avg_win = sum(t.pnl for t in wins)/len(wins) if wins else 0
    avg_loss = sum(t.pnl for t in losses)/len(losses) if losses else 0
    pf = abs(sum(t.pnl for t in wins)/sum(t.pnl for t in losses)) if losses and sum(t.pnl for t in losses)!=0 else 0

    # 청산 사유별
    reason_counts = {}
    for t in trades:
        reason_counts[t.exit_reason] = reason_counts.get(t.exit_reason, 0) + 1

    # 연도별
    yearly = {}
    for t in trades:
        yr = t.entry_date[:4]
        if yr not in yearly: yearly[yr] = {"trades":0,"wins":0,"pnl":0.0}
        yearly[yr]["trades"] += 1
        if t.result=="win": yearly[yr]["wins"] += 1
        yearly[yr]["pnl"] += t.pnl
    yearly_summary = {}
    for yr,d in yearly.items():
        yearly_summary[yr] = {"trades":d["trades"],
                              "win_rate":round(d["wins"]/d["trades"]*100,1) if d["trades"] else 0,
                              "pnl":round(d["pnl"],2)}

    n_days = (ohlcv[-1][0]-ohlcv[0][0])/(1000*86400) if len(ohlcv)>1 else 1
    n_years = n_days/365
    cagr = ((cash/CFG["starting_cash"])**(1/n_years)-1)*100 if n_years>0 and cash>0 else 0

    return {
        "leverage": leverage,
        "final_cash": round(cash,2),
        "total_return_pct": round(total_return,2),
        "cagr_pct": round(cagr,2),
        "mdd_pct": round(mdd,2),
        "n_trades": len(trades),
        "win_rate": round(win_rate,1),
        "yearly": yearly_summary,
        "reason_counts": reason_counts,
        "avg_win": round(avg_win,2),
        "avg_loss": round(avg_loss,2),
        "profit_factor": round(pf,2),
        "n_years": round(n_years,1),
    }

def print_result(res: dict, symbol: str):
    if "error" in res:
        print(f"  오류: {res['error']}")
        return
    lev = res["leverage"]
    print(f"\n{'='*60}")
    print(f"  코인 범위매매 백테스트 — {symbol} / {lev:.0f}배")
    print(f"  RSI 과매도 매수 / 과매수·목표% 매도 (떨사오팔 원리)")
    print(f"{'='*60}")
    print(f"  기간: {res['n_years']}년  |  거래: {res['n_trades']}회")
    print(f"  승률: {res['win_rate']:.1f}%  |  손익비(PF): {res['profit_factor']:.2f}")
    print(f"  평균 수익: ${res['avg_win']:+,.0f}  |  평균 손실: ${res['avg_loss']:+,.0f}")

    rc = res.get("reason_counts", {})
    if rc:
        parts = []
        labels = {"target":"목표달성","rsi":"과매수청산","stop":"손절","timeout":"시간초과"}
        for k, v in rc.items():
            parts.append(f"{labels.get(k,k)} {v}")
        print(f"  청산: {' / '.join(parts)}")

    print(f"\n  [최종 결과]")
    print(f"  최종 자본: ${res['final_cash']:,.0f}  ({res['total_return_pct']:+.1f}%)")
    print(f"  CAGR: {res['cagr_pct']:+.1f}%  |  MDD: -{res['mdd_pct']:.1f}%")

    if res.get("yearly"):
        print(f"\n  [연도별 성과]")
        regime = {"2022":"하락장","2023":"횡보/회복","2024":"상승장",
                  "2025":"변동성","2026":"횡보(현재)"}
        for yr in sorted(res["yearly"].keys()):
            d = res["yearly"][yr]
            mark = "[+]" if d["pnl"] > 0 else "[-]"
            print(f"  {yr}: {d['trades']:>3}회  승률 {d['win_rate']:>5.1f}%  "
                  f"{mark}${d['pnl']:>+9,.0f}   {regime.get(yr,'')}")

    print(f"\n  [평가]")
    if res['profit_factor'] >= 1.3:
        print(f"  [OK] 손익비 양호 (PF {res['profit_factor']:.2f}) — 수익 구조")
    elif res['profit_factor'] >= 1.0:
        print(f"  [~] 손익비 보통 (PF {res['profit_factor']:.2f})")
    else:
        print(f"  [X] 손익비 미달 (PF {res['profit_factor']:.2f})")
    print(f"{'='*60}")

def optimize(ohlcv):
    """범위매매 파라미터 최적화 탐색"""
    print(f"\n  파라미터 최적화 탐색 중...\n")
    best = None
    results = []
    for buy_rsi in [25, 30, 35, 40]:
        for target in [0.02, 0.03, 0.04, 0.05]:
            for stop in [0.02, 0.03, 0.04]:
                CFG["range_buy_rsi"] = buy_rsi
                CFG["range_target_pct"] = target
                CFG["range_stop_pct"] = stop
                res = run_backtest(ohlcv, 1.0)
                if "error" in res or res["n_trades"] < 20:
                    continue
                results.append((buy_rsi, target, stop, res))
                score = res["profit_factor"] * (1 if res["total_return_pct"]>0 else 0.5)
                if best is None or score > best[0]:
                    best = (score, buy_rsi, target, stop, res)
    # 상위 5개 출력
    results.sort(key=lambda x: x[3]["profit_factor"], reverse=True)
    print(f"  {'RSI매수':<8}{'목표%':<8}{'손절%':<8}{'거래':<7}{'승률':<8}{'PF':<7}{'수익률'}")
    for buy_rsi, target, stop, res in results[:8]:
        print(f"  {buy_rsi:<8}{target*100:<8.0f}{stop*100:<8.0f}{res['n_trades']:<7}"
              f"{res['win_rate']:<7.1f}%{res['profit_factor']:<7.2f}{res['total_return_pct']:+.1f}%")
    if best:
        print(f"\n  최적: RSI매수 {best[1]}, 목표 {best[2]*100:.0f}%, 손절 {best[3]*100:.0f}%")
        print(f"        → PF {best[4]['profit_factor']:.2f}, 수익률 {best[4]['total_return_pct']:+.1f}%")
        # 최적값으로 세팅
        CFG["range_buy_rsi"] = best[1]
        CFG["range_target_pct"] = best[2]
        CFG["range_stop_pct"] = best[3]
    return best

if __name__ == "__main__":
    args = sys.argv[1:]
    if "--symbol" in args:
        try: CFG["symbol"] = args[args.index("--symbol")+1]
        except: pass
    if "--leverage" in args:
        try: CFG["leverage"] = float(args[args.index("--leverage")+1])
        except: pass
    if "--years" in args:
        try: CFG["years"] = float(args[args.index("--years")+1])
        except: pass

    do_optimize = "--optimize" in args
    compare = "--compare" in args

    from datetime import timedelta
    since_dt = datetime.now(timezone.utc) - timedelta(days=int(CFG["years"]*365))
    since_date = since_dt.strftime("%Y-%m-%dT00:00:00Z")

    print(f"\n{'='*60}")
    print(f"  코인 범위매매 백테스트 — 데이터 수집 중")
    print(f"  {CFG['symbol']} / {CFG['timeframe']} / 최근 {CFG['years']:.0f}년")
    print(f"{'='*60}")

    if not HAS_CCXT:
        print("\n  ccxt 필요: pip install ccxt")
        sys.exit(1)

    ohlcv = fetch_ohlcv(CFG["symbol"], CFG["timeframe"], since_date)
    if len(ohlcv) < 100:
        print("  데이터 부족"); sys.exit(1)
    print(f"  {len(ohlcv)}개 봉 수집 완료")

    if do_optimize:
        optimize(ohlcv)
        if compare:
            # 최적 파라미터로 레버리지 비교
            print(f"\n  === 최적 파라미터로 레버리지 비교 ===")
            for lev in [1.0, 2.0, 3.0]:
                res = run_backtest(ohlcv, lev)
                print_result(res, CFG["symbol"])
        else:
            print(f"\n  === 최적 파라미터로 최종 백테스트 ===")
            res = run_backtest(ohlcv, CFG["leverage"])
            print_result(res, CFG["symbol"])
    elif compare:
        for lev in [1.0, 2.0, 3.0]:
            res = run_backtest(ohlcv, lev)
            print_result(res, CFG["symbol"])
    else:
        res = run_backtest(ohlcv, CFG["leverage"])
        print_result(res, CFG["symbol"])
