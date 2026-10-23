"""
관찰용 스냅샷 — IRYS/BIRB/BTC 그리드 봇의 상태·계좌 평가액을 30분마다 한 줄(JSON)로 기록한다 (2026-10-24 보고서용 임시).
logs/observe.jsonl 에 추가만 하며, 주문은 절대 내지 않는다(조회 전용).
"""
import json, os, time
from datetime import datetime, timezone
import ccxt
import coin_grid_btc_monitor as btc
import coin_grid_irys_monitor as irys
import coin_grid_birb_monitor as birb

BOTS = {"IRYS": irys, "BIRB": birb, "BTC": btc}
ex = btc.get_exchange()
b = ex.fetch_balance(params=btc.UTA).get("USDT", {})
snap = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "equity": b.get("total"), "free": b.get("free"), "bots": {}, "positions": []}
for p in ex.fetch_positions(params=btc.UTA):
    if float(p["contracts"] or 0) > 0:
        snap["positions"].append({"symbol": p["symbol"], "side": p["side"], "qty": float(p["contracts"]),
                                  "entry": float(p["entryPrice"] or 0), "upnl": float(p["unrealizedPnl"] or 0)})
for name, m in BOTS.items():
    st = m.load_state()
    snap["bots"][name] = {"price": m.fetch_price(m.CFG["symbol"]),
                          **{k: st.get(k) for k in ("reference_price", "peak_price", "qty", "holdings_usdt", "avg_cost", "cum_pnl", "cum_fees", "halted")}}
os.makedirs("logs", exist_ok=True)
with open("logs/observe.jsonl", "a", encoding="utf-8") as f:
    f.write(json.dumps(snap, ensure_ascii=False) + "\n")
print(json.dumps(snap, ensure_ascii=False))
