"""
코인 그리드매매 '숏' 범용 봇 — coin_grid_bot.py(롱)의 거울상 전략 (Bitget USDT-M 선물, 통합계정/UTA, 헤지 모드) (2026-10-25)
=================================================================================
롱 봇은 '떨어지면 사고 오르면 판다'. 이 봇은 반대로 **'오르면 숏을 열고 떨어지면 되산다(환매)'**. 하락 추세·급등 후 하락(펌프&덤프)에 맞춘다.
숏은 가격이 오르면 손실에 상한이 없어서, 롱 봇에 없는 **강제 손절(short_stop_pct)**을 기본으로 둔다.

[실행]  (설정 키는 롱 봇과 같은 이름을 쓰되 방향이 거울상이다)
  python coin_grid_short_bot.py --symbol ABC --check              # 점검만(주문·상태저장 없음)
  python coin_grid_short_bot.py --symbol ABC --live               # 실전(cron에는 --live)
  python coin_grid_short_bot.py --symbol ABC --status | --resume | --reset
  python coin_grid_short_bot.py --symbol ABC --set max_total_usdt=100 --set short_stop_pct=8
  cron 예시: * * * * * cd /home/ec2-user/trading-bot && flock -n /tmp/coin_short_abc.lock venv/bin/python coin_grid_short_bot.py --symbol ABC --live >> logs/coin_short_abc.log 2>&1
  상태 파일은 coin_grid_short_<abc>_state.json (롱 봇 상태와 별개). 거래소 헤지 모드라 같은 종목의 롱 봇·수동 롱과 숏은 서로 다른 포지션이다.

[전략 구성 — 롱 봇의 거울상]
  1) 숏 그리드: 기준가 대비 +1%(step_pct)마다 20USDT(order_usdt) 숏 진입, -2%(sell_step_pct)마다 30USDT(sell_order_usdt)어치 환매(reduce-only 매수).
     체결 가격이 새 기준가. 총 숏 누적은 max_total_usdt(200)까지. 봇이 직접 연 숏 수량(state의 qty)만 환매한다. 봇 숏이 0이면 기준가를 가격 하락에 따라 내린다.
  2) 급락 모드(롱 봇의 급등 모드): 1분 고점 대비 -3%(surge_pct, surge_window_min) 하락이면 30USDT(surge_chase_usdt) 추격 숏. 그리드를 멈추고 숏을 들고 간다.
     급락 저점 대비 +3/+4.5/+6%(surge_ladder) 반등마다 1/3씩 환매(각 단계 3번 연속 점검, 한 번에 한 단계).
  3) 급등 대응(롱 봇의 급락 대응): 5분 안에 +8%(crash_pct) 급등하면 신규 숏만 중단. 고점 대비 -2% 되돌리거나 30분간 새 고점 없으면 재개.
  4) 신규 숏 중단(롱 봇의 신규 매수 중단): 저점 대비 +15%(trail_exit_pct) 위면 신규 숏 중단, 보유 숏은 되돌림 때만 환매. 숏이 모두 환매되면 자동 재개.
  5) 강제 손절: 평균 진입가 대비 +10%(short_stop_pct)까지 오르면 숏 전량 환매 후 정지(--resume 로 재개). 0으로 두면 끈다(권하지 않음).
  6) 안전장치/알림: 거래소에 닫을 숏이 없으면(25227) 장부를 0으로 정정, 알림에 누적 수익(실현/평가/합계, 수수료 반영, 평균단가 방식).

[어떤 종목에 맞나 — 2026-10-25 모의 참고]  하락 추세·급등 후 하락에 맞고, 계속 오르는 상승 추세·펌프 초입에는 안 맞는다(손실 상한은 강제 손절로만 막는다).
  coin_grid_backtest.py --side short 로 먼저 모의하고, 펀딩비(숏은 펀딩이 양수일 때 받고 음수일 때 낸다)와 호가 깊이를 확인한다. 수동 숏은 봇과 분리한다(등록·청산하지 않는다).
"""
import sys, os, json, time
from datetime import datetime, timezone
import coin_grid_bot as g

CFG = g.CFG
CFG.setdefault("short_stop_pct", 10.0)
CFG.setdefault("rally_fast_pct", 3.0)        # 급등 보호(빠른 쪽): rally_fast_win_min분 안에 이 %만큼 오르면 신규 숏 중단 (0=끔)
CFG.setdefault("rally_fast_win_min", 1)
CFG.setdefault("entry_spacing", False)       # True면 추가 숏은 직전 진입가에서 step_pct% 더 올랐을 때만 (평균가 기준에서 매분 연속 진입되는 것 방지, 2026-10-26)
CFG.setdefault("ref_mode", "sma")           # 기준가 방식: "sma"=직전 ref_sma_min분 평균가(매분 갱신, 2026-10-26부터 숏 봇 기본전략), "last"=마지막 체결가(이전 방식)
CFG.setdefault("ref_sma_min", 5)             # sma 모드의 평균 구간(분, 최대 14 — 시세 기록이 15분만 남음)
CFG.setdefault("trend_entry", False)             # 추세 숏 진입(2026-10-27): 짧은 이동평균이 긴 이동평균을 아래로 뚫는 데드크로스(+느린 선 하락)가 나오면 숏 te_usdt를 연다. 하락 중에는 기준가가 따라 내려와 그리드 숏 진입이 안 나오는 문제를 보완. 환매·손절은 기존 숏 그리드 규칙 그대로
CFG.setdefault("te_bar_min", 5)                 # 신호용 봉(분)
CFG.setdefault("te_fast", 3)                    # 빠른 이동평균 봉 수
CFG.setdefault("te_slow", 12)                   # 느린 이동평균 봉 수
CFG.setdefault("te_confirm", 0)                 # 교차 후 같은 쪽에 더 머물러야 하는 봉 수(0=교차 직후)
CFG.setdefault("te_slope_bars", 3)              # 느린 선 기울기를 재는 봉 수
CFG.setdefault("te_usdt", 50.0)                 # 추세 진입 1회 크기(명목 USDT, 한도 안에서)
CFG.setdefault("trend_ladder", [])               # 하락 추세 모드의 반등 사다리: 저점 대비 +N%마다 보유 물량(사다리 시작 시점)의 1/단계수씩 환매. 예: [1,2,3,4,5] (비우면 끔, 2026-10-26)
CFG.setdefault("trend_ladder_confirm", 3)       # 각 단계선 위에 이 횟수(분)만큼 연속으로 머물러야 환매
CFG.setdefault("cover_min_profit_pct", 0.0)  # 그리드 환매는 현재가가 평균 진입가보다 이 %만큼 낮을 때만 (0/None=끔, 기본 끔). 급락 모드 단계 환매·강제 손절에는 적용 안 함
FEE_FB = 0.0006

# ── 숏 장부 (평균 진입가 방식, 수수료 반영) ────────────────────────────
def _open(state, qty, px, fee=None):
    q0 = state.get("qty", 0.0); c0 = state.get("avg_cost") or px
    fee = fee if fee is not None else qty * px * FEE_FB
    tot = q0 + qty
    state["avg_cost"] = (q0 * c0 + qty * px - fee) / tot if tot > 0 else px     # 진입 수수료는 실효 진입가를 낮춘다
    state["cum_fees"] = state.get("cum_fees", 0.0) + fee

def _cover(state, qty, px, fee=None):
    c0 = state.get("avg_cost") or px
    fee = fee if fee is not None else qty * px * FEE_FB
    state["cum_pnl"] = state.get("cum_pnl", 0.0) + qty * (c0 - px) - fee
    state["cum_fees"] = state.get("cum_fees", 0.0) + fee

def _footer(state, price):
    realized = state.get("cum_pnl", 0.0); q = state.get("qty", 0.0)
    unreal = q * ((state.get("avg_cost") or price) - price) if q > 0 else 0.0
    return (f"\n\n📊 <b>누적 수익 (봇 가동 이후, 수수료 반영, 숏)</b>\n실현 {realized:+,.2f} USDT | 보유 평가 {unreal:+,.2f} USDT | 합계 {realized + unreal:+,.2f} USDT\n"
            f"(누적 수수료 {state.get('cum_fees', 0.0):,.2f} USDT 포함)")

def _msg(state, text, price, trade=True):
    return text + (_footer(state, price) if trade else "")

def _send(msg, send):
    if msg and send:
        g.send_discord(msg)

def _hist(state, price):
    t = time.time()
    h = [x for x in state.get("px_hist", []) if t - x[0] <= 900]; h.append([t, price]); state["px_hist"] = h
    return t, h

def _cover_order(price, usdt, send, qty=None):
    return g.place_order(CFG["symbol"], "buy", usdt, price, reduce_only=True, dry_run=not send, qty_override=qty)

def _short_order(price, usdt, send):
    return g.place_order(CFG["symbol"], "sell", usdt, price, reduce_only=False, dry_run=not send)

def run_check(send=True):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{'='*56}\n  코인 그리드매매 (숏, 반대 전략) — {CFG['symbol']} — {now}\n{'='*56}")
    state = g.load_state(); price = g.fetch_price(CFG["symbol"])
    if price <= 0:
        print("  ❌ 시세 조회 실패"); return
    print(f"  현재가: {price:,.6g} USDT")
    if state.get("halted"):
        print(f"  ⏸️ 봇 정지 상태 ({state.get('halted_reason', '')}) — 재개하려면 --resume"); return
    if state.get("reference_price") is None:
        state.update(reference_price=price, trough_price=price, holdings_usdt=0.0, qty=0.0)
        print(f"  ✅ 기준가 {price:,.6g} 설정 완료")
        if send: g.save_state(state)
        return
    t, hist = _hist(state, price)
    if 0 < state.get("qty", 0.0) * price < 0.05:          # 부동소수점 잔량(예: 1e-11개)이 남으면 '숏 있음'으로 보고 0수량 환매를 반복하므로 가치 5센트 미만은 0으로 정리한다 (2026-10-27)
        state["qty"] = 0.0; state["holdings_usdt"] = 0.0
    qty = state.get("qty", 0.0); msg = None

    # ① 강제 손절
    stop = CFG.get("short_stop_pct")
    if stop and qty > 0 and state.get("avg_cost") and price >= state["avg_cost"] * (1 + stop / 100):
        ok, detail, q, info = _cover_order(price, 0, send, qty=qty)
        if ok:
            aq = info.get("filled_qty", qty); fp = info.get("avg_price", price)
            tp_note_ = g.pair_take_profit(1.0, price, send, "강제 손절") if aq > 0 else ""
            _cover(state, aq, fp, info.get("fee")); state.update(qty=max(0.0, qty - aq), holdings_usdt=0.0, halted=True,
                halted_reason=f"평균 진입가 {state['avg_cost']:,.6g} 대비 +{stop:g}% 손절 ({now})")
            print(f"  🛑 강제 손절 환매: {detail}")
            _send(_msg(state, f"🛑 <b>{CFG['symbol']} 숏 강제 손절 후 정지</b>\n평균 진입가 {state['avg_cost']:,.6g} → 현재가 {price:,.6g} (+{(price/state['avg_cost']-1)*100:.1f}%)\n📦 환매 {aq:.6f}개 @ {fp:,.6g}\n{detail}{tp_note_}\n봇은 정지되었습니다. 재개: coin_grid_short_bot.py --symbol … --resume", price), send)
        else:
            print(f"  ❌ 손절 환매 실패: {detail}")
            if g.no_position_error(detail):
                _send(g.zero_book(state, "손절 환매가 거절됨(25227)."), send)
            elif not state.get("fail_notified"):
                _send(f"❌ <b>{CFG['symbol']} 숏 손절 환매 실패</b>\n{detail}\n다음 점검에서 재시도됩니다", send); state["fail_notified"] = True
        if send: g.save_state(state)
        return

    # ①-c 추세 숏 진입(데드크로스, 기본 꺼짐): 새 봉이 마감될 때 한 번만 판단. 켠 직후의 첫 봉은 기록만 하고 거래하지 않는다.
    if CFG.get("trend_entry") and not (state.get("rally_on") or state.get("buy_paused")):
        try:
            from coin_ma_bot import signal as _ma_signal
            tf = f"{CFG['te_bar_min']}m"; need = CFG["te_slow"] + CFG["te_slope_bars"] + CFG["te_confirm"] + 6
            rows_ = g.get_exchange().fetch_ohlcv(CFG["symbol"], tf, limit=need)[:-1]      # 마지막 봉은 진행 중 → 제외
            bar_ts = rows_[-1][0] if rows_ else None
            if bar_ts is not None and state.get("te_bar_ts") is None:
                state["te_bar_ts"] = bar_ts
            elif bar_ts is not None and bar_ts > state.get("te_bar_ts", 0):
                state["te_bar_ts"] = bar_ts
                sg = _ma_signal([r_[4] for r_ in rows_], CFG["te_fast"], CFG["te_slow"], CFG["te_slope_bars"], CFG["te_confirm"])
                if sg and sg["cross"] == "down" and sg["slope_pct"] < 0 and state.get("holdings_usdt", 0.0) + CFG["te_usdt"] <= CFG["max_total_usdt"]:
                    ok, detail, q, info = _short_order(price, CFG["te_usdt"], send)
                    if ok:
                        aq = info.get("filled_qty", q); _open(state, aq, info.get("avg_price", price), info.get("fee"))
                        state["qty"] = state.get("qty", 0.0) + aq; state["holdings_usdt"] = state.get("holdings_usdt", 0.0) + CFG["te_usdt"]; state["last_entry_price"] = price
                        print(f"  📉 추세 숏 진입(데드크로스 {tf} {CFG['te_fast']}/{CFG['te_slow']}, 느린선 기울기 {sg['slope_pct']:+.3f}%): {detail}")
                        if send:
                            g.save_state(state)
                            _send(_msg(state, f"📉 <b>{CFG['symbol']} 추세 숏 진입</b> (데드크로스 {tf} {CFG['te_fast']}/{CFG['te_slow']})\n💰 금액: {info.get('cost', 0):,.2f} USDT\n💵 체결가: {info.get('avg_price', price):,.6g}\n📦 수량: {aq:.6f}개", price), send)
                        return
                    else:
                        print(f"  ❌ 추세 숏 진입 실패: {detail}")
        except Exception as e_:
            print(f"  (추세 숏 진입 점검 오류 무시: {type(e_).__name__}: {e_})")

    # ①-b 12시간 하락 추세 필터(2026-10-26): 하락 추세면 숏을 trend_hedge_usdt까지 시장가로 채우고 그리드 환매·급락 모드를 쉬며 들고 간다
    if CFG.get("trend_filter"):
        g.update_trend(state, price, t, send)
        if state.get("trend_flag") != -1:
            for k_ in ("tr_trough", "tr_k", "tr_n", "tr_base", "tr_rehedge_below"): state.pop(k_, None)     # 하락 추세가 끝나면 사다리 기록을 지운다
        else:
            lad = CFG.get("trend_ladder") or []; msg = None
            if "tr_trough" not in state:                                   # 저점 기준: 최근 60분 1분봉 저점과 현재가 중 낮은 값
                try:
                    rows = g.get_exchange().fetch_ohlcv(CFG["symbol"], "1m", limit=60)
                    state["tr_trough"] = min([price] + [r[3] for r in rows])
                except Exception:
                    state["tr_trough"] = price
            low = min(state["tr_trough"], price)
            if low < state["tr_trough"]: state["tr_n"] = 0                  # 새 저점이 나오면 연속 확인 횟수는 처음부터
            state["tr_trough"] = low
            k = state.get("tr_k", 0); qty_now = state.get("qty", 0.0); floor = state.get("tr_rehedge_below")
            if qty_now <= 1e-9 and (k > 0 or state.get("tr_base")):          # 숏이 0이 됐는데(수동 청산·장부 정정 등) 사다리 진행 기록이 남아 있으면 지운다
                if floor is None: floor = state["tr_rehedge_below"] = low
                k = 0; state["tr_k"] = 0; state["tr_n"] = 0; state.pop("tr_base", None)
            tgt = min(CFG.get("trend_hedge_usdt", 0.0), CFG["max_total_usdt"]); hold = state.get("holdings_usdt", 0.0)
            if k == 0 and tgt > 0 and (floor is None or price < floor) and min(100.0, tgt - hold) >= 6 and not state.get("rally_on"):
                u = min(100.0, tgt - hold)                                  # 하락 추세 보강: 목표 규모까지 시장가로 채운다(사다리 진행 중에는 채우지 않음)
                ok, detail, q, info = _short_order(price, u, send)
                if ok:
                    aq = info.get("filled_qty", q); _open(state, aq, info.get("avg_price", price), info.get("fee"))
                    if qty_now <= 1e-9: state["tr_trough"] = price; state["tr_n"] = 0      # 빈 상태에서 새로 보강하면 사다리 저점 기준도 지금 가격으로 새로 잡는다
                    state["qty"] = state.get("qty", 0.0) + aq; state["holdings_usdt"] = hold + u; state["last_entry_price"] = price
                    print(f"  🔻 하락 추세 숏 보강: {detail}")
                    msg = _msg(state, f"🔻 <b>{CFG['symbol']} 하락 추세 숏 보강</b> (12시간 {state.get('trend_T', 0):+.1f}%)\n💰 금액: {info.get('cost', 0):,.2f} USDT\n💵 체결가: {info.get('avg_price', price):,.6g}\n📦 수량: {aq:.6f}개\n목표 규모 {tgt:.0f} USDT까지 채웁니다", price)
                else:
                    print(f"  ❌ 하락 추세 숏 보강 실패: {detail}")
                    if not ("40762" in detail or "exceeds the balance" in detail): msg = f"❌ <b>{CFG['symbol']} 하락 추세 숏 보강 실패</b>\n{detail}\n다음 점검에서 재시도됩니다"
            elif lad and qty_now > 0 and k < len(lad):
                line = low * (1 + lad[k] / 100); conf = CFG.get("trend_ladder_confirm", 3)
                n = state.get("tr_n", 0) + 1 if price >= line else 0
                state["tr_n"] = n
                avg_ = state.get("avg_cost"); cmp_ = CFG.get("cover_min_profit_pct")
                in_profit = not (cmp_ is not None and avg_ and price > avg_ * (1 - cmp_ / 100))
                print(f"  📉 12시간 하락 추세({state.get('trend_T', 0):+.1f}%) — 숏 {hold:.0f} USDT 보유, 사다리 {k + 1}/{len(lad)}단계 (저점 {low:,.6g} +{lad[k]:g}% = {line:,.6g}, 현재 {price:,.6g}, 확인 {n}/{conf})")
                if price >= line and n >= conf and in_profit:
                    base = state.get("tr_base") or qty_now; state["tr_base"] = base
                    cq = qty_now if k == len(lad) - 1 else min(base / len(lad), qty_now)
                    if cq * price < 6 or qty_now - cq < 6 / price: cq = qty_now
                    ok, detail, q, info = _cover_order(price, 0, send, qty=cq)
                    if ok:
                        aq = info.get("filled_qty", cq); fp = info.get("avg_price", price); before = state.get("cum_pnl", 0.0)
                        _cover(state, aq, fp, info.get("fee")); old = qty_now; state["qty"] = max(0.0, old - aq)
                        state["holdings_usdt"] = 0.0 if state["qty"] <= 1e-9 else state.get("holdings_usdt", 0.0) * (state["qty"] / old)
                        if k == 0: state["tr_rehedge_below"] = low                  # 첫 단계 환매 뒤에는 저점을 새로 깨기 전까지 다시 채우지 않는다
                        k += 1; state["tr_k"] = k; state["tr_n"] = 0
                        if k >= len(lad) or state["qty"] <= 1e-9:                   # 사다리 완료: 다음 하락 때 다시 시작할 수 있게 초기화(저점 아래로 새로 내려가야 보강)
                            state["tr_k"] = 0; state.pop("tr_base", None)
                        pnl = state.get("cum_pnl", 0.0) - before
                        print(f"  💰 사다리 {k}단계 환매: {detail}")
                        msg = _msg(state, f"💰 <b>{CFG['symbol']} 하락 추세 반등 사다리 {k}/{len(lad)}단계 환매</b> (저점 {low:,.6g} 대비 +{lad[k - 1]:g}%)\n📦 환매 {aq:.6f}개 @ {fp:,.6g}\n실현 {pnl:+.2f} USDT (수수료 포함)\n남은 숏 {state['qty']:.6f}개", price)
                    else:
                        print(f"  ❌ 사다리 환매 실패: {detail}")
                        if g.no_position_error(detail): msg = g.zero_book(state, "사다리 환매가 거절됨(25227).")
                        elif not state.get("ladder_fail_notified"): msg = f"❌ <b>{CFG['symbol']} 사다리 환매 실패</b>\n{detail}\n다음 점검에서 재시도됩니다"; state["ladder_fail_notified"] = True
                elif price >= line and n >= conf and not in_profit:
                    print(f"  (단계선 도달했지만 평균 진입가 {avg_:,.6g}보다 높아 환매 보류 — 손실 구간 환매 안 함)")
            else:
                print(f"  📉 12시간 하락 추세({state.get('trend_T', 0):+.1f}%) — 숏 {hold:.0f}/{tgt:.0f} USDT 보유, 그리드 환매 중지하고 들고 감")
            if send:
                g.save_state(state)
                _send(msg, send)
            return

    # ② 급락 모드(추격 숏 + 반등 사다리 환매)
    ladder = CFG.get("surge_ladder") or [CFG["surge_trail_pct"]]
    if CFG.get("surge_mode"):
        if not state.get("crash_chase"):
            win = CFG.get("surge_window_min", 1) * 60 + 30
            hi = max(p for ts, p in hist if t - ts <= win)
            if price <= hi * (1 - CFG["surge_pct"] / 100):
                state.update(crash_chase=True, crash_low=price, crash_k=0, crash_start=now)
                print(f"  📉 급락 감지 — {CFG['surge_window_min']}분 고점 {hi:,.6g} → {price:,.6g} ({(price/hi-1)*100:.1f}%)")
                note = ""
                cu = 0.0 if (CFG.get("trend_up_block") and state.get("trend_flag") == 1) else CFG.get("surge_chase_usdt", 0.0)   # 12시간 상승 추세에서는 급락 추격 숏 안 함
                if cu > 0:
                    if state.get("holdings_usdt", 0.0) + cu > CFG["max_total_usdt"]:
                        note = "\n추격 숏은 한도 때문에 건너뜁니다."; print("  ⚠️ 추격 숏 건너뜀 — 한도 초과")
                    else:
                        ok, detail, q, info = _short_order(price, cu, send)
                        if ok:
                            aq = info.get("filled_qty", q); _open(state, aq, info.get("avg_price", price), info.get("fee"))
                            state["qty"] = state.get("qty", 0.0) + aq; state["holdings_usdt"] = state.get("holdings_usdt", 0.0) + cu
                            note = f"\n🛒 추격 숏 {info.get('cost', 0):,.2f} USDT / {aq:.6f}개 (체결가 {info.get('avg_price', price):,.6g})"; print(f"  🛒 추격 숏 체결: {detail}")
                        else:
                            note = f"\n❌ 추격 숏 실패: {detail}"; print(f"  ❌ 추격 숏 실패: {detail}")
                state["crash_base"] = state.get("qty", 0.0)
                lad = " / ".join(f"+{x:g}%" for x in ladder)
                _send(f"📉 <b>{CFG['symbol']} 급락 감지 (숏 전략)</b> ({(price/hi-1)*100:.1f}%, {CFG['surge_window_min']}분)\n💵 {hi:,.6g} → {price:,.6g}{note}\n그리드를 멈추고, 급락 저점 대비 {lad} 반등마다 나눠 환매합니다.", send)
                if send: g.save_state(state)
                return
        else:
            low = min(state.get("crash_low", price), price); state["crash_low"] = low
            k = state.get("crash_k", 0); have = state.get("qty", 0.0); base = state.get("crash_base") or have
            def finish():
                for kk in ("crash_chase", "crash_low", "crash_k", "crash_base", "crash_below_n", "crash_start", "crash_fail"): state.pop(kk, None)
                state.update(reference_price=price, trough_price=price)
                if state.get("qty", 0.0) <= 0: state.pop("buy_paused", None)
            if have <= 0:
                if price >= low * (1 + ladder[0] / 100):
                    print("  📈 급락 모드 종료 — 숏 물량 없음, 그리드 복귀"); finish(); _send(f"📈 <b>{CFG['symbol']} 급락 모드 종료</b>\n숏 물량이 없어 환매 없이 그리드로 복귀합니다.", send)
                else:
                    print(f"  📉 급락 모드(숏 없음) — 저점 {low:,.6g}, 현재가 {price:,.6g}")
                if send: g.save_state(state)
                return
            n = state.get("crash_below_n", 0) + 1 if (k < len(ladder) and price >= low * (1 + ladder[k] / 100)) else 0
            state["crash_below_n"] = n; sold = False; msgs = []
            while k < len(ladder) and price >= low * (1 + ladder[k] / 100) and state.get("qty", 0.0) > 0 and n >= CFG.get("surge_confirm_min", 1) and not sold:
                have = state["qty"]; q = have if k == len(ladder) - 1 else min(base / len(ladder), have)
                if q * price < 6 or have - q < 6 / price: q = have
                ok, detail, qq, info = _cover_order(price, 0, send, qty=q)
                if not ok and g.no_position_error(detail):
                    _send(g.zero_book(state, "환매가 거절됨(25227)."), send); break
                if not ok:
                    print(f"  ❌ 환매 실패: {detail}")
                    if not state.get("crash_fail"): _send(f"❌ <b>{CFG['symbol']} 숏 환매 실패</b>\n{detail}\n다음 점검에서 재시도됩니다", send); state["crash_fail"] = True
                    break
                aq = info.get("filled_qty", q); fp = info.get("avg_price", price); ref_b = state.get("reference_price") or price
                _cover(state, aq, fp, info.get("fee")); state["qty"] = max(0.0, have - aq)
                state["holdings_usdt"] = 0.0 if state["qty"] <= 1e-9 else state.get("holdings_usdt", 0.0) * (state["qty"] / have)
                k += 1; sold = True
                tp_note_ = g.pair_take_profit(min(1.0, aq / base) if base else 1.0, price, send, f"숏 분할 환매 {k}/{len(ladder)}")
                msgs.append(f"📈 <b>{CFG['symbol']} 숏 분할 환매 {k}/{len(ladder)}</b>\n📉 급락 저점 {low:,.6g} → {price:,.6g} (+{(price/low-1)*100:.1f}%)\n💵 체결가 {fp:,.6g} / 📦 {aq:.6f}개 (남은 숏 {state['qty']:.6f}개)\n💸 진입 평균가 대비(수수료 제외): {aq * ((state.get('avg_cost') or fp) - fp):+,.2f} USDT\n{detail}{tp_note_}")
                print(f"  📈 숏 분할 환매 {k}/{len(ladder)}: {detail}")
            state["crash_k"] = k; done = k >= len(ladder) or state.get("qty", 0.0) <= 0
            if msgs:
                _send(_msg(state, "\n\n".join(msgs) + ("\n\n✅ 마지막 단계까지 환매해 급락 모드를 끝내고 그리드로 복귀합니다." if done else ""), price), send)
            elif not done:
                print(f"  📉 급락 모드 — 저점 {low:,.6g}, 현재가 {price:,.6g} ({(price/low-1)*100:+.1f}%), 다음 환매선 {low*(1+ladder[k]/100):,.6g} ({k}/{len(ladder)}단계{', 확인 ' + str(n) + '/' + str(CFG.get('surge_confirm_min', 1)) if n else ''})")
            if done: finish()
            if send: g.save_state(state)
            return

    # ③ 급등 대응(신규 숏 중단) — 5분 내 +crash_pct% 급등
    if CFG.get("crash_mode"):
        win = CFG["crash_window_min"] * 60 + 30
        if not state.get("rally_on"):
            lo5 = min(p for ts, p in hist if t - ts <= win)
            fw = CFG.get("rally_fast_win_min", 1) * 60 + 30
            lo_f = min(p for ts, p in hist if t - ts <= fw)
            slow = price >= lo5 * (1 + CFG["crash_pct"] / 100)
            fast = bool(CFG.get("rally_fast_pct")) and price >= lo_f * (1 + CFG["rally_fast_pct"] / 100)
            if slow or fast:
                lo_, wmin = (lo5, CFG["crash_window_min"]) if slow else (lo_f, CFG["rally_fast_win_min"])
                state.update(rally_on=True, rally_high=price, rally_high_t=t)
                print(f"  📈 급등 감지 — {wmin}분 저점 {lo_:,.6g} → {price:,.6g} (+{(price/lo_-1)*100:.1f}%): 신규 숏 중단")
                _send(f"📈 <b>{CFG['symbol']} 급등 감지 (숏 전략)</b> (+{(price/lo_-1)*100:.1f}%, {wmin}분)\n신규 숏을 멈춥니다(보유 숏 환매는 그대로). 고점 대비 -{CFG['crash_rebound_pct']:g}% 되돌리거나 {CFG['crash_quiet_min']}분간 새 고점이 없으면 재개합니다.", send)
        else:
            if price > state.get("rally_high", price): state.update(rally_high=price, rally_high_t=t)
            hi_ = state["rally_high"]
            if price <= hi_ * (1 - CFG["crash_rebound_pct"] / 100) or t - state.get("rally_high_t", t) >= CFG["crash_quiet_min"] * 60:
                for kk in ("rally_on", "rally_high", "rally_high_t"): state.pop(kk, None)
                state["reference_price"] = price; print(f"  ▶️ 급등 진정 — 신규 숏 재개 (고점 {hi_:,.6g} → {price:,.6g})")
                _send(f"▶️ <b>{CFG['symbol']} 급등 대응 해제</b>\n고점 {hi_:,.6g} → {price:,.6g}. 신규 숏을 다시 시작합니다(기준가=현재가).", send)
            elif state.get("reference_price") and price >= state["reference_price"] * (1 + CFG["step_pct"] / 100):
                state["reference_price"] = price

    # ④ 신규 숏 중단(저점 대비 +trail_exit_pct%) — 숏 물량이 있을 때만
    trough = state.get("trough_price") or price
    if qty <= 0 and not state.get("buy_paused"):
        trough = price
    else:
        trough = min(trough, price)
    state["trough_price"] = trough
    if state.get("buy_paused"):
        if price > (state.get("reference_price") or price): state["reference_price"] = price
        if state.get("qty", 0.0) <= 0:
            state.pop("buy_paused", None); state.update(trough_price=price, reference_price=price)
            print("  ▶️ 숏 물량 모두 환매 — 신규 숏 재개"); _send(f"▶️ <b>{CFG['symbol']} 신규 숏 재개</b>\n숏을 모두 환매했습니다. 기준가·저점을 현재가 {price:,.6g}로 새로 잡습니다.", send)
    elif CFG.get("trail_exit_pct") and state.get("qty", 0.0) > 0 and price >= trough * (1 + CFG["trail_exit_pct"] / 100):
        state["buy_paused"] = True
        print(f"  ⏸️ 저점 {trough:,.6g} 대비 +{(price/trough-1)*100:.1f}% — 신규 숏 중단, 보유 숏만 환매")
        _send(f"⏸️ <b>{CFG['symbol']} 신규 숏 중단</b> (저점 대비 +{(price/trough-1)*100:.1f}%)\n📦 보유 숏 {state.get('qty', 0.0):.4f}개만 환매 감시합니다. 모두 환매되면 자동으로 재개합니다.", send)

    # ⑤ 숏 그리드
    ref = state["reference_price"]; qty = state.get("qty", 0.0)
    if CFG.get("ref_mode") == "sma":
        n_ = min(int(CFG.get("ref_sma_min", 5)), 14)
        prev_ = [p_ for ts_, p_ in hist[:-1] if t - ts_ <= n_ * 60 + 30]       # 현재가를 뺀 직전 n분 시세 기록(1분 간격)
        if len(prev_) < max(2, n_ - 1):
            print(f"  😴 {n_}분 평균가 준비 중 ({len(prev_)}/{n_}개 기록) — 이번 점검은 거래 보류")
            if send: g.save_state(state)
            return
        state["reference_price"] = ref = sum(prev_) / len(prev_)
    elif qty <= 0 and price < ref and not state.get("buy_paused"):
        state["reference_price"] = ref = price
    up = ref * (1 + CFG["step_pct"] / 100); down = ref * (1 - CFG.get("sell_step_pct", CFG["step_pct"]) / 100)
    paused = state.get("buy_paused") or state.get("rally_on")
    le_ = state.get("last_entry_price")
    spacing_wait = bool(CFG.get("entry_spacing") and qty > 0 and le_ and price < le_ * (1 + CFG["step_pct"] / 100))
    if price >= up and spacing_wait:
        print(f"  진입 간격 대기 — 직전 숏 진입가 {le_:,.6g} 대비 +{CFG['step_pct']:g}% 더 올라야({le_ * (1 + CFG['step_pct'] / 100):,.6g} 이상) 추가 진입 (기준가 {ref:,.6g})")
    elif price >= up:
        if CFG.get("trend_up_block") and state.get("trend_flag") == 1:
            print(f"  📈 12시간 상승 추세({state.get('trend_T', 0):+.1f}%) — 신규 숏 진입 보류 (보유 숏의 환매 감시는 계속, 기준가 {ref:,.6g})")
        elif paused:
            print(f"  신규 숏 중단 중 — 숏 진입 보류 (기준가 {ref:,.6g})")
        elif state.get("holdings_usdt", 0.0) >= CFG["max_total_usdt"]:
            print(f"  ⚠️ 총 숏 한도({CFG['max_total_usdt']:.0f}USDT) 도달 — 진입 중단")
            if not state.get("notified_budget_full"):
                _send(f"⚠️ <b>{CFG['symbol']} 숏 한도 도달</b>\n총 숏 한도({CFG['max_total_usdt']:.0f}USDT)에 닿았습니다. 환매 감시만 계속합니다.", send); state["notified_budget_full"] = True
        else:
            ok, detail, q, info = _short_order(price, CFG["order_usdt"], send)
            if ok:
                aq = info.get("filled_qty", q); _open(state, aq, info.get("avg_price", price), info.get("fee"))
                state["qty"] = state.get("qty", 0.0) + aq; state["holdings_usdt"] = state.get("holdings_usdt", 0.0) + CFG["order_usdt"]; state["reference_price"] = price; state["last_entry_price"] = price
                print(f"  🔻 숏 진입: {detail}")
                msg = _msg(state, f"🔻 <b>{CFG['symbol']} 숏 진입</b> (+{CFG['step_pct']:g}% 상승)\n💰 금액: {info.get('cost', 0):,.2f} USDT\n💵 체결가: {info.get('avg_price', price):,.6g}\n📦 수량: {aq:.6f}개\n누적 숏: {state['holdings_usdt']:.0f} / {CFG['max_total_usdt']:.0f} USDT\n{detail}", price)
            else:
                print(f"  ❌ 숏 진입 실패: {detail}")
                if not ("40762" in detail or "exceeds the balance" in detail): msg = f"❌ <b>{CFG['symbol']} 숏 진입 실패</b>\n{detail}\n다음 점검에서 재시도됩니다"
    elif price <= down and qty > 0 and CFG.get("cover_min_profit_pct") and state.get("avg_cost") and price > state["avg_cost"] * (1 - CFG["cover_min_profit_pct"] / 100):
        need = state["avg_cost"] * (1 - CFG["cover_min_profit_pct"] / 100)
        print(f"  환매선 {down:,.6g} 도달했지만 평균 진입가 {state['avg_cost']:,.6g} 대비 이익 {CFG['cover_min_profit_pct']:g}% 미달 — 환매 보류 (≤ {need:,.6g} 필요)" + (" [신규 숏 중단 중]" if paused else ""))
    elif price <= down and qty > 0:
        q = CFG.get("sell_order_usdt", CFG["order_usdt"]) * CFG["leverage"] / price
        allq = state["qty"] - q < q * 0.5
        ok, detail, qq, info = _cover_order(price, CFG.get("sell_order_usdt", CFG["order_usdt"]), send, qty=state["qty"] if allq else None)
        if ok:
            aq = info.get("filled_qty", q); fp = info.get("avg_price", price); ref_b = ref
            avg_b = state.get("avg_cost") or fp
            _cover(state, aq, fp, info.get("fee")); state["qty"] = max(0.0, state["qty"] - aq)
            state["holdings_usdt"] = 0.0 if state["qty"] <= 1e-9 else max(0.0, state.get("holdings_usdt", 0.0) - CFG.get("sell_order_usdt", CFG["order_usdt"])); state["reference_price"] = price
            print(f"  🔺 환매: {detail}")
            msg = _msg(state, f"🔺 <b>{CFG['symbol']} 숏 환매</b> (-{CFG.get('sell_step_pct', CFG['step_pct']):g}% 하락)\n💰 금액: {info.get('cost', 0):,.2f} USDT\n💵 체결가: {fp:,.6g}\n📦 수량: {aq:.6f}개\n💸 진입 평균가 대비 실현(수수료 제외): {aq * (avg_b - fp):+,.2f} USDT\n남은 숏 {state['qty']:.6f}개\n{detail}", price)
        else:
            print(f"  ❌ 환매 실패: {detail}")
            msg = g.zero_book(state, "환매 주문이 거절됨(25227).") if g.no_position_error(detail) else f"❌ <b>{CFG['symbol']} 환매 실패</b>\n{detail}\n다음 점검에서 재시도됩니다"
    else:
        print(f"  대기 (기준가 {ref:,.6g}{' [' + str(CFG.get('ref_sma_min', 5)) + '분 평균]' if CFG.get('ref_mode') == 'sma' else ''}, 숏진입 {up:,.6g} / 환매 {down:,.6g})" + (" [신규 숏 중단 중]" if paused else ""))
    if send: g.save_state(state)
    _send(msg, send)
    if not msg: print("\n  액션 없음 (대기)")

def main():
    args = sys.argv[1:]
    if not g._apply_cli(args):
        print("  ❌ 종목을 지정하세요: python coin_grid_short_bot.py --symbol ABC [--check|--status|--live ...]"); sys.exit(1)
    base = CFG["symbol"].split("/")[0].lower(); CFG["state_file"] = f"coin_grid_short_{base}_state.json"
    g._tag()
    if "--reset" in args:
        if os.path.exists(CFG["state_file"]): os.remove(CFG["state_file"])
        print("  ✅ 숏 그리드 상태 초기화 완료 (실제 포지션은 그대로)"); return
    if "--resume" in args:
        st = g.load_state()
        for k in ("halted", "halted_reason", "fail_notified", "buy_paused", "rally_on", "rally_high", "rally_high_t", "crash_chase", "crash_low", "crash_k", "crash_base", "crash_below_n", "px_hist"): st.pop(k, None)
        px = g.fetch_price(CFG["symbol"])
        if px > 0: st["reference_price"] = px; st["trough_price"] = px
        g.save_state(st); print(f"  ✅ 정지 해제 — 기준가·저점을 현재가 {px:,.6g}로 재설정 (봇 숏 {st.get('qty', 0):.4f}개 유지)"); return
    if "--status" in args:
        st = g.load_state()
        if not st or st.get("reference_price") is None: print("  아직 시작 안 함"); return
        px = g.fetch_price(CFG["symbol"]); q = st.get("qty", 0.0); ac = st.get("avg_cost") or px
        print(f"  [숏] 기준가 {st['reference_price']:,.6g} | 저점 {st.get('trough_price', 0):,.6g} | 숏 물량 {q:.4f} (투입 {st.get('holdings_usdt', 0):.0f}/{CFG['max_total_usdt']:.0f} USDT) | 평균 진입가 {ac:,.6g}")
        print(f"  실현 {st.get('cum_pnl', 0):+.2f} | 평가 {q * (ac - px):+.2f} | 수수료 {st.get('cum_fees', 0):.2f} | 손절선 {ac * (1 + (CFG.get('short_stop_pct') or 0) / 100):,.6g}" + (f" | ⏸️ {st.get('halted_reason')}" if st.get("halted") else "")
              + (" | 📉 급락 모드" if st.get("crash_chase") else "") + (" | 📈 급등 대응(신규 숏 중단)" if st.get("rally_on") else "") + (" | ⏸️ 신규 숏 중단" if st.get("buy_paused") else "")); return
    run_check(send=("--check" not in args))

if __name__ == "__main__":
    main()
