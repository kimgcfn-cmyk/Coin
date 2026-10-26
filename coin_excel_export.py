"""
코인 봇 수익률 엑셀 자동 생성 — T·ORDER 숏 봇, CAP 롱 봇의 체결 내역을 서버 로그에서 읽어 엑셀 3개를 만든다 (2026-10-26)
================================================================================
사용:  python3 coin_excel_export.py                  # 코인봇수익률 폴더에 엑셀 3개를 새로 만든다(덮어쓰기)
       python3 coin_excel_export.py --out 폴더경로      # 저장 폴더 지정
동작:  ssh(trading-ec2)로 서버의 봇 로그·상태 파일을 읽고(읽기만 함, 주문·변경 없음) → 체결 내역을 파싱 →
       Bitget 공개 시세로 현재가를 가져와 미실현 평가 → 엑셀 저장(임시 파일에 쓴 뒤 교체). 맥의 launchd가 5분마다 실행한다.
주의:  체결 금액·수량은 봇 로그 값이다. 수수료는 시장가(테이커) 0.04%로 계산한다. 과거 결과일 뿐 투자 조언이 아니다.
"""
import sys, os, re, json, subprocess, datetime as dt
import requests
import coin_excel_builder as b
from coin_excel_builder import add_shadow_sheet

OUT = "/Users/Muita/Desktop/Coin/코인봇수익률"
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "trading-ec2"]
BOTS = {"T": ("coin_grid_short_t", "TUSDT"), "ORDER": ("coin_grid_short_order", "ORDERUSDT"), "CAP": ("coin_grid_cap", "CAPUSDT")}
# 로그에 없는 체결(사용자 지시로 직접 낸 청산 주문). 주문 응답 값이고 시각은 근사다.
EXTRA = {"T": [dict(t="2026-10-25 22:47", label="수동 청산 환매", id="manual-t-close", amt=251.8573, qty=45160.0)],
         # 사용자가 직접 추가한 ORDER 숏 2,230개를 봇 장부에 편입(평균가 0.042681). 로그에 없는 건이라 직접 입력, 시각은 근사
         "ORDER": [dict(t="2026-10-26 10:05", label="수동 숏 편입 진입", id="manual-order-add", amt=95.1786, qty=2230.0)]}

LC = "='설명·가정'!B6"; LV = "='설명·가정'!B7"; FE = "='설명·가정'!B5"

def short_rules(name):
    sma = (name in ("ORDER", "T"))
    return [
     (("기준가 (직전 5분 평균)", "직전 5분 동안 1분마다 기록한 시세의 평균(현재가 제외). 매 점검(1분)마다 새로 계산합니다. 가동 직후 5분은 기록이 모자라 거래하지 않습니다.", "체결과 상관없이 시장 평균이 기준입니다. 숏 물량이 없을 때 기준가가 가격을 따라 내려가는 규칙은 없어졌습니다.") if sma else
      ("기준가", "마지막 체결가(처음 가동 시에는 그때 시세). 숏 물량이 없을 때 가격이 내려가면 기준가도 따라 내려갑니다.", "기준가를 기준으로 진입·환매 신호를 판단하고, 체결될 때마다 기준가를 체결가로 바꿉니다.")),
     ("숏 진입", "가격이 기준가보다 +0.5% 이상 올랐을 때 (한도 미달일 때만). 평균이 따라 올라올 때까지 매 점검마다 반복될 수 있음" if sma else "가격이 기준가보다 +0.5% 이상 올랐을 때 (한도 미달일 때만)", "50 USDT어치를 시장가로 숏 진입합니다. 점검 1회에 1건."),
     ("환매 (이익 실현)", "가격이 기준가(평균)보다 −1% 이상 내려왔을 때, 숏 물량이 있을 때. 평균이 더 높으면 진입가보다 손실로 환매될 수도 있음" if sma else "가격이 기준가보다 −1% 이상 내려왔을 때, 숏 물량이 있을 때", "100 USDT어치를 시장가로 환매합니다. 남는 물량이 100 USDT의 절반 미만이면 전량 환매합니다."),
     ("급락 모드", "1분 안에 고점 대비 −3% 이상 급락", "추격 숏 100 USDT를 열고, 신규 진입을 멈춥니다. 저점 대비 +3% / +4.5% / +6% 반등에서 3단계로 나눠 환매합니다(3번 연속 확인, 점검당 1단계). 숏이 0이면 저점 +3% 반등 시 모드 종료."),
     ("급등 보호", "5분 안에 +8% 이상, 또는 1분 안에 +3% 이상 급등", "신규 숏만 멈춥니다(보유 숏은 유지). 급등 고점에서 −2% 되돌리거나 30분 동안 새 고점이 없으면 재개하고 기준가를 현재가로 새로 잡습니다."),
     ("저점 대비 +15% 중단", "보유 숏이 있고 가격이 저점 대비 +15% 이상", "신규 숏을 멈추고 보유 숏 환매만 감시합니다. 물량이 0이 되면 자동 재개."),
     ("강제 손절", "가격이 평균 진입가 +10% 이상", "봇 물량 전량을 시장가로 환매하고 봇을 정지합니다(재개는 수동)."),
     ("실행 방식", "서버에서 1분마다 점검(크론)", "모든 주문은 시장가(테이커 수수료 0.04%). 수동 포지션은 봇 장부와 분리해서 건드리지 않습니다."),
     ("손익 계산", "이동평균법 (평균 진입가)", "환매 수량 × (평균 진입가 − 환매가) − 수수료. 거래내역 시트에서 확인합니다."),
    ]
def short_params(cap_note, sma=False):
    return ([("기준가 방식", "직전 5분 평균가", "ref_mode sma, ref_sma_min 5 (last 로 바꾸면 마지막 체결가 방식)")] if sma else []) + [
     ("숏 진입 간격 / 크기", "+0.5% / 50 USDT", "프리셋 step_pct 0.5, order_usdt 50"),
     ("환매 간격 / 크기", "−1% / 100 USDT", "프리셋 sell_step_pct 1.0, sell_order_usdt 100"),
     ("급락 추격 숏", "100 USDT", "프리셋 surge_chase_usdt 100"),
     ("총 숏 한도 (명목)", LC, cap_note),
     ("계정 레버리지", LV, "교차 마진. 봇은 명목금액 기준으로 주문하므로 크기는 레버리지와 무관"),
     ("거래 수수료율", FE, "시장가(테이커) 편도. 진입·환매 모두 부과"),
     ("강제 손절", "평균 진입가 +10%", "프리셋 short_stop_pct 10"),
     ("급락 모드 기준", "1분 −3%, 확인 3회, 3단계(+3/+4.5/+6%)", "롱 봇 급등 모드를 뒤집은 값(surge_pct 3, surge_ladder [3,4.5,6], surge_confirm_min 3)"),
     ("급등 보호 기준", "5분 +8% 또는 1분 +3%, 해제 −2% 또는 30분", "crash_pct 8, rally_fast_pct 3, crash_rebound_pct 2, crash_quiet_min 30"),
     ("저점 대비 중단", "+15%", "trail_exit_pct 15"),
    ]
SHORT_FIT = [
 ("유리한 장", "위아래로 크게 왕복하는 박스형 횡보, 완만한 하락. 반등 때 쌓은 숏을 −1% 되돌림에서 계속 회수합니다."),
 ("불리한 장", "한 방향으로 계속 오르는 상승장(숏이 쌓이며 평가손실 확대 → 평균가 +10% 손절), 갑작스런 펀딩·급등. 숏은 이론상 손실에 상한이 없어서 손절이 유일한 한도입니다."),
 ("쉬는 장", "숏이 없는 상태에서 가격이 일방적으로 내려가기만 하면 진입 신호가 없어 거래도 수익도 없습니다(손실도 없음)."),
]
SHORT_BT = [
 "· TUSDT (진입 100·환매 150·한도 1000, 환매 −2%): 최근 10/20/40시간 −29.5 / −61.5 / −56.1 USDT — 모두 오르는 구간이라 평균가 +10% 손절이 1회씩 발생.",
 "· ORDER (진입 +0.5%/50·환매 −1%/100·한도 1000): 20시간 +5.0, 40시간 −94.2 USDT — 상승 구간에서는 손실, 되돌림이 있는 구간에서는 이익.",
 "· 환매 −2%/200 대 −1%/100 (5종목 10구간 합계): −127 대 −172 USDT. 구간별 우열이 일정하지 않아 차이는 큰 손실 구간이 좌우함.",
 "· 환매에 '평균가보다 0.2% 이상 낮을 때만' 조건을 붙인 안은 합계 −115 vs −168로 나았지만 상승 구간 방어는 못 해 적용하지 않고 원래대로 되돌렸습니다.",
]
def mk_short(name, cap_note, history, subtitle):
    bt = SHORT_BT + (SMA_SHORT_BT if name in ("ORDER", "T") else [])
    return dict(subtitle=subtitle, rules=short_rules(name), params=short_params(cap_note, name in ("ORDER", "T")), history=history, fit=SHORT_FIT, backtests=bt)

SMA_SHORT_BT = [
 "· 기준가를 직전 N분 평균으로 바꾼 숏 백테스트(14종목 28구간, 슬리피지 0.28%): 현재 방식 −707 USDT, 5분 평균 −377, 10분 −751, 30분 −1,041. 5분 평균은 횡보(+40 대 −181)·하락(+237 대 +19) 구간에서 낫고 상승 구간에서는 더 나빴습니다(−654 대 −545). ORDER 자기 구간(2개)에서는 5분 평균이 오히려 더 나빴습니다(−40/−128 대 −23/−117).",
]
T_HIST = [
 ("10/25 12:34", "가동 시작: 진입 +1%에 20 USDT, 환매 −2%에 30 USDT, 한도 200, 레버리지 3배", "첫 가동 (이전 수동 숏 청산 후)"),
 ("10/25 16:4x", "진입 100 USDT, 환매 150 USDT, 한도 1000, 레버리지 5배로 확대", "그 직후 ORDER로 전환하며 T 봇 중지 (16:46)"),
 ("10/25 20:17", "T 봇 재가동: 진입 +1%에 100 USDT, 환매 −2%에 200 USDT(ORDER와 동일)", "급락 추격 200 USDT"),
 ("10/25 21:29", "진입 간격 +0.5%, 진입 크기 50 USDT", "ORDER와 함께 변경"),
 ("10/25 21:46", "급등 보호에 '1분 +3%' 조건 추가(신규 숏 중단)", "코드 추가"),
 ("10/25 22:08", "환매 −1%에 100 USDT, 급락 추격 100 USDT", "ORDER와 함께 변경"),
 ("10/25 22:33~22:44", "환매 '평균가 대비 0.2% 이익일 때만' 조건을 켰다가 원래대로 끔", ""),
 ("10/25 22:47", "사용자 지시로 봇 중지 및 숏 45,160개 시장가 전량 청산", "마지막 체결 행(수동 청산)"),
 ("10/26 11:37", "T 숏 봇 재가동 (기준가·저점을 현재가 0.004914로 재설정)", "이전 누적 실현 +6.90 USDT 이어서 계산"),
 ("10/26 11:45", "기준가를 직전 5분 평균가(ref_mode=sma)로 변경 — 이제 숏 봇의 기본전략", "ORDER와 동일. 마지막 체결가 방식으로 되돌리려면 ref_mode=last"),
]
O_HIST = [
 ("10/25 16:46", "ORDER 숏 봇 가동: 진입 +1%에 100 USDT, 환매 −2%에 150 USDT, 한도 1000, 레버리지 5배", "TUSDT 설정을 복사해 시작"),
 ("10/25 17:01", "환매 200 USDT, 급락 추격 200 USDT로 상향", "진입 100 USDT 유지"),
 ("10/25 21:29", "진입 간격 +0.5%, 진입 크기 50 USDT", "T 봇과 함께 변경"),
 ("10/25 21:46", "급등 보호에 '1분 +3%' 조건 추가(신규 숏 중단)", "코드 추가"),
 ("10/25 21:56", "급락 모드를 수동으로 해제하고 기준가를 현재가로 재설정", "사용자 지시"),
 ("10/25 22:08", "환매 −1%에 100 USDT, 급락 추격 100 USDT", "T 봇과 함께 변경"),
 ("10/25 22:33~22:44", "환매 '평균가 대비 0.2% 이익일 때만' 조건을 켰다가 원래대로 끔", ""),

 ("10/26 09:59", "기준가를 마지막 체결가에서 직전 5분 평균가(ref_mode=sma)로 변경", "백테스트 근거는 5분 평균 −377 대 현재 −707 USDT (28구간)"),
 ("10/26 10:05", "사용자가 직접 추가한 숏 2,230개(평균 0.042681)를 봇 장부에 편입해 자동 환매·손절 대상에 포함", "이전 수동 숏 100개는 계속 분리"),
]
CAP_RULES = [
 ("기준가 (직전 5분 평균)", "직전 5분 동안 1분마다 기록한 시세의 평균(현재가 제외). 매 점검(1분)마다 새로 계산합니다. 가동·재개 직후 5분은 기록이 모자라 거래하지 않습니다.", "체결과 상관없이 시장 평균이 기준입니다. 보유가 없을 때 기준가가 가격을 따라 올라가는 규칙은 없어졌습니다. (10/26 11:5x 이전에는 마지막 체결가 기준)"),
 ("매수", "가격이 기준가(평균)보다 −0.5% 이상 내려왔을 때 (한도 미달일 때만, 급락 대응 중이 아닐 때)", "50 USDT어치를 시장가로 매수합니다. 점검 1회에 1건."),
 ("매도 (이익 실현)", "가격이 기준가(평균)보다 +1% 이상 올랐을 때, 보유가 있을 때. 평균이 내 평균 매수가보다 낮으면 손실로 팔릴 수도 있음", "100 USDT어치를 시장가로 매도합니다. 남는 물량이 100 USDT의 절반 미만이면 전량 매도합니다."),
 ("급등 모드 (꺼짐)", "1분 안에 저점 대비 +3% 이상 급등 — 10/26부터 사용 안 함", "(이전 설정) 추격 매수 100 USDT. 그리드 매수·매도를 멈추고, 급등 고점 대비 −3% / −4.5% / −6%에서 3단계로 나눠 매도합니다(3번 연속 확인, 점검당 1단계). 새 고점이 나오면 남은 단계는 새 고점 기준으로 다시 잽니다."),
 ("급락 대응", "5분 안에 고점 대비 −8% 이상 급락", "신규 매수만 멈춥니다(보유분 매도는 그대로). 저점 대비 +2% 반등하거나 30분 동안 새 저점이 없으면 재개합니다."),
 ("트레일링 청산 + 휴식", "매수한 뒤 가격이 고점 대비 −10% 이하", "봇 물량 전량을 시장가로 청산하고 4시간(240분) 쉽니다. 휴식이 끝나면 기준가·고점을 현재가로 새로 잡고 자동 재개합니다. 보유가 0인 동안은 고점도 현재가로 계속 갱신(첫 매수 이후의 고점부터 잼)."),
 ("그림자 모드", "실거래와 별개로 같은 시세를 보며 가상 거래 기록", "기준가를 직전 5분 / 10분 평균가로 쓰는 두 가지 전략(B5, B10)을 가상으로 돌립니다(트레일 청산·급락 대응 포함, 슬리피지 0.28% 가정). 주문은 내지 않고, 그림자 비교 시트에서 실거래(A)와 비교합니다."),
 ("실행 방식", "서버에서 1분마다 점검(크론)", "모든 주문은 시장가(테이커 수수료 0.04%). 강제 손절은 없고 트레일링 청산이 하락 방어 역할을 합니다."),
 ("손익 계산", "이동평균법 (평균 매수가)", "매도 수량 × (매도가 − 평균 매수가) − 수수료. 거래내역 시트에서 확인합니다."),
]
CAP_PARAMS = [
 ("기준가 방식", "직전 5분 평균가", "ref_mode sma, ref_sma_min 5 (last 로 바꾸면 마지막 체결가 방식)"),
 ("매수 간격 / 크기", "−0.5% / 50 USDT", "프리셋 step_pct 0.5, order_usdt 50"),
 ("매도 간격 / 크기", "+1% / 100 USDT", "프리셋 sell_step_pct 1.0, sell_order_usdt 100"),
 ("급등 추격 매수", "100 USDT", "프리셋 surge_chase_usdt 100"),
 ("총 보유 한도 (명목)", LC, "프리셋 max_total_usdt 1000"),
 ("계정 레버리지", LV, "교차 마진. 봇은 명목금액 기준으로 주문하므로 크기는 레버리지와 무관"),
 ("거래 수수료율", FE, "시장가(테이커) 편도. 매수·매도 모두 부과"),
 ("급등 모드", "끔", "surge_mode false — 검증한 B5 전략에 없는 기능이라 끔 (켜면 1분 +3% 추격 매수·3단계 분할 매도)"),
 ("급락 대응 기준", "5분 −8%, 재개 +2% 또는 30분", "crash_pct 8, crash_rebound_pct 2, crash_quiet_min 30"),
 ("트레일링 청산 / 휴식", "고점 −10% / 240분", "trail_exit_pct 10, trail_action liquidate, trail_rest_min 240"),
 ("그림자 모드", "B5, B10 (평균 5분 / 10분)", "shadow_modes [5,10], shadow_slip 0.0028 — 가상 거래만 기록"),
]
CAP_HIST = [
 ("10/26 07:11", "CAP 롱 봇 가동: 매수 −1%에 50 USDT, 매도 +1.5%에 75 USDT, 한도 500, 레버리지 5배", "고점 −15%에서 신규 매수만 중단하는 기본 방식으로 시작"),
 ("10/26 07:40", "트레일링을 '고점 −10% 전량 청산 + 4시간 휴식 후 자동 재개'로 변경", "백테스트 후 사용자 선택 (코드 추가: trail_rest_min)"),
 ("10/26 09:59", "매수 −0.5%/50, 매도 +1%/100, 한도 1000, 급등 추격 100 USDT로 변경하고 그림자 모드(평균 5분·10분 가상 거래) 추가", "ORDER 숏 설정과 같은 간격·크기"),
 ("10/26 11:55", "기준가를 마지막 체결가에서 직전 5분 평균가(ref_mode=sma)로 변경, 급등 모드 끔", "사용자 판단: 그림자 비교에서 B5가 좋아 보여 실거래에 반영 (A 대비 B5 약 +2.7 대 +6.9 USDT, 1시간 40분 표본)"),
]
CAP_FIT = [
 ("유리한 장", "오르내림이 반복되는 상승·횡보장. −0.5% 눌림에서 사서 +1% 반등에서 파는 사이클이 반복됩니다(간격이 좁아 거래·비용이 늘어납니다)."),
 ("불리한 장", "고점 이후 계속 내려가는 하락 추세(보유가 쌓이며 평가손실). 트레일링(−10%)이 큰 하락은 막지만, 흔들림에 걸려 상승 이익을 일부 놓칠 수 있습니다."),
 ("쉬는 장", "눌림 없이 계속 오르기만 하면 매수 신호가 없어 거래·수익이 없습니다(백테스트에서는 분 단위 흔들림이 있어 실제로 쉬는 시간이 길지 않았음)."),
]
CAP_BT = [
 "· (이전 설정 −1%/50·+1.5%/75·한도 500 기준) CAP 최근 10/20/40시간 (슬리피지 0.05% 가정): +14.5 / +23.7 / +40.3 USDT. 스프레드 0.2%를 반영한 0.28% 가정에서는 +5.2 / +11.8 / +22.9 USDT.",
 "· 쉬는 문제를 막으려는 변형(간격 −0.5%, 시작 시 기본 물량, 완만 상승 추격)은 오르는 구간에서 모두 현재 설정보다 나빴습니다.",
 "· 트레일링 안전장치(6종목 12구간, 슬리피지 0.28%): 없음 −494, 10%+4시간 쉼 −283 USDT, 최악 낙폭 −250 → −105. 대신 CAP 상승 구간 이익이 +43.7 → +11.0으로 줄었습니다.",
 "· 기준가를 직전 5/10분 평균으로 바꾼 롱 안(14종목 28구간, 안전장치 포함, 슬리피지 0.28%): 상승 구간 6개에서 현재 +7 대비 5분 +442 / 10분 +360, 횡보·하락 구간에서는 같거나 나빴고 낙폭이 더 컸음(합계 현재 −317, 5분 +17, 10분 −99). 실거래에는 쓰지 않고 그림자 모드로 비교 중.",
 "· CAP은 24시간 +36% 뛴 뒤라 하락 반전 위험이 있고, 롱에는 강제 손절이 없습니다.",
]


KST = dt.timezone(dt.timedelta(hours=9))

def fetch_remote():
    """서버 로그(회전·압축 포함)에서 체결/시각 줄만 뽑고 상태 파일을 읽는다. 읽기 전용."""
    names = " ".join(v[0] for v in BOTS.values())
    cmd = ("cd /home/ec2-user/trading-bot && for n in %s; do echo \"##LOG $n\"; "
           "for f in $(ls -tr logs/$n.log* 2>/dev/null); do zcat -f \"$f\" | grep -E \"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9:]{5} UTC|체결 완료 \\(주문ID\"; done; "
           "echo \"##STATE $n\"; cat ${n}_state.json 2>/dev/null; echo; done; echo \"##SHADOW cap\"; cat coin_grid_cap_shadow.json 2>/dev/null; echo; echo \"##OUTCOME surge\"; cat coin_surge_outcomes.json 2>/dev/null; echo") % names
    r = subprocess.run(SSH + [cmd], capture_output=True, text=True, timeout=90)
    if r.returncode != 0 and not r.stdout:
        raise RuntimeError("서버 로그를 읽지 못했습니다: " + r.stderr.strip()[:200])
    return r.stdout

def split_sections(txt):
    sec = {}; cur = None
    for line in txt.splitlines():
        m = re.match(r"##(LOG|STATE|SHADOW|OUTCOME) (\S+)", line)
        if m: cur = (m.group(1), m.group(2)); sec[cur] = []; continue
        if cur: sec[cur].append(line)
    return sec

def parse_fills(lines):
    t = None; seen = set(); rows = []
    for l in lines:
        m = re.search(r"(\d{4}-\d\d-\d\d) (\d\d:\d\d) UTC", l)
        if m: t = dt.datetime.strptime(m.group(1) + " " + m.group(2), "%Y-%m-%d %H:%M")
        m = re.search(r"(?P<label>[^\n:]*?):? 체결 완료 \(주문ID (?P<id>\d+), 금액 (?P<amt>[\d.]+)USDT, 체결가 [\d.]+, 수량 (?P<q>[\d.]+)\)", l)
        if m and m.group("id") not in seen and t:
            seen.add(m.group("id")); lab = re.sub(r"[^\w\s/가-힣]", "", m.group("label")).strip()
            rows.append(dict(t=(t + dt.timedelta(hours=9)).strftime("%Y-%m-%d %H:%M"), label=lab, id=m.group("id"), amt=float(m.group("amt")), qty=float(m.group("q"))))
    rows.sort(key=lambda r: r["t"])
    return rows

def last_price(sym):
    try:
        r = requests.get("https://api.bitget.com/api/v2/mix/market/ticker", params={"symbol": sym, "productType": "USDT-FUTURES"}, timeout=15).json()
        return float(r["data"][0]["lastPr"])
    except Exception:
        return None

def save_atomic(path, builder_fn):
    tmp = path + ".tmp.xlsx"
    info = builder_fn(tmp); os.replace(tmp, path); return info

def main():
    out = OUT
    if "--out" in sys.argv: out = sys.argv[sys.argv.index("--out") + 1]
    os.makedirs(out, exist_ok=True)
    sec = split_sections(fetch_remote())
    now = dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    res = {}
    for key, (name, sym) in BOTS.items():
        fills = parse_fills(sec.get(("LOG", name), [])) + EXTRA.get(key, [])
        fills.sort(key=lambda r: r["t"])
        try: state = json.loads("\n".join(sec.get(("STATE", name), [])) or "{}")
        except Exception: state = {}
        px = last_price(sym)
        res[key] = (fills, state, px)
    # ── T ──
    fills, state, px = res["T"]
    save_atomic(os.path.join(out, "T봇_숏전략_수익률.xlsx"), lambda p: b.build(p, "T(TUSDT)", "short", fills, 1000, 5, px or 0.0, "조회 시점 시세 (자동 갱신)",
        f"서버 봇 로그의 실제 체결 {len(fills)}건으로 자동 생성한 파일입니다. 마지막 갱신 {now} KST.",
        ["· 마지막 행(10/25 22:47 KST 수동 청산)은 로그에 없는 체결이라 주문 응답 값을 직접 넣었고 시각은 근사입니다.", "· 기간 중 한도 200→1000, 진입 20→100→50 USDT, 레버리지 10→3→5배로 설정이 바뀌었습니다. 기준 자본은 현재 설정(1000÷5) 기준입니다."],
        strategy=mk_short("T", "프리셋 max_total_usdt 1000 (첫 가동 때는 200)", T_HIST, "TUSDT 가격이 기준가 대비 +0.5% 오를 때 숏을 쌓고 −1% 내리면 회수하는 '반대(숏) 그리드' 전략입니다. 10/26 11:37에 재가동했고 이 파일은 현재 설정을 요약합니다.")))
    # ── ORDER ──
    fills, state, px = res["ORDER"]
    save_atomic(os.path.join(out, "ORDER봇_숏전략_수익률.xlsx"), lambda p: b.build(p, "ORDER", "short", fills, 1000, 5, px or 0.0, "조회 시점 시세 (자동 갱신)",
        f"서버 봇 로그의 실제 체결 {len(fills)}건({fills[0]['t'] if fills else '-'} ~ {fills[-1]['t'] if fills else '-'} KST)으로 자동 생성한 파일입니다. 마지막 갱신 {now} KST.",
        ["· 거래소에는 사용자가 수동으로 들고 있는 ORDER 숏 100개가 따로 있지만 봇 거래가 아니라 이 표에 넣지 않았습니다. 10/26 10:05 KST에 사용자가 직접 추가한 숏 2,230개(평균 0.042681)는 봇 장부에 편입해서 표에 '수동 숏 편입' 행으로 넣었습니다(수수료는 같은 비율로 계산한 추정).", "· 기간 중 진입 크기 100→50 USDT, 환매 간격 −2%→−1% 등으로 설정이 바뀌었습니다. 기준 자본은 현재 설정(1000÷5) 기준입니다.",
         f"· 봇 상태 파일 비교: 누적 실현 {state.get('cum_pnl') or 0:.2f} USDT, 누적 수수료 {state.get('cum_fees') or 0:.2f} USDT (수수료 배분 방식 차이로 소폭 다를 수 있음)."],
        strategy=mk_short("ORDER", "프리셋 max_total_usdt 1000", O_HIST, "ORDER 가격이 기준가 대비 +0.5% 오를 때 숏을 쌓고 −1% 내리면 회수하는 '반대(숏) 그리드' 전략입니다. 현재 가동 중이며 이 파일은 현재 설정을 요약합니다.")))
    # ── CAP ──
    fills, state, px = res["CAP"]
    try: shadow = json.loads("\n".join(sec.get(("SHADOW", "cap"), [])) or "{}")
    except Exception: shadow = {}
    cap_intro = (f"서버 봇 로그의 실제 체결 {len(fills)}건으로 자동 생성한 파일입니다. 마지막 갱신 {now} KST." if fills else
                 f"CAP 롱 봇은 2026-10-26 07:11 KST에 시작했고 아직 체결이 없습니다(−1% 눌림 대기). 마지막 갱신 {now} KST. 체결이 생기면 자동으로 채워집니다.")
    save_atomic(os.path.join(out, "CAP봇_롱전략_수익률.xlsx"), lambda p: b.build(p, "CAP", "long", fills, 500, 5, px or 0.0, "조회 시점 시세 (자동 갱신)", cap_intro,
        ["· 체결 내역은 서버 로그에서 자동으로 채워집니다. 직접 수정한 내용은 다음 갱신 때 덮어써집니다."], spare=0 if fills else 60,
        strategy=dict(subtitle="CAP 가격이 기준가 대비 −0.5% 눌리면 사고 +1% 오르면 파는 롱 그리드 전략에 급등 추격·급락 대응·트레일링 청산(−10%, 4시간 휴식)을 더한 구성입니다. 10/26 11:55부터 기준가를 직전 5분 평균으로 쓰고, B5·B10 그림자 모드는 가상 비교를 계속합니다. 현재 가동 중입니다.",
                      rules=CAP_RULES, params=CAP_PARAMS, history=CAP_HIST, fit=CAP_FIT, backtests=CAP_BT),
        extra=lambda wb: add_shadow_sheet(wb, shadow, state, px)))
    # ── 급등 알림 사후 추적 ──
    try: surge = json.loads("\n".join(sec.get(("OUTCOME", "surge"), [])) or "{}")
    except Exception: surge = {}
    REF9 = [("ERAUSDT", 32, 0.09139, 0.08426), ("BRUSDT", 257, 0.47914, 0.49658), ("AKEUSDT", 576, 0.03008, 0.031291), ("QUSDT", 650, 0.07547, 0.056556), ("SKYAIUSDT", 873, 0.03662, 0.03408),
            ("TAGUSDT", 996, 0.0006758, 0.0004908), ("JCTUSDT", 1013, 0.007184, 0.005781), ("DOODUSDT", 1202, 0.001645, 0.001395), ("MAGMAUSDT", 1206, 0.38125, 0.34554)]
    save_atomic(os.path.join(out, "급등알림_사후추적.xlsx"), lambda p: b.build_surge_workbook(p, surge.get("alerts", []), REF9))
    print(f"[{now} KST] 저장 완료 → {out} | T {len(res['T'][0])}건, ORDER {len(res['ORDER'][0])}건, CAP {len(res['CAP'][0])}건")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("실패:", type(e).__name__, e); sys.exit(1)
