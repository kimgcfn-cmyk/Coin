# 재생 모의(백테스트) 스크립트 모음

1분봉(Bitget 공개 시세)을 코인 봇 코드(`coin_avg_ladder_bot.step`)에 그대로 흘려보내는 재생 모의입니다. 실제 주문은 없습니다.

- `cap_pair_bt.py` — 1분봉 내려받기(`candles`)와 초기 단순 시뮬. 다른 스크립트가 불러 씁니다.
- `hold2_bt.py` / `hold3_bt.py` — 누적수익률·MDD·**평균 보유시간** 비교(롱숏합동전략). `V` 목록의 설정만 바꿔서 실행합니다.
- `limit_bt.py`(한도), `gap2_bt.py`(진입 속도 제한), `risk3_bt.py`(헤지·소프트컷), `entryref3_bt.py`(진입 기준 N분 평균), `trend2_bt.py`(추세 보유), `relax_bt.py`(반대쪽 완화), `derisk2_bt.py`(노출 축소), `maxstage_bt.py`(단계 제한), `qshort_bt.py`(숏 단독) — 개별 아이디어 비교 기록.
- `mini_eval.py` — 엑셀 수식을 LibreOffice 없이 검증하는 간이 평가기(`python3 mini_eval.py 파일.xlsx`).
- `pair_screen.py` — 종목 스크리닝 초기 버전.

실행: 이 폴더에서 `python3 hold2_bt.py` (Coin 폴더의 `coin_avg_ladder_bot.py`, `coin_grid_bot.py`를 경로로 불러옵니다).
수수료 0.04%, 슬리피지 0.15%, 시작 보유 0, 60분 평균용 시세는 직전 70분을 미리 채워 시작합니다.
**주의:** 일부 스크립트는 옛 경로(`/private/tmp/...`)를 가정하는 줄이 있을 수 있으니 실행 전 확인하세요.
