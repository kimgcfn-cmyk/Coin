"""
급등 후 하락(펌프&덤프 되돌림) 이벤트 스캐너 + 모의 러너 (2026-10-27)
=================================================================================
배경: Bitget 선물 356종목의 최근 약 12일 1시간봉에서 '24시간 안에 저→고 +30% 이상 오른 뒤 고점 대비 4% 내려온' 이벤트 56건을 1분봉으로 24시간씩 재생 모의했다
      (backtests/pump_events.py, event_strat 결과는 봇운용지침.md 참고). 단순 손절 숏은 기대값이 음수였고, 합동전략 사다리 구조(넓은 사다리 3/6/3)를 쓴 변형만 나았다.
역할:
  python coin_pumpfade.py scan [--notify]  # (cron 5분) 급등 후 하락 신호를 찾아 coin_pumpfade_events.json 에 기록한다(--notify 를 줄 때만 디스코드 알림)
  python coin_pumpfade.py run    # (cron 1분) 활성 이벤트마다 모의(paper) 봇을 24시간 돌리고 결과를 같은 파일에 기록한다. 실제 주문 없음
  python coin_pumpfade.py status # 이벤트 현황 출력
이벤트마다 모의 변형 두 개를 돌린다: W(숏 단독, 넓은 사다리) / PW(롱+숏 합동, 넓은 사다리). 둘 다 한도 300, 3/6/3 사다리, 60분 평균 기준 진입, 5분 간격.
동시에 활성인 이벤트는 최대 MAX_ACTIVE개. 이 파일은 신호·모의 기록만 하며 실전 주문을 내지 않는다.
주의: 과거 결과일 뿐 투자 조언이 아니다.
"""
import sys, os, json, time, requests
import datetime as dt
import coin_avg_ladder_bot as b
import coin_grid_bot as g

EVENTS_FILE = "coin_pumpfade_events.json"
STATE_DIR = "pf_state"
BASE = "https://api.bitget.com/api/v2/mix/market"
MAX_ACTIVE = 3
PUMP = 0.30            # 24시간 안에 저→고 상승률
ROLL = 0.04            # 고점 대비 하락 확인
MAX_DROP = 0.12        # 고점 대비 이 이상 이미 내려왔으면 신호로 보지 않음(되돌림 초기만)
MAX_AGE_BARS = 8       # 고점이 이 시간봉 수보다 오래전이면 신호로 보지 않음
MIN_VOL = 3_000_000    # 24시간 거래대금(USDT) 하한 (스캔 후보용)
MIN_CHG = 0.20         # 티커 24시간 변동률 하한(후보 압축용)
HORIZON_H = 24
KST = dt.timezone(dt.timedelta(hours=9))
BASE_CFG = dict(b.CFG)
WIDE = dict(max_total_usdt=300, ladder_usdt=100, first_entry_usdt=100, reinvest_usdt=100, derisk_usdt=100, keep_usdt=100.0, derisk_hold_usdt=150.0,
            entry_base_pct=3.0, ladder_base_pct=6.0, ladder_step_pct=3.0, entry_ref_min=60, entry_min_gap_min=5, paper=True, paper_slip=0.0015, leverage=1,
            reinvest_cond=True, stops=[8.0, 10.0, 12.0], rest_min=120, ref_min=5, crash_pct=8.0, crash_window_min=5, relax_opp_stage=2, relax_keep_usdt=100.0,
            trend_hold=True, trend_pullback_pct=2.5, trend_window_min=30, trend_gain_pct=3.0, trend_opp_stage=3, trend_sell_stages=1, trend_flip_pct=3.0,
            derisk_pct=1.0, derisk_underwater_pct=3.0, derisk_block_min=30, ladder_extra=4)
VARIANTS = {"W": ("short",), "PW": ("short", "long")}


def load_events():
    try: return json.load(open(EVENTS_FILE, encoding="utf-8"))
    except Exception: return {"events": []}


def save_events(d):
    tmp = EVENTS_FILE + ".tmp"; json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False); os.replace(tmp, EVENTS_FILE)


def hourly(sym, n=30):
    r = requests.get(f"{BASE}/candles", params={"symbol": sym, "productType": "USDT-FUTURES", "granularity": "1H", "limit": str(n)}, timeout=15).json().get("data") or []
    return sorted([(int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4])) for x in r])


def detect(rows, price):
    """최근 24시간 시간봉과 현재가로 신호를 판정. 반환: dict(pump, hi) 또는 None."""
    if len(rows) < 26: return None
    w = rows[-25:]
    hi_idx = max(range(len(w)), key=lambda k: w[k][2]); hi = w[hi_idx][2]
    lo = min(x[3] for x in w[:hi_idx + 1])
    if hi / lo - 1 < PUMP or price > hi * (1 - ROLL): return None
    if price < hi * (1 - MAX_DROP) or (len(w) - 1 - hi_idx) > MAX_AGE_BARS: return None      # 이미 많이 떨어졌거나 고점이 오래전이면 '되돌림 시작' 신호가 아님
    return dict(pump=round((hi / lo - 1) * 100, 1), hi=hi, lo=lo)


def scan():
    d = load_events(); now = time.time(); evs = d["events"]
    tk = requests.get(f"{BASE}/tickers", params={"productType": "USDT-FUTURES"}, timeout=20).json()["data"]
    active = sum(1 for e in evs if e["status"] == "active")
    new = []
    for t in tk:
        try:
            if float(t.get("change24h") or 0) < MIN_CHG or float(t.get("usdtVolume") or 0) < MIN_VOL: continue
        except Exception: continue
        sym = t["symbol"]; price = float(t["lastPr"])
        if any(e["sym"] == sym and now - e["ts"] < 24 * 3600 for e in evs): continue
        sig = detect(hourly(sym), price)
        if not sig: continue
        e = dict(id=f"{sym}_{dt.datetime.now(KST):%m%d%H%M}", sym=sym, ts=now, entry=price, pump=sig["pump"], hi=sig["hi"], vol24=float(t.get("usdtVolume") or 0),
                 chg24=float(t.get("change24h") or 0) * 100, fund=float(t.get("fundingRate") or 0) * 100, status="active" if active + len(new) < MAX_ACTIVE else "skipped",
                 note="" if active + len(new) < MAX_ACTIVE else "동시 활성 한도 초과로 모의 생략", curve=[], res={})
        new.append(e)
    evs.extend(new); save_events(d)
    for e in new:
        msg = (f"🔻 <b>급등 후 하락 신호 — {e['sym']}</b>\n24시간 저→고 +{e['pump']:.0f}% 후 고점({e['hi']:.6g})에서 {ROLL*100:.0f}% 이상 내려옴 · 현재가 {e['entry']:.6g} · 24h {e['chg24']:+.0f}% · 거래대금 {e['vol24']/1e6:.1f}M · 펀딩 {e['fund']:+.3f}%\n"
               f"{'모의 봇(W 숏 단독 / PW 롱숏 합동, 한도 300)을 24시간 가동합니다(실주문 없음).' if e['status'] == 'active' else '모의 생략: ' + e['note']}\n※ 알림·모의 전용. 과거 결과일 뿐 투자 조언이 아닙니다.")
        if "--notify" in sys.argv:          # 디스코드 알림은 --notify 를 줬을 때만(기본은 기록·모의만)
            try: g.send_discord(msg)
            except Exception: pass
        print(f"[{dt.datetime.now(KST):%H:%M}] 신호 {e['sym']} pump {e['pump']}% 상태 {e['status']}")
    if not new: print(f"[{dt.datetime.now(KST):%H:%M}] 신호 없음 (활성 {active}건)")


def _cfg_for(e, variant, side, sides, state_path):
    b.CFG.clear(); b.CFG.update(BASE_CFG); b.CFG.update(WIDE)
    b.CFG.update(symbol=g._normalize_symbol(e["sym"][:-4]), side=side, state_file=state_path, paper=True, auto_trade=False)
    return b.CFG


def run():
    d = load_events(); now = time.time(); os.makedirs(STATE_DIR, exist_ok=True); changed = False
    for e in d["events"]:
        if e["status"] != "active": continue
        age_h = (now - e["ts"]) / 3600
        sym = e["sym"]; price = None
        paths = {(v, s): os.path.join(STATE_DIR, f"{e['id']}_{v}_{s}.json") for v, sides in VARIANTS.items() for s in sides}
        # 시세
        _cfg_for(e, "W", "short", ("short",), paths[("W", "short")])
        price = g.fetch_price(b.CFG["symbol"])
        if not price or price <= 0: continue
        parts = {}
        for variant, sides in VARIANTS.items():
            for side in sides:
                st = json.load(open(paths[(variant, side)], encoding="utf-8")) if os.path.exists(paths[(variant, side)]) else {}
                if age_h < HORIZON_H:
                    _cfg_for(e, variant, side, sides, paths[(variant, side)])
                    opp = "long" if side == "short" else "short"
                    if opp in sides:
                        try: b.CFG["opp_bs"] = int(json.load(open(paths[(variant, opp)], encoding="utf-8")).get("bs", 0))
                        except Exception: b.CFG["opp_bs"] = 0
                    else:
                        b.CFG.update(opp_state="", relax_opp_stage=0, trend_hold=False)
                    order_fn = lambda sd, usdt, p, ro, qty: g.place_order(b.CFG["symbol"], sd, usdt, p, reduce_only=ro, dry_run=False, qty_override=qty)
                    b.step(st, price, now, order_fn, lambda *a: None)
                    json.dump(st, open(paths[(variant, side)], "w", encoding="utf-8"), ensure_ascii=False)
                q = st.get("qty", 0.0) or 0.0; avg = st.get("avg", 0.0) or 0.0
                un = q * ((avg - price) if side == "short" else (price - avg)) - q * price * 0.0004 if q > 0 else 0.0
                parts[(variant, side)] = dict(cum=st.get("cum_pnl", 0.0) or 0.0, un=un, hold=st.get("hold", 0.0) or 0.0, outs=st.get("n_out", 0), stops=st.get("n_stop", 0))
        eq = {v: sum(parts[(v, s)]["cum"] + parts[(v, s)]["un"] for s in sides) for v, sides in VARIANTS.items()}
        e["curve"].append([int(now), round(eq["W"], 3), round(eq["PW"], 3), price])
        changed = True
        if age_h >= HORIZON_H:
            res = {}
            for v, sides in VARIANTS.items():
                cap = 60.0 * len(sides); curve = [c[1 if v == "W" else 2] for c in e["curve"]]
                peak = 0.0; mdd = 0.0
                for x in curve: peak = max(peak, x); mdd = max(mdd, peak - x)
                res[v] = dict(pnl=round(eq[v], 2), pct=round(eq[v] / cap * 100, 1), mdd=round(mdd, 2), mdd_pct=round(mdd / cap * 100, 1), cap=cap,
                              outs=sum(parts[(v, s)]["outs"] for s in sides), stops=sum(parts[(v, s)]["stops"] for s in sides))
            e["res"] = res; e["status"] = "done"; e["end_price"] = price; e["end_ts"] = now
            print(f"[{dt.datetime.now(KST):%H:%M}] 종료 {e['id']}: W {res['W']['pct']:+.1f}%/MDD {res['W']['mdd_pct']:.1f}% · PW {res['PW']['pct']:+.1f}%/MDD {res['PW']['mdd_pct']:.1f}%")
        else:
            print(f"[{dt.datetime.now(KST):%H:%M}] {e['id']} {age_h:.1f}h 현재가 {price:,.6g} W {eq['W']:+.2f} PW {eq['PW']:+.2f}")
    if changed: save_events(d)


def status():
    d = load_events()
    for e in d["events"]:
        r = e.get("res") or {}
        print(e["id"], e["status"], f"pump {e['pump']}%", f"W {r.get('W', {}).get('pct', '-')}", f"PW {r.get('PW', {}).get('pct', '-')}")
    print(f"총 {len(d['events'])}건")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    {"scan": scan, "run": run, "status": status}[cmd]()
