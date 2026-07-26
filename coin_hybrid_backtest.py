"""
코인 하이브리드 데이트레이딩 백테스트.. 상승신호시 돌파전략 나중에 활용하기
========================================
검증된 3요소를 결합한 코인 단타 전략의 백테스트.

[결합한 검증된 전략]
  1) 변동성 돌파 (래리 윌리엄스) — 진입 신호
     당일 시가 + (전일 변동폭 × K) 돌파 시 매수
  2) RSI 필터 — 가짜 돌파 제거
     RSI가 과열(70+)이면 진입 안 함
  3) 카나리아 (추세 필터) — 하락장 방어
     장기 이동평균 위일 때만 매수 (하락장엔 쉼)

[검증된 리스크 관리 — 자료 기반]
  - 1회 거래당 리스크: 계좌의 1% (권장 0.5~1%)
  - 손익비: 1:2 (손절 -1R, 익절 +2R)
  - 일일 손실 한도: 3연속 손절 시 당일 중단
  - 메이저 코인만 (BTC/ETH/SOL — 유동성)

[레버리지 비교]
  1배 / 2배 / 3배를 각각 돌려 실제 위험을 숫자로 확인.
  (감으로 5배 정하지 말고, 백테스트로 검증)

실행 (EC2 등 ccxt 있는 환경):
  python coin_hybrid_backtest.py                  # BTC, 1x
  python coin_hybrid_backtest.py --symbol ETH/USDT
  python coin_hybrid_backtest.py --leverage 3     # 3배 검증
  python coin_hybrid_backtest.py --compare        # 1/2/3배 비교
"""

import sys, time
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional

# ── ccxt는 실행 환경(EC2)에 있음 ──────────────────────
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

    # ── 변동성 돌파 (래리 윌리엄스) ────────────────────
    "k_value"         : 0.5,         # 돌파 계수 (0.5 표준)

    # ── RSI 필터 ───────────────────────────────────────
    "rsi_period"      : 14,
    "rsi_max"         : 70.0,        # RSI 70 이상이면 진입 안 함 (과열)
    "rsi_min"         : 30.0,        # 참고용

    # ── 카나리아 (추세 필터) ───────────────────────────
    "ma_period"       : 50,          # 50봉 이동평균 (장기 추세)
    "use_canary"      : True,        # 하락장 방어 사용

    # ── 검증된 리스크 관리 ─────────────────────────────
    "risk_per_trade"  : 0.01,        # 1회 거래당 계좌의 1% 리스크
    "stop_loss_pct"   : 0.02,        # 가격 -2% 손절 (진입가 대비)
    "risk_reward"     : 2.0,         # 손익비 1:2 (익절 = 손절폭 × 2)
    "daily_loss_limit": 3,           # 3연속 손절 시 당일 중단

    # ── 레버리지 ───────────────────────────────────────
    "leverage"        : 1.0,         # 기본 1배 (--leverage로 변경)

    # ── 비용 ───────────────────────────────────────────
    "fee_pct"         : 0.06,        # Bitget 선물 taker 수수료 약 0.06%
    "slippage_pct"    : 0.02,        # 슬리피지 근사
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

def run_backtest(ohlcv: list, leverage: float = 1.0) -> dict:
    """
    변동성 돌파 + RSI 필터 + 카나리아 백테스트.

    각 봉마다:
      1) 돌파선 = 당일 시가 + (전일 변동폭 × K)
      2) 고가가 돌파선 넘으면 진입 (RSI·카나리아 통과 시)
      3) 손절(-2%) 또는 익절(+4%=2R) 도달 시 청산
      4) 3연속 손절이면 당일 신규진입 중단
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
    trades: list[Trade] = []
    peak = cash
    mdd = 0.0

    consecutive_losses = 0
    last_day = ""

    i = CFG["ma_period"]
    while i < n - 1:
        ts, o, h, l, c, v = ohlcv[i]
        date = datetime.utcfromtimestamp(ts/1000).strftime("%Y-%m-%d %H:%M")
        day = date[:10]

        # 날이 바뀌면 연속손절 카운트 리셋
        if day != last_day:
            consecutive_losses = 0
            last_day = day

        # 일일 손실 한도 도달 시 당일 신규진입 스킵
        if consecutive_losses >= CFG["daily_loss_limit"]:
            equity_curve.append((date, round(cash, 2)))
            i += 1
            continue

        # ── 변동성 돌파 신호 ──────────────────────────
        prev = ohlcv[i-1]
        prev_range = prev[2] - prev[3]   # 전일 고가-저가
        breakout_price = o + prev_range * CFG["k_value"]

        # ── 필터: RSI 과열 + 카나리아(추세) ───────────
        cur_rsi = rsi[i]
        cur_sma = sma[i]
        rsi_ok = (cur_rsi is not None and cur_rsi < CFG["rsi_max"])
        canary_ok = True
        if CFG["use_canary"] and cur_sma is not None:
            canary_ok = c > cur_sma   # 50봉 MA 위 = 상승추세만

        # ── 진입 판정 (고가가 돌파선 도달) ────────────
        if h >= breakout_price and rsi_ok and canary_ok:
            entry = breakout_price
            stop = entry * (1 - CFG["stop_loss_pct"])
            target = entry * (1 + CFG["stop_loss_pct"] * CFG["risk_reward"])

            # 포지션 크기: 계좌의 risk% 를 손절폭으로 나눠 계산
            risk_amount = cash * CFG["risk_per_trade"]
            # 손절 시 잃는 금액이 risk_amount가 되도록
            # (레버리지는 손익을 배수로 키움)
            position_value = (risk_amount / CFG["stop_loss_pct"]) * leverage
            position_value = min(position_value, cash * leverage)  # 최대 한도

            # 진입 이후 봉들에서 손절/익절 도달 확인
            exit_price = None
            exit_date = date
            result = "open"
            for j in range(i, min(i + 30, n)):  # 최대 30봉 내 청산
                _, oj, hj, lj, cj, _ = ohlcv[j]
                ej_date = datetime.utcfromtimestamp(ohlcv[j][0]/1000).strftime("%Y-%m-%d %H:%M")
                # 손절 먼저 체크 (보수적)
                if lj <= stop:
                    exit_price = stop
                    exit_date = ej_date
                    result = "loss"
                    break
                if hj >= target:
                    exit_price = target
                    exit_date = ej_date
                    result = "win"
                    break
            if exit_price is None:  # 시간 초과 청산
                exit_price = closes[min(i+30, n-1)]
                exit_date = datetime.utcfromtimestamp(ohlcv[min(i+30,n-1)][0]/1000).strftime("%Y-%m-%d %H:%M")
                result = "win" if exit_price > entry else "loss"

            # 손익 계산 (레버리지 반영, 수수료 왕복)
            price_change = (exit_price - entry) / entry
            gross_pnl = position_value * price_change
            cost = position_value * fee * 2   # 진입+청산 수수료
            net_pnl = gross_pnl - cost
            cash += net_pnl

            trades.append(Trade(
                entry_date=date, exit_date=exit_date,
                entry_price=entry, exit_price=exit_price,
                result=result, pnl=net_pnl,
                pnl_pct=net_pnl/CFG["starting_cash"]*100,
            ))

            if result == "loss":
                consecutive_losses += 1
            else:
                consecutive_losses = 0

            # 청산된 봉으로 점프
            exit_i = i
            for j in range(i, min(i+30, n)):
                ej = datetime.utcfromtimestamp(ohlcv[j][0]/1000).strftime("%Y-%m-%d %H:%M")
                if ej == exit_date:
                    exit_i = j
                    break
            i = max(exit_i, i) + 1
        else:
            i += 1

        equity_curve.append((date, round(cash, 2)))
        if cash > peak:
            peak = cash
        dd = (peak - cash) / peak * 100 if peak > 0 else 0
        mdd = max(mdd, dd)

        if cash <= 0:  # 파산
            print("  ⚠️ 계좌 소진 (파산)")
            break

    wins = [t for t in trades if t.result == "win"]
    losses = [t for t in trades if t.result == "loss"]
    win_rate = len(wins) / len(trades) * 100 if trades else 0
    total_return = (cash / CFG["starting_cash"] - 1) * 100

    avg_win = sum(t.pnl for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t.pnl for t in losses) / len(losses) if losses else 0
    profit_factor = abs(sum(t.pnl for t in wins) / sum(t.pnl for t in losses)) \
                    if losses and sum(t.pnl for t in losses) != 0 else 0

    n_days = (ohlcv[-1][0] - ohlcv[0][0]) / (1000*86400) if len(ohlcv) > 1 else 1
    n_years = n_days / 365
    cagr = ((cash/CFG["starting_cash"]) ** (1/n_years) - 1) * 100 if n_years > 0 and cash > 0 else 0

    # ── 연도별 성과 분석 (국면별 진단용) ──────────────
    #   횡보장(2022~2023)과 상승장(2024)의 성과를 분리해서 본다.
    yearly = {}
    for t in trades:
        yr = t.entry_date[:4]
        if yr not in yearly:
            yearly[yr] = {"trades": 0, "wins": 0, "pnl": 0.0}
        yearly[yr]["trades"] += 1
        if t.result == "win":
            yearly[yr]["wins"] += 1
        yearly[yr]["pnl"] += t.pnl
    yearly_summary = {}
    for yr, d in yearly.items():
        yearly_summary[yr] = {
            "trades": d["trades"],
            "win_rate": round(d["wins"]/d["trades"]*100, 1) if d["trades"] else 0,
            "pnl": round(d["pnl"], 2),
        }

    return {
        "leverage": leverage,
        "final_cash": round(cash, 2),
        "total_return_pct": round(total_return, 2),
        "cagr_pct": round(cagr, 2),
        "mdd_pct": round(mdd, 2),
        "n_trades": len(trades),
        "win_rate": round(win_rate, 1),
        "yearly": yearly_summary,
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "profit_factor": round(profit_factor, 2),
        "n_years": round(n_years, 1),
    }

# ══════════════════════════════════════════════════════
#  출력
# ══════════════════════════════════════════════════════
def print_result(res: dict, symbol: str):
    if "error" in res:
        print(f"  ❌ {res['error']}")
        return
    lev = res["leverage"]
    print(f"\n{'='*60}")
    print(f"  📊 코인 하이브리드 백테스트 — {symbol} / {lev:.0f}배")
    print(f"  변동성돌파 + RSI필터 + 카나리아 | 손익비 1:{CFG['risk_reward']:.0f}")
    print(f"{'='*60}")
    print(f"  기간: {res['n_years']}년  |  거래: {res['n_trades']}회")
    print(f"  승률: {res['win_rate']:.1f}%  |  손익비(PF): {res['profit_factor']:.2f}")
    print(f"  평균 수익: ${res['avg_win']:+,.0f}  |  평균 손실: ${res['avg_loss']:+,.0f}")
    print(f"\n  [최종 결과]")
    print(f"  최종 자본: ${res['final_cash']:,.0f}  ({res['total_return_pct']:+.1f}%)")
    print(f"  📈 CAGR: {res['cagr_pct']:+.1f}%")
    print(f"  📉 MDD: -{res['mdd_pct']:.1f}%")

    # ── 연도별 성과 (국면 진단) ────────────────────────
    if res.get("yearly"):
        print(f"\n  [연도별 성과 — 국면 진단]")
        print(f"  {'연도':<8}{'거래':<8}{'승률':<10}{'손익':<15}{'국면'}")
        # 대략적 국면 라벨 (참고용)
        regime = {
            "2022": "하락장", "2023": "횡보/회복",
            "2024": "상승장", "2025": "변동성", "2026": "횡보(현재)",
        }
        for yr in sorted(res["yearly"].keys()):
            d = res["yearly"][yr]
            mark = "✅" if d["pnl"] > 0 else "🔴"
            print(f"  {yr:<8}{d['trades']:<8}{d['win_rate']:<9.1f}%"
                  f"  {mark}${d['pnl']:>+10,.0f}   {regime.get(yr, '')}")
        print(f"\n  💡 횡보장 연도의 손익이 마이너스라면,")
        print(f"     현재 횡보 국면에는 이 전략이 안 맞는다는 증거입니다.")

    # 검증된 원칙 대비 평가
    print(f"\n  [리스크 평가]")
    if res['mdd_pct'] <= 20:
        print(f"  ✅ MDD 양호 (-{res['mdd_pct']:.1f}%)")
    elif res['mdd_pct'] <= 40:
        print(f"  🟡 MDD 주의 (-{res['mdd_pct']:.1f}%)")
    else:
        print(f"  🔴 MDD 위험 (-{res['mdd_pct']:.1f}%) — 레버리지 축소 권고")
    print(f"{'='*60}")

# ══════════════════════════════════════════════════════
#  실행
# ══════════════════════════════════════════════════════
if __name__ == "__main__":
    args = sys.argv[1:]
    if "--symbol" in args:
        try: CFG["symbol"] = args[args.index("--symbol")+1]
        except: pass
    if "--leverage" in args:
        try: CFG["leverage"] = float(args[args.index("--leverage")+1])
        except: pass
    if "--timeframe" in args:
        try: CFG["timeframe"] = args[args.index("--timeframe")+1]
        except: pass
    if "--years" in args:
        try: CFG["years"] = float(args[args.index("--years")+1])
        except: pass

    compare = "--compare" in args

    # years → since_date 변환 (ccxt는 시작 날짜 문자열이 필요)
    from datetime import timedelta
    since_dt = datetime.utcnow() - timedelta(days=int(CFG["years"] * 365))
    since_date = since_dt.strftime("%Y-%m-%dT00:00:00Z")

    print(f"\n{'='*60}")
    print(f"  📡 코인 하이브리드 백테스트 데이터 수집 중...")
    print(f"  {CFG['symbol']} / {CFG['timeframe']} / 최근 {CFG['years']:.0f}년 ({since_date[:10]}~)")
    print(f"{'='*60}")

    if not HAS_CCXT:
        print("\n  ❌ ccxt가 없습니다. EC2에서 실행하세요:")
        print("     pip install ccxt")
        print("     python coin_hybrid_backtest.py")
        sys.exit(1)

    ohlcv = fetch_ohlcv(CFG["symbol"], CFG["timeframe"], since_date)
    if len(ohlcv) < 100:
        print("  ❌ 데이터 부족")
        sys.exit(1)
    print(f"  ✅ {len(ohlcv)}개 봉 수집 완료")

    if compare:
        # 레버리지 1/2/3배 비교
        print(f"\n  🔬 레버리지 비교 (1배 / 2배 / 3배)")
        results = []
        for lev in [1.0, 2.0, 3.0]:
            res = run_backtest(ohlcv, leverage=lev)
            results.append(res)
            print_result(res, CFG["symbol"])
        # 요약 비교표
        print(f"\n  {'='*60}")
        print(f"  📋 레버리지별 요약")
        print(f"  {'배수':<6}{'CAGR':<12}{'MDD':<12}{'승률':<10}")
        for res in results:
            if "error" not in res:
                print(f"  {res['leverage']:.0f}배   {res['cagr_pct']:+.1f}%      "
                      f"-{res['mdd_pct']:.1f}%      {res['win_rate']:.1f}%")
        print(f"  {'='*60}")
        print(f"  💡 CAGR은 높지만 MDD가 급증하면 위험. 균형점을 고르세요.")
    else:
        res = run_backtest(ohlcv, leverage=CFG["leverage"])
        print_result(res, CFG["symbol"])
