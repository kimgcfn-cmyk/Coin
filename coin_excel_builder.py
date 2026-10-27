"""
코인 봇 수익률 엑셀 생성기 (coin_excel_export.py 가 사용) — 거래내역·수익률·전략 요약 3개 시트로 된 xlsx 를 만든다 (2026-10-26)
================================================================================
build(...) 하나로 숏/롱 봇의 체결 목록을 받아 수식이 들어간 엑셀을 저장한다. 수식(이동평균 평균가, 수수료 배분, 누적 수익률)은 엑셀이 계산한다.
"""
import json
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.chart import LineChart, ScatterChart, Series, Reference
from openpyxl.chart.layout import Layout, ManualLayout
from openpyxl.chart.shapes import GraphicalProperties
from openpyxl.drawing.line import LineProperties
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.formula import Tokenizer
from openpyxl.utils import get_column_letter as L

AR = "Arial"
f_norm = Font(name=AR, size=10); f_bold = Font(name=AR, size=10, bold=True)
f_green = Font(name=AR, size=10, color="2E7D32")
f_in = Font(name=AR, size=10, color="0000FF"); f_head = Font(name=AR, size=10, bold=True, color="FFFFFF")
f_title = Font(name=AR, size=14, bold=True); f_note = Font(name=AR, size=9, italic=True, color="595959")
fill_head = PatternFill("solid", fgColor="1F3864"); fill_key = PatternFill("solid", fgColor="FFFF00")
fill_cover = PatternFill("solid", fgColor="F2F2F2")
thin = Side(style="thin", color="BFBFBF"); box = Border(left=thin, right=thin, top=thin, bottom=thin)
ctr = Alignment(horizontal="center", vertical="center", wrap_text=True)

import math
def add_strategy_sheet(wb, name, short, st):
    ws = wb.create_sheet("전략 요약", 0)
    sec_fill = PatternFill("solid", fgColor="D9E2F3")
    widths = {"A": 24, "B": 62, "C": 72}
    for k, w in widths.items(): ws.column_dimensions[k].width = w
    wrap = Alignment(wrap_text=True, vertical="top")
    def h(r, text, ncols=3):
        for j in range(1, ncols + 1):
            c = ws.cell(r, j); c.fill = sec_fill; c.border = box
        ws.cell(r, 1, text).font = f_bold
    def height(r, vals):
        lines = 1
        for col, v in zip("ABC", vals):
            if isinstance(v, str) and not v.startswith("="):
                lines = max(lines, math.ceil(len(v) * 1.75 / widths[col]) + v.count("\n"))
        ws.row_dimensions[r].height = max(15, 13.5 * lines)
    ws["A1"] = f"{name} {'숏' if short else '롱'} 그리드 봇 — 전략 요약"; ws["A1"].font = f_title
    ws["A2"] = st["subtitle"]; ws["A2"].font = f_note; ws.merge_cells("A2:C2"); ws["A2"].alignment = wrap; ws.row_dimensions[2].height = 28
    r = 4
    h(r, "1. 매매 규칙 (한눈에)"); r += 1
    for j, t in enumerate(["구분", "조건", "동작"], 1):
        c = ws.cell(r, j, t); c.font = f_head; c.fill = fill_head; c.alignment = ctr; c.border = box
    r += 1
    for a_, b_, c_ in st["rules"]:
        for j, v in enumerate([a_, b_, c_], 1):
            c = ws.cell(r, j, v); c.font = f_bold if j == 1 else f_norm; c.alignment = wrap; c.border = box
        height(r, [a_, b_, c_]); r += 1
    r += 1; h(r, "2. 현재 설정값"); r += 1
    for j, t in enumerate(["항목", "값", "설명 / 출처"], 1):
        c = ws.cell(r, j, t); c.font = f_head; c.fill = fill_head; c.alignment = ctr; c.border = box
    r += 1
    for a_, b_, c_ in st["params"]:
        ws.cell(r, 1, a_).font = f_bold
        cb = ws.cell(r, 2, b_); cb.font = f_norm
        if isinstance(b_, str) and b_.startswith("='설명·가정'"):
            cb.font = Font(name=AR, size=10, color="008000")        # 다른 시트 링크 = 초록
        cb.alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
        ws.cell(r, 3, c_).font = f_note
        for j in (1, 2, 3):
            ws.cell(r, j).border = box
            if j != 2: ws.cell(r, j).alignment = wrap
        height(r, [a_, b_, c_]); r += 1
    r += 1; h(r, "3. 설정 변경 이력 (한국시간, KST)"); r += 1
    for j, t in enumerate(["일시", "변경 내용", "비고"], 1):
        c = ws.cell(r, j, t); c.font = f_head; c.fill = fill_head; c.alignment = ctr; c.border = box
    r += 1
    for a_, b_, c_ in st["history"]:
        for j, v in enumerate([a_, b_, c_], 1):
            c = ws.cell(r, j, v); c.font = f_norm if j != 3 else f_note; c.alignment = wrap; c.border = box
        height(r, [a_, b_, c_]); r += 1
    r += 1; h(r, "4. 이 전략이 유리한 장 / 불리한 장"); r += 1
    for a_, b_ in st["fit"]:
        ws.cell(r, 1, a_).font = f_bold; c = ws.cell(r, 2, b_); c.font = f_norm; ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=3)
        c.alignment = wrap; ws.cell(r, 1).alignment = wrap
        for j in (1, 2, 3): ws.cell(r, j).border = box
        height(r, [a_, b_ + " " * 12, ""]); r += 1
    r += 1; h(r, "5. 참고 백테스트 (과거 데이터, 1분 종가 기준)"); r += 1
    for t_ in st["backtests"]:
        c = ws.cell(r, 1, t_); c.font = f_norm; c.alignment = wrap; ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=3)
        ws.row_dimensions[r].height = max(15, 13.5 * math.ceil(len(t_) * 1.75 / 158)); r += 1
    r += 1
    c = ws.cell(r, 1, "※ 과거 데이터와 시뮬레이션일 뿐 미래 수익을 보장하지 않으며 투자 조언이 아닙니다. 수수료는 시장가(테이커) 0.04% 기준입니다.")
    c.font = f_note; ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=3)
    ws.sheet_view.showGridLines = False
    return ws

def build(path, name, mode, fills, cap, lev, price, price_note, intro, extra_notes, spare=0, rate=0.0004, strategy=None, extra=None, now_kst=None):
    short = mode == "short"
    ENT, EXT = ("숏 진입", "환매") if short else ("매수", "매도")
    isE = (lambda lab: ("진입" in lab or "추격" in lab or "보강" in lab or "편입" in lab)) if short else (lambda lab: "매수" in lab)
    def kind(lab, entry):
        if "추격" in lab: return "급락 추격 숏" if short else "급등 추격 매수"
        if "분할" in lab: return "급락 분할 환매 " + lab.split()[-1] if short else "급등 분할 매도"
        if "편입" in lab: return "수동 숏 편입" if short else "수동 롱 편입"
        if "보강" in lab: return "하락 추세 숏 보강"
        if "사다리" in lab: return "하락 추세 사다리 환매"
        if "수동" in lab: return "수동 청산(시장가)"
        if "고점" in lab: return "고점 청산"
        return ("그리드 진입" if entry else "그리드 환매") if short else ("그리드 매수" if entry else "그리드 매도")
    wb = Workbook(); a = wb.active; a.title = "설명·가정"
    a["A1"] = f"{name} {'숏' if short else '롱'} 그리드 봇 — 매수·매도 과정과 수익률"; a["A1"].font = f_title
    a["A2"] = intro; a["A2"].font = f_note
    a["A3"] = "파란 글자 = 입력값, 검은 글자 = 수식, 노란 칸 = 핵심 가정. 거래내역의 노란 헤더 아래 파란 칸(일시·구분·내용·수량·금액·펀딩비)만 입력하면 나머지는 자동 계산됩니다."; a["A3"].font = f_note
    inputs = [("거래 수수료율 (테이커, 편도)", rate, "0.00%", "실제 체결 검증: 청산 251.8573 USDT에 수수료 0.10074 USDT = 0.04%. 시장가(테이커)라 진입·청산 모두 같은 비율"),
              ("봇 한도 (명목, USDT)", cap, "#,##0", "봇 프리셋의 max_total_usdt"),
              ("계정 레버리지 (배)", lev, "0", "교차 마진. 봇은 명목금액 기준 주문이라 손익 계산에는 영향 없음(기준 자본 계산에만 사용)")]
    a["A4"] = "입력값"; a["A4"].font = f_bold
    for i, (lab, val, fmt, note) in enumerate(inputs):
        r = 5 + i; a.cell(r, 1, lab).font = f_norm
        c = a.cell(r, 2, val); c.font = f_in; c.number_format = fmt; c.fill = fill_key; c.border = box
        a.cell(r, 3, note).font = f_note
    a["A8"] = "수익률 기준 자본 (증거금, USDT)"; a["A8"].font = f_norm
    a["B8"] = "=B6/B7"; a["B8"].font = f_norm; a["B8"].number_format = "#,##0.00"; a["B8"].border = box
    a["C8"] = "= 한도 ÷ 레버리지. 누적 수익률의 분모"; a["C8"].font = f_note
    a["A9"] = "기준 자본 직접 입력 (비우면 위 값 사용)"; a["A9"].font = f_norm
    a["B9"].font = f_in; a["B9"].fill = fill_key; a["B9"].border = box; a["B9"].number_format = "#,##0.00"
    a["A10"] = "실제 사용 기준 자본 (USDT)"; a["A10"].font = f_bold
    a["B10"] = "=IF(N(B9)>0,B9,B8)"; a["B10"].font = f_bold; a["B10"].number_format = "#,##0.00"; a["B10"].border = box
    a["A11"] = "현재가 (미실현 평가용, USDT)"; a["A11"].font = f_norm
    c = a["B11"]; c.value = price; c.font = f_in; c.fill = fill_key; c.border = box; c.number_format = "0.000000"
    a["C11"] = price_note; a["C11"].font = f_note
    a["A13"] = "열 설명"; a["A13"].font = f_bold
    pnl_def = "= 환매수량 × (평균 진입가 − 환매가). 숏이라 가격이 내리면 이익" if short else "= 매도수량 × (매도가 − 평균 매수가). 롱이라 가격이 오르면 이익"
    defs = [("체결가", "= 체결금액 ÷ 체결수량 (로그의 체결가는 소수 2자리로 잘려 있어서 금액÷수량으로 계산)"),
            ("보유금액", "두 열입니다. ① 레버리지 반영(명목) = 보유수량(거래 후) × 체결가 — 거래 직후 보유 물량의 포지션 크기. ② 레버리지 미반영(증거금) = ①을 레버리지(5배)로 나눈 값 — 거래소 화면에 투입금으로 보이는 금액. 과거에 레버리지가 달랐던 구간도 현재 설정(5배)으로 나눈 값입니다."),
            ("거래수수료", "= 체결금액 × 수수료율. 진입·청산 모두 부과"),
            ("펀딩비", "거래소가 정산하는 펀딩비. 봇이 기록하지 않아 0으로 둠 — 알면 입력(받으면 +, 내면 −)"),
            ("평균 " + ("진입가" if short else "매수가"), "이동평균법. 진입 시 (기존수량×기존평균 + 체결금액) ÷ 새 수량, 청산은 평균가 그대로, 물량 0이면 초기화"),
            ("미배분 진입수수료", "아직 청산되지 않은 물량에 붙은 진입 수수료. 청산할 때 청산 수량 비율만큼 그 청산의 비용으로 넘김"),
            ("실현손익(수수료 전)", pnl_def),
            ("순손익(수수료 후)", "= 수수료 전 손익 − 청산 수수료 − 배분된 진입 수수료 − 펀딩비"),
            ("수익률", "= 손익 ÷ (청산수량 × 평균가), 즉 청산한 물량의 진입 명목금액 대비. 수수료 전/후"),
            ("누적 수익률", "= 누적 손익 ÷ 기준 자본. 수수료 전/후"),
            ("미실현 평가(요약 시트)", "남은 물량을 현재가로 평가: 평가손익 − 미배분 진입수수료 − 예상 청산 수수료")]
    for i, (k, v) in enumerate(defs): a.cell(14 + i, 1, k).font = f_bold; a.cell(14 + i, 2, v).font = f_norm
    a["A25"] = "참고·한계"; a["A25"].font = f_bold
    for i, tx in enumerate(extra_notes + ["· 금액·수량은 봇 로그 값이며 거래소 체결 내역과 반올림 차이가 있을 수 있습니다.", "· 과거 결과일 뿐이며 투자 조언이 아닙니다."]): a.cell(26 + i, 1, tx).font = f_note
    a.column_dimensions["A"].width = 40; a.column_dimensions["B"].width = 16; a.column_dimensions["C"].width = 95

    t = wb.create_sheet("거래내역"); t["A1"] = f"{name} {'숏' if short else '롱'} 봇 — {ENT}·{EXT} 과정과 수익률"; t["A1"].font = f_title
    t["A2"] = f"행 1건 = 체결 1건. {EXT} 행(회색)에서 손익이 확정됩니다. 구분 칸에는 '{ENT}' 또는 '{EXT}'만 입력하세요."; t["A2"].font = f_note
    pa = "진입가" if short else "매수가"
    heads = ["No", "일시(KST)", "구분", "내용", "체결수량(개)", "체결금액(USDT)", "체결가(USDT)", "수수료율", "거래수수료(USDT)", "펀딩비(USDT, 입력)",
             "보유수량(거래 후)", "보유금액(USDT)\n레버리지 반영(명목)", "보유금액(USDT)\n레버리지 미반영(증거금)", "평균 " + pa, "미배분 진입수수료", "배분된 진입수수료", "실현손익 수수료 전(USDT)", "순손익 수수료 후(USDT)",
             "수익률 수수료 전", "수익률 수수료 후", "누적 손익 수수료 전(USDT)", "누적 순손익 수수료 후(USDT)", "누적 수익률 수수료 전", "누적 수익률 수수료 후", "누적 수수료(USDT)", "경과시간(시간)"]
    HR = 4
    for j, h in enumerate(heads, 1):
        c = t.cell(HR, j, h); c.font = f_head; c.fill = fill_head; c.alignment = ctr; c.border = box
    t.row_dimensions[HR].height = 42
    first = HR + 1; n = len(fills) + spare; last = first + n - 1
    E_ = f'"{ENT}"'
    for i in range(n):
        x = first + i; p = x - 1; d = fills[i] if i < len(fills) else None
        gd = f'IF($E{x}="","",'
        if d:
            entry = isE(d["label"])
            t.cell(x, 1, i + 1); t.cell(x, 2, d["t"]); t.cell(x, 3, ENT if entry else EXT); t.cell(x, 4, kind(d["label"], entry))
            t.cell(x, 5, d["qty"]); t.cell(x, 6, d["amt"])
        else:
            t.cell(x, 1, i + 1)
        t.cell(x, 10, 0 if d else None)
        t.cell(x, 7, f'={gd}F{x}/E{x})')
        t.cell(x, 8, f"={gd}'설명·가정'!$B$5)")
        t.cell(x, 9, f'={gd}F{x}*H{x})')
        t.cell(x, 11, f'={gd}N(K{p})+IF($C{x}={E_},E{x},-E{x}))')
        t.cell(x, 12, f'={gd}K{x}*G{x})')
        t.cell(x, 13, f"={gd}L{x}/'설명·가정'!$B$7)")
        t.cell(x, 14, f'={gd}IF($C{x}={E_},(N(K{p})*N(N{p})+F{x})/K{x},IF(ROUND(K{x},6)=0,0,N(N{p}))))')
        t.cell(x, 15, f'={gd}ROUND(IF($C{x}={E_},N(O{p})+I{x},N(O{p})-P{x}),10))')
        t.cell(x, 16, f'={gd}IF($C{x}={E_},0,N(O{p})*E{x}/N(K{p})))')
        t.cell(x, 17, f'={gd}IF($C{x}={E_},0,' + (f'E{x}*(N(N{p})-G{x})' if short else f'E{x}*(G{x}-N(N{p}))') + '))')
        t.cell(x, 18, f'={gd}IF($C{x}={E_},0,Q{x}-I{x}-P{x}-J{x}))')
        t.cell(x, 19, f'={gd}IF($C{x}={E_},0,Q{x}/(E{x}*N(N{p}))))')
        t.cell(x, 20, f'={gd}IF($C{x}={E_},0,R{x}/(E{x}*N(N{p}))))')
        t.cell(x, 21, f'={gd}N(U{p})+Q{x})'); t.cell(x, 22, f'={gd}N(V{p})+R{x})')
        t.cell(x, 23, f"={gd}U{x}/'설명·가정'!$B$10)"); t.cell(x, 24, f"={gd}V{x}/'설명·가정'!$B$10)")
        t.cell(x, 25, f'={gd}N(Y{p})+I{x})')
        t.cell(x, 26, f'={gd}ROUND((DATEVALUE(LEFT($B{x},10))+TIMEVALUE(MID($B{x},12,5))-DATEVALUE(LEFT($B${first},10))-TIMEVALUE(MID($B${first},12,5)))*24,4))')
        for j in range(1, 27):
            c = t.cell(x, j); c.font = f_in if j in (2, 3, 4, 5, 6, 10) else (f_green if j == 22 else f_norm); c.border = box
            if d and d["label"] and not isE(d["label"]) and not short and False: pass
            if d and not (isE(d["label"])): c.fill = fill_cover
            if j in (1, 3, 4): c.alignment = Alignment(horizontal="center")
    fm = {5: "#,##0", 6: "#,##0.00", 7: "0.000000", 8: "0.00%", 9: "0.0000", 10: "0.0000;[Red]-0.0000;-", 11: "#,##0", 12: "#,##0.00;;-", 13: "#,##0.00;;-", 14: "0.000000;;-", 15: "0.0000;;-", 16: "0.0000;;-",
          17: "0.0000;[Red]-0.0000;-", 18: "0.0000;[Red]-0.0000;-", 19: "0.00%;[Red]-0.00%;-", 20: "0.00%;[Red]-0.00%;-", 21: "0.0000;[Red]-0.0000;-", 22: "0.0000;[Red]-0.0000;-",
          23: "0.00%;[Red]-0.00%;-", 24: "0.00%;[Red]-0.00%;-", 25: "0.0000", 26: "0.00"}
    for x in range(first, last + 1):
        for j, f in fm.items(): t.cell(x, j).number_format = f
    for j, w in enumerate([5, 17, 9, 18, 12, 14, 13, 9, 13, 12, 13, 17, 17, 13, 13, 13, 15, 15, 11, 11, 16, 16, 13, 13, 12, 12], 1): t.column_dimensions[L(j)].width = w
    t.freeze_panes = t.cell(first, 5)
    dv = DataValidation(type="list", formula1=f'"{ENT},{EXT}"', allow_blank=True); t.add_data_validation(dv); dv.add(f"C{first}:C{last}")

    s = wb.create_sheet("요약"); s["A1"] = "요약 (거래내역 시트에서 자동 계산)"; s["A1"].font = f_title
    rg = lambda col: f"거래내역!${col}${first}:${col}${last}"
    last_num = lambda col: f"IFERROR(LOOKUP(9.99E+307,{rg(col)}),0)"
    now_kst = now_kst or globals().get("NOW_KST")
    elapsed_now = None
    if fills and now_kst:
        try:
            import datetime as _dt
            f0 = _dt.datetime.strptime(fills[0]["t"], "%Y-%m-%d %H:%M"); n0 = _dt.datetime.strptime(now_kst, "%Y-%m-%d %H:%M")
            elapsed_now = round(max(0.0, (n0 - f0).total_seconds() / 3600), 2)
        except Exception: elapsed_now = None
    items = [
     (f"{ENT} 체결 횟수", f'=COUNTIF({rg("C")},"{ENT}")', "0"),                      # 4
     (f"{EXT} 체결 횟수", f'=COUNTIF({rg("C")},"{EXT}")', "0"),                      # 5
     ("총 체결금액 (USDT)", f"=SUM({rg('F')})", "#,##0.00"),                           # 6
     (f"총 거래수수료 ({ENT}+{EXT}, USDT)", f"=SUM({rg('I')})", "0.0000"),             # 7
     ("총 펀딩비 (USDT)", f"=SUM({rg('J')})", "0.0000"),                               # 8
     ("실현손익 수수료 전 (USDT)", f"=SUM({rg('Q')})", "0.0000;[Red]-0.0000"),         # 9
     ("실현 순손익 수수료 후 (USDT)", f"=SUM({rg('R')})", "0.0000;[Red]-0.0000"),       # 10
     ("수수료가 손익에서 차지한 비중", "=IF(B9=0,0,B7/B9)", "0.0%"),                    # 11
     ("기준 자본 (USDT)", "='설명·가정'!B10", "#,##0.00"),                              # 12
     ("실현 누적 수익률 수수료 전", "=B9/B12", "0.00%;[Red]-0.00%"),                   # 13
     ("실현 누적 수익률 수수료 후", "=B10/B12", "0.00%;[Red]-0.00%"),                  # 14
     (f"승률 (순손익 > 0인 {EXT} 비율)", f'=IF(B5=0,0,COUNTIFS({rg("C")},"{EXT}",{rg("R")},">0")/B5)', "0.0%"),  # 15
     (f"{EXT} 1회 평균 순손익 (USDT)", "=IF(B5=0,0,B10/B5)", "0.0000;[Red]-0.0000"),    # 16
     ("현재 보유수량 (개)", f"={last_num('K')}", "#,##0"),                              # 17
     ("현재 평균 " + pa, f"={last_num('N')}", "0.000000"),                              # 18
     ("현재가 (USDT)", "='설명·가정'!B11", "0.000000"),                                 # 19
     ("미실현 평가손익 수수료 전 (USDT)", ("=IF(B17=0,0,B17*(B18-B19))" if short else "=IF(B17=0,0,B17*(B19-B18))"), "0.0000;[Red]-0.0000"),  # 20
     ("미배분 진입수수료 (USDT)", f"={last_num('O')}", "0.0000"),                       # 21
     ("예상 청산 수수료 (USDT)", "=B17*B19*'설명·가정'!B5", "0.0000"),                  # 22
     ("미실현 순손익 수수료 후 (USDT)", "=B20-B21-B22", "0.0000;[Red]-0.0000"),          # 23
     ("총 순손익 (실현+미실현, USDT)", "=B10+B23", "0.0000;[Red]-0.0000"),               # 24
     ("총 수익률 (기준 자본 대비)", "=B24/B12", "0.00%;[Red]-0.00%"),                    # 25
     ("── 단타 점검 (12~24시간 기준) ──", None, None),                                   # 26
     ("점검 시각까지 경과 (시간, 첫 체결부터)", elapsed_now, "0.0"),                      # 27
     ("최근 12시간 실현 순손익 (USDT)", f'=SUMIFS({rg("R")},{rg("Z")},">"&(B27-12))', "0.0000;[Red]-0.0000;-"),      # 28
     ("최근 12시간 체결 횟수", f'=COUNTIFS({rg("Z")},">"&(B27-12))', "0"),                                              # 29
     ("최근 12시간 수익률 (기준 자본 대비)", "=B28/B12", "0.00%;[Red]-0.00%;-"),                                       # 30
     ("최근 24시간 실현 순손익 (USDT)", f'=SUMIFS({rg("R")},{rg("Z")},">"&(B27-24))', "0.0000;[Red]-0.0000;-"),      # 31
     ("최근 24시간 체결 횟수", f'=COUNTIFS({rg("Z")},">"&(B27-24))', "0"),                                              # 32
     ("최근 24시간 수익률 (기준 자본 대비)", "=B31/B12", "0.00%;[Red]-0.00%;-"),                                       # 33
    ]
    s["A3"] = "항목"; s["B3"] = "값"
    for c in (s["A3"], s["B3"]): c.font = f_head; c.fill = fill_head; c.alignment = ctr
    for i, (lab, fml, fmt) in enumerate(items):
        r = 4 + i; bold = r in (10, 24, 25, 26, 28, 31)
        s.cell(r, 1, lab).font = f_bold if bold else f_norm
        c = s.cell(r, 2, fml)
        c.font = f_bold if bold else f_norm
        if fmt: c.number_format = fmt
        if fml is None: s.cell(r, 1).fill = PatternFill("solid", fgColor="D9E2F3"); c.fill = PatternFill("solid", fgColor="D9E2F3")
        s.cell(r, 1).border = box; c.border = box
    s.column_dimensions["A"].width = 40; s.column_dimensions["B"].width = 18
    s["A36"] = "※ 12·24시간 항목은 점검(파일 갱신) 시각 기준으로 직전 N시간에 체결된 환매·매도의 순손익입니다. 보유 중인 물량의 평가손익은 포함하지 않습니다."; s["A36"].font = f_note
    s["A35"] = "※ 수수료 비중 = 총 수수료 ÷ 수수료 전 실현손익. 미실현은 현재가(설명·가정 시트 B11) 기준 추정입니다."; s["A35"].font = f_note
    last_filled = first + len(fills) - 1 if fills else first
    ch = ScatterChart(); ch.title = "누적 순손익 (수수료 후, USDT)"; ch.style = 2
    ch.x_axis.title = "경과시간 (시간, 첫 체결 기준)"; ch.y_axis.title = "누적 순손익 (USDT)"
    xs = Reference(t, min_col=26, min_row=first, max_row=last_filled); ys = Reference(t, min_col=22, min_row=first, max_row=last_filled)
    se = Series(ys, xs, title="누적 순손익"); se.marker.symbol = "circle"; se.marker.size = 5; se.smooth = False
    se.graphicalProperties.line.width = 22000; se.graphicalProperties.line.solidFill = "2E7D32"      # 초록
    se.marker.graphicalProperties.solidFill = "2E7D32"; se.marker.graphicalProperties.line.solidFill = "2E7D32"
    ch.series.append(se)
    ch.legend = None                                   # 색인표(범례) 제거
    ch.x_axis.delete = False; ch.y_axis.delete = False  # 축 눈금·숫자 표시
    ch.y_axis.number_format = '#,##0.00'; ch.x_axis.number_format = '0.0'
    ch.x_axis.tickLblPos = "low"; ch.y_axis.tickLblPos = "low"        # 누적 손익이 마이너스여도 눈금 숫자는 가장자리에 표시
    # 글씨가 그래프 위에 겹치지 않게: 제목은 그래프 밖(위), 가로축 제목은 아래, 세로축 제목은 왼쪽 바깥에 두고 그래프 영역을 안쪽으로 줄인다
    for ttl in (ch.title, ch.x_axis.title, ch.y_axis.title):
        ttl.overlay = False
    ch.layout = Layout(manualLayout=ManualLayout(layoutTarget="inner", xMode="edge", yMode="edge", x=0.11, y=0.14, w=0.85, h=0.64))
    for ax in (ch.x_axis, ch.y_axis):
        ax.majorGridlines.spPr = GraphicalProperties(ln=LineProperties(solidFill="D9D9D9", w=6350))   # 눈금선은 연한 회색
    ch.height = 11; ch.width = 22; s.add_chart(ch, "D3")
    # 수식 구문 점검(괄호 균형)
    bad = 0
    for ws in wb:
        for row in ws.iter_rows():
            for c in row:
                if isinstance(c.value, str) and c.value.startswith("="):
                    tk = Tokenizer(c.value)
                    if c.value.count("(") != c.value.count(")"): bad += 1; print("괄호 불일치:", ws.title, c.coordinate, c.value)
    if strategy: add_strategy_sheet(wb, name, short, strategy)
    if extra: extra(wb)
    wb.calculation.fullCalcOnLoad = True
    wb.save(path); return dict(first=first, last=last, bad=bad)

def side_rows(rows, short):
    return rows

def add_shadow_sheet(wb, sh, state, price):
    """CAP 그림자 비교 시트 — 실거래(A, 마지막 체결가 기준)와 가상 B5·B10(평균가 기준)을 같은 기간으로 비교. 값은 내보낼 때 계산한 숫자다."""
    import datetime as dt
    ws = wb.create_sheet("그림자 비교", 1)
    ws["A1"] = "그림자 비교 — 실거래(A) 대 평균가 기준 가상 거래(B5·B10)"; ws["A1"].font = f_title
    if not sh or not sh.get("modes"):
        ws["A3"] = "아직 그림자 모드 기록이 없습니다. 봇이 가동되어 가상 거래가 쌓이면 자동으로 채워집니다."; ws["A3"].font = f_note
        return ws
    kst = dt.timezone(dt.timedelta(hours=9))
    start = dt.datetime.fromtimestamp(sh["start"], kst).strftime("%Y-%m-%d %H:%M")
    px = price or sh["hist"][-1][1]
    slip, fee = 0.0028, 0.0004
    ws["A2"] = f"비교 시작 {start} KST부터. 가상 거래는 수수료 0.04%·슬리피지 0.28%를 반영하고 주문은 내지 않습니다. 트레일 청산(−10%, 4시간 휴식)·급락 대응은 모두 같은 조건입니다."; ws["A2"].font = f_note
    heads = ["구분", "기준가 방식", "순손익 (USDT)", "실현손익 (USDT)", "미실현 평가 (USDT)", "매수 횟수", "매도 횟수", "트레일 청산", "최대 낙폭 (USDT)", "현재 보유 (USDT)"]
    for j, h in enumerate(heads, 1):
        c = ws.cell(4, j, h); c.font = f_head; c.fill = fill_head; c.alignment = ctr; c.border = box
    ws.row_dimensions[4].height = 30
    live0 = sh.get("live0", {})
    cum = (state.get("cum_pnl") or 0.0) - (live0.get("cum_pnl") or 0.0)
    q, avg = state.get("qty", 0.0), state.get("avg_cost") or 0.0
    unreal = q * (px - avg) if q > 0 and avg else 0.0
    unreal0 = (live0.get("qty") or 0.0) * (live0.get("price", px) - (live0.get("avg_cost") or 0.0)) if live0.get("qty") else 0.0
    rows = [("A (실거래)", "마지막 체결가", cum + unreal - unreal0, cum, unreal - unreal0, None, None, None, None, q * px)]
    for key in sorted(sh["modes"], key=int):
        m = sh["modes"][key]
        eq = m["cash"] + m["qty"] * px * (1 - slip) * (1 - fee)
        rows.append((f"B{key} (가상)", f"직전 {key}분 평균가", eq, m["realized"], eq - m["realized"], m["buys"], m["sells"], m["trails"], m["mdd"], m["qty"] * px))
    for i, r in enumerate(rows):
        for j, v in enumerate(r, 1):
            c = ws.cell(5 + i, j, v); c.font = f_bold if j in (1, 3) else f_norm; c.border = box
            if j >= 3: c.number_format = "#,##0.00;[Red]-#,##0.00;-" if j not in (6, 7, 8) else "0"
    note_r = 5 + len(rows) + 1
    ws.cell(note_r, 1, "· A의 순손익은 같은 기간의 봇 상태 파일 누적 실현(수수료 포함)과 보유 평가손익 변화로 계산했습니다. 비교 시작 시점에 보유가 있었다면 그 평가분은 제외했습니다.").font = f_note
    ws.cell(note_r + 1, 1, "· 가상 거래는 1분 단위 점검 시세로 체결했다고 가정한 모의이며 실제 체결과 차이가 있습니다. 과거 결과일 뿐 투자 조언이 아닙니다.").font = f_note
    er = note_r + 3
    ws.cell(er, 1, "최근 가상 거래 (최대 30건, UTC 표기 시각은 한국시간으로 바꿔 표시)").font = f_bold
    for j, h in enumerate(["일시(KST)", "모드", "구분", "가격", "금액(USDT)", "수량", "손익(USDT)"], 1):
        c = ws.cell(er + 1, j, h); c.font = f_head; c.fill = fill_head; c.alignment = ctr; c.border = box
    for i, e in enumerate(sh.get("events", [])[-30:]):
        t_ = e[0]
        try: t_ = (dt.datetime.strptime(t_.replace(" UTC", ""), "%Y-%m-%d %H:%M") + dt.timedelta(hours=9)).strftime("%Y-%m-%d %H:%M")
        except Exception: pass
        vals = [t_, "B" + str(e[1]), e[2], e[3], e[4], e[5], e[6]]
        for j, v in enumerate(vals, 1):
            c = ws.cell(er + 2 + i, j, v); c.font = f_norm; c.border = box
            if j == 4: c.number_format = "0.000000"
            if j in (5, 7): c.number_format = "#,##0.00;[Red]-#,##0.00;-"
            if j == 6: c.number_format = "#,##0.0"
    for j, w in enumerate([22, 20, 16, 16, 18, 10, 10, 12, 16, 16], 1): ws.column_dimensions[L(j)].width = w
    ws.sheet_view.showGridLines = False
    return ws

def build_surge_workbook(path, alerts, ref_rows):
    """급등 알림 사후 추적 엑셀. alerts: coin_surge_outcomes.json 의 alerts 목록, ref_rows: 추적 시작 전 알림(참고용) [(종목, 알림 후 분, 알림가, 현재가)]."""
    import datetime as dt
    wb = Workbook(); ws = wb.active; ws.title = "알림 사후추적"
    ws["A1"] = "급등 알림 사후 추적 — 알림 1시간·4시간 뒤 가격 변화"; ws["A1"].font = f_title
    ws["A2"] = "알림가 대비 변화: 마이너스 = 알림 뒤 내려감(되돌림), 플러스 = 더 올라감. 자동 갱신 값이며 직접 고친 내용은 다음 갱신 때 덮어써집니다. 과거 결과일 뿐 투자 조언이 아닙니다."; ws["A2"].font = f_note
    heads = ["No", "알림시각(KST)", "종목", "봇 운용", "알림가", "24시간 등락(%)", "24시간 거래대금(M USDT)", "거래량 배수(100분 대비)", "10시간 등락(%)", "10시간 고점 대비(%)",
             "호가 깊이(USDT)", "스프레드(%)", "유동성 판정", "1시간 후 가격", "1시간 후 변화", "4시간 후 가격", "4시간 후 변화", "4시간 내 최고 변화", "4시간 내 최저 변화"]
    HR = 17
    for j, h in enumerate(heads, 1):
        c = ws.cell(HR, j, h); c.font = f_head; c.fill = fill_head; c.alignment = ctr; c.border = box
    ws.row_dimensions[HR].height = 42
    first = HR + 1; n = max(len(alerts), 1); last = first + len(alerts) - 1 if alerts else first
    kst = dt.timezone(dt.timedelta(hours=9))
    for i, a in enumerate(sorted(alerts, key=lambda a: a["ts"])):
        x = first + i
        vals = [i + 1, dt.datetime.fromtimestamp(a["ts"], kst).strftime("%Y-%m-%d %H:%M"), a["sym"], "⭐" if a.get("own") else "", a["price"], a.get("chg24"), (a.get("vol") or 0) / 1e6,
                a.get("ratio"), a.get("net10h"), a.get("from_high"), a.get("depth"), a.get("spread"), "OK" if a.get("liquid") else "얇음",
                a.get("p60"), None, a.get("p240"), None, None, None]
        for j, v in enumerate(vals, 1):
            c = ws.cell(x, j, v); c.font = f_in if j in (5, 14, 16) else f_norm; c.border = box
        ws.cell(x, 15, f'=IF(N{x}="","",N{x}/E{x}-1)'); ws.cell(x, 17, f'=IF(P{x}="","",P{x}/E{x}-1)')
        hi, lo = a.get("hi240", a.get("hi")), a.get("lo240", a.get("lo"))
        ws.cell(x, 18, (hi / a["price"] - 1) if hi else None); ws.cell(x, 19, (lo / a["price"] - 1) if lo else None)
        for j in (15, 17, 18, 19):
            c = ws.cell(x, j); c.font = f_norm; c.border = box; c.number_format = "0.0%;[Red]-0.0%;-"
        ws.cell(x, 5).number_format = "0.000000"; ws.cell(x, 14).number_format = "0.000000"; ws.cell(x, 16).number_format = "0.000000"
        for j in (6, 7, 8, 9, 10, 12): ws.cell(x, j).number_format = "0.0"
        ws.cell(x, 11).number_format = "#,##0"
    rg = lambda col: f"{col}{first}:{col}{last}"
    ws["A4"] = "요약"; ws["A4"].font = f_bold
    rows = [("기록된 알림 (건)", f"=COUNT({rg('E')})", "0"),
            ("1시간 후 평균 변화", f'=IFERROR(AVERAGE({rg("O")}),0)', "0.0%;[Red]-0.0%;-"),
            ("1시간 후 하락 비율", f'=IF(COUNT({rg("O")})=0,0,COUNTIF({rg("O")},"<0")/COUNT({rg("O")}))', "0%"),
            ("4시간 후 평균 변화", f'=IFERROR(AVERAGE({rg("Q")}),0)', "0.0%;[Red]-0.0%;-"),
            ("4시간 후 하락 비율", f'=IF(COUNT({rg("Q")})=0,0,COUNTIF({rg("Q")},"<0")/COUNT({rg("Q")}))', "0%"),
            ("유동성 OK 종목만: 4시간 후 평균 변화", f'=IFERROR(AVERAGEIFS({rg("Q")},{rg("M")},"OK"),0)', "0.0%;[Red]-0.0%;-")]
    for i, (lab, f, fmt) in enumerate(rows):
        ws.cell(5 + i, 1, lab).font = f_norm; c = ws.cell(5 + i, 2, f); c.font = f_bold; c.number_format = fmt
        ws.cell(5 + i, 1).border = box; c.border = box
    ws["D4"] = "참고: 사후 추적 시작 전 알림 9건 (알림가 → 2026-10-26 10:2x KST 시세)"; ws["D4"].font = f_bold
    for j, h in enumerate(["종목", "알림 후(분)", "알림가", "그때 시세", "변화"], 4):
        c = ws.cell(5, j, h); c.font = f_head; c.fill = fill_head; c.alignment = ctr; c.border = box
    # 참고표는 4행~(5+9)행 영역을 쓰므로 표 머리글(HR)과 겹치지 않게 위쪽 10행 안에 둔다
    for i, (sym, mins, p0, p1) in enumerate(ref_rows[:9]):
        r = 6 + i
        if r >= HR - 1: break
        for j, v in enumerate([sym, mins, p0, p1], 4):
            c = ws.cell(r, j, v); c.font = f_norm; c.border = box
        c = ws.cell(r, 8, f"=G{r}/F{r}-1"); c.font = f_norm; c.border = box; c.number_format = "0.0%;[Red]-0.0%;-"
    for j, w in enumerate([8, 17, 13, 8, 12, 12, 14, 14, 12, 14, 12, 11, 11, 14, 12, 14, 12, 12, 12], 1): ws.column_dimensions[L(j)].width = w
    ws.column_dimensions["A"].width = 34
    ws.freeze_panes = ws.cell(first, 4)
    wb.calculation.fullCalcOnLoad = True
    wb.save(path)
