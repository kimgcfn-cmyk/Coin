"""
단타 점검 리포트 — 봇별 최근 12시간·24시간 성과와 시장 상태를 모아 '유지 / 주의 / 교체 검토'를 알려준다 (2026-10-26)
================================================================================
사용자는 단타 전략(투자기간 12~24시간)이라 며칠 단위가 아니라 12~24시간 단위로 봇과 종목을 점검한다.
  python coin_review_report.py            # 리포트를 디스코드(기본 채널)로 전송 — 서버 cron: 09:00·21:00 KST (UTC 00:00·12:00)
  python coin_review_report.py --check    # 전송 없이 화면에만 출력
동작: 서버의 봇 로그에서 체결을 읽어 환매·매도의 순손익(평균단가·수수료 0.04% 반영)을 12h·24h 창으로 합산하고,
      봇 상태 파일에서 보유·평가손익을, 스크리너 지표(coin_grid_screener.evaluate)에서 시장 유형·스프레드·거래대금을 가져온다.
판정 기준: 24시간 손익 합계(실현+평가)가 마이너스, 롱인데 하락 추세/급등 후 하락, 숏인데 상승 추세, 스프레드 > 0.2%, 24시간 거래대금 < 10M
          중 2개 이상이면 '교체 검토', 1개면 '주의', 없으면 '유지'. (자동으로 종목을 바꾸지는 않는다 — 알림만)
주의: 과거·가상 결과일 뿐 투자 조언이 아니다.
"""
import sys, os, re, json, glob, gzip, time, datetime as dt
import coin_grid_bot as g
import coin_grid_screener as sc

KST = dt.timezone(dt.timedelta(hours=9))
FEE = 0.0004
# (표시 이름, 방향, 심볼, 로그 이름, 상태 파일, 모의 여부, 로그에 없는 체결(수동 청산·편입))
BOTS = [
    ("ORDER 숏", "short", "ORDERUSDT", "coin_grid_short_order", "coin_grid_short_order_state.json", False,
     [dict(t="2026-10-26 10:05", label="수동 숏 편입 진입", amt=95.1786, qty=2230.0)]),
    ("T 숏", "short", "TUSDT", "coin_grid_short_t", "coin_grid_short_t_state.json", False,
     [dict(t="2026-10-25 22:47", label="수동 청산 환매", amt=251.8573, qty=45160.0)]),
    ("CAP 숏", "short", "CAPUSDT", "coin_grid_short_cap", "coin_grid_short_cap_state.json", False, []),
    ("IRYS 롱(모의)", "long", "IRYSUSDT", "coin_grid_irys", "coin_grid_irys_state.json", True, []),
    ("IRYS 숏(모의)", "short", "IRYSUSDT", "coin_grid_short_irys", "coin_grid_short_irys_state.json", True, []),
]

def read_log_lines(name):
    for p in sorted(glob.glob(f"logs/{name}.log*")):
        op = gzip.open if p.endswith(".gz") else open
        try:
            with op(p, "rt", encoding="utf-8") as f:
                for l in f: yield l
        except Exception:
            continue

def parse_fills(name, extra):
    t = None; seen = set(); rows = []
    for l in read_log_lines(name):
        m = re.search(r"(\d{4}-\d\d-\d\d) (\d\d:\d\d) UTC", l)
        if m: t = dt.datetime.strptime(m.group(1) + " " + m.group(2), "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc)
        m = re.search(r"(?P<label>[^\n:]*?):? 체결 완료 \(주문ID (?P<id>\d+), 금액 (?P<amt>[\d.]+)USDT, 체결가 [\d.]+, 수량 (?P<q>[\d.]+)\)", l)
        if m and m.group("id") not in seen and t:
            seen.add(m.group("id")); rows.append(dict(ts=t.timestamp(), label=re.sub(r"[^\w\s/가-힣]", "", m.group("label")).strip(), amt=float(m.group("amt")), qty=float(m.group("q"))))
    for x in extra:
        ts = dt.datetime.strptime(x["t"], "%Y-%m-%d %H:%M").replace(tzinfo=KST).timestamp()
        rows.append(dict(ts=ts, label=x["label"], amt=x["amt"], qty=x["qty"]))
    rows.sort(key=lambda r: r["ts"]); return rows

def exits_net(fills, side):
    """평균단가법 + 진입 수수료 배분. 반환: [(시각, 순손익)] (환매·매도만), 마지막 보유수량."""
    is_entry = (lambda l: "진입" in l or "추격" in l) if side == "short" else (lambda l: "매수" in l)
    q = avg = pool = 0.0; out = []
    for r in fills:
        f = r["amt"] * FEE
        if is_entry(r["label"]):
            avg = (q * avg + r["amt"]) / (q + r["qty"]) if q + r["qty"] > 0 else 0; q += r["qty"]; pool += f
        else:
            if q <= 0: continue
            alloc = pool * min(r["qty"], q) / q; px = r["amt"] / r["qty"]
            gross = r["qty"] * ((avg - px) if side == "short" else (px - avg))
            out.append((r["ts"], gross - f - alloc)); pool -= alloc; q -= r["qty"]
            if q <= 1e-9: q = avg = pool = 0.0
    return out, q

def main():
    send = "--check" not in sys.argv
    now = time.time(); stamp = dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    try: tk = {x["symbol"]: x for x in sc.tickers()}
    except Exception: tk = {}
    lines = [f"📊 <b>단타 점검 리포트</b> ({stamp} KST) — 12·24시간 기준"]
    flagged = []
    for name, side, sym, log, sf, paper, extra in BOTS:
        try: st = json.load(open(sf, encoding="utf-8"))
        except Exception: st = {}
        ex, _ = exits_net(parse_fills(log, extra), side)
        n10 = sum(p for ts, p in ex if now - ts <= 12 * 3600); n24 = sum(p for ts, p in ex if now - ts <= 24 * 3600)
        c10 = sum(1 for ts, p in ex if now - ts <= 12 * 3600); c24 = sum(1 for ts, p in ex if now - ts <= 24 * 3600)
        qty = st.get("qty", 0.0) or 0.0; avg = st.get("avg_cost") or 0.0
        price = float(tk[sym]["lastPr"]) if sym in tk else 0.0
        unreal = qty * ((avg - price) if side == "short" else (price - avg)) if (qty > 0 and avg and price) else 0.0
        info = None
        try: info = sc.evaluate(tk[sym], sc.bars_5m(sym)) if sym in tk else None
        except Exception: info = None
        flags = []
        if info and "kind" in info:
            if side == "long" and (info["kind"] in ("하락 추세", "급등 후 하락") or info["last2h"] <= -6): flags.append(f"하락 구간({info['kind']})")
            if side == "short" and (info["kind"] in ("꾸준한 상승", "상승(되돌림 큼)") or info["net"] >= 8): flags.append(f"상승 구간({info['kind']})")
            if info["spread"] > 0.2: flags.append(f"스프레드 {info['spread']:.2f}%")
            if info["vol24"] < 10_000_000: flags.append(f"거래대금 {info['vol24']/1e6:.1f}M")
        if n24 + unreal < 0: flags.append(f"24시간 손익 {n24 + unreal:+.1f}")
        verdict = "교체 검토" if len(flags) >= 2 else ("주의" if flags else "유지")
        icon = {"유지": "🟢", "주의": "🟡", "교체 검토": "🔴"}[verdict]
        kind = info["kind"] if info and "kind" in info else "?"
        lines.append(f"{icon} <b>{name}</b> [{verdict}] — 12h {n10:+.2f} ({c10}회) / 24h {n24:+.2f} ({c24}회) USDT, 보유 {qty * price:,.0f}USDT 평가 {unreal:+.2f} · 시장 {kind}"
                     + (f"\n    ⚠ " + " · ".join(flags) if flags else "") + (" (모의)" if paper else ""))
        if verdict != "유지" and not paper: flagged.append(name)
    try:
        ex_ = g.get_exchange(); a = (ex_.privateUtaGetV3AccountAssets({}).get("data") or {})
        lines.append(f"💼 계좌 총자산 {float(a.get('accountEquity', 0)):,.1f} USDT · 사용 증거금 {float(a.get('imr', 0)):,.1f} USDT")
    except Exception: pass
    if flagged: lines.append(f"👉 점검 필요: {', '.join(flagged)} — 교체·크기 조정을 검토하세요(자동 변경 없음)")
    lines.append("※ 실현 순손익은 환매·매도 기준(수수료·진입 수수료 배분 반영). 과거·가상 결과일 뿐 투자 조언이 아닙니다.")
    msg = "\n".join(lines)
    if send: g.send_discord(msg); print("  ✅ 리포트 전송 완료")
    print(msg.replace("<b>", "").replace("</b>", ""))

if __name__ == "__main__":
    main()
