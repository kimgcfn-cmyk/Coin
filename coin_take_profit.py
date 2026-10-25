"""
코인 수익률 조건 부분 익절 — 화면상 수익률이 N%에 닿으면 포지션 총량의 M%를 시장가로 판다 (2026-10-25)
================================================================================
거래소 화면(앱)에 표시되는 수익률(= 미실현손익 / 증거금, 레버리지가 높으면 가격 변동의 레버리지배)을 기준으로 한다.
봇 물량이든 수동 물량이든 구분하지 않고 해당 종목 롱 포지션 전체의 M%를 reduce-only 시장가로 판다(숏이 열리지 않는다).

사용:
  python coin_take_profit.py --symbol T --roe 5 --sell-pct 50            # 모의: 지금 조건이 맞는지만 보고 주문은 내지 않음
  python coin_take_profit.py --symbol T --roe 5 --sell-pct 50 --live     # 실전 (cron에는 --live)
  --repeat  : 한 번 판 뒤에도 다시 무장(조건을 다시 채우면 또 판다). 기본은 1회만 하고 멈춘다(상태 파일 coin_tp_<종목>_state.json).
  --reset   : 상태 파일을 지워 다시 무장
cron 예시(1분마다): * * * * * cd /home/ec2-user/trading-bot && flock -n /tmp/coin_tp_t.lock venv/bin/python coin_take_profit.py --symbol T --roe 5 --sell-pct 50 --live >> logs/coin_tp_t.log 2>&1
주의: 수동 물량도 함께 팔리는 것이 이 도구의 목적이다(사용자 지시). 그리드 봇이 같은 종목을 돌고 있으면 봇 장부 수량이 실제보다 많아질 수 있고,
      거래소에 닫을 롱이 없을 때는 봇이 장부를 0으로 정정한다.
"""
import sys, os, json, time
from datetime import datetime, timezone
import coin_grid_bot as g

def _norm(x):
    return g._normalize_symbol(x)

def main():
    args = sys.argv[1:]
    def val(flag, default=None):
        return args[args.index(flag) + 1] if flag in args and args.index(flag) + 1 < len(args) else default
    sym_in = val("--symbol")
    if not sym_in:
        print("  ❌ 종목을 지정하세요: --symbol T --roe 5 --sell-pct 50 [--live] [--repeat]"); sys.exit(1)
    symbol = _norm(sym_in); base = symbol.split("/")[0].lower()
    roe_target = float(val("--roe", 5)); sell_pct = float(val("--sell-pct", 50)); live = "--live" in args; repeat = "--repeat" in args
    state_file = f"coin_tp_{base}_state.json"
    if "--reset" in args:
        if os.path.exists(state_file): os.remove(state_file)
        print("  ✅ 상태 초기화(다시 무장)"); return
    st = {}
    if os.path.exists(state_file):
        try: st = json.load(open(state_file, encoding="utf-8"))
        except Exception: st = {}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n=== 수익률 익절 감시 — {symbol} — {now} (목표 ROE {roe_target:g}%, 총량의 {sell_pct:g}% 매도, {'실전' if live else '모의'}{', 반복' if repeat else ', 1회'}) ===")
    if st.get("done") and not repeat:
        print(f"  이미 실행됨({st.get('fired_at')}) — 더 하지 않음. 다시 무장하려면 --reset"); return
    g.CFG["symbol"] = symbol
    ex = g.get_exchange()
    if not ex:
        print("  ❌ 거래소 연결 실패"); return
    try:
        pos = [p for p in ex.fetch_positions([symbol], params=g.UTA) if float(p["contracts"] or 0) > 0 and p["side"] == "long"]
    except Exception as e:
        print(f"  ⚠️ 포지션 조회 실패: {e}"); return
    if not pos:
        print("  롱 포지션 없음"); return
    p = pos[0]; info = p.get("info") or {}
    total = float(p["contracts"]); entry = float(p["entryPrice"]); upl = float(p["unrealizedPnl"] or 0); lev = float(p["leverage"] or 1)
    notional = float(p["notional"] or 0); margin = float(p.get("initialMargin") or (notional / lev if lev else 0)) or 1.0
    roe = float(info["profitRate"]) * 100 if info.get("profitRate") not in (None, "") else upl / margin * 100
    price = g.fetch_price(symbol)
    tgt_price = entry * (1 + roe_target / 100 / lev)
    print(f"  롱 {total:.0f}개 평균가 {entry:.6g} 현재가 {price:.6g} 레버리지 {lev:g}배 | 화면식 수익률 {roe:+.2f}% (목표 {roe_target:g}% ≈ 가격 {tgt_price:.6g})")
    if roe < roe_target:
        print("  조건 미충족 — 대기"); return
    ex.load_markets()
    mk = ex.market(symbol); step = float(mk["precision"]["amount"]); mn = float(mk["limits"]["amount"]["min"] or step)
    qty = float(ex.amount_to_precision(symbol, int(total * sell_pct / 100 / step) * step))
    if qty < mn:
        print(f"  ❌ 매도 수량 {qty}가 최소 단위 {mn} 미만"); return
    print(f"  🎯 조건 충족 — {total:.0f}개 중 {qty:.0f}개({sell_pct:g}%, 약 {qty*price:,.1f} USDT어치) 매도 {'실행' if live else '(모의: 주문 안 냄)'}")
    if not live:
        return
    try:
        o = ex.create_market_order(symbol, "sell", qty, params={"hedged": True, "reduceOnly": True, **g.UTA})
        time.sleep(1.2)
        f = g.fill_from_order(ex.fetch_order(o["id"], symbol, g.UTA)) if o.get("id") else {}
    except Exception as e:
        print(f"  ❌ 주문 실패: {e}")
        if not st.get("fail_notified"):
            g.send_discord(f"❌ <b>{symbol} 수익률 익절 주문 실패</b>\n{e}\n다음 점검에서 재시도됩니다")
            st["fail_notified"] = True; json.dump(st, open(state_file, "w", encoding="utf-8"), ensure_ascii=False)
        return
    fq = f.get("filled") or qty; fp = f.get("average") or price; fc = f.get("cost") or fq * fp
    realized = fq * (fp - entry) - (f.get("fee_usdt") or 0)
    print(f"  ✅ 체결 완료 (주문ID {o.get('id')}, 금액 {fc:,.2f}USDT, 체결가 {fp:.6g}, 수량 {fq:.0f})")
    st.update({"done": not repeat, "fired_at": now, "qty": fq, "price": fp, "roe": roe, "order_id": o.get("id")}); st.pop("fail_notified", None)
    json.dump(st, open(state_file, "w", encoding="utf-8"), ensure_ascii=False)
    g.send_discord(f"🎯 <b>{symbol} 수익률 익절</b> (화면 수익률 {roe:+.1f}% ≥ {roe_target:g}%)\n📦 총 {total:.0f}개 중 {sell_pct:g}% = {fq:.0f}개 매도 (봇·수동 물량 포함)\n"
                   f"💵 체결가: {fp:.6g} USDT / 금액 {fc:,.2f} USDT\n💸 평균가 대비 실현(수수료 반영, 추정): {realized:+,.2f} USDT\n남은 롱 약 {total - fq:.0f}개"
                   + ("" if repeat else "\n(1회 실행 후 멈춤 — 다시 무장하려면 --reset)"))

if __name__ == "__main__":
    main()
