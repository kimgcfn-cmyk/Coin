"""
코인 그리드매매 범용 봇 — "떨사오팔" 전략 (Bitget USDT-M 선물, 통합계정/UTA, 헤지 모드)
=================================================================================
종목을 --symbol 로 지정해서 어떤 코인에든 쓸 수 있다. IRYS/BIRB/BTC/MUSDT 봇으로 검증한 전략을 하나로 합친 것 (2026-10-25).

[실행]
  python coin_grid_bot.py --symbol ABC --check             # 1회 점검, 주문·상태 저장 없음
  python coin_grid_bot.py --symbol ABC                     # 모의(paper) 모드 — auto_trade=False라서 주문은 안 나가고 추정 체결로 상태만 움직임
  python coin_grid_bot.py --symbol ABC --live              # 실전 주문 (cron에는 반드시 --live를 넣는다)
  python coin_grid_bot.py --symbol ABC --status | --resume | --reset
  python coin_grid_bot.py --symbol ABC --set max_total_usdt=100 --set order_usdt=10   # CFG 값을 이 실행에만 덮어쓰기(JSON 값)
  python coin_grid_bot.py --find-symbol ABC                # 거래소에서 심볼 이름 확인
  python coin_grid_bot.py --preset irys --check            # 보관된 프리셋(coin_grid_presets/irys.json = IRYS 봇 마지막 설정과 운영 이력) 적용. --symbol 로 다른 종목에 쓸 수도 있다
  --symbol 은 ABC / ABCUSDT / ABC/USDT:USDT 어느 형식이든 된다. 상태 파일은 coin_grid_<abc>_state.json, 원장은 <abc>_ledger.csv.
  cron 예시(1분마다):  * * * * * cd /home/ec2-user/trading-bot && flock -n /tmp/coin_grid_abc.lock venv/bin/python coin_grid_bot.py --symbol ABC --live >> logs/coin_grid_abc.log 2>&1
  ⚠️ 시작 전: 계정의 해당 종목 레버리지를 1배(교차)로 맞추고(봇은 바꾸지 않는다), 포지션·미체결 주문이 없는지 확인한다.

[전략 구성 — 모두 CFG로 켜고 끈다]
  1) 그리드: 기준가 대비 -1%(step_pct)마다 20USDT(order_usdt) 매수, +1.5%(sell_step_pct)마다 30USDT(sell_order_usdt) 매도.
     체결 가격이 새 기준가(래칫). 총 매수 누적은 max_total_usdt(200)까지. 봇이 직접 산 물량(state의 qty)만 reduce-only로 판다.
     봇 물량이 0이면 기준가를 가격 상승에 따라 올린다(그래야 직전 고가 -1%에서 다시 산다).
  2) 급등 모드(surge_mode): 1분 저점 대비 +3%(surge_pct, surge_window_min) 상승이면 급등으로 보고 30USDT(surge_chase_usdt) 추격 매수.
     급등 중에는 그리드 매도·신규 매수를 멈추고 물량을 들고 간다. 급등 고점 대비 -3/-4.5/-6%(surge_ladder)에서 1/3씩 나눠 판다.
     각 단계는 선 아래에 3번 연속 점검(surge_confirm_min)해야 팔고 한 번에 한 단계만 판다(가짜 내림 신호 방지).
     거래량 필터(surge_vol_ratio, 0=끔): 켜면 최근 5분 거래량이 직전 100분의 5분 평균 대비 N배 이상일 때만 급등으로 인정.
  3) 급락 대응(crash_mode): 5분 안에 -8%(crash_pct) 이상 급락하면 신규 매수만 중단. 저점 +2% 반등 또는 30분간 새 저점 없으면 재개.
  4) 신규 매수 중단(trail_action="pause_buy"): 고점 대비 -15%(trail_exit_pct) 아래면 신규 매수 중단, 보유분은 반등 때만 판다.
     보유분이 모두 팔리면 자동 재개. "liquidate"로 바꾸면 전량 청산 후 정지.
  5) 안전장치: 거래소에 닫을 롱이 없으면(25227) 봇 장부를 0으로 정정, 시세 조회 실패는 건너뜀, 40762(잔고 부족) 알림 생략.
  6) 알림(Discord): 체결마다 누적 수익(실현/평가/합계, 수수료 반영, 평균단가 방식) 표시.

[이 전략이 맞는 종목 / 안 맞는 종목 — 2026-10-23~24 실측·모의 결과]
  - 잘 맞음: 하루 고저폭이 10% 이상이고 거래량이 큰 종목(급등락이 반복). 완만하게 오르내리는 장.
  - 안 맞음: BTC처럼 변동이 작은 종목(왕복 비용 약 0.12%가 이익을 먹음, 기대값 ≈ 0). 한 방향으로 계속 내리는 하락 추세(물량이 쌓여 손실).
  - 강한 한 방향 상승에서는 그냥 보유보다 적게 번다. 하락 추세 대응은 '신규 매수 중단'뿐이라 이익이 나게 해주지는 않는다.
  - 돌리기 전에 coin_grid_backtest.py 로 최근 구간을 모의하고, 한도(max_total_usdt)는 계좌의 15~20% 이하로 잡는다. 여러 종목 한도 합계는 계좌 총액을 넘기지 않는다.

[운영 규칙 — 사용자 지시]
  - 수동 물량은 봇과 분리한다. 거래소 포지션이 봇 qty보다 크면 그 차이는 사용자 물량이므로 등록·청산하지 않는다.
    (헤지 모드에서는 같은 방향 포지션이 한 포지션으로 합쳐져, 사용자가 수동으로 전량 청산하면 봇 물량도 같이 사라진다 → 장부 정정이 자동 동작)
  - auto_trade 꺼진 모의 모드는 안전한 '일시정지'가 아니다(추정 체결로 상태가 움직임). 멈추려면 cron을 주석 처리한다.
  - 상태 파일을 손으로 고칠 때는 봇의 flock 잠금을 건 상태에서 고친다(덮어쓰기 경쟁 방지).

[전략 분류] 롱 전략(이 파일 단독) · 숏 전략(coin_grid_short_bot.py 단독) · 양방향 전략(한 종목에 롱·숏 봇을 동시 가동 — 별개 전략으로 구분, 정의·운용 설정은 coin_grid_pair_strategy.md, 2026-10-26)

[파일] coin_grid_bot.py(이 파일) · coin_grid_backtest.py(모의) · trade_ledger.py(원장/엑셀) · coin_surge_monitor.py(급등 탐지 알림)
"""

import sys, json, os, re, time
from datetime import datetime, timezone

try:
    import ccxt
    HAS_CCXT = True
except ImportError:
    HAS_CCXT = False

try:
    import config as _cfg
    def _cget(key):
        return _cfg._get(key) if hasattr(_cfg, "_get") else getattr(_cfg, key, "")
    DISCORD_WEBHOOK_URL   = _cget("DISCORD_WEBHOOK_URL_GRID") or _cget("DISCORD_WEBHOOK_URL")
    BITGET_API_KEY        = _cget("BITGET_API_KEY")
    BITGET_API_SECRET     = _cget("BITGET_API_SECRET") or _cget("BITGET_SECRET_KEY")
    BITGET_PASSPHRASE     = _cget("BITGET_PASSPHRASE") or _cget("BITGET_PASSWORD")
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
    if not HAS_LEDGER or CFG.get("paper"):   # 모의(paper) 거래는 원장·엑셀에 기록하지 않는다
        return
    try:
        trade_ledger.append_trade(CFG["symbol"].split("/")[0].upper(), side, time_str, price, note)
    except Exception as e:
        print(f"  ⚠️ 거래 기록(엑셀) 실패: {e}")

# ══════════════════════════════════════════════════════
#  설정 — "떨사오팔" 그리드 파라미터
# ══════════════════════════════════════════════════════
CFG = {
    "symbol"          : "",              # --symbol 로 채움  # 2026-10-23 확인 (Bitget USDT 선물)
    "sell_step_pct"   : 1.5,               # 매도 간격(상승 %) — 2026-10-23 매수 1% / 매도 1.5%로 비대칭 (아래 [비대칭 간격] 참고)
    "step_pct"        : 1.0,               # 그리드 간격 1% (등락이 큰 종목 — 모의 결과로 선택, 위 문서 참고)
    "sell_order_usdt" : 30.0,              # 1회 매도 금액(명목 USDT) — 매수 20 / 매도 30 (2026-10-24 변동이 커서 2배로 키움; 그 전엔 10/15)
    "order_usdt"      : 20.0,              # 1회 매수 금액(명목 USDT) — 20 (2026-10-24 2배; 그 전엔 10)
    "max_total_usdt"  : 200.0,             # ⚠️ 총 투입 한도 — 무제한 물타기 방지 (2026-10-24 주문 금액 2배에 맞춰 100→200; 매수 20USDT 기준 10회분)
    "leverage"        : 1,                 # 수량 계산용(명목 = order_usdt × 1). 교차 마진이라 계정 레버리지는 증거금 사용량에만 영향
    "guard_leverage"  : False,             # 계정 레버리지가 달라도 주문 보류하지 않음
    "trail_exit_pct"  : 15.0,              # 고점 대비 -15% 하락 시 봇 물량 전량 청산 후 정지 (BIRB는 2026-10-24 -12%→-15%로 넓힘; 급등 이벤트 모의에서 -15%가 평균 최고)
    "paper"           : False,             # True면 거래소 주문 없이 모의 체결만(상태·로그는 실거래 봇과 동일하게 쌓임). 디스코드·원장 기록도 생략 (2026-10-26 IRYS 그림자용)
    "paper_slip"      : 0.0028,            # 모의 체결 슬리피지(편도)
    "sell_min_profit_pct": None,           # 숫자(예: 0)면 그리드 매도는 현재가가 봇 평균 매수가보다 이 %만큼 높을 때만 (손실 구간 자동 매도 방지). None=끔. CAP는 2026-10-26부터 0 (본전 이상에서만 매도)
    "entry_spacing"   : False,             # True면 추가 매수는 직전 매수가에서 step_pct% 더 내려왔을 때만 (평균가 기준에서 매분 연속 매수되는 것 방지, 2026-10-26)
    "ref_mode"        : "last",            # 기준가 방식: "last"=마지막 체결가(기본, 래칫), "sma"=직전 ref_sma_min분 평균가(매분 갱신). CAP는 2026-10-26부터 sma 5분
    "ref_sma_min"     : 5,                 # sma 모드의 평균 구간(분, 최대 14 — 시세 기록이 15분만 남음)
    "shadow_modes"    : [],                # 그림자 모드: 실거래 없이 "직전 N분 평균가를 기준가로 쓰는" 가상 거래를 병렬로 기록. 예: [5, 10] (2026-10-26 CAP용)
    "shadow_slip"     : 0.0028,            # 가상 거래의 편도 슬리피지(백테스트와 같은 값)
    "trail_rest_min"  : 0,                 # (trail_action="liquidate"일 때) 고점 청산 후 이 분만큼 쉬고 자동 재개. 0이면 청산 후 정지(--resume 필요). 2026-10-26 CAP용 추가
    "trail_action"    : "pause_buy",       # 고점 하락 시 동작: "pause_buy"=신규 매수 중단+보유분만 매도(물량 0이면 자동 재개), "liquidate"=전량 청산 후 정지 (2026-10-24 pause_buy로 변경)
    "surge_mode"      : True,              # 급등 모드 (2026-10-24): 급등하면 그리드 매도·신규 매수를 멈추고 트레일링으로 판다
    "surge_pct"       : 3.0,               # 급등 기준: surge_window_min 분 안의 최저가 대비 +3% (2026-10-24 5분 +5% → 1분 +3%)
    "surge_window_min": 1,
    "surge_trail_pct" : 3.0,               # (ladder가 없을 때만 쓰는 단일 매도선)
    "surge_ladder"    : [3.0, 4.5, 6.0],   # 급등 고점 대비 -3%, -4.5%, -6%에서 급등 시작 때 물량의 1/3씩 분할 매도 (2026-10-24)
    "surge_confirm_min": 3,                # 사다리 각 단계는 매도선 아래에 3분 연속(3번 연속 점검) 머물러야 판다 + 한 번에 한 단계만 (가짜 내림 신호 방지, 2026-10-24)
    "crash_mode"      : True,              # 급락 대응 (2026-10-24): 5분 안에 고점 대비 -8% 이상 급락하면 신규 매수만 중단(보유분 매도는 그대로)
    "crash_pct"       : 8.0,
    "crash_window_min": 5,
    "crash_rebound_pct": 2.0,              # 급락 저점 대비 +2% 반등하면 매수 재개
    "crash_quiet_min" : 30,                # 또는 30분 동안 새 저점이 없으면 재개
    "surge_chase_usdt": 30.0,              # 급등 감지 시 추격 매수 금액(명목 USDT) — 급등 1회당 1번, 기존 물량과 상관없이 새로 산다 (2026-10-24)
    "surge_vol_ratio" : 0,                 # 가짜 급등 필터(거래량 배수). 0 = 끔 — IRYS와 같은 전략으로 맞추려고 2026-10-24 사용자 지시로 끔. 켜면 최근 5분 거래량이 직전 100분의 5분 평균 대비 N배 이상일 때만 급등으로 인정
    "surge_vol_lookback_min": 100,
    "stop_loss"       : False,             # 2026-10-23 끔 — 고점 청산으로 대체 (켜면 단계 손절+숏 모드 동작, 아래 설명)
    "stop_loss_frac"  : 0.10,              # 손절 시작 시점 보유 수량의 10%씩 (매번 같은 수량)
    "trend_filter"    : False,             # 12시간 추세 필터(하락 쪽만, 2026-10-26): 켜면 12시간 변화율이 -trend_pct% 이하일 때 롱은 신규 매수·급등 추격 매수를 멈추고, 숏은 trend_hedge_usdt까지 시장가로 채워 들고 간다(그리드 환매 중지)
    "trend_pct"       : 5.0,               # 하락 추세 진입 기준(12시간 변화율 -5% 이하)
    "trend_release_pct": 2.5,              # 해제 기준: 12시간 변화율이 -2.5% 위로 올라오면 정상 그리드 복귀(히스테리시스)
    "trend_lookback_h": 12,
    "trend_up_block"  : False,             # (숏 봇) True면 12시간 변화율이 +trend_pct% 이상인 상승 추세에서 신규 숏 진입·급락 추격 숏을 막는다(보유 숏의 환매·손절은 그대로, 2026-10-26)
    "trend_hedge_usdt": 0.0,               # (숏 봇) 하락 추세에서 채울 숏 목표 규모(명목 USDT). 0이면 환매 중지 없이 필터만 표시
    "auto_trade"      : False,             # 기본은 모의(paper). 실전은 --live 로 켠다
    "state_file"      : "",
}

# ══════════════════════════════════════════════════════
#  디스코드
# ══════════════════════════════════════════════════════
def _html_to_discord_markdown(text: str) -> str:
    text = re.sub(r"<b>(.*?)</b>", r"**\1**", text, flags=re.DOTALL)
    text = re.sub(r"<i>(.*?)</i>", r"*\1*", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", "", text)
    return text

def send_discord(text: str) -> bool:
    if CFG.get("paper"):      # 모의(paper) 거래는 디스코드로 보내지 않는다(로그에만 남김)
        print("  (모의 거래 — 디스코드 전송 생략)")
        return False
    if not DISCORD_WEBHOOK_URL:
        print("  (디스코드 웹훅 미설정 — 콘솔 출력만)")
        print(text)
        return False
    content = _html_to_discord_markdown(text)
    if len(content) > 1900:
        content = content[:1900] + "\n…(생략)"
    try:
        r = requests.post(DISCORD_WEBHOOK_URL, json={"content": content}, timeout=10)
        return r.status_code in (200, 204)
    except Exception as e:
        print(f"  디스코드 오류: {e}")
        return False

# ══════════════════════════════════════════════════════
#  상태 저장/로드
# ══════════════════════════════════════════════════════
def load_state() -> dict:
    path = CFG["state_file"]
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_state(s: dict):
    with open(CFG["state_file"], "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)

# ══════════════════════════════════════════════════════
#  거래소 (Bitget, ccxt)
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

UTA = {"uta": True, "productType": "USDT-FUTURES"}   # 통합계정(UTA) API 사용 (2026-10-15 계정 전환, Classic API는 40085)

def fill_from_order(o: dict) -> dict:
    """fetch_order 결과에서 실제 체결 수량/평균가/체결금액을 뽑는다.
    통합계정 응답은 ccxt가 average/cost를 못 채우는 경우가 있어 원본(info)의
    cumExecQty / avgPrice / cumExecValue를 직접 읽는다. 값이 없으면 해당 항목은 None."""
    inf = o.get("info") or {}
    def num(*vals):
        for v in vals:
            try:
                f = float(v)
                if f > 0:
                    return f
            except (TypeError, ValueError):
                pass
        return None
    fee = None
    try:   # 실제 체결 수수료(USDT) — feeDetail 합계. 없으면 None(누적 손익 계산에서 기본 요율 사용)
        fd = inf.get("feeDetail") or []
        if fd:
            fee = sum(abs(float(x.get("fee") or 0)) for x in fd if (x.get("feeCoin") or "USDT") == "USDT")
    except (TypeError, ValueError):
        fee = None
    return {"filled": num(o.get("filled"), inf.get("cumExecQty")),
            "average": num(o.get("average"), inf.get("avgPrice")),
            "cost": num(o.get("cost"), inf.get("cumExecValue")),
            "fee_usdt": fee}

def check_leverage(ex, symbol: str) -> tuple:
    """계정에 설정된 해당 종목 레버리지가 CFG와 같은지 확인. 사용자가 수동 포지션 때문에 바꿔둔
    경우(같은 종목, 교차 마진) 봇 수량 계산이 어긋나므로 다르면 주문을 보류한다.
    반환: (통과여부, 메시지)"""
    try:
        r = ex.privateUtaGetV3AccountSettings({})
        data = r.get("data") or {}
        sid = symbol.split("/")[0] + "USDT"
        for c in data.get("symbolConfigList", []):
            if c.get("symbol") == sid and c.get("category") == "USDT-FUTURES":
                lev = float(c.get("leverage") or c.get("longLeverage") or 0)
                if lev and abs(lev - CFG["leverage"]) > 1e-9:
                    return False, f"계정 레버리지 {lev:g}배 ≠ 설정 {CFG['leverage']}배 — 주문 보류(계정 설정 확인 필요)"
        return True, ""
    except Exception as e:
        return False, f"레버리지 확인 실패: {e}"

def find_symbol(keyword: str = "BTC"):
    """정확한 거래 심볼 형식을 찾는 도구. 최초 1회 반드시 실행 권장."""
    ex = get_exchange()
    if not ex:
        return
    try:
        markets = ex.load_markets()
    except Exception as e:
        print(f"  ❌ 마켓 목록 조회 실패: {e}")
        return
    matches = [m for m in markets if keyword.upper() in m.upper()]
    if not matches:
        print(f"  ⚠️  '{keyword}' 포함 심볼을 찾지 못했습니다. "
              f"Bitget에 해당 자산이 없거나 다른 이름일 수 있습니다.")
        return
    print(f"  '{keyword}' 관련 심볼 {len(matches)}개:")
    for m in matches:
        info = markets[m]
        kind = "선물(swap)" if info.get("swap") else ("현물(spot)" if info.get("spot") else "?")
        print(f"    {m}   [{kind}]")
    print(f"\n  이 중 정확한 심볼을 CFG['symbol']에 넣으세요 (기본값: <심볼> 는 2026-10-23 확인된 값입니다).")

def fetch_price(symbol: str) -> float:
    ex = get_exchange()
    if not ex:
        return 0.0
    for attempt in range(4):                 # 429(요청 과다) 등 일시 오류는 잠깐 쉬고 다시 시도 (2026-10-26: 봇이 늘어 같은 초에 요청이 몰림)
        try:
            ticker = ex.fetch_ticker(symbol)
            return float(ticker.get("last") or 0)
        except Exception as e:
            transient = ("429" in str(e) or "Too Many" in str(e) or "timed out" in str(e).lower() or "Timeout" in type(e).__name__)
            if transient and attempt < 3:
                time.sleep(1.0 + attempt + (hash(symbol) % 5) * 0.2)
                continue
            print(f"  시세 조회 오류: {e}")
            return 0.0
    return 0.0

def place_order(symbol: str, side: str, margin_usdt: float, price: float,
                 reduce_only: bool = False, dry_run: bool = False,
                 qty_override: float = None) -> tuple:
    """
    실제 시장가 주문. margin_usdt는 투입 증거금이고, 실제 체결 수량은
    레버리지가 곱해진 명목가치(notional) 기준으로 계산된다.

    ⚠️ 2026-08-21 최종 수정: ccxt bitget.py 소스코드 직접 확인 결과,
    holdSide를 직접 넣는 건 무시되고 ccxt가 'hedged' bool 값을 보고
    자동으로 holdSide/posSide를 계산해서 넣는 구조였다.
    (ccxt==4.5.64 기준, venv/lib/python3.9/site-packages/ccxt/bitget.py
     5113~5235줄 참고 — hedged=True면 holdSide/posSide를 hedge_mode
     방식으로 자동 세팅해줌)
    계좌가 hedge_mode이므로 hedged=True 고정.

    ⚠️ 2026-10-23: 통합계정(UTA) 주문으로 전환 — Classic 주문 API가 40085로 막힘.
    UTA 파라미터({"uta": True, "productType": "USDT-FUTURES"})로 주문하고, 주문 직후 fetch_order로
    실제 체결 수량/가격을 조회해 반환한다(조회 실패 시에만 추정치). 양방향(hedge) 모드라
    사용자의 수동 숏과 봇의 롱은 별개 포지션이다. 레버리지는 봇이 바꾸지 않고 check_leverage로 확인만 한다.

    ⚠️ 2026-09-05: dry_run 인자 추가 — 이전엔 auto_trade만 보고 --check에서도
    실주문이 나가는 사고가 있었다(run_check(send=False)여도 이 함수는
    send를 몰랐음). 이제 dry_run=True(=--check)면 auto_trade와 무관하게
    무조건 주문을 막는다.

    반환: (성공여부, 메시지, 체결수량, 체결정보dict)
      체결정보dict: {"cost": 실제체결금액USDT, "avg_price": 체결평균가,
                    "filled_qty": 체결수량, "estimated": bool(추정치여부)}
    """
    lev = CFG["leverage"]
    notional = margin_usdt * lev
    qty = notional / price if price > 0 else 0
    if qty_override is not None:   # 손절처럼 수량을 직접 지정하는 주문
        qty = qty_override
        notional = qty * price
    if dry_run:
        info = {"cost": notional, "avg_price": price, "filled_qty": qty, "estimated": True}
        return True, f"--check 모드 — 실제 주문 없음(알림만, {lev}배 노출 {notional:.0f}USDT 가정)", qty, info
    if CFG.get("paper"):
        # 모의 체결: 거래소를 전혀 부르지 않고 현재가에 슬리피지(paper_slip)를 반영한 체결을 가정한다. 수수료 0.04%는 봇 장부가 계산한다.
        if qty <= 0:
            return False, "수량 계산 오류(가격 0)", 0, {}
        fp = price * (1 + CFG.get("paper_slip", 0.0028)) if side == "buy" else price * (1 - CFG.get("paper_slip", 0.0028))
        q = round(qty, 6); cost = q * fp; fee = cost * 0.0004
        info = {"cost": cost, "avg_price": fp, "filled_qty": q, "estimated": False, "fee": fee}
        return True, (f"체결 완료 (주문ID {int(time.time() * 1000)}, 금액 {cost:,.2f}USDT, 체결가 {fp:,.6f}, 수량 {q:.6f}) [모의 체결]"), q, info
    if not CFG["auto_trade"]:
        info = {"cost": notional, "avg_price": price, "filled_qty": qty, "estimated": True}
        return True, f"auto_trade 꺼짐 — 실제 주문 없음(알림만, {lev}배 노출 {notional:.0f}USDT 가정)", qty, info
    ex = get_exchange()
    if not ex:
        return False, "거래소 연결 실패", 0, {}
    if qty <= 0:
        return False, "수량 계산 오류(가격 0)", 0, {}
    if CFG.get("guard_leverage", True) and not reduce_only:   # 신규 진입만 확인 — 청산(reduce-only) 주문은 어떤 경우에도 막지 않는다
        ok_lev, lev_msg = check_leverage(ex, symbol)
        if not ok_lev:
            return False, lev_msg, 0, {}
    try:
        ex.load_markets()
        qty = float(ex.amount_to_precision(symbol, qty))   # 거래소 수량 단위로 내림 (기록과 실제 체결을 맞춤)
        if qty <= 0:
            return False, "수량이 최소 단위 미만 — 주문 금액을 늘려야 합니다", 0, {}
        order_params = {"hedged": True, "reduceOnly": reduce_only, **UTA}
        order = ex.create_market_order(symbol, side, qty, params=order_params)

        # 통합계정 주문 응답에는 체결 정보가 없어서, 주문 조회로 실제 체결 수량/가격을 가져온다
        fill = {}
        oid = order.get("id")
        for _ in range(4):
            if not oid:
                break
            try:
                f = fill_from_order(ex.fetch_order(oid, symbol, UTA))
                if f["filled"]:
                    fill = f
                    break
            except Exception:
                pass
            time.sleep(0.6)
        if fill:
            order = {**order, **{k: v for k, v in fill.items() if v is not None}}

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
                "filled_qty": actual_filled, "estimated": estimated,
                "fee": order.get("fee_usdt")}
        est_note = " (체결정보 미수신, 요청값 기준 추정)" if estimated else ""
        return True, (f"체결 완료 (주문ID {order.get('id','?')}, "
                      f"금액 {actual_cost:,.2f}USDT, 체결가 {actual_avg:,.2f}, "
                      f"수량 {actual_filled:.6f}{est_note})"), qty, info
    except Exception as e:
        return False, f"주문 실패: {e}", 0, {}

# ══════════════════════════════════════════════════════
#  누적 손익 (봇 가동 이후, 평균단가 방식, 수수료 제외)
# ══════════════════════════════════════════════════════
FEE_FALLBACK = 0.0006   # 체결 수수료를 못 읽었을 때 쓰는 기본 요율(Bitget USDT 선물 taker 0.06%)

def _fee_of(fee, qty: float, px: float) -> float:
    return fee if fee is not None else qty * px * FEE_FALLBACK

def _acct_buy(state: dict, qty: float, px: float, fee=None):
    """봇 물량의 평균단가 갱신 — 매수 수수료는 취득원가에 포함 (매수 체결 직전에 호출, state['qty']는 이전 값)."""
    q0 = state.get("qty", 0.0)
    c0 = state.get("avg_cost") or px
    fee = _fee_of(fee, qty, px)
    tot = q0 + qty
    state["avg_cost"] = (q0 * c0 + qty * px + fee) / tot if tot > 0 else px
    state["cum_fees"] = state.get("cum_fees", 0.0) + fee

def _acct_sell(state: dict, qty: float, px: float, fee=None):
    """매도 체결 실현손익을 평균단가 기준으로 누적 (매도 수수료 차감)."""
    c0 = state.get("avg_cost") or px
    fee = _fee_of(fee, qty, px)
    state["cum_pnl"] = state.get("cum_pnl", 0.0) + qty * (px - c0) - fee
    state["cum_fees"] = state.get("cum_fees", 0.0) + fee

def _pnl_footer(state: dict, price: float) -> str:
    realized = state.get("cum_pnl", 0.0)
    qty = state.get("qty", 0.0)
    unreal = qty * (price - (state.get("avg_cost") or price)) if qty > 0 else 0.0
    return (f"\n\n📊 <b>누적 수익 (봇 가동 이후, 수수료 반영)</b>\n"
            f"실현 {realized:+,.2f} USDT | 보유 평가 {unreal:+,.2f} USDT | 합계 {realized + unreal:+,.2f} USDT\n"
            f"(누적 수수료 {state.get('cum_fees', 0.0):,.2f} USDT 포함)")

# ══════════════════════════════════════════════════════
#  그리드 로직
# ══════════════════════════════════════════════════════
def update_trend(state: dict, price: float, now_ts: float, send: bool = True) -> int:
    """12시간 추세 필터 상태를 갱신하고 flag를 돌려준다: -1=하락 추세 중, 0=보통, +1=상승 추세 중(숏 봇 trend_up_block일 때만). (trend_filter가 꺼져 있으면 항상 0)
    12시간 전 가격은 15분봉으로 5분에 한 번만 조회해 state에 캐시한다. 조회 실패 시 직전 값을 그대로 쓴다(없으면 필터 해제).
    진입 -trend_pct% 이하, 해제 -trend_release_pct% 초과(히스테리시스). trend_up_block이 켜져 있으면 +trend_pct% 이상은 flag=+1(상승 추세, 해제 +trend_release_pct% 미만). 백테스트(2026-10-26): CAP·14개 코인 하락 추세 26구간에서 합계 -319 → -23 USDT."""
    if not CFG.get("trend_filter"):
        return 0
    try:
        if now_ts - state.get("trend_t", 0) >= 300:
            ex = get_exchange()
            rows = ex.fetch_ohlcv(CFG["symbol"], "15m", limit=80) if ex else []
            tgt = (now_ts - CFG.get("trend_lookback_h", 12) * 3600) * 1000
            if rows and rows[0][0] <= tgt + 15 * 60 * 1000:
                c = min(rows, key=lambda r: abs(r[0] - tgt))
                state["trend_ref"] = float(c[1]); state["trend_t"] = now_ts
    except Exception as e:
        print(f"  (추세 조회 오류 무시: {type(e).__name__}: {e})")
    ref = state.get("trend_ref")
    if not ref or now_ts - state.get("trend_t", 0) > 3600:      # 값이 없거나 1시간 넘게 갱신 실패면 필터 끔
        state["trend_flag"] = 0; state.pop("trend_T", None)
        return 0
    T = (price / ref - 1) * 100; state["trend_T"] = round(T, 2)
    flag = state.get("trend_flag", 0); pct = CFG.get("trend_pct", 5.0); rel = CFG.get("trend_release_pct", 2.5); up_on = CFG.get("trend_up_block")
    if flag == 1 and not up_on:
        flag = 0
    if flag == 0 and T <= -pct:
        flag = -1; msg = f"📉 <b>{CFG['symbol']} 12시간 하락 추세 필터 켜짐</b> ({T:+.1f}%)\n롱: 신규 매수 중단 / 숏: 숏을 들고 가며 그리드 환매 중지"
        print(f"  📉 12시간 변화 {T:+.1f}% — 하락 추세 필터 켜짐")
        if send: send_discord(msg)
    elif flag == 0 and up_on and T >= pct:
        flag = 1; msg = f"📈 <b>{CFG['symbol']} 12시간 상승 추세 필터 켜짐</b> ({T:+.1f}%)\n숏: 신규 진입·급락 추격 중단 (보유 숏의 환매 감시와 손절은 계속)"
        print(f"  📈 12시간 변화 {T:+.1f}% — 상승 추세 필터 켜짐 (신규 숏 중단)")
        if send: send_discord(msg)
    elif flag == -1 and T > -rel:
        flag = 0; msg = f"▶️ <b>{CFG['symbol']} 12시간 하락 추세 필터 해제</b> ({T:+.1f}%)\n정상 그리드로 복귀합니다"
        print(f"  ▶️ 12시간 변화 {T:+.1f}% — 하락 추세 필터 해제")
        if send: send_discord(msg)
    elif flag == 1 and T < rel:
        flag = 0; msg = f"▶️ <b>{CFG['symbol']} 12시간 상승 추세 필터 해제</b> ({T:+.1f}%)\n신규 숏 진입을 재개합니다"
        print(f"  ▶️ 12시간 변화 {T:+.1f}% — 상승 추세 필터 해제")
        if send: send_discord(msg)
    state["trend_flag"] = flag
    return flag

def check_grid(state: dict, price: float) -> dict:
    """
    현재가를 기준가와 비교해 매수/매도 신호 판단.
    반환: {"action": "buy"|"sell"|None, "reason": str}
    """
    ref = state.get("reference_price")
    if ref is None or ref <= 0:
        return {"action": "init", "reason": "최초 실행 — 기준가 설정"}

    step = CFG["step_pct"] / 100
    drop_trigger = ref * (1 - step)
    rise_trigger = ref * (1 + CFG.get("sell_step_pct", CFG["step_pct"]) / 100)

    if price <= drop_trigger:
        if state.get("crash_on"):
            return {"action": None, "reason": f"급락 대응 — 신규 매수 중단 중 (저점 {state.get('crash_low', 0):,.4f}, 반등 시 재개)"}
        if state.get("trend_flag") == -1:
            return {"action": None, "reason": f"12시간 하락 추세({state.get('trend_T', 0):+.1f}%) — 신규 매수 중단 (보유분 매도 감시는 계속)"}
        if state.get("buy_paused"):
            return {"action": None, "reason": f"신규 매수 중단 중({state.get('buy_paused_reason', '')}) — 보유분만 매도 감시 (기준가 {ref:,.4f}, 매도트리거 {rise_trigger:,.4f})"}
        le = state.get("last_entry_price")
        if CFG.get("entry_spacing") and state.get("qty", 0.0) > 0 and le and price > le * (1 - step):
            return {"action": None, "reason": f"진입 간격 대기 — 직전 매수가 {le:,.4f} 대비 -{CFG['step_pct']:g}% 더 내려와야({le * (1 - step):,.4f} 이하) 추가 매수 (기준가 {ref:,.4f})"}
        invested = state.get("holdings_usdt", 0.0)
        if CFG.get("stop_loss") and (state.get("stop_active") or invested >= CFG["max_total_usdt"]):
            if state.get("qty", 0.0) <= 0:
                return {"action": None, "reason": "손절 모드 — 정리할 물량 없음, 가격 회복 대기"}
            return {"action": "stop_loss", "reason":
                    f"기준가 {ref:,.4f} 대비 -{CFG['step_pct']:.0f}% 추가 하락 — 손절 단계 도달"}
        if state.get("stop_active"):
            return {"action": None, "reason": "손절 모드 — 신규 매수 중단"}
        if invested >= CFG["max_total_usdt"]:
            return {"action": "buy_blocked", "reason":
                    f"총 투입한도({CFG['max_total_usdt']:.0f}USDT) 도달 — 매수 중단(안전장치)"}
        return {"action": "buy", "reason": f"기준가 {ref:,.4f} 대비 -{CFG['step_pct']:.0f}% 도달"}

    if price >= rise_trigger:
        qty_needed = (CFG["order_usdt"] * CFG["leverage"]) / price
        if state.get("qty", 0.0) <= 0:
            return {"action": "sell_blocked", "reason": "보유 수량 부족 — 매도할 물량 없음"}
        smp = CFG.get("sell_min_profit_pct")
        if smp is not None and state.get("avg_cost") and price < state["avg_cost"] * (1 + smp / 100):
            return {"action": None, "reason": f"매도 보류 — 평균 매수가 {state['avg_cost']:,.4f} 대비 +{smp:g}% 미만이라 손실 구간 매도 안 함 (기준가 {ref:,.4f}, 필요 {state['avg_cost'] * (1 + smp / 100):,.4f} 이상)"}
        return {"action": "sell", "reason": f"기준가 {ref:,.4f} 대비 +{CFG.get('sell_step_pct', CFG['step_pct']):g}% 도달"}

    return {"action": None, "reason": f"대기 (기준가 {ref:,.4f}, "
            f"매수트리거 {drop_trigger:,.4f} / 매도트리거 {rise_trigger:,.4f})"}

def no_position_error(detail: str) -> bool:
    """거래소에 닫을 롱이 없다는 오류(Bitget 25227) — 사용자가 수동으로 청산하는 등 봇 장부보다 거래소 물량이 적을 때."""
    return "25227" in detail or "No position available to close" in detail

def zero_book(state: dict, why: str) -> str:
    """거래소에 롱이 없는데 봇 장부에 물량이 남아 있으면 장부를 0으로 정정하고 알림 문구를 돌려준다."""
    old = state.get("qty", 0.0)
    state["qty"] = 0.0
    state["holdings_usdt"] = 0.0
    return (f"⚠️ <b>{CFG['symbol']} 장부 정정</b>\n{why}\n거래소에 닫을 롱이 없어 봇 장부 {old:.4f}개를 0으로 정정했습니다 "
            f"(사용자가 수동으로 청산했을 가능성). 이후 매도 재시도는 없습니다.")

# ══════════════════════════════════════════════════════
#  급등 모드 (2026-10-24 추가) — 급등하면 물량을 들고 가다가 꺾이면 트레일링으로 판다
# ══════════════════════════════════════════════════════
def surge_volume_ratio():
    """최근 5분 거래대금 / 직전 surge_vol_lookback_min분의 5분 평균 거래대금. 조회 실패·데이터 부족이면 None."""
    try:
        sym = CFG["symbol"].split("/")[0] + "USDT"
        r = requests.get("https://api.bitget.com/api/v2/mix/market/candles",
                         params={"symbol": sym, "productType": "USDT-FUTURES", "granularity": "1m", "limit": "150"}, timeout=8).json()
        rows = sorted((int(x[0]), float(x[6])) for x in (r.get("data") or []))
        look = CFG.get("surge_vol_lookback_min", 100)
        if len(rows) < 40:
            return None
        recent = sum(v for _, v in rows[-5:])
        prior = [v for _, v in rows[-5 - look:-5]]
        if len(prior) < 30 or sum(prior) <= 0:
            return None
        return recent / (sum(prior) / len(prior) * 5)
    except Exception as e:
        print(f"  ⚠️ 거래량 조회 실패: {e}")
        return None

def surge_step(state: dict, price: float, send: bool, now: str) -> bool:
    """매 사이클 호출. 급등 모드가 이번 사이클을 처리했으면 True(그리드·고점 로직 건너뜀).
    - 감지: 최근 surge_window_min분 안의 최저가 대비 현재가가 surge_pct% 이상 올라 있으면 시작
    - 급등 중: 그리드 매도(+%)와 신규 매수(-%)를 모두 멈추고 급등 고점을 추적
    - 종료(분할 매도): 급등 고점 대비 surge_ladder의 각 % 아래로 내려올 때마다 급등 시작 때 물량의 1/단계수씩 시장가 매도.
      새 고점이 나오면 남은 단계는 새 고점 기준으로 다시 잰다(다시 오를 수 있으므로 남은 물량은 계속 들고 감).
      마지막 단계까지 다 팔리면(또는 봇 물량이 0이면) 기준가·고점을 현재가로 새로 잡고 그리드로 복귀.
    """
    if not CFG.get("surge_mode"):
        return False
    t = time.time()
    hist = [h for h in state.get("px_hist", []) if t - h[0] <= 900]
    hist.append([t, price])
    state["px_hist"] = hist
    ladder = CFG.get("surge_ladder") or [CFG["surge_trail_pct"]]
    if not state.get("surge"):
        win = CFG.get("surge_window_min", 5) * 60 + 30
        lo = min(p for ts, p in hist if t - ts <= win)
        if price >= lo * (1 + CFG["surge_pct"] / 100):
            vr = surge_volume_ratio() if CFG.get("surge_vol_ratio") else 999.0
            if vr is None or vr < CFG.get("surge_vol_ratio", 0):
                print(f"  ↗️ 5분 +{(price/lo-1)*100:.1f}% 상승이지만 거래량 {('조회 실패' if vr is None else f'{vr:.1f}배')} (<{CFG.get('surge_vol_ratio')}배) — 급등으로 보지 않음")
                return False
            vtxt = "거래량 필터 꺼짐" if vr >= 999 else f"거래량 {vr:.1f}배"
            state["surge"] = True
            state["surge_peak"] = price
            state["surge_start"] = now
            state["surge_k"] = 0
            lad = " / ".join(f"-{x:g}%" for x in ladder)
            print(f"  🚀 급등 감지 — {CFG['surge_window_min']}분 저점 {lo:,.4f} → {price:,.4f} (+{(price/lo-1)*100:.1f}%), {vtxt}")
            held0 = state.get("qty", 0.0)
            chase_note = ""
            cu = 0.0 if state.get("trend_flag") == -1 else CFG.get("surge_chase_usdt", 0.0)   # 12시간 하락 추세에서는 추격 매수 안 함
            if cu > 0:
                if state.get("holdings_usdt", 0.0) + cu > CFG["max_total_usdt"]:
                    chase_note = f"\n추격 매수는 한도({CFG['max_total_usdt']:.0f} USDT) 때문에 건너뜁니다."
                    print(f"  ⚠️ 추격 매수 건너뜀 — 한도({CFG['max_total_usdt']:.0f} USDT) 초과")
                else:
                    ok, detail, qty, info = place_order(CFG["symbol"], "buy", cu, price, reduce_only=False, dry_run=not send)
                    if ok:
                        aq = info.get("filled_qty", qty)
                        _acct_buy(state, aq, info.get("avg_price", price), info.get("fee"))
                        state["qty"] = state.get("qty", 0.0) + aq
                        state["holdings_usdt"] = state.get("holdings_usdt", 0.0) + cu
                        chase_note = f"\n🛒 추격 매수 {info.get('cost', 0):,.2f} USDT / {aq:.6f}개 (체결가 {info.get('avg_price', price):,.4f})"
                        print(f"  🛒 추격 매수 체결: {detail}")
                        if send:
                            _record("buy", now, price, "급등 추격")
                    else:
                        chase_note = f"\n❌ 추격 매수 실패: {detail}"
                        print(f"  ❌ 추격 매수 실패: {detail}")
            state["surge_base"] = state.get("qty", 0.0)
            held = state.get("qty", 0.0)
            if send:
                send_discord(f"🚀 <b>{CFG['symbol']} 급등 감지</b> (+{(price/lo-1)*100:.1f}%, {CFG['surge_window_min']}분, {vtxt})\n"
                             f"💵 {lo:,.4f} → {price:,.4f}\n📦 봇 보유 {held0:.4f}개 → {held:.4f}개{chase_note}\n"
                             f"그리드 매도·신규 매수를 멈추고, 급등 고점 대비 {lad}에서 {len(ladder)}번에 나눠 매도합니다.")
            return True
        return False
    peak = max(state.get("surge_peak") or price, price)
    state["surge_peak"] = peak
    state["peak_price"] = max(state.get("peak_price") or price, price)
    k = state.get("surge_k", 0)
    have = state.get("qty", 0.0)
    base = state.get("surge_base") or have
    done = False
    msgs = []

    def finish():
        for key in ("surge", "surge_peak", "surge_start", "surge_fail_notified", "surge_base", "surge_k", "surge_below_n"):
            state.pop(key, None)
        state["reference_price"] = price
        state["peak_price"] = price
        if state.get("qty", 0.0) <= 0:
            for key in ("buy_paused", "buy_paused_reason"):
                state.pop(key, None)

    if have <= 0:
        # 팔 물량이 없으면 첫 단계 선까지 내려왔을 때 모드만 끝낸다
        if price <= peak * (1 - ladder[0] / 100):
            if send:
                send_discord(f"📉 <b>{CFG['symbol']} 급등 종료</b>\n급등 고점 {peak:,.4f} → {price:,.4f} ({(price/peak-1)*100:.1f}%). 봇 물량이 없어 매도는 없고, 그리드로 복귀합니다.")
            print("  📉 급등 종료 — 물량 없음, 그리드 복귀")
            finish()
        else:
            print(f"  🚀 급등 모드(물량 없음) — 고점 {peak:,.4f}, 현재가 {price:,.4f} ({(price/peak-1)*100:+.1f}%)")
        return True
    confirm = CFG.get("surge_confirm_min", 1)
    if k < len(ladder) and price <= peak * (1 - ladder[k] / 100):
        state["surge_below_n"] = state.get("surge_below_n", 0) + 1
    else:
        state["surge_below_n"] = 0
    sold_total = 0.0
    sold_one = False
    while (k < len(ladder) and price <= peak * (1 - ladder[k] / 100) and state.get("qty", 0.0) > 0
           and state.get("surge_below_n", 0) >= confirm and not sold_one):
        have = state["qty"]
        q = have if k == len(ladder) - 1 else min(base / len(ladder), have)
        if q * price < 6 or have - q < 6 / price:        # 최소 주문금액(5USDT) 미만이 남지 않게 마지막으로 몰아서 판다
            q = have
        ok, detail, qty, info = place_order(CFG["symbol"], "sell", 0, price, reduce_only=True, dry_run=not send, qty_override=q)
        if not ok and no_position_error(detail):
            if send:
                send_discord(zero_book(state, "급등 분할 매도가 거절됨(25227)."))
            else:
                zero_book(state, "")
            break
        if not ok:
            print(f"  ❌ 급등 분할 매도 실패: {detail}")
            if not state.get("surge_fail_notified") and send:
                send_discord(f"❌ <b>{CFG['symbol']} 급등 분할 매도 실패</b>\n{detail}\n다음 점검에서 재시도됩니다")
                state["surge_fail_notified"] = True
            break
        aq = info.get("filled_qty", q)
        fp = info.get("avg_price", price)
        ref_before = state.get("reference_price") or price
        _acct_sell(state, aq, fp, info.get("fee"))
        state["qty"] = max(0.0, have - aq)
        state["holdings_usdt"] = 0.0 if state["qty"] <= 1e-9 else state.get("holdings_usdt", 0.0) * (state["qty"] / have)
        k += 1
        sold_total += aq
        m = (f"📉 <b>{CFG['symbol']} 급등 분할 매도 {k}/{len(ladder)}</b>\n📈 급등 고점 {peak:,.4f} → {price:,.4f} ({(price/peak-1)*100:.1f}%)\n"
             f"💵 체결가: {fp:,.4f} USDT\n📦 수량: {aq:.6f}개 (남은 봇 물량 {state['qty']:.6f}개)\n"
             f"💸 직전 기준가 대비(수수료 제외): {aq * (fp - ref_before):+,.2f} USDT\n{detail}")
        msgs.append(m)
        print(f"  📉 급등 분할 매도 {k}/{len(ladder)}: {detail}")
        if send:
            _record("sell", now, price, f"급등 분할 {k}/{len(ladder)}")
        sold_one = True
    state["surge_k"] = k
    if k >= len(ladder) or state.get("qty", 0.0) <= 0:
        done = True
    if msgs:
        text = msgs[-1] if len(msgs) == 1 else "\n\n".join(msgs)
        if done:
            text += "\n\n✅ 마지막 단계까지 매도해 급등 모드를 끝내고 그리드로 복귀합니다."
        text += _pnl_footer(state, price)
        if send:
            send_discord(text)
    elif not done:
        nxt = peak * (1 - ladder[k] / 100) if k < len(ladder) else peak
        wait = f", 이탈 확인 {state.get('surge_below_n', 0)}/{confirm}" if state.get("surge_below_n", 0) > 0 else ""
        print(f"  🚀 급등 모드 — 고점 {peak:,.4f}, 현재가 {price:,.4f} ({(price/peak-1)*100:+.1f}%), 다음 매도선 {nxt:,.4f} ({k}/{len(ladder)}단계 완료{wait})")
    if done:
        finish()
    return True

# ══════════════════════════════════════════════════════
#  메인 점검
# ══════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════
#  그림자 모드 — 실거래와 별개로 '평균가 기준' 전략을 가상으로 돌려 비교용 기록만 남긴다 (주문 없음)
# ══════════════════════════════════════════════════════
SHADOW_FEE = 0.0004

def _shadow_path() -> str:
    sf = CFG.get("state_file", "coin_grid_state.json")
    return sf.replace("_state.json", "_shadow.json")

def _shadow_new_mode() -> dict:
    return {"qty": 0.0, "cash": 0.0, "hold": 0.0, "avg": 0.0, "peak": 0.0, "crash": False, "low": 0.0, "low_t": 0.0, "rest_until": 0.0,
            "fees": 0.0, "buys": 0, "sells": 0, "wins": 0, "trails": 0, "crashes": 0, "realized": 0.0, "eq_peak": 0.0, "mdd": 0.0}

def _record_ref_hist(state: dict, price: float, now_ts: float) -> None:
    """점검 때마다 시세 샘플을 15분치 남긴다. 평균가 기준가(ref_mode=sma)와 급락 감지가 함께 쓴다(급등 모드 켜짐 여부와 무관)."""
    h = [x for x in state.get("ref_hist", []) if now_ts - x[0] <= 900]
    h.append([now_ts, price]); state["ref_hist"] = h

def shadow_step(state: dict, price: float, now_ts: float, now_str: str) -> None:
    """매 점검(약 1분)마다 호출. 모드별(N분 평균가)로 가상 매수·매도·트레일 청산·급락 대응을 모의하고 파일에 저장한다.
    규칙은 백테스트(sma_eval)와 같다: 기준가=직전 N분 평균, 매수=기준가-step_pct%, 매도=기준가+sell_step_pct%, 트레일 청산 후 휴식, 5분 -8% 급락 시 매수 중단."""
    modes = CFG.get("shadow_modes") or []
    if not modes:
        return
    path = _shadow_path()
    try:
        sh = json.load(open(path, encoding="utf-8"))
    except Exception:
        sh = {}
    if not sh:
        sh = {"start": now_ts, "start_str": now_str, "start_price": price, "hist": [], "modes": {}, "events": [],
              "live0": {"cum_pnl": state.get("cum_pnl") or 0.0, "qty": state.get("qty", 0.0), "avg_cost": state.get("avg_cost") or 0.0, "price": price}}
    hist = [h for h in sh.get("hist", []) if now_ts - h[0] <= 15 * 60]
    prev = list(hist); hist.append([now_ts, price]); sh["hist"] = hist
    slip = CFG.get("shadow_slip", 0.0028)
    bs, ss = CFG["step_pct"], CFG.get("sell_step_pct", CFG["step_pct"])
    bu, su, cap = CFG["order_usdt"], CFG.get("sell_order_usdt", CFG["order_usdt"]), CFG["max_total_usdt"]
    trail = CFG.get("trail_exit_pct") if CFG.get("trail_action") == "liquidate" else None
    rest = CFG.get("trail_rest_min") or 240
    lines = []
    for n in modes:
        key = str(n)
        m = sh["modes"].setdefault(key, _shadow_new_mode())
        def buy(usdt):
            fp = price * (1 + slip); q = usdt / fp; fee = usdt * SHADOW_FEE
            m["avg"] = (m["qty"] * m["avg"] + usdt + fee) / (m["qty"] + q) if m["qty"] + q > 0 else fp
            m["qty"] += q; m["cash"] -= usdt + fee; m["hold"] += usdt; m["fees"] += fee; m["buys"] += 1
            sh["events"].append([now_str, key, "매수", round(price, 8), round(usdt, 2), round(q, 6), 0.0])
        def sell(q, why="매도"):
            q = min(q, m["qty"]); fp = price * (1 - slip); pr = q * fp; fee = pr * SHADOW_FEE
            pnl = q * (fp - m["avg"]) - fee
            m["cash"] += pr - fee; m["fees"] += fee; m["qty"] -= q; m["realized"] += pnl; m["sells"] += 1; m["wins"] += 1 if pnl > 0 else 0
            if m["qty"] <= 1e-12: m["qty"] = 0.0; m["hold"] = 0.0
            sh["events"].append([now_str, key, why, round(price, 8), round(pr, 2), round(q, 6), round(pnl, 4)])
        def eq(): return m["cash"] + m["qty"] * price * (1 - slip) * (1 - SHADOW_FEE)
        def track():
            e = eq(); m["eq_peak"] = max(m["eq_peak"], e); m["mdd"] = min(m["mdd"], e - m["eq_peak"])
        if now_ts < m["rest_until"]:
            track(); lines.append(f"B{n} 휴식중 {eq():+.2f}"); continue
        if m["rest_until"] and now_ts >= m["rest_until"]:
            m["rest_until"] = 0.0; m["peak"] = 0.0
        if m["qty"] > 0: m["peak"] = max(m["peak"], price)
        else: m["peak"] = 0.0
        if trail and m["qty"] > 0 and price <= m["peak"] * (1 - trail / 100):
            sell(m["qty"], "트레일청산"); m["trails"] += 1; m["rest_until"] = now_ts + rest * 60; m["peak"] = 0.0; m["crash"] = False
            track(); lines.append(f"B{n} 트레일청산 {eq():+.2f}"); continue
        hi5 = max([p for t_, p in hist if now_ts - t_ <= CFG["crash_window_min"] * 60 + 30] or [price])
        if not m["crash"]:
            if CFG.get("crash_mode") and price <= hi5 * (1 - CFG["crash_pct"] / 100):
                m["crash"] = True; m["crashes"] += 1; m["low"] = price; m["low_t"] = now_ts
        else:
            if price < m["low"]: m["low"] = price; m["low_t"] = now_ts
            if price >= m["low"] * (1 + CFG["crash_rebound_pct"] / 100) or now_ts - m["low_t"] >= CFG["crash_quiet_min"] * 60:
                m["crash"] = False
        pm = [p for t_, p in prev if now_ts - t_ <= n * 60 + 30]
        if len(pm) < max(2, n - 1):
            track(); lines.append(f"B{n} 평균 준비중({len(pm)}/{n})"); continue
        r = sum(pm) / len(pm)
        if m["qty"] <= 0: m["last_buy"] = 0.0
        if price <= r * (1 - bs / 100):
            if CFG.get("entry_spacing") and m.get("last_buy") and price > m["last_buy"] * (1 - bs / 100):
                pass                                    # 진입 간격 대기
            elif not m["crash"] and m["hold"] < cap: buy(bu); m["last_buy"] = price
        elif m["qty"] > 0 and price >= r * (1 + ss / 100):
            q = su / price
            if m["qty"] - q < q * 0.5: q = m["qty"]
            m["hold"] = max(0.0, m["hold"] - su); sell(q)
        track(); lines.append(f"B{n} {eq():+.2f}(보유 {m['qty'] * price:.0f}USDT, 매수{m['buys']}/매도{m['sells']})")
    sh["events"] = sh["events"][-400:]
    sh["updated"] = now_ts
    tmp = path + ".tmp"
    json.dump(sh, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
    os.replace(tmp, path)
    print("  👻 그림자(가상) 손익: " + " | ".join(lines))

def run_check(send: bool = True):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{'='*56}")
    print(f"  코인 그리드매매 (떨사오팔) — {CFG['symbol']} — {now}")
    print(f"{'='*56}")

    if not HAS_CCXT:
        print("  ❌ ccxt 필요: pip install ccxt")
        return

    state = load_state()
    price = fetch_price(CFG["symbol"])
    if price <= 0:
        print(f"  ❌ {CFG['symbol']} 시세 조회 실패 — --find-symbol 로 정확한 심볼 확인 필요")
        return
    print(f"  현재가: {price:,.4f} USDT")
    if 0 < state.get("qty", 0.0) * price < 0.05:          # 부동소수점 잔량 정리 (롱 봇도 같은 문제 가능, 2026-10-27)
        state["qty"] = 0.0; state["holdings_usdt"] = 0.0

    _record_ref_hist(state, price, time.time())
    update_trend(state, price, time.time(), send)

    if send and CFG.get("shadow_modes"):
        try:
            shadow_step(state, price, time.time(), now)
        except Exception as e:                       # 그림자 모드 오류가 실거래에 영향을 주지 않게 한다
            print(f"  (그림자 모드 오류 무시: {type(e).__name__}: {e})")

    if state.get("halted"):
        print(f"  ⏸️ 봇 정지 상태 ({state.get('halted_reason', '')}) — 재개하려면 --resume")
        return

    # 고점 청산 후 휴식(trail_rest_min) — 휴식이 끝나면 기준가·고점을 현재가로 새로 잡고 자동 재개
    if state.get("rest_until"):
        if time.time() < state["rest_until"]:
            print(f"  😴 고점 청산 후 휴식 중 — 약 {(state['rest_until'] - time.time()) / 60:.0f}분 뒤 재개 ({state.get('rest_reason', '')})")
            return
        for k in ("rest_until", "rest_reason"):
            state.pop(k, None)
        state["reference_price"] = price; state["peak_price"] = price
        print(f"  ▶️ 휴식 끝 — 기준가·고점을 현재가 {price:,.4f}로 새로 잡고 매수를 재개합니다")
        if send:
            send_discord(f"▶️ <b>{CFG['symbol']} 휴식 끝 — 매수 재개</b>\n기준가·고점을 현재가 {price:,.4f}로 새로 잡았습니다.")
            save_state(state)

    # 급락 대응 — 5분 내 -8% 급락이면 신규 매수 중단, 저점 +2% 반등 또는 30분간 새 저점 없으면 재개
    if CFG.get("crash_mode") and not state.get("surge"):
        tnow = time.time()
        hist = [h for h in (state.get("ref_hist") or state.get("px_hist", [])) if tnow - h[0] <= CFG["crash_window_min"] * 60 + 30]
        if not state.get("crash_on"):
            hi = max([p for _, p in hist] + [price])
            if price <= hi * (1 - CFG["crash_pct"] / 100):
                state["crash_on"] = True
                state["crash_low"] = price
                state["crash_low_t"] = tnow
                print(f"  📉 급락 감지 — {CFG['crash_window_min']}분 고점 {hi:,.4f} → {price:,.4f} ({(price/hi-1)*100:.1f}%): 신규 매수 중단")
                if send:
                    send_discord(f"📉 <b>{CFG['symbol']} 급락 감지</b> ({(price/hi-1)*100:.1f}%, {CFG['crash_window_min']}분)\n💵 {hi:,.4f} → {price:,.4f}\n"
                                 f"신규 매수를 멈춥니다(보유분 {state.get('qty', 0.0):.4f}개 매도는 그대로). 저점 대비 +{CFG['crash_rebound_pct']:g}% 반등하거나 {CFG['crash_quiet_min']}분간 새 저점이 없으면 재개합니다.")
        else:
            if price < state.get("crash_low", price):
                state["crash_low"] = price
                state["crash_low_t"] = tnow
            low = state["crash_low"]
            if price >= low * (1 + CFG["crash_rebound_pct"] / 100) or tnow - state.get("crash_low_t", tnow) >= CFG["crash_quiet_min"] * 60:
                for k in ("crash_on", "crash_low", "crash_low_t"):
                    state.pop(k, None)
                state["reference_price"] = price
                print(f"  ▶️ 급락 진정 — 신규 매수 재개 (저점 {low:,.4f} → {price:,.4f})")
                if send:
                    send_discord(f"▶️ <b>{CFG['symbol']} 급락 대응 해제</b>\n저점 {low:,.4f} → 현재가 {price:,.4f}. 신규 매수를 다시 시작합니다(기준가=현재가).")
            elif state.get("reference_price") and price <= state["reference_price"] * (1 - CFG["step_pct"] / 100):
                state["reference_price"] = price        # 급락 중에는 매수 대신 기준가만 따라 내린다

    # 급등 모드 — 급등 중이면 그리드·고점 로직을 건너뛴다
    if surge_step(state, price, send, now):
        if send:
            save_state(state)
        return

    # 고점 추적 + 고점 대비 하락 시 처리 (trail_action)
    trail = CFG.get("trail_exit_pct")
    trail_mode = CFG.get("trail_action", "liquidate")
    if trail_mode == "pause_buy":
        note_msg = None
        if state.get("buy_paused"):
            # 신규 매수 중단 중: 기준가를 가격 아래로 따라 내려서(반등 +% 때 보유분을 팔도록) 하고, 물량이 0이 되면 재개
            if price < (state.get("reference_price") or price):
                state["reference_price"] = price
            if state.get("qty", 0.0) <= 0:
                for k in ("buy_paused", "buy_paused_reason"):
                    state.pop(k, None)
                state["peak_price"] = price
                state["reference_price"] = price
                note_msg = (f"▶️ <b>{CFG['symbol']} 신규 매수 재개</b>\n보유분을 모두 팔았습니다. 기준가·고점을 현재가 {price:,.4f}로 새로 잡고 매수를 다시 시작합니다.")
                print("  ▶️ 보유분 모두 매도 — 신규 매수 재개")
        else:
            if state.get("qty", 0.0) <= 0:
                state["peak_price"] = price          # 물량이 없으면 고점도 현재가로 새로 시작
            else:
                state["peak_price"] = max(state.get("peak_price") or price, price)
            if trail and state.get("qty", 0.0) > 0 and price <= state["peak_price"] * (1 - trail / 100):
                peak = state["peak_price"]
                state["buy_paused"] = True
                state["buy_paused_reason"] = f"고점 {peak:,.4f} 대비 {(price/peak-1)*100:.1f}% ({now})"
                note_msg = (f"⏸️ <b>{CFG['symbol']} 신규 매수 중단</b> (고점 대비 {(price/peak-1)*100:.1f}%)\n"
                            f"📈 고점 {peak:,.4f} → 현재가 {price:,.4f}\n📦 보유분 {state.get('qty', 0.0):.4f}개만 매도 감시 (기준가는 가격을 따라 내려갑니다)\n"
                            f"보유분이 모두 팔리면 자동으로 매수를 재개합니다.")
                print(f"  ⏸️ 고점 대비 {(price/peak-1)*100:.1f}% — 신규 매수 중단, 보유분만 매도")
        if note_msg and send:
            send_discord(note_msg)
    else:
        if CFG.get("trail_rest_min") and state.get("qty", 0.0) <= 0:
            state["peak_price"] = price          # 휴식 모드: 물량이 없으면 고점도 현재가로 새로 시작(첫 매수 이후의 고점 기준으로 재야 함)
        else:
            state["peak_price"] = max(state.get("peak_price") or price, price)
    if trail_mode == "liquidate" and trail and not (CFG.get("trail_rest_min") and state.get("qty", 0.0) <= 0) and price <= state["peak_price"] * (1 - trail / 100):
        have = state.get("qty", 0.0)
        peak = state["peak_price"]
        print(f"  🚨 고점 {peak:,.4f} 대비 {(price/peak-1)*100:.1f}% — 전량 청산 조건 ({trail:.0f}%)")
        ok, detail, qty, info = True, "보유 물량 없음 — 청산 생략", 0, {}
        if have > 0:
            ok, detail, qty, info = place_order(CFG["symbol"], "sell", 0, price, reduce_only=True,
                                                 dry_run=not send, qty_override=have)
        if ok:
            aq = info.get("filled_qty", have) if have > 0 else 0.0
            fp = info.get("avg_price", price)
            ref_before = state.get("reference_price") or price
            if aq > 0:
                _acct_sell(state, aq, fp, info.get("fee"))
            pnl = aq * (fp - ref_before)
            rest_min = CFG.get("trail_rest_min") or 0
            state.update({"qty": max(0.0, have - aq), "holdings_usdt": 0.0})
            if rest_min:
                state.update({"rest_until": time.time() + rest_min * 60, "rest_reason": f"고점 {peak:,.4f} 대비 -{trail:.0f}% 청산 ({now})"})
            else:
                state.update({"halted": True, "halted_reason": f"고점 {peak:,.4f} 대비 -{trail:.0f}% 청산 ({now})"})
            msg = (f"🚨 <b>{CFG['symbol']} 고점 대비 -{trail:.0f}% 전량 청산 후 {'%d분 휴식' % rest_min if rest_min else '정지'}</b>\n"
                   f"📈 고점: {peak:,.4f} → 현재가: {price:,.4f} ({(price/peak-1)*100:.1f}%)\n"
                   f"💵 체결가: {fp:,.4f} USDT\n📦 청산 수량: {aq:.6f}개\n"
                   f"💸 직전 기준가 대비 손익(수수료 제외): {pnl:+,.2f} USDT\n{detail}\n"
                   + (f"{rest_min}분 뒤 기준가·고점을 현재가로 새로 잡고 자동 재개합니다." if rest_min else "봇은 정지되었습니다. 재개: python coin_grid_bot.py --resume"))
            msg += _pnl_footer(state, price)
            print(f"  🚨 청산 완료: {detail}")
            if send and have > 0:
                _record("sell", now, price, "고점청산")
        else:
            msg = None if state.get("trail_fail_notified") else f"❌ <b>{CFG['symbol']} 고점 청산 실패</b>\n{detail}\n다음 점검에서 재시도됩니다"
            state["trail_fail_notified"] = True
            print(f"  ❌ 청산 실패: {detail}")
        if send:
            save_state(state)
        if msg:
            if send:
                send_discord(msg); print("\n  ✅ 알림 발송")
            else:
                print("\n  [--check, 발송 생략]\n  " + msg.replace("<b>","").replace("</b>",""))
        return

    msg = None
    if state.get("mode") != "short" and state.get("stop_active") and price >= state.get("stop_origin", float("inf")):
        origin = state.get("stop_origin")
        for k in ("stop_active", "stop_base_qty", "stop_origin"):
            state.pop(k, None)
        state.pop("notified_budget_full", None)
        if state.get("qty", 0.0) <= 0:
            state["reference_price"] = price   # 물량을 전부 정리했다면 현재가로 기준가를 새로 잡는다
        print(f"  ✅ 가격이 손절 시작 기준가 {origin:,.4f}까지 회복 — 손절 모드 해제")
        msg = (f"✅ <b>{CFG['symbol']} 손절 모드 해제</b>\n"
               f"가격이 손절 시작 기준가 {origin:,.4f}까지 회복했습니다. 일반 그리드로 복귀합니다.")

    # 봇 물량이 없으면 기준가를 상승에 따라 올린다 — 안 그러면 다 팔고 난 뒤 기준가가 마지막 매도가에 머물러
    # 이후 상승분을 놓치고, 그 가격의 -1%까지 내려와야만 다시 산다 (2026-10-24 추가)
    if CFG.get("ref_mode") == "sma" and state.get("mode") != "short":
        n_ = min(int(CFG.get("ref_sma_min", 5)), 14); tnow_ = time.time()
        prev_ = [p_ for ts_, p_ in (state.get("ref_hist") or [])[:-1] if tnow_ - ts_ <= n_ * 60 + 30]     # 현재가를 뺀 직전 n분 시세 기록
        if len(prev_) < max(2, n_ - 1):
            print(f"  😴 {n_}분 평균가 준비 중 ({len(prev_)}/{n_}개 기록) — 이번 점검은 거래 보류")
            if send: save_state(state)
            return
        state["reference_price"] = sum(prev_) / len(prev_)
    if CFG.get("ref_mode") != "sma" and state.get("mode") != "short" and state.get("qty", 0.0) <= 0 and price > (state.get("reference_price") or price):
        print(f"  ⬆️ 봇 물량 없음 — 기준가 {state['reference_price']:,.4f} → {price:,.4f} (상승 추종)")
        state["reference_price"] = price

    if state.get("mode") == "short":
        return run_short(state, price, send, now)

    result = check_grid(state, price)
    action = result["action"]
    print(f"  {result['reason']}")

    if action == "init":
        state["reference_price"] = price
        state.setdefault("holdings_usdt", 0.0)
        state.setdefault("qty", 0.0)
        print(f"  ✅ 기준가 {price:,.4f} 설정 완료")

    elif action == "buy":
        ok, detail, qty, info = place_order(CFG["symbol"], "buy", CFG["order_usdt"], price,
                                             reduce_only=False, dry_run=not send)
        if ok:
            actual_qty = info.get("filled_qty", qty)
            _acct_buy(state, actual_qty, info.get("avg_price", price), info.get("fee"))
            state["qty"] = state.get("qty", 0.0) + actual_qty
            state["holdings_usdt"] = state.get("holdings_usdt", 0.0) + CFG["order_usdt"]
            state["reference_price"] = price   # 래칫: 기준가를 더 아래로
            state["last_entry_price"] = price  # 진입 간격 규칙용 직전 매수가
            est_note = " (추정치)" if info.get("estimated") else ""
            msg = (f"🟢 <b>{CFG['symbol']} 매수</b> ({CFG['step_pct']:.0f}% 하락)\n"
                   f"💰 매수 금액: {info.get('cost', 0):,.2f} USDT{est_note}\n"
                   f"💵 체결가: {info.get('avg_price', price):,.4f} USDT\n"
                   f"📦 수량: {info.get('filled_qty', qty):.6f}개\n"
                   f"증거금: {CFG['order_usdt']:.0f} USDT × {CFG['leverage']}배 레버리지\n"
                   f"누적 증거금: {state['holdings_usdt']:.0f} / {CFG['max_total_usdt']:.0f} USDT\n"
                   f"{detail}")
            print(f"  🟢 매수 체결: {detail}")
            if send:
                _record("buy", now, price)
        else:
            print(f"  ❌ 매수 실패: {detail}")
            if "40762" in detail or "exceeds the balance" in detail:
                # 잔고 부족(Bitget 40762): 10분마다 같은 실패가 반복되므로 디스코드 알림은 생략 (로그에만 남김)
                msg = None
            else:
                msg = f"❌ <b>{CFG['symbol']} 매수 실패</b>\n{detail}\n다음 점검에서 재시도됩니다"

    elif action == "sell":
        qty_to_sell = (CFG.get("sell_order_usdt", CFG["order_usdt"]) * CFG["leverage"]) / price
        have_q = state.get("qty", 0.0)
        sell_all = have_q - qty_to_sell < qty_to_sell * 0.5     # 남는 게 한 칸의 절반 미만이면 전량 (잔량이 영영 못 팔리는 것 방지)
        if sell_all:
            qty_to_sell = have_q
        ok, detail, qty, info = place_order(CFG["symbol"], "sell", CFG.get("sell_order_usdt", CFG["order_usdt"]), price,
                                             reduce_only=True, dry_run=not send,
                                             qty_override=qty_to_sell if sell_all else None)
        if ok:
            actual_qty = info.get("filled_qty", qty_to_sell)
            _acct_sell(state, actual_qty, info.get("avg_price", price), info.get("fee"))
            ref_before = state.get("reference_price") or price   # 이번 매도의 기준(직전 기준가)
            state["qty"] = max(0.0, state.get("qty", 0.0) - actual_qty)
            state["holdings_usdt"] = max(0.0, state.get("holdings_usdt", 0.0) - CFG.get("sell_order_usdt", CFG["order_usdt"]))
            if state["qty"] <= 1e-9:
                state["holdings_usdt"] = 0.0
            state["reference_price"] = price   # 래칫: 기준가를 더 위로
            # 실제 체결가와 직전 기준가로 이번 구간 수익률/손익 계산 (BTC 봇과 동일 방식)
            fill_price = info.get("avg_price", price)
            leg_ret = (fill_price - ref_before) / ref_before * 100
            leg_profit = actual_qty * (fill_price - ref_before)
            est_note = " (추정치)" if info.get("estimated") else ""
            msg = (f"🔴 <b>{CFG['symbol']} 매도</b> ({CFG.get('sell_step_pct', CFG['step_pct']):g}% 상승)\n"
                   f"💰 매도 금액: {info.get('cost', 0):,.2f} USDT{est_note}\n"
                   f"💵 체결가: {info.get('avg_price', price):,.4f} USDT\n"
                   f"📦 수량: {info.get('filled_qty', qty_to_sell):.6f}개\n"
                   f"📊 이번 거래 수익률: {leg_ret:+.2f}% "
                   f"(레버리지 반영 {leg_ret*CFG['leverage']:+.2f}%)\n"
                   f"💸 실현 손익(추정, 수수료 제외): {leg_profit:+,.2f} USDT\n"
                   f"잔여 증거금: {state['holdings_usdt']:.0f} USDT\n"
                   f"{detail}")
            print(f"  🔴 매도 체결: {detail}")
            if send:
                _record("sell", now, price)
        else:
            print(f"  ❌ 매도 실패: {detail}")
            if no_position_error(detail):
                msg = zero_book(state, "매도 주문이 거절됨(25227).")
            else:
                msg = f"❌ <b>{CFG['symbol']} 매도 실패</b>\n{detail}\n다음 점검에서 재시도됩니다"

    elif action == "stop_loss":
        first = not state.get("stop_active")
        base_qty = state["qty"] if first else state.get("stop_base_qty", state["qty"])
        have = state.get("qty", 0.0)
        qty_to_sell = min(base_qty * CFG["stop_loss_frac"], have)
        if have - qty_to_sell < (CFG["order_usdt"] * CFG["leverage"]) / price:   # 남는 게 한 칸 미만이면 전량
            qty_to_sell = have
        ok, detail, qty, info = place_order(CFG["symbol"], "sell", 0, price,
                                             reduce_only=True, dry_run=not send,
                                             qty_override=qty_to_sell)
        if ok:
            actual_qty = info.get("filled_qty", qty_to_sell)
            _acct_sell(state, actual_qty, info.get("avg_price", price), info.get("fee"))
            ref_before = state.get("reference_price") or price
            if first:
                state["stop_active"] = True
                state["stop_base_qty"] = base_qty
                state["stop_origin"] = ref_before
            frac_left = max(0.0, have - actual_qty) / have if have > 0 else 0.0
            state["qty"] = max(0.0, have - actual_qty)
            state["holdings_usdt"] = state.get("holdings_usdt", 0.0) * frac_left
            state["reference_price"] = price   # 래칫: 기준가를 더 아래로
            fill_price = info.get("avg_price", price)
            leg_ret = (fill_price - ref_before) / ref_before * 100
            leg_profit = actual_qty * (fill_price - ref_before)
            est_note = " (추정치)" if info.get("estimated") else ""
            msg = (f"🛑 <b>{CFG['symbol']} 손절 매도</b> ({'손절 시작' if first else '손절 진행'}, "
                   f"시작 수량의 {CFG['stop_loss_frac']*100:.0f}%)\n"
                   f"💰 매도 금액: {info.get('cost', 0):,.2f} USDT{est_note}\n"
                   f"💵 체결가: {fill_price:,.4f} USDT\n"
                   f"📦 수량: {info.get('filled_qty', qty_to_sell):.6f}개\n"
                   f"📊 직전 기준가 대비: {leg_ret:+.2f}%\n"
                   f"💸 손익(직전 기준가 대비, 수수료 제외): {leg_profit:+,.2f} USDT\n"
                   f"남은 봇 물량: {state['qty']:.0f}개 (약 {state['qty']*price:,.1f} USDT)\n"
                   f"{detail}")
            print(f"  🛑 손절 체결: {detail}")
            if send:
                _record("sell", now, price, "손절")
            if state["qty"] <= 0:
                for k in ("stop_active", "stop_base_qty", "stop_origin", "notified_budget_full"):
                    state.pop(k, None)
                state["mode"] = "short"
                state["reference_price"] = price
                state["qty"] = 0.0
                state["holdings_usdt"] = 0.0
                msg += (f"\n\n🔁 <b>롱 전량 손절 완료 → 숏 모드 전환</b>\n"
                        f"숏 기준가 {price:,.4f} — +{CFG['step_pct']:.0f}% 오르면 {CFG['order_usdt']:.0f}USDT 숏 진입, "
                        f"-{CFG['step_pct']:.0f}% 내리면 청산")
        else:
            msg = f"❌ <b>{CFG['symbol']} 손절 매도 실패</b>\n{detail}\n다음 점검에서 재시도됩니다"
            print(f"  ❌ 손절 실패: {detail}")

    elif action == "buy_blocked":
        print(f"  ⚠️ {result['reason']}")
        # 매수 막힘 알림은 반복 스팸 방지를 위해 상태에 1회만 기록
        if not state.get("notified_budget_full"):
            msg = f"⚠️ <b>{CFG['symbol']} 매수 한도 도달</b>\n{result['reason']}\n(매도는 계속 감시 중)"
            state["notified_budget_full"] = True

    elif action == "sell_blocked":
        print(f"  ⚠️ {result['reason']}")

    if send:
        save_state(state)
    else:
        print("  (--check 모드: 상태 저장 생략)")

    if msg and action in ("buy", "sell", "stop_loss") and not msg.startswith("❌"):
        msg += _pnl_footer(state, price)

    if msg:
        if send:
            send_discord(msg)
            print("\n  ✅ 알림 발송")
        else:
            print("\n  [--check, 발송 생략]")
            print("  " + msg.replace("<b>","").replace("</b>",""))
    else:
        print("\n  액션 없음 (대기)")

# ══════════════════════════════════════════════════════
#  숏 모드 (롱 전량 손절 후) — 롱 전략의 정반대
# ══════════════════════════════════════════════════════
def check_grid_short(state: dict, price: float) -> dict:
    ref = state.get("reference_price")
    step = CFG["step_pct"] / 100
    rise_trigger = ref * (1 + step)
    drop_trigger = ref * (1 - step)
    qty = state.get("qty", 0.0)

    if price >= rise_trigger:
        if state.get("stop_active") or state.get("holdings_usdt", 0.0) >= CFG["max_total_usdt"]:
            if qty <= 0:
                return {"action": None, "reason": "숏 손절 모드 — 정리할 물량 없음"}
            return {"action": "short_stop", "reason":
                    f"기준가 {ref:,.4f} 대비 +{CFG['step_pct']:.0f}% 추가 상승 — 숏 손절 단계 도달"}
        return {"action": "short_open", "reason": f"기준가 {ref:,.4f} 대비 +{CFG['step_pct']:.0f}% 도달"}

    if price <= drop_trigger:
        if qty <= 0:
            return {"action": "short_follow", "reason": f"숏 물량 없음 — 기준가를 {price:,.4f}로 하향 추종"}
        return {"action": "short_cover", "reason": f"기준가 {ref:,.4f} 대비 -{CFG['step_pct']:.0f}% 도달"}

    return {"action": None, "reason": f"[숏 모드] 대기 (기준가 {ref:,.4f}, "
            f"청산트리거 {drop_trigger:,.4f} / 진입트리거 {rise_trigger:,.4f})"}

def run_short(state: dict, price: float, send: bool, now: str):
    # 손절 모드 해제: 가격이 손절 시작 기준가까지 내려옴
    msg = None
    if state.get("stop_active") and price <= state.get("stop_origin", 0):
        origin = state.get("stop_origin")
        for k in ("stop_active", "stop_base_qty", "stop_origin"):
            state.pop(k, None)
        print(f"  ✅ 가격이 숏 손절 시작 기준가 {origin:,.4f}까지 하락 — 숏 손절 모드 해제")
        msg = (f"✅ <b>{CFG['symbol']} 숏 손절 모드 해제</b>\n"
               f"가격이 숏 손절 시작 기준가 {origin:,.4f}까지 내려왔습니다. 일반 숏 그리드로 복귀합니다.")

    result = check_grid_short(state, price)
    action = result["action"]
    print(f"  {result['reason']}")
    ref_before = state.get("reference_price") or price
    est = lambda info: " (추정치)" if info.get("estimated") else ""

    if action == "short_follow":
        state["reference_price"] = price

    elif action == "short_open":
        ok, detail, qty, info = place_order(CFG["symbol"], "sell", CFG["order_usdt"], price,
                                             reduce_only=False, dry_run=not send)
        if ok:
            aq = info.get("filled_qty", qty)
            state["qty"] = state.get("qty", 0.0) + aq
            state["holdings_usdt"] = state.get("holdings_usdt", 0.0) + CFG["order_usdt"]
            state["reference_price"] = price
            msg = (f"🔻 <b>{CFG['symbol']} 숏 진입</b> ({CFG['step_pct']:.0f}% 상승)\n"
                   f"💰 금액: {info.get('cost', 0):,.2f} USDT{est(info)}\n"
                   f"💵 체결가: {info.get('avg_price', price):,.4f} USDT\n"
                   f"📦 수량: {aq:.6f}개\n"
                   f"누적 숏: {state['holdings_usdt']:.0f} / {CFG['max_total_usdt']:.0f} USDT\n{detail}")
            print(f"  🔻 숏 진입: {detail}")
        else:
            print(f"  ❌ 숏 진입 실패: {detail}")
            if not ("40762" in detail or "exceeds the balance" in detail):
                msg = f"❌ <b>{CFG['symbol']} 숏 진입 실패</b>\n{detail}\n다음 점검에서 재시도됩니다"

    elif action in ("short_cover", "short_stop"):
        have = state.get("qty", 0.0)
        first = False
        if action == "short_cover":
            qty_to_buy = (CFG["order_usdt"] * CFG["leverage"]) / price
            if have - qty_to_buy < qty_to_buy * 0.5 or have < qty_to_buy:   # 잔량이 한 칸 못 되면 전량
                qty_to_buy = have
            label = f"🔺 <b>{CFG['symbol']} 숏 청산</b> ({CFG['step_pct']:.0f}% 하락)"
        else:
            first = not state.get("stop_active")
            base_qty = have if first else state.get("stop_base_qty", have)
            qty_to_buy = min(base_qty * CFG["stop_loss_frac"], have)
            if have - qty_to_buy < (CFG["order_usdt"] * CFG["leverage"]) / price:
                qty_to_buy = have
            label = (f"🛑 <b>{CFG['symbol']} 숏 손절</b> ({'손절 시작' if first else '손절 진행'}, "
                     f"시작 수량의 {CFG['stop_loss_frac']*100:.0f}%)")
        ok, detail, qty, info = place_order(CFG["symbol"], "buy", 0, price,
                                             reduce_only=True, dry_run=not send,
                                             qty_override=qty_to_buy)
        if ok:
            aq = info.get("filled_qty", qty_to_buy)
            if action == "short_stop" and first:
                state["stop_active"] = True
                state["stop_base_qty"] = base_qty
                state["stop_origin"] = ref_before
            frac_left = max(0.0, have - aq) / have if have > 0 else 0.0
            state["qty"] = max(0.0, have - aq)
            state["holdings_usdt"] = state.get("holdings_usdt", 0.0) * frac_left
            state["reference_price"] = price
            fp = info.get("avg_price", price)
            leg_ret = (ref_before - fp) / ref_before * 100      # 숏은 내려야 이익
            leg_profit = aq * (ref_before - fp)
            msg = (f"{label}\n"
                   f"💰 금액: {info.get('cost', 0):,.2f} USDT{est(info)}\n"
                   f"💵 체결가: {fp:,.4f} USDT\n"
                   f"📦 수량: {aq:.6f}개\n"
                   f"📊 직전 기준가 대비 수익률(숏): {leg_ret:+.2f}%\n"
                   f"💸 손익(직전 기준가 대비, 수수료 제외): {leg_profit:+,.2f} USDT\n"
                   f"남은 숏: {state['qty']:.0f}개 (약 {state['qty']*price:,.1f} USDT)\n{detail}")
            print(f"  {'🛑' if action == 'short_stop' else '🔺'} 숏 청산 체결: {detail}")
            if action == "short_stop" and state["qty"] <= 0:
                for k in ("stop_active", "stop_base_qty", "stop_origin"):
                    state.pop(k, None)
                state["mode"] = "long"
                state["reference_price"] = price
                state["holdings_usdt"] = 0.0
                state.pop("notified_budget_full", None)
                msg += (f"\n\n🔁 <b>숏 전량 손절 완료 → 롱 모드 복귀</b>\n"
                        f"롱 기준가 {price:,.4f} — -{CFG['step_pct']:.0f}% 내리면 {CFG['order_usdt']:.0f}USDT 매수")
        else:
            msg = f"❌ <b>{CFG['symbol']} 숏 청산 실패</b>\n{detail}\n다음 점검에서 재시도됩니다"
            print(f"  ❌ 숏 청산 실패: {detail}")

    if send:
        save_state(state)
    else:
        print("  (--check 모드: 상태 저장 생략)")
    if msg:
        if send:
            send_discord(msg)
            print("\n  ✅ 알림 발송")
        else:
            print("\n  [--check, 발송 생략]")
            print("  " + msg.replace("<b>","").replace("</b>",""))
    else:
        print("\n  액션 없음 (대기)")

# ══════════════════════════════════════════════════════
#  실행
# ══════════════════════════════════════════════════════
def _normalize_symbol(x: str) -> str:
    x = x.strip().upper()
    if "/" in x:
        return x
    base = x[:-4] if x.endswith("USDT") else x
    return f"{base}/USDT:USDT"

def _tag():
    """프리셋에 state_tag가 있으면 상태 파일 이름에 붙인다(같은 종목의 모의 봇이 실전 봇 장부·엑셀과 섞이지 않게, 2026-10-27)."""
    if CFG.get("state_tag") and CFG.get("state_file"):
        CFG["state_file"] = CFG["state_file"].replace("_state.json", f"_{CFG['state_tag']}_state.json")

def _apply_cli(args: list):
    """--preset 이름 / --symbol / --live / --set k=v 를 CFG에 반영. 반환: 종목이 지정됐는지.
    프리셋은 coin_grid_presets/<이름>.json (이전에 쓰던 종목별 설정을 보관). --symbol 이 있으면 프리셋 종목보다 우선한다."""
    if "--preset" in args:
        i = args.index("--preset")
        if i + 1 >= len(args):
            print("  ❌ --preset 뒤에 이름을 적어야 합니다 (coin_grid_presets/ 폴더 참고)"); sys.exit(1)
        pth = os.path.join(os.path.dirname(os.path.abspath(__file__)), "coin_grid_presets", args[i + 1] + ".json")
        try:
            pr = json.load(open(pth, encoding="utf-8"))
        except Exception as e:
            print(f"  ❌ 프리셋을 읽을 수 없습니다: {pth} ({e})"); sys.exit(1)
        CFG.update(pr.get("cfg", {}))
        if pr.get("symbol") and "--symbol" not in args:
            args = args + ["--symbol", pr["symbol"]]
    if "--symbol" in args:
        i = args.index("--symbol")
        if i + 1 >= len(args):
            print("  ❌ --symbol 뒤에 종목을 적어야 합니다"); sys.exit(1)
        CFG["symbol"] = _normalize_symbol(args[i + 1])
        base = CFG["symbol"].split("/")[0].lower()
        CFG["state_file"] = f"coin_grid_{base}_state.json"
    for j, a in enumerate(args):
        if a == "--set" and j + 1 < len(args) and "=" in args[j + 1]:
            k, v = args[j + 1].split("=", 1)
            try:
                CFG[k] = json.loads(v)
            except ValueError:
                CFG[k] = v
    if "--live" in args:
        CFG["auto_trade"] = True
    _tag()
    return bool(CFG["symbol"])

if __name__ == "__main__":
    args = sys.argv[1:]
    if "--find-symbol" not in args and not _apply_cli(args):
        print("  ❌ 종목을 지정하세요: python coin_grid_bot.py --symbol ABC [--check|--status|--live ...]  (자세한 사용법은 파일 맨 위 설명 참고)")
        sys.exit(1)

    if "--find-symbol" in args:
        idx = args.index("--find-symbol")
        kw = args[idx+1] if idx+1 < len(args) else "BTC"
        find_symbol(kw)
        sys.exit(0)

    if "--resume" in args:
        st = load_state()
        for k in ("halted", "halted_reason", "rest_until", "rest_reason", "peak_price", "trail_fail_notified", "buy_paused", "buy_paused_reason", "surge", "surge_peak", "surge_start", "surge_fail_notified", "surge_base", "surge_k", "surge_below_n", "px_hist", "ref_hist", "crash_on", "crash_low", "crash_low_t"):
            st.pop(k, None)
        px = fetch_price(CFG["symbol"])
        if px > 0:
            st["reference_price"] = px
            st["peak_price"] = px
        save_state(st)
        print(f"  ✅ 정지 해제 — 기준가·고점을 현재가 {px:,.4f}로 재설정 (봇 보유 수량 {st.get('qty', 0):.4f}개 유지)")
        sys.exit(0)

    if "--reset" in args:
        if os.path.exists(CFG["state_file"]):
            os.remove(CFG["state_file"])
        print("  ✅ 그리드 상태 초기화 완료 (실제 보유 코인은 그대로입니다)")
        sys.exit(0)

    if "--status" in args:
        st = load_state()
        if not st or st.get("reference_price") is None:
            print("  아직 시작 안 함 (기준가 미설정)")
        else:
            print(f"  모드: {'숏' if st.get('mode') == 'short' else '롱'}")
            print(f"  기준가: {st['reference_price']:,.4f}")
            if st.get("rest_until"):
                print(f"  😴 고점 청산 후 휴식 중 — 약 {max(0, (st['rest_until'] - time.time()) / 60):.0f}분 뒤 자동 재개 ({st.get('rest_reason', '')})")
            if st.get("peak_price"):
                print(f"  고점: {st['peak_price']:,.4f}  ({'매수중단선' if CFG.get('trail_action') == 'pause_buy' else '청산선'} {st['peak_price']*(1-CFG['trail_exit_pct']/100):,.4f})"
                      f"{'  ⏸️ 신규 매수 중단: ' + st.get('buy_paused_reason', '') if st.get('buy_paused') else ''}"
                      f"{'  ⏸️ 정지: ' + st.get('halted_reason','') if st.get('halted') else ''}")
            if st.get("crash_on"):
                print(f"  📉 급락 대응 중: 저점 {st.get('crash_low', 0):,.4f} — 신규 매수 중단")
            if st.get("surge"):
                print(f"  🚀 급등 모드: 고점 {st.get('surge_peak', 0):,.4f} (매도선 {st.get('surge_peak', 0)*(1-CFG['surge_trail_pct']/100):,.4f}) — 그리드 매도·신규 매수 중지")
            print(f"  레버리지: {CFG['leverage']}배")
            print(f"  보유 수량: {st.get('qty',0):.4f}")
            print(f"  누적 증거금: {st.get('holdings_usdt',0):.0f} / {CFG['max_total_usdt']:.0f} USDT")
            print(f"  누적 노출액(레버리지 반영): {st.get('holdings_usdt',0)*CFG['leverage']:.0f} USDT")
        sys.exit(0)

    run_check(send=("--check" not in args))
