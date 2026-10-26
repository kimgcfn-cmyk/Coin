"""
코인 이동평균 교차 봇 — 추세 추종 (Bitget USDT-M 선물, 통합계정/UTA, 헤지 모드)
=================================================================================
기존 그리드 봇(평균 회귀)과 반대 성격의 전략: 이동평균선 움직임을 따라 자동 매수·매도한다. 2026-10-26 사용자 요청으로 추가. coin_grid_bot.py의 주문·상태·알림 함수를 그대로 쓴다.

[전략 — 모두 CFG(프리셋)로 조절]
  1) 신호: timeframe(15분봉) 종가 기준 빠른 이동평균(ma_fast=5)이 느린 이동평균(ma_slow=20)을 위로 뚫으면 골든크로스(롱), 아래로 뚫으면 데드크로스(숏).
     봉이 마감된 뒤에만 판단한다(진행 중인 봉은 쓰지 않음). 봇을 처음 켤 때는 그 시점의 교차를 무시하고 다음 새 교차부터 거래한다.
  2) 기울기 필터(slope_filter): 느린 이동평균이 slope_bars(3)개 봉 전보다 오른 상태에서만 롱, 내린 상태에서만 숏 진입(횡보 속 가짜 교차 거르기, 거래가 반 이하로 줄어듦).
  3) 방향(direction): "both"=롱+숏, "long"=롱만, "short"=숏만. 반대 교차가 나오면 기존 포지션을 청산하고, 필터를 통과하면 반대로 진입한다.
  4) 포지션: 한 번에 하나, 크기는 order_usdt(명목, 추가 진입 없음). max_total_usdt는 안전 한도(order_usdt가 넘으면 거래 안 함).
  5) 청산: 반대 교차 / 손절 stop_pct(진입가 대비 3%) / 트레일링 trail_pct(진입 후 유리했던 최고가 대비 3% 되돌림) 중 먼저 오는 것. 손절·트레일링은 매분 현재가로 점검(한 줄 스톱 = 둘 중 유리한 쪽).
  6) 쿨다운: 손절·트레일링으로 청산한 뒤 cooldown_bars(2)개 봉은 신규 진입 안 함. 연속 loss_streak_pause(3)회 손실이면 streak_pause_bars(8)개 봉(15분봉이면 2시간) 쉰다.
  7) 모의(paper): 프리셋 paper=true면 실제 주문 없이 현재가+슬리피지로 체결을 가정해 상태·로그만 쌓는다(디스코드·원장 없음). --live 를 빼고 cron에 넣는다. 실전은 paper=false + --live.

[실행]
  python coin_ma_bot.py --preset ma_cap_paper --check      # 1회 점검, 주문·상태 저장 없음
  python coin_ma_bot.py --preset ma_cap_paper               # 모의 가동(cron 1분)
  python coin_ma_bot.py --preset ma_cap_paper --status | --reset
  cron 예시(1분): * * * * * sleep 25; cd /home/ec2-user/trading-bot && flock -n /tmp/coin_ma_cap.lock venv/bin/python coin_ma_bot.py --preset ma_cap_paper >> logs/coin_ma_cap.log 2>&1
  상태 파일 coin_ma_<프리셋 이름>_state.json(--preset 없이 --symbol만 쓰면 coin_ma_<abc>_state.json). 수동 거래는 이 봇과 분리한다(봇이 연 포지션만 청산).

[모의 참고 — 16종목 120시간, 구현 전 단순 시뮬(전액 진입, 슬리피지 0.05~0.10%)]
  15분봉 5/20 롱+숏: 종목 평균 +36~39%, 승률 33~34%, 이익 종목 11~12/16. 승률이 낮아 작은 손실이 쌓이다 큰 추세에서 번다. 횡보장에서는 손실이 이어진다. 과거 결과일 뿐 투자 조언이 아니다.
"""
import sys, os, json, time
from datetime import datetime, timezone
import coin_grid_bot as g

CFG = g.CFG
for k_, v_ in (("timeframe", "15m"), ("ma_fast", 5), ("ma_slow", 20), ("slope_filter", True), ("slope_bars", 3), ("direction", "both"),
               ("stop_pct", 3.0), ("trail_pct", 3.0), ("cooldown_bars", 2), ("loss_streak_pause", 3), ("streak_pause_bars", 8),
               ("tp_pct", 0.0), ("confirm_bars", 0), ("slope_min_pct", 0.0)):
    CFG.setdefault(k_, v_)
FEE = 0.0004

def tf_ms(tf: str) -> int:
    n = int(tf[:-1]); u = tf[-1]
    return n * {"m": 60, "h": 3600, "d": 86400}[u] * 1000

def signal(closes, fast, slow, slope_bars, confirm=0):
    """마감된 봉 종가 목록(오래된→최신)으로 교차·기울기를 계산한다. 데이터가 모자라면 None.
    confirm=N이면 교차 후 N개 봉이 지나도 빠른 선이 같은 쪽에 머물렀을 때만 신호로 인정한다(가짜 교차 거르기, N=0은 교차 직후 바로)."""
    if len(closes) < slow + slope_bars + confirm + 2: return None
    def sd(end):      # end(음수 인덱스) 시점의 빠른선−느린선
        c = closes[:len(closes) + end + 1] if end < -1 else closes
        return sum(c[-fast:]) / fast - sum(c[-slow:]) / slow
    sides = [sd(-1 - j) for j in range(confirm + 2)]          # [현재, 1봉 전, ...]
    cur = sides[0]; prev = sides[confirm + 1]; mid = sides[1:confirm + 1]
    cross = None
    if prev <= 0 < cur and all(x > 0 for x in mid): cross = "up"
    elif prev >= 0 > cur and all(x < 0 for x in mid): cross = "down"
    f1 = sum(closes[-fast:]) / fast; s1 = sum(closes[-slow:]) / slow
    s_old = sum(closes[-slow - slope_bars:-slope_bars]) / slow
    return dict(cross=cross, slope=s1 - s_old, fast=f1, slow=s1, slope_pct=(s1 - s_old) / s1 * 100)

def stop_price(state):
    """한 줄 스톱: 롱은 max(진입가−손절, 최고가−트레일), 숏은 min(진입가+손절, 최저가+트레일)."""
    e = state["entry"]; b = state["best"]
    if state["pos"] == "long": return max(e * (1 - CFG["stop_pct"] / 100), b * (1 - CFG["trail_pct"] / 100))
    return min(e * (1 + CFG["stop_pct"] / 100), b * (1 + CFG["trail_pct"] / 100))

def step(state, closed, price, order_fn, log, notify=None):
    """한 번의 점검. closed = 마감된 봉 [[ts,o,h,l,c,v],...](오래된→최신), price = 현재가. 상태를 직접 갱신하고 이벤트 목록을 돌려준다."""
    ev = []; bar_ms = tf_ms(CFG["timeframe"]); last_ts = closed[-1][0]; closes = [b[4] for b in closed]
    if "last_bar_ts" not in state:
        state["last_bar_ts"] = last_ts; log(f"  ✅ 시작 — 마지막 마감 봉 {datetime.fromtimestamp(last_ts / 1000, timezone.utc):%m-%d %H:%M} UTC 이후 새 교차부터 거래합니다"); return ev
    def close_pos(reason, p):
        side = "sell" if state["pos"] == "long" else "buy"
        ok, detail, q, info = order_fn(side, 0.0, p, True, state["qty"])
        if not ok:
            log(f"  ❌ 청산 실패({reason}): {detail}"); ev.append(("fail", reason)); return False
        aq = info.get("filled_qty", q) or state["qty"]; fp = info.get("avg_price", p); fee = info.get("fee") or fp * aq * FEE
        gross = aq * ((fp - state["entry"]) if state["pos"] == "long" else (state["entry"] - fp)); net = gross - fee - state.get("entry_fee", 0.0)
        state["cum_pnl"] = state.get("cum_pnl", 0.0) + net; state["cum_fees"] = state.get("cum_fees", 0.0) + fee + state.get("entry_fee", 0.0)
        state["n_closed"] = state.get("n_closed", 0) + 1; state["n_wins"] = state.get("n_wins", 0) + (1 if net > 0 else 0)
        state["sum_win"] = state.get("sum_win", 0.0) + (net if net > 0 else 0.0); state["sum_loss"] = state.get("sum_loss", 0.0) + (net if net <= 0 else 0.0)
        state["loss_streak"] = 0 if net > 0 else state.get("loss_streak", 0) + 1
        log(f"  {'🟢' if net > 0 else '🔴'} {'롱' if state['pos'] == 'long' else '숏'} 청산({reason}): {detail} | 진입 {state['entry']:,.6g} → 청산 {fp:,.6g}, 순손익 {net:+.2f} USDT (누적 {state['cum_pnl']:+.2f})")
        ev.append(("close", state["pos"], reason, net)); old = state["pos"]
        for k_ in ("pos", "qty", "entry", "entry_fee", "best", "entry_ts"): state.pop(k_, None)
        if notify: notify(f"{'🟢' if net > 0 else '🔴'} <b>{CFG['symbol']} {'롱' if old == 'long' else '숏'} 청산</b> ({reason})\n순손익 {net:+.2f} USDT · 누적 {state['cum_pnl']:+.2f} USDT")
        if reason in ("손절·트레일링",):
            state["cool_until"] = last_ts + CFG["cooldown_bars"] * bar_ms
        if state["loss_streak"] >= CFG["loss_streak_pause"]:
            state["cool_until"] = max(state.get("cool_until", 0), last_ts + CFG["streak_pause_bars"] * bar_ms); state["loss_streak"] = 0
            log(f"  ⏸️ 연속 손실 {CFG['loss_streak_pause']}회 — {CFG['streak_pause_bars']}개 봉({CFG['streak_pause_bars'] * bar_ms // 60000}분) 신규 진입 쉼")
        return True
    def open_pos(side, p, why):
        if CFG["order_usdt"] > CFG["max_total_usdt"]:
            log("  ⚠️ order_usdt가 한도(max_total_usdt)보다 커서 진입 안 함"); return False
        ok, detail, q, info = order_fn("buy" if side == "long" else "sell", CFG["order_usdt"], p, False, None)
        if not ok:
            log(f"  ❌ 진입 실패: {detail}"); ev.append(("fail", "open")); return False
        aq = info.get("filled_qty", q); fp = info.get("avg_price", p); fee = info.get("fee") or fp * aq * FEE
        state.update(pos=side, qty=aq, entry=fp, entry_fee=fee, best=fp, entry_ts=time.time())
        state["n_entries"] = state.get("n_entries", 0) + 1
        log(f"  {'🔺' if side == 'long' else '🔻'} {'롱' if side == 'long' else '숏'} 진입({why}): {detail} | 손절·트레일링선 {stop_price(state):,.6g}")
        ev.append(("open", side, why))
        if notify: notify(f"{'🔺' if side == 'long' else '🔻'} <b>{CFG['symbol']} {'롱' if side == 'long' else '숏'} 진입</b> ({why})\n💵 {fp:,.6g} · 📦 {aq:.6f}개 · 손절·트레일링선 {stop_price(state):,.6g}")
        return True
    # ① 손절·트레일링 (매 점검)
    if state.get("pos"):
        state["best"] = max(state["best"], price) if state["pos"] == "long" else min(state["best"], price)
        sp = stop_price(state); hit = (price <= sp) if state["pos"] == "long" else (price >= sp)
        tp = CFG["tp_pct"]
        if hit: close_pos("손절·트레일링", price)
        elif tp and ((state["pos"] == "long" and price >= state["entry"] * (1 + tp / 100)) or (state["pos"] == "short" and price <= state["entry"] * (1 - tp / 100))): close_pos("익절", price)
    # ② 새 봉 마감 시 교차 판단
    if last_ts > state["last_bar_ts"]:
        state["last_bar_ts"] = last_ts
        sig = signal(closes, CFG["ma_fast"], CFG["ma_slow"], CFG["slope_bars"], CFG["confirm_bars"])
        if sig is None:
            log("  😴 봉 데이터 부족 — 대기"); return ev
        cross = sig["cross"]; slope = sig["slope"]; d = CFG["direction"]; filt = CFG["slope_filter"]
        if cross: log(f"  📈 {'골든크로스' if cross == 'up' else '데드크로스'} — 빠른 {sig['fast']:,.6g} / 느린 {sig['slow']:,.6g}, 느린선 기울기 {slope:+.6g}")
        want = None
        smin = CFG["slope_min_pct"]
        if cross == "up" and d in ("both", "long") and (not filt or sig["slope_pct"] > smin): want = "long"
        if cross == "down" and d in ("both", "short") and (not filt or sig["slope_pct"] < -smin): want = "short"
        pos = state.get("pos")
        if pos and cross and ((pos == "long" and cross == "down") or (pos == "short" and cross == "up")):
            close_pos("반대 교차", price)
        if not state.get("pos") and want:
            if last_ts < state.get("cool_until", 0): log(f"  ⏸️ 쿨다운 중 — {want} 신호 무시")
            else: open_pos(want, price, "골든크로스" if want == "long" else "데드크로스")
        elif cross and not want and not state.get("pos"):
            log(f"  (교차 신호가 {'기울기 필터' if filt and ((cross == 'up' and slope <= 0) or (cross == 'down' and slope >= 0)) else '방향 설정'}에 걸려 진입 안 함)")
    return ev

def fetch_closed(sym, tf, n):
    ex = g.get_exchange(); rows = ex.fetch_ohlcv(sym, tf, limit=n)
    return rows[:-1] if rows else []        # 마지막 봉은 진행 중 → 제외

def run_check(send=True):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{'=' * 56}\n  코인 이동평균 교차 봇 — {CFG['symbol']} — {now}{' [모의]' if CFG.get('paper') else ''}\n{'=' * 56}")
    state = g.load_state(); price = g.fetch_price(CFG["symbol"])
    if price <= 0: print("  ❌ 시세 조회 실패"); return
    try: closed = fetch_closed(CFG["symbol"], CFG["timeframe"], CFG["ma_slow"] + CFG["slope_bars"] + 6)
    except Exception as e: print(f"  ❌ 봉 조회 실패: {type(e).__name__}: {e}"); return
    if not closed: print("  ❌ 봉 데이터 없음"); return
    print(f"  현재가: {price:,.6g} USDT")
    order_fn = lambda side, usdt, p, ro, qty: g.place_order(CFG["symbol"], side, usdt, p, reduce_only=ro, dry_run=not send, qty_override=qty)
    ev = step(state, closed, price, order_fn, print, notify=(lambda m: g.send_discord(m)) if (send and not CFG.get("paper")) else None)
    if not ev and "last_bar_ts" in state:
        sig = signal([b[4] for b in closed], CFG["ma_fast"], CFG["ma_slow"], CFG["slope_bars"], CFG["confirm_bars"])
        pos = state.get("pos")
        txt = f"빠른 {sig['fast']:,.6g} / 느린 {sig['slow']:,.6g} / 기울기 {sig['slope']:+.6g}" if sig else "봉 데이터 부족"
        if pos: print(f"  보유: {'롱' if pos == 'long' else '숏'} {state['qty']:.4f}개 진입 {state['entry']:,.6g} 현재 {((price / state['entry'] - 1) * 100 * (1 if pos == 'long' else -1)):+.2f}% | 스톱선 {stop_price(state):,.6g} | {txt}")
        else: print(f"  대기 — 포지션 없음 | {txt}" + (f" | 쿨다운 {max(0, (state.get('cool_until', 0) - closed[-1][0]) // 60000)}분" if state.get("cool_until", 0) > closed[-1][0] else ""))
    if send: g.save_state(state)

def main():
    args = sys.argv[1:]
    if not g._apply_cli(args):
        print("  ❌ 종목을 지정하세요: python coin_ma_bot.py --preset 이름 [--check|--status|--reset]"); sys.exit(1)
    base = CFG["symbol"].split("/")[0].lower(); CFG["state_file"] = f"coin_ma_{base}_state.json"
    if "--preset" in args and args.index("--preset") + 1 < len(args):      # 프리셋마다 상태 파일을 따로 쓴다(같은 종목에 설정 여러 개를 모의로 비교하려고)
        CFG["state_file"] = f"coin_ma_{args[args.index('--preset') + 1]}_state.json"
    if "--reset" in args:
        if os.path.exists(CFG["state_file"]): os.remove(CFG["state_file"])
        print("  ✅ 이동평균 봇 상태 초기화 완료 (실제 포지션은 그대로)"); return
    if "--status" in args:
        st = g.load_state()
        if not st: print("  아직 시작 안 함"); return
        px = g.fetch_price(CFG["symbol"]); pos = st.get("pos"); n = st.get("n_closed", 0)
        print(f"  [{CFG['symbol']}{' 모의' if CFG.get('paper') else ''}] 누적 순손익 {st.get('cum_pnl', 0):+.2f} USDT (수수료 {st.get('cum_fees', 0):.2f}) | 청산 {n}건 승률 {(st.get('n_wins', 0) / n * 100 if n else 0):.0f}% | 진입 {st.get('n_entries', 0)}회")
        if pos: print(f"  보유: {'롱' if pos == 'long' else '숏'} {st['qty']:.4f}개 진입 {st['entry']:,.6g} 현재 {px:,.6g} ({((px / st['entry'] - 1) * 100 * (1 if pos == 'long' else -1)):+.2f}%) 스톱선 {stop_price(st):,.6g}")
        else: print("  보유: 없음")
        return
    run_check(send=("--check" not in args))

if __name__ == "__main__":
    main()
