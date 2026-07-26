"""
박스 전략 (Box Strategy) 백테스트 — 스윙 하이/로우 구조 매매
================================================================
"The Rumers" 채널의 Box Strategy를 코인 시장용 백테스트로 구현.

[전략 원리 — 원문 요약]
  "가격은 이전 스윙 하이와 스윙 로우 사이에서 반응한다"
  1) 상위 타임프레임(1시간봉)으로 박스(지도)를 그린다
  2) 박스 하단 = 매수만 / 중앙 = 관망(Do Nothing) / 상단 = 목표가
  3) 하위 타임프레임(15분봉)에서 '확정 신호'를 기다린다:
     - (트랩) 박스 하단을 살짝 이탈해 손절 사냥 후 반등하면 가산점
     - 반전 양봉 출현
     - 다음 캔들이 그 양봉의 고점을 돌파하는 순간 진입
  4) 손절 = 신호 캔들 저점 아래 / 목표 = 박스 상단
  5) +1R 도달 시 손절을 진입가로 이동(브레이크이븐)

[코인 적용 단순화]
  - 롱 전용 (원문도 상승편향 시장에서 롱 중심을 권장)
  - 박스 상단 숏은 코인 변동성에서 위험해 제외
  - 손익비 필터: 목표/손절 비율이 min_rr 미만이면 진입 포기

[데이터]
  바이낸스 15분봉 (1시간봉은 리샘플링으로 생성 — 데이터 일관성 보장)

실행:
  python coin_box_backtest.py                  # BTC, 2년, 1배
  python coin_box_backtest.py --years 1
  python coin_box_backtest.py --symbol ETH/USDT
  python coin_box_backtest.py --compare        # 레버리지 1/2/3배
  python coin_box_backtest.py --optimize       # 파라미터 탐색
"""

import sys, time
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
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
    "symbol"         : "BTC/USDT",
    "years"          : 2.0,        # 15분봉이라 기본 2년 (데이터량 고려)
    "starting_cash"  : 10_000.0,

    # ── 다중 코인 테스트 (--multi) — 대형 코인 위주 ────
    "multi_symbols"  : ["BTC/USDT", "ETH/USDT", "SOL/USDT",
                        "XRP/USDT", "BNB/USDT", "DOGE/USDT", "ADA/USDT"],

    # ── 박스 설정 (1시간봉 = 15분봉 4개 리샘플) ────────
    "swing_lookback" : 5,     # 스윙 하이/로우 판정: 좌우 5봉보다 높/낮아야
    "box_min_pct"    : 1.5,   # 박스 높이 최소 1.5% (너무 좁은 박스 제외)
    "box_max_pct"    : 25.0,  # 박스 높이 최대 25% (너무 넓은 박스 제외)

    # ── 구역 정의 ──────────────────────────────────────
    "buy_zone_pct"   : 25.0,  # 박스 하단 25% 구간에서만 매수 탐색
                              # (중앙 50%는 관망 = 원문의 Do Nothing)

    # ── 15분봉 확정 신호 ───────────────────────────────
    "trap_bonus"     : True,  # 박스 하단 이탈 후 반등(트랩)이면 신호 강화
    "trap_pct"       : 0.3,   # 하단 -0.3% 이탈까지를 트랩으로 인정
    "confirm_bars"   : 6,     # 반전 양봉 후 몇 봉 안에 고점 돌파해야 하는지

    # ── 리스크 관리 ────────────────────────────────────
    "sl_buffer_pct"  : 0.2,   # 신호 캔들 저점 아래 0.2% 버퍼
    "min_rr"         : 1.5,   # 최소 손익비 (목표거리/손절거리) — 미달 시 포기
    "breakeven_at_r" : 1.0,   # +1R 도달 시 손절을 진입가로 이동
    "max_hold_bars"  : 96,    # 최대 보유 96봉(15분×96=24시간) — 시간청산
    "risk_per_trade" : 0.01,  # 1회 리스크 계좌의 1%
    "daily_loss_limit": 3,    # 3연속 손절 시 당일 중단

    # ── 레버리지/비용 ──────────────────────────────────
    "leverage"       : 1.0,
    "fee_pct"        : 0.06,
    "slippage_pct"   : 0.02,
}

# ══════════════════════════════════════════════════════
#  데이터 수집 (바이낸스 15분봉)
# ══════════════════════════════════════════════════════
def fetch_ohlcv(symbol: str, since_date: str) -> list:
    """바이낸스 15분봉 페이징 수집"""
    if not HAS_CCXT:
        print("  ccxt 필요: pip install ccxt")
        return []
    ex = ccxt.binance()
    spot = symbol.split(":")[0]
    out, since, limit = [], ex.parse8601(since_date), 1000
    for _ in range(120):   # 15분봉 2년 ≈ 7만 개 → 70페이지 여유
        try:
            batch = ex.fetch_ohlcv(spot, "15m", since=since, limit=limit)
        except Exception as e:
            print(f"  수집 오류: {e}")
            break
        if not batch:
            break
        out += batch
        since = batch[-1][0] + 1
        if len(batch) < limit:
            break
        time.sleep(0.25)
    return out

def resample_1h(m15: list) -> list:
    """15분봉 4개 → 1시간봉 1개로 리샘플 (같은 시간대 정렬)"""
    h1 = []
    bucket = []
    for c in m15:
        ts = c[0]
        # 정시(1시간 경계) 기준으로 묶음
        hour_start = ts - (ts % 3_600_000)
        if bucket and bucket[0][0] - (bucket[0][0] % 3_600_000) != hour_start:
            o = bucket[0][1]
            h = max(x[2] for x in bucket)
            l = min(x[3] for x in bucket)
            cl = bucket[-1][4]
            v = sum(x[5] for x in bucket)
            h1.append([bucket[0][0] - (bucket[0][0] % 3_600_000), o, h, l, cl, v])
            bucket = []
        bucket.append(c)
    if bucket:
        o = bucket[0][1]
        h = max(x[2] for x in bucket)
        l = min(x[3] for x in bucket)
        cl = bucket[-1][4]
        v = sum(x[5] for x in bucket)
        h1.append([bucket[0][0] - (bucket[0][0] % 3_600_000), o, h, l, cl, v])
    return h1

# ══════════════════════════════════════════════════════
#  스윙 하이/로우 감지 → 박스 시계열 생성
# ══════════════════════════════════════════════════════
def build_box_series(h1: list) -> list:
    """
    각 1시간봉 시점의 '현재 유효한 박스'를 계산한다.
    스윙 하이 = 좌우 lookback봉의 고가보다 높은 봉의 고가
    스윙 로우 = 좌우 lookback봉의 저가보다 낮은 봉의 저가
    확정은 우측 lookback봉이 지나야 가능 (미래 미사용).

    반환: [(ts, box_low, box_high) or (ts, None, None), ...]
    """
    lb = CFG["swing_lookback"]
    n = len(h1)
    highs = [c[2] for c in h1]
    lows  = [c[3] for c in h1]

    # 스윙 확정 시점 기록: i번째 봉이 스윙이면, i+lb 시점에 확정
    swing_high_at = {}   # 확정시점 idx -> 가격
    swing_low_at  = {}
    for i in range(lb, n - lb):
        if highs[i] == max(highs[i-lb:i+lb+1]):
            swing_high_at[i + lb] = highs[i]
        if lows[i] == min(lows[i-lb:i+lb+1]):
            swing_low_at[i + lb] = lows[i]

    series = []
    high_history: list = []   # 확정된 스윙하이 누적
    low_history: list = []    # 확정된 스윙로우 누적
    for i in range(n):
        if i in swing_high_at:
            high_history.append(swing_high_at[i])
        if i in swing_low_at:
            low_history.append(swing_low_at[i])
        box = (None, None)
        if low_history and high_history:
            cur_low = low_history[-1]
            # 현재 로우보다 높은 가장 최근 스윙하이 (상승 중 박스 소실 방지)
            cur_high = None
            for hv in reversed(high_history):
                if hv > cur_low:
                    cur_high = hv
                    break
            if cur_high:
                height_pct = (cur_high - cur_low) / cur_low * 100
                if CFG["box_min_pct"] <= height_pct <= CFG["box_max_pct"]:
                    box = (cur_low, cur_high)
        series.append((h1[i][0], box[0], box[1]))
    return series

# ══════════════════════════════════════════════════════
#  백테스트 엔진
# ══════════════════════════════════════════════════════
@dataclass
class Trade:
    entry_date: str = ""
    exit_date: str = ""
    entry: float = 0.0
    exit: float = 0.0
    result: str = ""
    pnl: float = 0.0
    exit_reason: str = ""   # target/stop/breakeven/timeout
    trapped: bool = False   # 트랩 패턴이었는지

def run_backtest(m15: list, leverage: float = 1.0) -> dict:
    fee = (CFG["fee_pct"] + CFG["slippage_pct"]) / 100
    n = len(m15)
    if n < 500:
        return {"error": "데이터 부족"}

    # 1시간봉 리샘플 + 박스 시계열
    h1 = resample_1h(m15)
    box_series = build_box_series(h1)
    # 15분봉 ts → 해당 시각 박스 빠른 조회 (1시간 경계 매핑)
    box_map = {}
    for ts, bl, bh in box_series:
        box_map[ts] = (bl, bh)

    def box_at(ts15):
        hour = ts15 - (ts15 % 3_600_000)
        return box_map.get(hour, (None, None))

    cash = CFG["starting_cash"]
    trades: list = []
    peak, mdd = cash, 0.0
    consecutive_losses = 0
    last_day = ""

    i = 400   # 워밍업 (박스 형성 대기)
    while i < n - CFG["confirm_bars"] - 2:
        ts, o, h, l, c, v = m15[i]
        date = datetime.fromtimestamp(ts/1000, timezone.utc).strftime("%Y-%m-%d %H:%M")
        day = date[:10]
        if day != last_day:
            consecutive_losses = 0
            last_day = day
        if consecutive_losses >= CFG["daily_loss_limit"]:
            i += 1
            continue

        box_low, box_high = box_at(ts)
        if not box_low:
            i += 1
            continue

        # ── 매수존 판정: 박스 하단 buy_zone% 안에 있는가 ──
        box_h = box_high - box_low
        buy_zone_top = box_low + box_h * CFG["buy_zone_pct"] / 100
        in_buy_zone = l <= buy_zone_top   # 저가가 매수존에 닿음

        if not in_buy_zone:
            i += 1
            continue

        # ── 확정 1: 반전 양봉인가 ──
        is_bull = c > o
        if not is_bull:
            i += 1
            continue

        # ── 트랩 보너스: 박스 하단을 살짝 이탈했다 회복했나 ──
        trapped = (l < box_low and l >= box_low * (1 - CFG["trap_pct"]/100)
                   and c > box_low) if CFG["trap_bonus"] else False

        # ── 확정 2: 이후 confirm_bars 안에 이 양봉 고점 돌파 ──
        signal_high = h
        signal_low = l
        entry_price = None
        entry_i = None
        for j in range(i+1, min(i+1+CFG["confirm_bars"], n)):
            if m15[j][2] >= signal_high:   # 고가가 신호봉 고점 돌파
                entry_price = signal_high  # 돌파 지점에서 체결 가정
                entry_i = j
                break
            if m15[j][3] < signal_low:     # 돌파 전에 신호봉 저점 이탈 → 무효
                break
        if entry_price is None:
            i += 1
            continue

        # ── 손절/목표/손익비 필터 ──
        sl = signal_low * (1 - CFG["sl_buffer_pct"]/100)
        target = box_high
        risk_dist = entry_price - sl
        reward_dist = target - entry_price
        if risk_dist <= 0 or reward_dist / risk_dist < CFG["min_rr"]:
            i = entry_i + 1
            continue

        # ── 포지션 크기 (1% 리스크) ──
        risk_amount = cash * CFG["risk_per_trade"]
        stop_pct = risk_dist / entry_price
        position_value = min((risk_amount / stop_pct) * leverage, cash * leverage)

        # ── 청산 시뮬레이션 (브레이크이븐 포함) ──
        be_price = entry_price + risk_dist * CFG["breakeven_at_r"]
        cur_sl = sl
        be_moved = False
        exit_price = None
        exit_reason = ""
        exit_i = entry_i
        for j in range(entry_i, min(entry_i + CFG["max_hold_bars"], n)):
            _, oj, hj, lj, cj, _ = m15[j]
            # 손절 먼저 (보수적)
            if lj <= cur_sl:
                exit_price = cur_sl
                exit_reason = "breakeven" if be_moved else "stop"
                exit_i = j
                break
            if hj >= target:
                exit_price = target
                exit_reason = "target"
                exit_i = j
                break
            # 브레이크이븐 이동
            if not be_moved and hj >= be_price:
                cur_sl = entry_price
                be_moved = True
        if exit_price is None:
            exit_i = min(entry_i + CFG["max_hold_bars"], n-1)
            exit_price = m15[exit_i][4]
            exit_reason = "timeout"

        # ── 손익 반영 ──
        change = (exit_price - entry_price) / entry_price
        net = position_value * change - position_value * fee * 2
        cash += net
        result = "win" if net > 0 else "loss"
        exit_date = datetime.fromtimestamp(m15[exit_i][0]/1000, timezone.utc).strftime("%Y-%m-%d %H:%M")
        trades.append(Trade(entry_date=date, exit_date=exit_date,
                            entry=entry_price, exit=exit_price,
                            result=result, pnl=net,
                            exit_reason=exit_reason, trapped=trapped))
        consecutive_losses = consecutive_losses + 1 if result == "loss" else 0

        if cash > peak:
            peak = cash
        dd = (peak - cash) / peak * 100 if peak > 0 else 0
        mdd = max(mdd, dd)
        if cash <= 0:
            print("  계좌 소진")
            break

        i = exit_i + 1

    # ── 통계 ──
    wins = [t for t in trades if t.result == "win"]
    losses = [t for t in trades if t.result == "loss"]
    win_rate = len(wins)/len(trades)*100 if trades else 0
    total_return = (cash/CFG["starting_cash"]-1)*100
    pf = abs(sum(t.pnl for t in wins)/sum(t.pnl for t in losses)) \
         if losses and sum(t.pnl for t in losses) != 0 else 0

    reason_counts = {}
    for t in trades:
        reason_counts[t.exit_reason] = reason_counts.get(t.exit_reason, 0) + 1
    trap_trades = [t for t in trades if t.trapped]
    trap_wins = [t for t in trap_trades if t.result == "win"]

    yearly = {}
    for t in trades:
        yr = t.entry_date[:4]
        if yr not in yearly:
            yearly[yr] = {"trades": 0, "wins": 0, "pnl": 0.0}
        yearly[yr]["trades"] += 1
        if t.result == "win":
            yearly[yr]["wins"] += 1
        yearly[yr]["pnl"] += t.pnl
    yearly_summary = {yr: {"trades": d["trades"],
                           "win_rate": round(d["wins"]/d["trades"]*100,1) if d["trades"] else 0,
                           "pnl": round(d["pnl"],2)} for yr, d in yearly.items()}

    n_days = (m15[-1][0]-m15[0][0])/(1000*86400) if len(m15) > 1 else 1
    n_years = n_days/365
    cagr = ((cash/CFG["starting_cash"])**(1/n_years)-1)*100 if n_years > 0 and cash > 0 else 0

    return {
        "leverage": leverage,
        "final_cash": round(cash,2),
        "total_return_pct": round(total_return,2),
        "cagr_pct": round(cagr,2),
        "mdd_pct": round(mdd,2),
        "n_trades": len(trades),
        "win_rate": round(win_rate,1),
        "profit_factor": round(pf,2),
        "yearly": yearly_summary,
        "reason_counts": reason_counts,
        "trap_stats": {"n": len(trap_trades),
                       "win_rate": round(len(trap_wins)/len(trap_trades)*100,1) if trap_trades else 0},
        "n_years": round(n_years,1),
    }

# ══════════════════════════════════════════════════════
#  출력
# ══════════════════════════════════════════════════════
def print_result(res: dict, symbol: str):
    if "error" in res:
        print(f"  오류: {res['error']}")
        return
    print(f"\n{'='*60}")
    print(f"  박스 전략 백테스트 — {symbol} / {res['leverage']:.0f}배")
    print(f"  1시간봉 박스(스윙H/L) + 15분봉 확정 진입 (롱 전용)")
    print(f"{'='*60}")
    print(f"  기간: {res['n_years']}년  |  거래: {res['n_trades']}회")
    print(f"  승률: {res['win_rate']:.1f}%  |  손익비(PF): {res['profit_factor']:.2f}")

    rc = res.get("reason_counts", {})
    labels = {"target":"목표달성","stop":"손절","breakeven":"본전청산","timeout":"시간청산"}
    parts = [f"{labels.get(k,k)} {v}" for k, v in rc.items()]
    if parts:
        print(f"  청산: {' / '.join(parts)}")

    tr = res.get("trap_stats", {})
    if tr.get("n"):
        print(f"  트랩 패턴: {tr['n']}회 (승률 {tr['win_rate']:.1f}%) — 원문의 '손절사냥 후 반등'")

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

def optimize(m15):
    """핵심 파라미터 탐색: 매수존 폭 / 최소 손익비 / 스윙 lookback"""
    print(f"\n  파라미터 최적화 탐색 중...\n")
    results = []
    for zone in [20.0, 25.0, 33.0]:
        for min_rr in [1.2, 1.5, 2.0]:
            for lb in [4, 5, 7]:
                CFG["buy_zone_pct"] = zone
                CFG["min_rr"] = min_rr
                CFG["swing_lookback"] = lb
                res = run_backtest(m15, 1.0)
                if "error" in res or res["n_trades"] < 15:
                    continue
                results.append((zone, min_rr, lb, res))
    results.sort(key=lambda x: x[3]["profit_factor"], reverse=True)
    print(f"  {'매수존%':<8}{'최소RR':<8}{'스윙LB':<8}{'거래':<7}{'승률':<9}{'PF':<7}{'수익률'}")
    for zone, rr, lb, res in results[:8]:
        print(f"  {zone:<8.0f}{rr:<8.1f}{lb:<8}{res['n_trades']:<7}"
              f"{res['win_rate']:<8.1f}%{res['profit_factor']:<7.2f}{res['total_return_pct']:+.1f}%")
    if results:
        best = results[0]
        print(f"\n  최적: 매수존 {best[0]:.0f}%, 최소RR {best[1]}, 스윙 {best[2]}봉")
        CFG["buy_zone_pct"], CFG["min_rr"], CFG["swing_lookback"] = best[0], best[1], best[2]
    return results

# ══════════════════════════════════════════════════════
#  실행
# ══════════════════════════════════════════════════════
def run_multi(years: float, do_optimize: bool):
    """여러 대형 코인을 순차 백테스트하고 비교표 출력"""
    since_dt = datetime.now(timezone.utc) - timedelta(days=int(years*365))
    since_date = since_dt.strftime("%Y-%m-%dT00:00:00Z")
    summary = []

    for sym in CFG["multi_symbols"]:
        print(f"\n{'='*60}")
        print(f"  [{sym}] 데이터 수집 중... (15분봉 {years:.0f}년)")
        m15 = fetch_ohlcv(sym, since_date)
        if len(m15) < 500:
            print(f"  {sym}: 데이터 부족 — 건너뜀")
            continue
        print(f"  {len(m15)}개 봉 수집")

        best_params = None
        if do_optimize:
            # 코인별 최적 파라미터 탐색 (조용히)
            results = []
            for zone in [20.0, 25.0, 33.0]:
                for min_rr in [1.2, 1.5, 2.0]:
                    CFG["buy_zone_pct"] = zone
                    CFG["min_rr"] = min_rr
                    r = run_backtest(m15, 1.0)
                    if "error" in r or r["n_trades"] < 15:
                        continue
                    results.append((zone, min_rr, r))
            if results:
                results.sort(key=lambda x: x[2]["profit_factor"], reverse=True)
                zone, min_rr, _ = results[0]
                CFG["buy_zone_pct"], CFG["min_rr"] = zone, min_rr
                best_params = f"존{zone:.0f}%/RR{min_rr}"

        res = run_backtest(m15, CFG["leverage"])
        if "error" in res:
            print(f"  {sym}: {res['error']}")
            continue
        print_result(res, sym)
        summary.append((sym, res, best_params))

        # 파라미터 원복 (다음 코인은 다시 탐색 또는 기본값)
        CFG["buy_zone_pct"], CFG["min_rr"] = 25.0, 1.5

    # ── 요약 비교표 ──
    if summary:
        print(f"\n{'='*60}")
        print(f"  코인별 박스전략 비교 요약 ({years:.0f}년, {CFG['leverage']:.0f}배)")
        print(f"{'='*60}")
        header = f"  {'코인':<12}{'거래':<7}{'승률':<9}{'PF':<7}{'MDD':<9}{'수익률':<10}"
        if do_optimize:
            header += "최적파라미터"
        print(header)
        for sym, res, bp in sorted(summary, key=lambda x: x[1]["profit_factor"], reverse=True):
            label = sym.replace("/USDT", "")
            line = (f"  {label:<12}{res['n_trades']:<7}{res['win_rate']:<8.1f}%"
                    f"{res['profit_factor']:<7.2f}-{res['mdd_pct']:<8.1f}%"
                    f"{res['total_return_pct']:+.1f}%")
            if do_optimize and bp:
                line += f"   {bp}"
            print(line)
        print(f"{'='*60}")
        print(f"  PF 1.3 이상인 코인이 이 전략과 잘 맞는 코인입니다.")

if __name__ == "__main__":
    args = sys.argv[1:]
    if "--symbol" in args:
        try: CFG["symbol"] = args[args.index("--symbol")+1]
        except: pass
    if "--years" in args:
        try: CFG["years"] = float(args[args.index("--years")+1])
        except: pass
    if "--leverage" in args:
        try: CFG["leverage"] = float(args[args.index("--leverage")+1])
        except: pass
    do_optimize = "--optimize" in args
    compare = "--compare" in args
    multi = "--multi" in args

    if not HAS_CCXT:
        print("\n  ccxt 필요: pip install ccxt")
        sys.exit(1)

    if multi:
        # 여러 대형 코인 일괄 테스트
        run_multi(CFG["years"], do_optimize)
        sys.exit(0)

    since_dt = datetime.now(timezone.utc) - timedelta(days=int(CFG["years"]*365))
    since_date = since_dt.strftime("%Y-%m-%dT00:00:00Z")

    print(f"\n{'='*60}")
    print(f"  박스 전략 백테스트 — 데이터 수집 중")
    print(f"  {CFG['symbol']} / 15분봉 / 최근 {CFG['years']:.0f}년")
    print(f"{'='*60}")

    m15 = fetch_ohlcv(CFG["symbol"], since_date)
    if len(m15) < 500:
        print("  데이터 부족")
        sys.exit(1)
    print(f"  {len(m15)}개 15분봉 수집 완료")

    if do_optimize:
        optimize(m15)
        if compare:
            print(f"\n  === 최적 파라미터로 레버리지 비교 ===")
            for lev in [1.0, 2.0, 3.0]:
                print_result(run_backtest(m15, lev), CFG["symbol"])
        else:
            print(f"\n  === 최적 파라미터로 최종 백테스트 ===")
            print_result(run_backtest(m15, CFG["leverage"]), CFG["symbol"])
    elif compare:
        for lev in [1.0, 2.0, 3.0]:
            print_result(run_backtest(m15, lev), CFG["symbol"])
    else:
        print_result(run_backtest(m15, CFG["leverage"]), CFG["symbol"])
