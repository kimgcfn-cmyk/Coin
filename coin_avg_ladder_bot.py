"""
코인 평균단가 사다리 봇 — 롱/숏 (Bitget USDT-M 선물, 통합계정/UTA, 헤지 모드)
=================================================================================
2026-10-27 사용자 요청으로 추가한 "D 방식" 전략. 기존 그리드 봇(기준가=직전 5분 평균, 한 칸씩 반복)과 달리 보유 중에는 평균단가를 기준으로 삼는다.
coin_grid_bot.py 의 주문·시세·상태 도구를 그대로 쓴다(모의 체결, 슬리피지, 디스코드, 실전 주문 경로 동일).

[전략 — 롱 기준, 숏은 방향만 반대(거울)]
  0) 첫 진입: 처음 가동하면(시세 5분 기록 후) 롱·숏 모두 기다리지 않고 바로 first_entry_usdt(100 USDT)를 같이 진입한다(2026-10-27 사용자 지시).
  1) 진입 사다리: 보유가 없으면 직전 5분 평균가가 기준 → 그보다 −1%(entry_base_pct, 2026-10-27 사용자 지시 −2%→−1%) 낮을 때 100 USDT 매수. 보유 중이면 평균단가 기준으로
     −1% → −2% → −3% → −4% → −5% 단계마다 100 USDT씩 추가 매수(최대 5단계, 한도 max_total_usdt 600 안에서).
  2) 청산 사다리(keep_usdt>0이면 보유 금액이 그 이하일 때는 청산하지 않고 초과분만 청산 — 2026-10-27, 항상 최소 100 USDT 유지): 평균단가 +1% → +2% → … → +5%에서 단계마다 100 USDT씩 시장가 매도(평균가 아래에서는 팔지 않음). 남는 물량이 반 칸 미만이면 전량.
     매도가 한 번 나가면 진입 사다리는 처음 단계(−2%)로, 추가 매수가 나가면 청산 사다리가 처음 단계로 돌아간다.
  2-1) 추세 보유(trend_hold, 2026-10-27): 이익 쪽이 최근 30분 안에 평균단가 +3%에 닿고 반대쪽 사다리가 3단계 이상이면 추세로 보고 청산 1단계까지만 판 뒤 들고 간다.
     고점(숏은 저점) 대비 trend_pullback_pct(2.5%) 되돌림, 1분 3% 급반전, 이익 소멸 중 먼저 오는 것에 전량 정리. 유지 금액 keep_usdt(300) 이하는 청산하지 않고 초과분만 청산.
  2-2) 반대쪽 사다리가 relax_opp_stage(2)단계 이상이면 최소 보유를 relax_keep_usdt(100)로 낮춰 이익을 더 청산한다(2026-10-27 C 방식).
  2-3) 노출 축소(derisk): 보유 derisk_hold_usdt(300) 이상 & 평균단가 대비 derisk_underwater_pct(3%) 이상 불리 & 직전 5분 평균 대비 derisk_pct(1%) 되돌림이면 derisk_usdt(100)를 청산하고
     derisk_block_min(30)분간 같은 방향 신규 진입을 막는다(2026-10-27 E 방식).
  2-4) 진입 기준 시세(entry_ref_min): 0이면 평균단가, N이면 직전 N분 평균 시세를 진입 사다리(−1~−5% / +1~+5%)의 기준으로 쓴다(CAP 2026-10-27 N=60 실전).
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
import sys, os, time, json
from datetime import datetime, timezone
import coin_grid_bot as g

CFG = g.CFG
for k_, v_ in (("side", "long"), ("ladder_usdt", 100.0), ("ladder_base_pct", 2.0), ("ladder_step_pct", 1.0), ("ladder_extra", 4),
               ("reinvest_usdt", 100.0), ("reinvest_cond", True), ("stops", [8.0, 10.0, 12.0]), ("rest_min", 120),
               ("ref_min", 5), ("crash_pct", 8.0), ("crash_window_min", 5), ("first_entry_usdt", 0.0), ("sell_pct", 0.0), ("sell_pct_of", "start"), ("keep_usdt", 0.0), ("deep_rebound_pct", 0.0), ("deep_from_stage", 3), ("trend_hold", False), ("trend_window_min", 30), ("trend_gain_pct", 3.0),
               ("trend_opp_stage", 3), ("trend_sell_stages", 1), ("trend_pullback_pct", 1.5), ("trend_flip_pct", 3.0), ("opp_state", ""), ("relax_opp_stage", 0), ("derisk_hold_usdt", 0.0), ("derisk_pct", 1.0), ("derisk_usdt", 100.0), ("derisk_underwater_pct", 0.0), ("entry_ref_min", 0), ("entry_ref_mix", 0), ("derisk_block_min", 0), ("relax_keep_usdt", 0.0), ("entry_base_pct", 0.0), ("trend_tight_gain_pct", 0.0), ("trend_tight_pullback_pct", 1.5), ("opp_bs", 0)):
    CFG.setdefault(k_, v_)
FEE = 0.0004


def step(state, price, now, order_fn, log, notify=None):
    """한 번의 점검. state를 직접 갱신하고 체결 이벤트 목록을 돌려준다. now = 초 단위 시각."""
    ev = []
    L = CFG["side"] == "long"
    word_in, word_out = ("매수", "매도") if L else ("숏 진입", "환매")
    nst = 1 + int(CFG["ladder_extra"]); u = float(CFG["ladder_usdt"]); stops = list(CFG.get("stops") or [])
    cap = float(CFG["max_total_usdt"])
    keep_eff = float(CFG.get("keep_usdt") or 0)                  # 반대쪽 사다리가 relax_opp_stage단계 이상이면 최소 보유를 relax_keep_usdt로 낮춰 이익을 청산한다
    if CFG.get("relax_opp_stage") and int(CFG.get("opp_bs") or 0) >= int(CFG["relax_opp_stage"]):
        keep_eff = float(CFG.get("relax_keep_usdt") or 0)
    st = state
    for k_, v_ in (("qty", 0.0), ("avg", 0.0), ("hold", 0.0), ("bs", 0), ("ss", 0), ("stopped", 0), ("full", False), ("q_at_full", 0.0),
                   ("avg_at_full", 0.0), ("want_re", False), ("cum_pnl", 0.0), ("cum_fees", 0.0)):
        st.setdefault(k_, v_)
    if 0 < st["qty"] * price < 0.05:                       # 부동소수점 잔량 정리
        st.update(qty=0.0, hold=0.0, avg=0.0)
    # 시세 기록(직전 N분 평균·급변 감지용)
    hist = [h for h in st.get("ref_hist", []) if now - h[0] <= max(3700, int(CFG.get("entry_ref_min") or 0) * 60 + 300)]
    prev = [h for h in hist if now - h[0] <= CFG["ref_min"] * 60 + 20]
    hist_old = list(hist)
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

    if not st.get("started") and st["qty"] <= 1e-12 and CFG.get("first_entry_usdt"):      # 첫 진입: 롱·숏 모두 시작하자마자 같이 first_entry_usdt 진입 (2026-10-27 사용자 지시)
        if do_in(float(CFG["first_entry_usdt"]), "첫 진입"):
            st.update(started=True, bs=0, ss=0, full=False, stopped=0)
        return ev
    # ⓪ 추세 보유 모드(2026-10-27): 이익 쪽이 추세로 판정되면 청산 사다리를 trend_sell_stages단계까지만 쓰고 나머지는 들고 가다가,
    #    이상 징후(최고 이익 지점 대비 되돌림 / 1분 급반전 / 이익 소멸) 때 전량 정리한다.
    if CFG.get("trend_hold"):
        if st["qty"] <= 1e-12:
            for k_ in ("trend", "t_peak"): st.pop(k_, None)
        else:
            sgn = 1 if L else -1
            prof = (price / st["avg"] - 1) * sgn * 100
            st["t_peak"] = max(st.get("t_peak", price), price) if L else min(st.get("t_peak", price), price)
            if not st.get("trend"):
                w30 = [p for t, p in hist if now - t <= CFG["trend_window_min"] * 60 + 30]
                fav = ((max(w30) / st["avg"] - 1) if L else (1 - min(w30) / st["avg"])) * 100
                if fav >= CFG["trend_gain_pct"] and prof > 0 and int(CFG.get("opp_bs") or 0) >= int(CFG["trend_opp_stage"]):
                    st["trend"] = True; st["t_peak"] = price
                    log(f"  🚀 추세 보유 모드 ON — 이익 {prof:+.1f}% (최근 {CFG['trend_window_min']}분 최대 {fav:+.1f}%), 반대쪽 사다리 {int(CFG.get('opp_bs') or 0)}단계")
            else:
                pk = st["t_peak"]
                pull = ((pk - price) / pk if L else (price - pk) / pk) * 100
                pb = CFG["trend_pullback_pct"]
                if CFG.get("trend_tight_gain_pct") and ((pk / st["avg"] - 1) if L else (1 - pk / st["avg"])) * 100 >= CFG["trend_tight_gain_pct"]:
                    pb = CFG["trend_tight_pullback_pct"]          # 최고 이익이 trend_tight_gain_pct%를 넘으면 되돌림 기준을 좁혀 이익을 지킨다(2단계 트레일링)
                w1 = [p for t, p in hist if now - t <= 90]
                flip = ((max(w1) - price) / max(w1) if L else (price - min(w1)) / min(w1)) * 100
                why = (f"고점 대비 {pull:.1f}% 되돌림" if pull >= pb else f"1분 {flip:.1f}% 급반전" if flip >= CFG["trend_flip_pct"] else "이익 소멸" if prof <= 0 else "")
                if why:
                    if do_out(st["qty"], f"추세 이탈 정리 ({why})"):
                        st.update(qty=0.0, hold=0.0, avg=0.0, bs=0, ss=0, stopped=0, full=False, want_re=bool(CFG["reinvest_usdt"]))
                        for k_ in ("trend", "t_peak"): st.pop(k_, None)
                    return ev
    # ⓪-1 노출 축소: 보유가 derisk_hold_usdt 이상이면 평균단가 수익선을 기다리지 않고, 직전 5분 평균가 대비 롱 +derisk_pct% / 숏 −derisk_pct% 움직일 때마다
    #      derisk_usdt씩 줄인다(손실 구간이어도 실행, 2026-10-27 사용자 제안).
    if CFG.get("derisk_hold_usdt") and st["qty"] > 1e-12 and st["hold"] >= float(CFG["derisk_hold_usdt"]) and len(prev) >= max(3, CFG["ref_min"] - 1):
        mv = (price / ref - 1) * 100 * (1 if L else -1)
        uw = (1 - price / st["avg"]) * 100 * (1 if L else -1)           # 평균단가 대비 불리한 정도(%)
        if mv >= float(CFG.get("derisk_pct") or 1.0) and uw >= float(CFG.get("derisk_underwater_pct") or 0):
            qq = min(st["qty"], float(CFG["derisk_usdt"]) / price)
            if do_out(qq, f"노출 축소 (5분 평균 대비 {'+' if L else '-'}{mv:.1f}%)"):
                st["bs"] = max(0, st["bs"] - 1); st["ss"] = 0; st["full"] = False; st["stopped"] = 0
                if CFG.get("derisk_block_min"): st["derisk_block_until"] = now + CFG["derisk_block_min"] * 60      # 줄인 뒤 같은 방향 신규 진입을 잠시 막는다
                if st["qty"] <= 1e-12:
                    st.update(qty=0.0, hold=0.0, avg=0.0, bs=0, ss=0)
            return ev
    # ① 수익실현으로 비었으면 재투입
    if st["want_re"] and st["qty"] <= 1e-12 and not moved and (not CFG["reinvest_cond"] or ((price < ref) if L else (price > ref))):
        if do_in(float(CFG["reinvest_usdt"]), "재투입"):
            st.update(bs=0, ss=0, want_re=False, full=False, stopped=0, n_re=st.get("n_re", 0) + 1)
        return ev
    base = st["avg"] if st["qty"] > 1e-12 else ref
    erm = int(CFG.get("entry_ref_min") or 0)         # >0이면 진입 사다리 기준을 평균단가가 아니라 직전 erm분 평균 시세로 쓴다(2026-10-27 모의)
    ent_base_ok = True
    if erm:
        eh = [p for t, p in hist_old if now - t <= erm * 60 + 20]
        ent_base_ok = len(eh) >= max(3, int(erm * 0.8))
        if ent_base_ok: base_entry = sum(eh) / len(eh)
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
    eb = float(CFG["entry_base_pct"]) if CFG.get("entry_base_pct") else float(CFG["ladder_base_pct"])       # 진입 사다리 시작(10/27: ±1%), 청산 사다리는 ladder_base_pct(±2%)
    eref = base_entry if (erm and ent_base_ok) else base
    mixm = int(CFG.get("entry_ref_mix") or 0)        # 1=보수적(보유 중엔 평균단가와 N분 평균 중 롱은 낮은 쪽/숏은 높은 쪽), 2=공격적(반대)
    if mixm and erm and ent_base_ok and st["qty"] > 1e-12:
        eref = (min(st["avg"], base_entry) if L else max(st["avg"], base_entry)) if mixm == 1 else (max(st["avg"], base_entry) if L else min(st["avg"], base_entry))
    ent_lv = eref * (1 - (eb + CFG["ladder_step_pct"] * st["bs"]) / 100) if L else eref * (1 + (eb + CFG["ladder_step_pct"] * st["bs"]) / 100)
    ext_lv = base * (1 + (CFG["ladder_base_pct"] + CFG["ladder_step_pct"] * st["ss"]) / 100) if L else base * (1 - (CFG["ladder_base_pct"] + CFG["ladder_step_pct"] * st["ss"]) / 100)
    can_in = ((price <= ent_lv) if L else (price >= ent_lv)) and ent_base_ok
    can_out = (price >= ext_lv) if L else (price <= ext_lv)
    dr = float(CFG.get("deep_rebound_pct") or 0)
    if dr and can_in and st["qty"] > 1e-12 and st["bs"] + 1 >= int(CFG.get("deep_from_stage", 3)):      # 깊은 단계는 직전 crash_window_min분 저점(롱)/고점(숏)에서 dr% 되돌린 뒤에만 진입
        if not ((price >= min(win) * (1 + dr / 100)) if L else (price <= max(win) * (1 - dr / 100))):
            can_in = False
    if can_in and not moved and st["bs"] < nst and st["hold"] + u <= cap and st["stopped"] == 0 and now >= st.get("derisk_block_until", 0):
        if do_in(u, f"사다리 {st['bs'] + 1}단계"):
            st["bs"] += 1; st["ss"] = 0
            if st["bs"] >= nst or st["hold"] + u > cap:
                st.update(full=True, q_at_full=st["qty"], avg_at_full=st["avg"])
                log(f"  📌 사다리 완료 — 평균단가 {st['avg']:,.6g}, 손절선 " + " / ".join(f"{(st['avg'] * (1 - s / 100) if L else st['avg'] * (1 + s / 100)):,.6g}" for s in stops))
    elif can_out and st["qty"] > 1e-12 and st["ss"] < nst and ((price >= st["avg"]) if L else (price <= st["avg"])) and not (st.get("trend") and st["ss"] >= int(CFG["trend_sell_stages"])) and st["hold"] > keep_eff + 1e-9 and \
            (st["qty"] * (st["hold"] - keep_eff) / st["hold"]) * price >= 6:
        if CFG.get("sell_pct"):        # 보유물량의 sell_pct% 매도: start=이번 청산 사다리 시작 때 물량 기준(5단계×20%=전량), current=그때그때 남은 물량 기준
            if st["ss"] == 0 or not st.get("ss_base"): st["ss_base"] = st["qty"]
            qq = (st["ss_base"] if CFG.get("sell_pct_of", "start") == "start" else st["qty"]) * CFG["sell_pct"] / 100
            if st["ss"] >= nst - 1 or qq * price < 6 or st["qty"] - qq < qq * 0.5: qq = st["qty"]
        else:
            qq = u / price
            if st["qty"] - qq < qq * 0.5: qq = st["qty"]
        keep = keep_eff
        if keep > 0:        # 보유 금액이 keep_usdt 이하면 청산하지 않고, 초과분만 청산한다 (2026-10-27 사용자 지시)
            qq = min(qq, st["qty"] * (st["hold"] - keep) / st["hold"])
        if do_out(qq, f"청산 {st['ss'] + 1}단계"):
            st["ss"] += 1; st["bs"] = 0; st["stopped"] = 0; st["full"] = False
            if st["qty"] <= 1e-12:
                st.update(qty=0.0, hold=0.0, avg=0.0, bs=0, ss=0, want_re=bool(CFG["reinvest_usdt"]))
                log("  ✅ 수익실현으로 전량 정리 — 조건이 맞으면 바로 재투입")
    elif not ev:
        pos = f"보유 {st['qty']:.4f}개 평균 {st['avg']:,.6g} ({(price / st['avg'] - 1) * 100 * (1 if L else -1):+.2f}%) {st['hold']:.0f}USDT 진입단계 {st['bs']} 청산단계 {st['ss']}" if st["qty"] > 1e-12 else \
              ("재투입 대기" if st["want_re"] else "보유 없음")
        if st["qty"] > 1e-12 and can_out and ((price >= st["avg"]) if L else (price <= st["avg"])) and st["hold"] <= keep_eff + 1e-9:
            pos += f" | 청산선 도달했지만 보유 {st['hold']:.0f}USDT ≤ 유지 {keep_eff:.0f}USDT라 청산 보류"
        log(f"  대기 — {pos} | 기준 {base:,.6g} 진입선 {ent_lv:,.6g} 청산선 {ext_lv:,.6g}" + (" | 급변 중(신규 진입 중단)" if moved else ""))
    return ev


def run_check(send=True):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{'=' * 56}\n  코인 평균단가 사다리 봇 — {CFG['symbol']} {'롱' if CFG['side'] == 'long' else '숏'} — {now}{' [모의]' if CFG.get('paper') else ''}\n{'=' * 56}")
    state = g.load_state(); price = g.fetch_price(CFG["symbol"])
    if price <= 0: print("  ❌ 시세 조회 실패"); return
    print(f"  현재가: {price:,.6g} USDT")
    if CFG.get("opp_state"):        # 추세 보유 모드가 반대쪽 봇의 사다리 단계를 본다 (읽기 전용)
        try: CFG["opp_bs"] = int(json.load(open(CFG["opp_state"], encoding="utf-8")).get("bs", 0))
        except Exception: CFG["opp_bs"] = 0
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
