"""
코인 평균단가 사다리 봇 — 롱/숏 (Bitget USDT-M 선물, 통합계정/UTA, 헤지 모드)
=================================================================================
2026-10-27 사용자 요청으로 추가한 "D 방식" 전략. 기존 그리드 봇(기준가=직전 5분 평균, 한 칸씩 반복)과 달리 보유 중에는 평균단가를 기준으로 삼는다.
coin_grid_bot.py 의 주문·시세·상태 도구를 그대로 쓴다(모의 체결, 슬리피지, 디스코드, 실전 주문 경로 동일).

[전략 — 롱 기준, 숏은 방향만 반대(거울)]
  1) 진입 사다리: 보유가 없으면 직전 5분 평균가가 기준 → 그보다 −2% 낮을 때 100 USDT 매수. 보유 중이면 평균단가 기준으로
     −2% → −3% → −4% → −5% → −6% 단계마다 100 USDT씩 추가 매수(최대 5단계, 한도 max_total_usdt 600 안에서).
  2) 청산 사다리: 평균단가 +2% → +3% → … → +6%에서 단계마다 100 USDT씩 시장가 매도(평균가 아래에서는 팔지 않음). 남는 물량이 반 칸 미만이면 전량.
     매도가 한 번 나가면 진입 사다리는 처음 단계(−2%)로, 추가 매수가 나가면 청산 사다리가 처음 단계로 돌아간다.
  3) 재투입: 수익실현으로 보유가 0이 되면, 현재가가 직전 5분 평균보다 유리할 때(롱은 낮을 때, 숏은 높을 때) reinvest_usdt(100)를 바로 투입한다.
     조건이 안 맞으면 맞을 때까지 기다린다. 손절로 비었을 때는 재투입하지 않는다.
  4) 손절 3구간: 사다리가 다 찼을 때(5단계 완료 또는 한도 때문에 더 못 살 때)의 평균단가에서 −8% → −10% → −12%(숏은 +8/+10/+12%)에
     닿을 때마다 그때 물량의 1/3씩 시장가로 정리(마지막은 전량). 손절이 한 번 나가면 물량이 0이 될 때까지 신규 진입 중단, 전량 정리 후 rest_min(120)분 쉰다.
  5) 급변 보호: 5분 안에 롱은 −8%, 숏은 +8% 이상 움직이면 그동안 신규 진입(재투입 포함)만 중단.
  모의 재생(Q/CAP 1·3·5·10시간, 슬리피지 0.15%): coin_ma_bot.py 와 같은 방식의 재생 스크립트(avg_ladder4_bt.py, D 방식)로 검증. 12시간 변화율 필터는 쓰지 않는다.

[실행]
  python coin_avg_ladder_bot.py --preset avg_q_long_paper --check     # 1회 점검, 주문·상태 저장 없음
  python coin_avg_ladder_bot.py --preset avg_q_long_paper              # 모의 가동(cron 1분)
  python coin_avg_ladder_bot.py --preset avg_q_long_paper --status | --reset
  상태 파일 coin_avg_<프리셋 이름>_state.json. 모의(paper)는 실제 주문·디스코드·원장 기록이 없다.
"""
import sys, os, time
from datetime import datetime, timezone
import coin_grid_bot as g

CFG = g.CFG
for k_, v_ in (("side", "long"), ("ladder_usdt", 100.0), ("ladder_base_pct", 2.0), ("ladder_step_pct", 1.0), ("ladder_extra", 4),
               ("reinvest_usdt", 100.0), ("reinvest_cond", True), ("stops", [8.0, 10.0, 12.0]), ("rest_min", 120),
               ("ref_min", 5), ("crash_pct", 8.0), ("crash_window_min", 5)):
    CFG.setdefault(k_, v_)
FEE = 0.0004


def step(state, price, now, order_fn, log, notify=None):
    """한 번의 점검. state를 직접 갱신하고 체결 이벤트 목록을 돌려준다. now = 초 단위 시각."""
    ev = []
    L = CFG["side"] == "long"
    word_in, word_out = ("매수", "매도") if L else ("숏 진입", "환매")
    nst = 1 + int(CFG["ladder_extra"]); u = float(CFG["ladder_usdt"]); stops = list(CFG.get("stops") or [])
    cap = float(CFG["max_total_usdt"])
    st = state
    for k_, v_ in (("qty", 0.0), ("avg", 0.0), ("hold", 0.0), ("bs", 0), ("ss", 0), ("stopped", 0), ("full", False), ("q_at_full", 0.0),
                   ("avg_at_full", 0.0), ("want_re", False), ("cum_pnl", 0.0), ("cum_fees", 0.0)):
        st.setdefault(k_, v_)
    if 0 < st["qty"] * price < 0.05:                       # 부동소수점 잔량 정리
        st.update(qty=0.0, hold=0.0, avg=0.0)
    # 시세 기록(직전 N분 평균·급변 감지용)
    hist = [h for h in st.get("ref_hist", []) if now - h[0] <= 900]
    prev = [h for h in hist if now - h[0] <= CFG["ref_min"] * 60 + 20]
    hist.append([now, price]); st["ref_hist"] = hist
    if now < st.get("rest_until", 0):
        log(f"  😴 손절 후 휴식 중 — 약 {(st['rest_until'] - now) / 60:.0f}분 뒤 재개"); return ev
    if len(prev) < max(3, CFG["ref_min"] - 1):
        log(f"  😴 시세 기록 모으는 중 ({len(prev)}/{CFG['ref_min']}분) — 아직 거래 안 함"); return ev
    ref = sum(p for _, p in prev) / len(prev)
    win = [p for t, p in hist if now - t <= CFG["crash_window_min"] * 60 + 30]
    moved = (price <= max(win) * (1 - CFG["crash_pct"] / 100)) if L else (price >= min(win) * (1 + CFG["crash_pct"] / 100))

    def do_in(usdt, why):
        ok, detail, q, info = order_fn("buy" if L else "sell", usdt, price, False, None)
        if not ok:
            log(f"  ❌ {word_in} 실패: {detail}"); ev.append(("fail", why)); return False
        aq = info.get("filled_qty", q); fp = info.get("avg_price", price); fee = info.get("fee") or fp * aq * FEE
        st["avg"] = (st["qty"] * st["avg"] + aq * fp) / (st["qty"] + aq); st["qty"] += aq; st["hold"] += usdt
        st["cum_fees"] += fee; st["cum_pnl"] -= fee
        log(f"  {'🔺' if L else '🔻'} {word_in}({why}): {detail} | 평균단가 {st['avg']:,.6g} 보유 {st['hold']:.0f}USDT")
        ev.append(("in", why, usdt))
        if notify: notify(f"{'🔺' if L else '🔻'} <b>{CFG['symbol']} {word_in}</b> ({why})\n💵 {fp:,.6g} · 평균 {st['avg']:,.6g} · 보유 {st['hold']:.0f}USDT")
        return True

    def do_out(qq, why, stop=False):
        qq = min(qq, st["qty"])
        ok, detail, q, info = order_fn("sell" if L else "buy", 0.0, price, True, qq)
        if not ok and ("25227" in str(detail) or "No position" in str(detail)):      # 거래소에 포지션이 없음 → 장부를 0으로 맞춘다
            log(f"  ⚠️ 거래소에 포지션이 없어 장부를 0으로 맞춤: {detail}")
            st.update(qty=0.0, hold=0.0, avg=0.0, bs=0, ss=0, stopped=0, full=False); ev.append(("zero", why)); return False
        if not ok:
            log(f"  ❌ {word_out} 실패: {detail}"); ev.append(("fail", why)); return False
        aq = info.get("filled_qty", q) or qq; fp = info.get("avg_price", price); fee = info.get("fee") or fp * aq * FEE
        gross = aq * ((fp - st["avg"]) if L else (st["avg"] - fp)); net = gross - fee
        st["cum_pnl"] += net; st["cum_fees"] += fee
        st["hold"] = st["hold"] * (st["qty"] - aq) / st["qty"] if st["qty"] > 0 else 0.0; st["qty"] -= aq
        st["n_out"] = st.get("n_out", 0) + 1
        if stop: st["n_stop"] = st.get("n_stop", 0) + 1; st["stop_pnl"] = st.get("stop_pnl", 0.0) + net
        log(f"  {'🟢' if net > 0 else '🔴'} {word_out}({why}): {detail} | 평균단가 {st['avg']:,.6g} → {fp:,.6g}, 순손익 {net:+.2f} USDT")
        ev.append(("out", why, net))
        if notify: notify(f"{'🟢' if net > 0 else '🔴'} <b>{CFG['symbol']} {word_out}</b> ({why})\n순손익 {net:+.2f} USDT · 누적 {st['cum_pnl']:+.2f}")
        return True

    # ① 수익실현으로 비었으면 재투입
    if st["want_re"] and st["qty"] <= 1e-12 and not moved and (not CFG["reinvest_cond"] or ((price < ref) if L else (price > ref))):
        if do_in(float(CFG["reinvest_usdt"]), "재투입"):
            st.update(bs=0, ss=0, want_re=False, full=False, stopped=0, n_re=st.get("n_re", 0) + 1)
        return ev
    base = st["avg"] if st["qty"] > 1e-12 else ref
    # ② 손절 3구간 (사다리가 다 찬 뒤)
    if stops and st["qty"] > 1e-12 and st["full"] and st["stopped"] < len(stops):
        k = st["stopped"]; lvl = st["avg_at_full"] * (1 - stops[k] / 100) if L else st["avg_at_full"] * (1 + stops[k] / 100)
        if (price <= lvl) if L else (price >= lvl):
            qq = st["qty"] if k == len(stops) - 1 else min(st["qty"], st["q_at_full"] / len(stops))
            if do_out(qq, f"손절 {k + 1}/{len(stops)}구간 ({stops[k]:g}%)", stop=True):
                st["stopped"] += 1
                if st["qty"] <= 1e-12:
                    st.update(qty=0.0, hold=0.0, avg=0.0, bs=0, ss=0, stopped=0, full=False, rest_until=now + CFG["rest_min"] * 60)
                    log(f"  ⏸️ 전량 정리 — {CFG['rest_min']}분 쉽니다")
            return ev
    # ③ 진입 / 청산 사다리
    ent_lv = base * (1 - (CFG["ladder_base_pct"] + CFG["ladder_step_pct"] * st["bs"]) / 100) if L else base * (1 + (CFG["ladder_base_pct"] + CFG["ladder_step_pct"] * st["bs"]) / 100)
    ext_lv = base * (1 + (CFG["ladder_base_pct"] + CFG["ladder_step_pct"] * st["ss"]) / 100) if L else base * (1 - (CFG["ladder_base_pct"] + CFG["ladder_step_pct"] * st["ss"]) / 100)
    can_in = (price <= ent_lv) if L else (price >= ent_lv)
    can_out = (price >= ext_lv) if L else (price <= ext_lv)
    if can_in and not moved and st["bs"] < nst and st["hold"] + u <= cap and st["stopped"] == 0:
        if do_in(u, f"사다리 {st['bs'] + 1}단계"):
            st["bs"] += 1; st["ss"] = 0
            if st["bs"] >= nst or st["hold"] + u > cap:
                st.update(full=True, q_at_full=st["qty"], avg_at_full=st["avg"])
                log(f"  📌 사다리 완료 — 평균단가 {st['avg']:,.6g}, 손절선 " + " / ".join(f"{(st['avg'] * (1 - s / 100) if L else st['avg'] * (1 + s / 100)):,.6g}" for s in stops))
    elif can_out and st["qty"] > 1e-12 and st["ss"] < nst and ((price >= st["avg"]) if L else (price <= st["avg"])):
        qq = u / price
        if st["qty"] - qq < qq * 0.5: qq = st["qty"]
        if do_out(qq, f"청산 {st['ss'] + 1}단계"):
            st["ss"] += 1; st["bs"] = 0; st["stopped"] = 0; st["full"] = False
            if st["qty"] <= 1e-12:
                st.update(qty=0.0, hold=0.0, avg=0.0, bs=0, ss=0, want_re=bool(CFG["reinvest_usdt"]))
                log("  ✅ 수익실현으로 전량 정리 — 조건이 맞으면 바로 재투입")
    elif not ev:
        pos = f"보유 {st['qty']:.4f}개 평균 {st['avg']:,.6g} ({(price / st['avg'] - 1) * 100 * (1 if L else -1):+.2f}%) {st['hold']:.0f}USDT 진입단계 {st['bs']} 청산단계 {st['ss']}" if st["qty"] > 1e-12 else \
              ("재투입 대기" if st["want_re"] else "보유 없음")
        log(f"  대기 — {pos} | 기준 {base:,.6g} 진입선 {ent_lv:,.6g} 청산선 {ext_lv:,.6g}" + (" | 급변 중(신규 진입 중단)" if moved else ""))
    return ev


def run_check(send=True):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{'=' * 56}\n  코인 평균단가 사다리 봇 — {CFG['symbol']} {'롱' if CFG['side'] == 'long' else '숏'} — {now}{' [모의]' if CFG.get('paper') else ''}\n{'=' * 56}")
    state = g.load_state(); price = g.fetch_price(CFG["symbol"])
    if price <= 0: print("  ❌ 시세 조회 실패"); return
    print(f"  현재가: {price:,.6g} USDT")
    order_fn = lambda side, usdt, p, ro, qty: g.place_order(CFG["symbol"], side, usdt, p, reduce_only=ro, dry_run=not send, qty_override=qty)
    step(state, price, time.time(), order_fn, print, notify=(lambda m: g.send_discord(m)) if (send and not CFG.get("paper")) else None)
    if send: g.save_state(state)


def main():
    args = sys.argv[1:]
    if not g._apply_cli(args):
        print("  ❌ 종목을 지정하세요: python coin_avg_ladder_bot.py --preset 이름 [--check|--status|--reset]"); sys.exit(1)
    base = CFG["symbol"].split("/")[0].lower(); CFG["state_file"] = f"coin_avg_{base}_state.json"
    if "--preset" in args and args.index("--preset") + 1 < len(args):
        CFG["state_file"] = f"coin_avg_{args[args.index('--preset') + 1]}_state.json"
    if "--reset" in args:
        if os.path.exists(CFG["state_file"]): os.remove(CFG["state_file"])
        print("  ✅ 상태 초기화 완료 (실제 포지션은 그대로)"); return
    if "--status" in args:
        st = g.load_state()
        if not st: print("  아직 시작 안 함"); return
        L = CFG["side"] == "long"; px = g.fetch_price(CFG["symbol"])
        unreal = (st["qty"] * ((px - st["avg"]) if L else (st["avg"] - px))) if st.get("qty", 0) > 0 else 0.0
        print(f"  [{CFG['symbol']} {'롱' if L else '숏'}{' 모의' if CFG.get('paper') else ''}] 누적 순손익(수수료 후) {st.get('cum_pnl', 0):+.2f} + 미실현 {unreal:+.2f} = {st.get('cum_pnl', 0) + unreal:+.2f} USDT | "
              f"수수료 {st.get('cum_fees', 0):.2f} | 청산 {st.get('n_out', 0)}건 손절 {st.get('n_stop', 0)}건({st.get('stop_pnl', 0):+.2f}) 재투입 {st.get('n_re', 0)}회")
        if st.get("qty", 0) > 0: print(f"  보유: {st['qty']:.4f}개 평균 {st['avg']:,.6g} 현재 {px:,.6g} ({(px / st['avg'] - 1) * 100 * (1 if L else -1):+.2f}%) 보유금액 {st['hold']:.0f}USDT")
        else: print("  보유: 없음")
        return
    run_check(send=("--check" not in args))


if __name__ == "__main__":
    main()
