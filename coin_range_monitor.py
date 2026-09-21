"""
코인 범위매매 실전 자동매매 — BTC 전용 (신호 감지 + 실제 주문 실행)
============================================================
백테스트로 검증된 범위매매 전략(PF 1.81, 전 국면 수익)을
실전에서 자동 매매한다. 신호 발생 시 Bitget에 실제 주문을 넣고,
결과를 디스코드로 알린다.

[2026-08-24 변경]
  - SOL 제거, BTC만 운용
  - 알림 전용 → 실전 자동매매로 전환 (Bitget API 실주문)
  - 계좌가 헷지모드(hedge_mode)라 주문 시 hedged=True 파라미터 필수
    (2026-08-21 coin_grid_monitor.py에서 ccxt 소스코드 직접 확인해
     찾아낸 방식과 동일하게 적용)

[.env 설정 — 이미 있다면 재사용]
  DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
  BITGET_API_KEY=...
  BITGET_API_SECRET=...
  BITGET_PASSPHRASE=...

[검증된 파라미터 — BTC]
  RSI≤30 매수 / +4% 익절 / -4% 손절 (레버리지 3배)

[동작]
  실행(cron) 시:
    1) 매수: RSI 조건 충족 시 1회분(order_usdt) 매수 → 진입가/수량을 하나의 lot으로 기록
       (보유 lot이 있어도 추가 매수 가능: max_lots 이내, 최저 진입가보다 buy_gap_pct 이상 낮을 때)
    2) 매도: lot별로 자기 매수가 기준 익절/손절 도달 확인 → 해당 lot 수량만 매도
  → 주문 성공을 확인한 뒤에만 상태를 기록한다 (실패 시 유령 포지션 방지)

[안전장치]
  - auto_trade 기본값 False (알림만, 실제 주문 없음) — 반드시 --check로
    충분히 확인 후 True로 전환할 것
  - 매수 실패 시 보유기록 남기지 않음, 매도 실패 시 보유기록 유지(재시도)

실행:
  python coin_range_monitor.py          # 1회 점검 (cron용)
  python coin_range_monitor.py --check  # 상태만 출력 (주문 없음, 저장도 안 함)
  python coin_range_monitor.py --status # 현재 보유 현황
  python coin_range_monitor.py --reset  # 보유기록 초기화 (실제 보유는 그대로)
"""

import sys, json, os, re, time
from datetime import datetime, timezone

try:
    import ccxt
    HAS_CCXT = True
except ImportError:
    HAS_CCXT = False

# ── config.py에서 디스코드 웹훅 + Bitget API 키 로드 ──
try:
    import config as _cfg
    def _cget(key):
        return _cfg._get(key) if hasattr(_cfg, "_get") else getattr(_cfg, key, "")
    DISCORD_WEBHOOK_URL = _cget("DISCORD_WEBHOOK_URL")
    BITGET_API_KEY      = _cget("BITGET_API_KEY")
    BITGET_API_SECRET   = _cget("BITGET_API_SECRET") or _cget("BITGET_SECRET_KEY")
    BITGET_PASSPHRASE   = _cget("BITGET_PASSPHRASE") or _cget("BITGET_PASSWORD")
except ImportError:
    print("⚠️  config.py 없음 — .env 설정 확인 필요")
    DISCORD_WEBHOOK_URL = BITGET_API_KEY = BITGET_API_SECRET = BITGET_PASSPHRASE = ""

import requests

try:
    import trade_ledger
    HAS_LEDGER = True
except ImportError:
    HAS_LEDGER = False

def _record(side: str, time_str: str, price: float, note: str = ""):
    """체결 기록 실패가 실거래 로직에 영향 주지 않도록 별도 방어."""
    if not HAS_LEDGER:
        return
    try:
        trade_ledger.append_trade("BTC", side, time_str, price, note)
    except Exception as e:
        print(f"  ⚠️ 거래 기록(엑셀) 실패: {e}")

# ══════════════════════════════════════════════════════
#  설정 — 백테스트 검증 최적 파라미터
# ══════════════════════════════════════════════════════
COINS = {
    "BTC/USDT": {
        "rsi_buy"      : 50,      # RSI 50 이하 매수 (2026-09-12: 40→50, 매매 빈도 증대. 백테스트 값 아님)
        "target_pct"   : 5.0,     # +5% 익절 (2026-09-05: 3%→5%, 백테스트로 PF 개선 확인 후 변경)
        "stop_pct"     : 3.0,     # -3% 손절 (유지)
        "label"        : "BTC",
        # ⚠️ 시세(RSI)는 Binance에서 조회(콜론 없는 표기)하지만,
        # 실제 주문은 Bitget 선물(USDT-M Perpetual)에 넣어야 하므로
        # 정확한 실행용 심볼을 별도로 지정 (2026-08-24 확인된 필수 사항 —
        # 콜론 없이 주문하면 "Insufficient balance"로 오해할 오류가 남)
        "exec_symbol"  : "BTC/USDT:USDT",
    },
}

CFG = {
    "timeframe"    : "15m",      # 2026-08-30: 4h→15m, 매매 빈도 증대 목적 (백테스트 값 아님, 주의)
    "rsi_period"   : 14,
    "ma_period"    : 50,         # 하락장 필터용
    "down_guard"   : 8.0,        # MA -8% 이탈 시 매수 보류
    "leverage"     : 3,          # 거래소에 실제 설정할 레버리지
    "order_usdt"   : 10.0,       # ⚠️ 1회 매매 증거금(USDT) — ×3배 레버리지 = 노출 30USDT (2026-09-19: 30→10, 리스크 축소)
    "auto_trade"   : True,       # ✅ 실전 자동매매 활성화 (2026-08-24 사용자 확정)
    "max_lots"     : 5,          # 동시에 보유할 수 있는 매수 lot 최대 개수 (2026-09-21 추가)
    "buy_gap_pct"  : 1.0,        # 추가 매수는 보유 중인 lot 중 최저 진입가보다 이 % 이상 낮을 때만 (0이면 RSI만 충족하면 매 점검마다 매수)
    "state_file"   : "coin_range_holdings.json",
}

# ══════════════════════════════════════════════════════
#  디스코드 (텔레그램 대체)
# ══════════════════════════════════════════════════════
def _html_to_discord_markdown(text: str) -> str:
    """<b>...</b> → **...** 등 텔레그램 HTML 태그를 디스코드 마크다운으로 변환"""
    text = re.sub(r"<b>(.*?)</b>", r"**\1**", text, flags=re.DOTALL)
    text = re.sub(r"<i>(.*?)</i>", r"*\1*", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", "", text)   # 남은 태그는 제거
    return text

def send_discord(text: str) -> bool:
    if not DISCORD_WEBHOOK_URL:
        print("  (디스코드 웹훅 미설정 — 콘솔 출력만)")
        print(text)
        return False
    content = _html_to_discord_markdown(text)
    if len(content) > 1900:   # 디스코드 메시지 길이 제한(2000자) 안전 마진
        content = content[:1900] + "\n…(생략)"
    try:
        r = requests.post(DISCORD_WEBHOOK_URL, json={"content": content}, timeout=10)
        # 디스코드 웹훅은 성공 시 204 No Content 반환
        return r.status_code in (200, 204)
    except Exception as e:
        print(f"  디스코드 오류: {e}")
        return False

# ══════════════════════════════════════════════════════
#  실제 주문 실행 (Bitget, ccxt)
# ══════════════════════════════════════════════════════
def get_exchange():
    if not HAS_CCXT:
        print("  ❌ ccxt 필요: pip install ccxt")
        return None
    if not (BITGET_API_KEY and BITGET_API_SECRET and BITGET_PASSPHRASE):
        print("  ⚠️  Bitget API 키 미설정 — 시세 조회는 되지만 주문은 불가")
    return ccxt.bitget({
        "apiKey": BITGET_API_KEY, "secret": BITGET_API_SECRET,
        "password": BITGET_PASSPHRASE, "enableRateLimit": True,
    })

_leverage_set = set()   # 심볼별로 실행당 1회만 레버리지 설정 시도

def ensure_leverage(ex, symbol: str):
    if symbol in _leverage_set or not CFG["auto_trade"]:
        return
    try:
        if not ex.markets:
            ex.load_markets()
        market = ex.market(symbol)
        margin_coin = market.get("settleId") or market.get("settle") or "USDT"
        ex.set_leverage(CFG["leverage"], symbol, params={"marginCoin": margin_coin})
        print(f"  ✅ 레버리지 {CFG['leverage']}배 설정 완료 ({symbol})")
    except Exception as e:
        print(f"  ⚠️ 레버리지 설정 실패(이미 설정돼 있거나 확인 필요): {e}")
    _leverage_set.add(symbol)

def place_order(symbol: str, side: str, margin_usdt: float, price: float, reduce_only: bool = False, qty_override: float = None) -> tuple:
    """
    실제 시장가 주문. margin_usdt는 투입 증거금, 체결 수량은
    레버리지가 곱해진 명목가치(notional) 기준으로 계산된다.

    ⚠️ Bitget 계좌가 헷지모드(hedge_mode)이므로 hedged=True 필수
    (2026-08-21 coin_grid_monitor.py에서 ccxt bitget.py 소스코드 직접
     확인해 검증된 방식 — holdSide 직접 지정은 무시되고, hedged=True를
     주면 ccxt가 side/reduceOnly를 보고 posSide를 자동 계산해줌)

    반환: (성공여부, 메시지, 체결수량, 체결정보dict)
      체결정보dict: {"cost": 실제체결금액USDT, "avg_price": 체결평균가,
                    "filled_qty": 체결수량, "estimated": bool(추정치여부)}
    """
    lev = CFG["leverage"]
    notional = margin_usdt * lev
    qty = notional / price if price > 0 else 0
    if qty_override:   # 청산 시 설정값이 아니라 실제 보유수량 기준으로 주문
        qty = qty_override
        notional = qty * price
    if not CFG["auto_trade"]:
        info = {"cost": notional, "avg_price": price, "filled_qty": qty, "estimated": True}
        return True, f"auto_trade 꺼짐 — 실제 주문 없음(알림만, {lev}배 노출 {notional:.0f}USDT 가정)", qty, info
    ex = get_exchange()
    if not ex:
        return False, "거래소 연결 실패", 0, {}
    if qty <= 0:
        return False, "수량 계산 오류(가격 0)", 0, {}
    ensure_leverage(ex, symbol)
    try:
        order_params = {"hedged": True, "reduceOnly": reduce_only}
        # Bitget 선물 시장가 매수는 총비용(amount*price) 계산을 위해
        # price 인자가 필요함 (ccxt 오류 메시지로 확인된 사양)
        order = ex.create_market_order(symbol, side, qty, price, params=order_params)

        # 실제 체결 정보 추출 시도 (시장가 주문 직후엔 비어있을 수 있어 추정치로 폴백)
        actual_cost = order.get("cost")
        actual_avg = order.get("average") or order.get("price")
        actual_filled = order.get("filled") or order.get("amount")
        estimated = False
        if actual_avg is None:
            actual_avg = price
            estimated = True
        if actual_filled is None:
            actual_filled = qty
            estimated = True
        if actual_cost is None:
            actual_cost = actual_filled * actual_avg
            estimated = True

        info = {"cost": actual_cost, "avg_price": actual_avg,
                "filled_qty": actual_filled, "estimated": estimated}
        est_note = " (체결정보 미수신, 요청값 기준 추정)" if estimated else ""
        return True, (f"체결 완료 (주문ID {order.get('id','?')}, "
                      f"금액 {actual_cost:,.2f}USDT, 체결가 {actual_avg:,.2f}, "
                      f"수량 {actual_filled:.6f}{est_note})"), qty, info
    except Exception as e:
        return False, f"주문 실패: {e}", 0, {}

# ══════════════════════════════════════════════════════
#  보유 상태 저장/로드 (봇이 자동 추적)
# ══════════════════════════════════════════════════════
def _normalize(h: dict) -> dict:
    """옛 형식(심볼당 단일 포지션)을 lot 목록 형식으로 변환."""
    out = {}
    for sym, pos in h.items():
        if "lots" in pos:
            out[sym] = pos
        elif pos.get("entry_price"):
            keys = ("entry_price", "entry_time", "qty", "auto_traded")
            out[sym] = {"lots": [{k: pos[k] for k in keys if k in pos}]}
    return out

def load_holdings() -> dict:
    path = CFG["state_file"]
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return _normalize(json.load(f))
        except Exception:
            return {}
    return {}

def save_holdings(h: dict):
    with open(CFG["state_file"], "w", encoding="utf-8") as f:
        json.dump(h, f, ensure_ascii=False, indent=2)

# ══════════════════════════════════════════════════════
#  지표
# ══════════════════════════════════════════════════════
def calc_rsi(closes, period=14):
    n = len(closes)
    if n < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, n):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0)); losses.append(max(-d, 0))
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for i in range(period, n-1):
        avg_g = (avg_g*(period-1) + gains[i]) / period
        avg_l = (avg_l*(period-1) + losses[i]) / period
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return 100 - (100 / (1 + rs))

def calc_sma(values, period):
    if len(values) < period:
        return None
    return sum(values[-period:]) / period

# ══════════════════════════════════════════════════════
#  시세 조회 (Binance — 백테스트와 동일 소스)
# ══════════════════════════════════════════════════════
def fetch_recent(symbol: str, timeframe: str, limit: int = 120):
    if not HAS_CCXT:
        return []
    ex = ccxt.binance()
    try:
        return ex.fetch_ohlcv(symbol, timeframe, limit=limit)
    except Exception as e:
        print(f"  {symbol} 시세 조회 오류: {e}")
        return []

# ══════════════════════════════════════════════════════
#  핵심: 코인별 점검
# ══════════════════════════════════════════════════════
def check_coin(symbol: str, params: dict, holdings: dict) -> dict:
    """
    한 코인을 점검. 매수는 lot 단위로 독립 관리한다:
    각 lot은 자기 매수가 기준으로 +target_pct 익절 / -stop_pct 손절되며, 해당 lot만 매도한다.
    반환: {"sells": [{"lot", "change", "reason"}], "buy": bool, ...}
    """
    ohlcv = fetch_recent(symbol, CFG["timeframe"], 120)
    if len(ohlcv) < CFG["ma_period"] + 2:
        return {"error": "데이터 부족", "sells": [], "buy": False, "lots": []}

    closes = [c[4] for c in ohlcv]
    cur_price = closes[-1]
    rsi = calc_rsi(closes, CFG["rsi_period"])
    ma = calc_sma(closes, CFG["ma_period"])
    label = params["label"]
    lots = holdings.get(symbol, {}).get("lots", [])

    sells = []
    for lot in lots:
        change = (cur_price - lot["entry_price"]) / lot["entry_price"] * 100
        if change >= params["target_pct"]:
            sells.append({"lot": lot, "change": change, "reason": "target"})
        elif change <= -params["stop_pct"]:
            sells.append({"lot": lot, "change": change, "reason": "stop"})

    res = {"price": cur_price, "rsi": rsi, "label": label, "sells": sells,
           "lots": lots, "buy": False, "down_market": False}
    if rsi is None:
        return res

    # 하락장 필터 (칼 안 잡기)
    if ma and ma > 0 and (cur_price - ma) / ma * 100 < -CFG["down_guard"]:
        res["down_market"] = True

    if rsi <= params["rsi_buy"] and not res["down_market"]:
        if len(lots) >= CFG["max_lots"]:
            res["skip"] = f"최대 {CFG['max_lots']}개 lot 보유 중"
        elif lots and cur_price > min(l["entry_price"] for l in lots) * (1 - CFG["buy_gap_pct"] / 100):
            res["skip"] = f"추가매수 간격 미달(최저 진입가 대비 -{CFG['buy_gap_pct']:.1f}% 필요)"
        else:
            res["buy"] = True
    return res

# ══════════════════════════════════════════════════════
#  알림 메시지 생성
# ══════════════════════════════════════════════════════
def msg_buy(r: dict, symbol: str) -> str:
    price = r["price"]
    tgt = price * (1 + r["target_pct"]/100)
    stp = price * (1 - r["stop_pct"]/100)
    lev = CFG["leverage"]
    return (
        f"🟢 <b>{r['label']} 매수 신호!</b>\n"
        f"RSI {r['rsi']:.1f} (과매도)\n"
        f"현재가: ${price:,.2f}\n\n"
        f"목표: +{r['target_pct']:.0f}% → ${tgt:,.2f}\n"
        f"손절: -{r['stop_pct']:.0f}% → ${stp:,.2f}\n\n"
        f"💡 {lev}배 레버리지 기준\n"
        f"  익절 시 증거금 +{r['target_pct']*lev:.0f}%\n"
        f"  손절 시 증거금 -{r['stop_pct']*lev:.0f}%\n\n"
        f"📌 <b>예약주문 권장</b> (밤 대비):\n"
        f"  지정가 매수 + OCO(익절/손절) 걸어두기\n"
        f"⚠️ 손절 반드시 설정하세요"
    )

def msg_sell(r: dict, symbol: str, reason: str) -> str:
    icon = "🎯" if reason == "target" else "🔴"
    word = "익절" if reason == "target" else "손절"
    lev = CFG["leverage"]
    margin_change = r["change"] * lev
    return (
        f"{icon} <b>{r['label']} {word} 신호!</b>\n"
        f"진입가: ${r['entry']:,.2f}\n"
        f"현재가: ${r['price']:,.2f}\n"
        f"수익률: {r['change']:+.1f}% (가격)\n"
        f"{lev}배 증거금 기준: {margin_change:+.1f}%\n\n"
        f"📌 매도(청산) 권고"
    )

# ══════════════════════════════════════════════════════
#  메인 점검 루프
# ══════════════════════════════════════════════════════
def run_check(send: bool = True):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{'='*56}")
    print(f"  코인 범위매매 모니터 — {now}")
    print(f"{'='*56}")

    if not HAS_CCXT:
        print("  ❌ ccxt 필요: pip install ccxt")
        return

    holdings = load_holdings()
    alerts = []
    live = CFG["auto_trade"] and send

    for symbol, params in COINS.items():
        r = check_coin(symbol, params, holdings)
        label = params["label"]
        exec_symbol = params.get("exec_symbol", symbol)
        pos = holdings.setdefault(symbol, {"lots": []})
        lots = pos["lots"]
        sold_any = False

        # ── 매도: lot별로 자기 매수가 기준 익절/손절 ──────────
        for sl in r["sells"]:
            lot, reason = sl["lot"], sl["reason"]
            word = "익절" if reason == "target" else "손절"
            rr = {"label": label, "entry": lot["entry_price"], "price": r["price"], "change": sl["change"]}
            msg = msg_sell(rr, symbol, reason)
            qty_lot = lot.get("qty")
            sold_any = True

            if live and qty_lot:
                ok, detail, _, info = place_order(exec_symbol, "sell", CFG["order_usdt"], r["price"], reduce_only=True, qty_override=qty_lot)
                if ok:
                    filled = info.get("filled_qty", qty_lot)
                    profit_usdt = filled * (r["price"] - lot["entry_price"])
                    est_note = " (추정치)" if info.get("estimated") else ""
                    msg += (f"\n\n🤖 <b>자동매도 실행됨 (해당 매수분만)</b>\n"
                            f"💰 매도 금액: {info.get('cost', 0):,.2f} USDT{est_note}\n"
                            f"💵 체결가: {info.get('avg_price', 0):,.2f}\n"
                            f"📦 수량: {info.get('filled_qty', 0):.6f}\n"
                            f"📊 이번 거래 수익률: {sl['change']:+.2f}% "
                            f"(레버리지 반영 {sl['change']*CFG['leverage']:+.2f}%)\n"
                            f"💸 실현 손익(추정, 수수료 제외): {profit_usdt:+,.2f} USDT\n"
                            f"{detail}")
                    remaining = qty_lot - filled
                    if remaining > qty_lot * 0.01:
                        lot["qty"] = remaining   # 일부만 체결되면 남은 수량으로 유지
                    else:
                        lots.remove(lot)         # 청산 성공 시에만 해당 lot 삭제
                    print(f"  {'🎯' if reason=='target' else '🔴'} {label}: {word} 체결 (매수 ${lot['entry_price']:,.2f} 기준 {sl['change']:+.1f}%) {detail}")
                    _record("sell", now, r["price"], note=word)
                else:
                    msg += f"\n\n🤖 ❌ 자동매도 실패: {detail}\n→ 즉시 확인 필요! 다음 점검에서 재시도됩니다"
                    print(f"  ❌ {label}: 매도 실패 — {detail} (lot 유지, 재시도 예정)")
            else:
                if live and not qty_lot:
                    msg += "\n\n🤖 자동매도 생략: 봇이 기록한 보유수량 없음(수동 매수분으로 추정) — 직접 매도하세요"
                    lots.remove(lot)
                elif send:
                    lots.remove(lot)   # 알림전용 모드는 기록만 정리
                print(f"  {'🎯' if reason=='target' else '🔴'} {label}: {word} 신호 (매수 ${lot['entry_price']:,.2f} 기준 {sl['change']:+.1f}%) [알림전용/점검모드 또는 수량미상]")
            alerts.append(msg)

        # ── 매수: 같은 점검에서 매도가 있었으면 건너뜀(매도-재매수 반복 방지) ──
        if r["buy"] and not sold_any:
            rb = {"price": r["price"], "rsi": r["rsi"], "label": label,
                  "target_pct": params["target_pct"], "stop_pct": params["stop_pct"]}
            msg = msg_buy(rb, symbol)
            if live:
                # ⚠️ 체결 성공을 확인한 뒤에만 lot 기록 (실패 시 유령 포지션 방지)
                ok, detail, qty, info = place_order(exec_symbol, "buy", CFG["order_usdt"], r["price"], reduce_only=False)
                if ok:
                    lots.append({"entry_price": info.get("avg_price", r["price"]), "entry_time": now,
                                 "qty": info.get("filled_qty", qty), "auto_traded": True})
                    est_note = " (추정치)" if info.get("estimated") else ""
                    msg += (f"\n\n🤖 <b>자동매수 실행됨</b> (보유 {len(lots)}/{CFG['max_lots']}개 lot)\n"
                            f"💰 매수 금액: {info.get('cost', 0):,.2f} USDT{est_note}\n"
                            f"💵 체결가: {info.get('avg_price', 0):,.2f}\n"
                            f"📦 수량: {info.get('filled_qty', 0):.6f}\n"
                            f"{detail}")
                    print(f"  🟢 {label}: 매수 체결 (RSI {r['rsi']:.1f}, ${r['price']:,.2f}) {detail}")
                    _record("buy", now, r["price"])
                else:
                    msg += f"\n\n🤖 ❌ 자동매수 실패: {detail}\n→ 신호는 유효하나 주문은 안 됨. 다음 점검에서 재시도됩니다"
                    print(f"  ❌ {label}: 매수 실패 — {detail}")
            else:
                if send:   # 알림 전용 모드: 참고용으로만 기록 (실주문 없음)
                    lots.append({"entry_price": r["price"], "entry_time": now})
                print(f"  🟢 {label}: 매수 신호 (RSI {r['rsi']:.1f}, ${r['price']:,.2f}) [알림전용/점검모드]")
            alerts.append(msg)

        # ── 상태 출력 ──────────────────────────────────────
        if not r["sells"] and not (r["buy"] and not sold_any):
            if lots:
                for l in lots:
                    ch = (r["price"] - l["entry_price"]) / l["entry_price"] * 100
                    print(f"  ⏳ {label}: 보유 lot 진입 ${l['entry_price']:,.2f} 수량 {l.get('qty','?')} (현재 {ch:+.1f}%)")
                if r.get("skip"):
                    print(f"     추가매수 보류: {r['skip']}")
            else:
                rsi_str = f"RSI {r.get('rsi'):.1f}" if r.get('rsi') else "데이터부족"
                dm = " [하락장 보류]" if r.get("down_market") else ""
                print(f"  ⚪ {label}: 대기 ({rsi_str}){dm}")

        if not lots:
            holdings.pop(symbol, None)

    if send:
        save_holdings(holdings)

    # 신호가 있으면 디스코드 발송
    if alerts and send:
        for msg in alerts:
            send_discord(msg)
        print(f"\n  ✅ 신호 {len(alerts)}건 디스코드 발송")
    elif not alerts:
        print(f"\n  신호 없음 (대기)")

    return holdings

# ══════════════════════════════════════════════════════
#  실행
# ══════════════════════════════════════════════════════
if __name__ == "__main__":
    args = sys.argv[1:]

    if "--reset" in args:
        if os.path.exists(CFG["state_file"]):
            os.remove(CFG["state_file"])
        print("  ✅ 보유 기록 초기화 완료")
        sys.exit(0)

    if "--check" in args:
        # 상태만 출력, 디스코드 발송 안 함
        run_check(send=False)
        sys.exit(0)

    if "--status" in args:
        h = load_holdings()
        if not h:
            print("  보유 없음 (idle)")
        else:
            for symbol, pos in h.items():
                for i, lot in enumerate(pos["lots"], 1):
                    print(f"  {symbol} lot{i}: 진입 ${lot.get('entry_price',0):,.2f}, "
                          f"수량 {lot.get('qty','?')}, "
                          f"{'[자동매매]' if lot.get('auto_traded') else '[알림전용]'}")
        sys.exit(0)

    # 기본: 점검 + 신호 발송
    run_check(send=True)
