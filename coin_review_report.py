"""
롱숏합동전략 점검 리포트 — CAP·Q 롱숏합동전략의 12시간·24시간 성과를 롱+숏 합계 중심으로 보고한다 (2026-10-27 개편)
================================================================================
운용 지침(봇운용지침.md): 투자기간 12~24시간, 수익률 우선·낮은 MDD(통과 기준 MDD ≤ 기준 자본의 30%).
  python coin_review_report.py            # 리포트를 디스코드(기본 채널)로 전송 — 서버 cron: 09:00·21:00 KST (UTC 00:00·12:00)
  python coin_review_report.py --check    # 전송 없이 화면에만 출력
동작: 서버의 합동전략 봇 로그(logs/coin_hd_<종목>_<long|short>.log)에서 체결과 봇이 기록한 청산 순손익(평균단가·수수료 반영)을 읽고,
      봇 상태 파일(coin_avg_hd_*_state.json)에서 보유·평균단가·사다리 단계·누적 손익을, 거래소에서 실제 포지션을(장부 대조), 티커에서 현재가·거래대금·스프레드를 가져온다.
지표(항상 같은 정의): 누적수익률 = 총 순손익(실현+미실현) ÷ 기준 자본(롱·숏 한도÷5의 합) / MDD = 고점 대비 최대 낙폭 / 평균 보유시간(FIFO).
      12h·24h 창의 값은 해당 창의 청산 순손익(실현)과 현재 미실현을 합친 곡선 기준이다. 누적(가동 이후) 총 순손익은 봇 장부의 누적 손익(수동 정정 포함)을 쓴다.
판정: 주의 신호가 2개 이상이면 '조정 검토', 1개면 '주의', 없으면 '유지'(자동 변경 없음, 알림만).
      신호 = 24h 총손익<0 / 12h 또는 24h MDD>30% / 순노출>한도의 60% / 장부-거래소 불일치(수동 거래 가능성) / 24h 거래대금<20M / 스프레드>0.2% / 손절 구간 활성.
주의: 과거 결과일 뿐 투자 조언이 아니다.
"""
import sys, os, re, json, glob, gzip, time, datetime as dt
import coin_grid_bot as g

KST = dt.timezone(dt.timedelta(hours=9))
SYMBOLS = [("CAP", "CAPUSDT"), ("Q", "QUSDT")]
MDD_LIMIT = 30.0          # 운용 지침 통과 기준 (기준 자본 대비 %)
NET_LIMIT = 0.6           # 순노출이 한도의 이 비율을 넘으면 주의
FILL_RE = re.compile(r"(?P<icon>🔺|🔻|🟢|🔴)\s*(?P<word>매수|매도|숏 진입|환매)\((?P<why>.*?)\):\s*체결 완료 \(주문ID (?P<id>\d+), 금액 (?P<amt>[\d.]+)USDT, 체결가 [\d.]+, 수량 (?P<q>[\d.]+)\)(?P<rest>.*)")
NET_RE = re.compile(r"순손익 ([+-][\d.]+) USDT")


def read_log_lines(name):
    for p in sorted(glob.glob(f"logs/{name}.log*")):
        op = gzip.open if p.endswith(".gz") else open
        try:
            with op(p, "rt", encoding="utf-8") as f:
                for l in f: yield l
        except Exception:
            continue


def parse_bot_log(name):
    """봇 로그에서 체결을 읽는다. 반환: [dict(ts, kind('in'|'out'), qty, amt, net(청산이면 봇이 기록한 순손익), why)] 시간순."""
    t = None; seen = set(); rows = []
    for l in read_log_lines(name):
        m = re.search(r"(\d{4}-\d\d-\d\d) (\d\d:\d\d) UTC", l)
        if m: t = dt.datetime.strptime(m.group(1) + " " + m.group(2), "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc)
        m = FILL_RE.search(l)
        if m and t and m.group("id") not in seen:
            seen.add(m.group("id")); out = m.group("icon") in ("🟢", "🔴")
            n = NET_RE.search(m.group("rest") or "")
            rows.append(dict(ts=t.timestamp(), kind="out" if out else "in", qty=float(m.group("q")), amt=float(m.group("amt")),
                             net=float(n.group(1)) if (out and n) else 0.0, why=m.group("why")))
    rows.sort(key=lambda r: r["ts"]); return rows


def mdd_of(points):
    """곡선(누적 손익 목록, 처음 0 포함)의 고점 대비 최대 낙폭."""
    peak = 0.0; md = 0.0
    for v in [0.0] + list(points):
        peak = max(peak, v); md = max(md, peak - v)
    return md


def hold_hours(rows, now):
    """FIFO 평균 보유시간(시간): 청산된 물량의 보유시간과 현재 들고 있는 물량의 경과시간을 가중 평균."""
    lots = []; tot = w = 0.0
    for r in rows:
        if r["kind"] == "in": lots.append([r["ts"], r["qty"]])
        else:
            rem = r["qty"]
            while rem > 1e-9 and lots:
                take = min(rem, lots[0][1]); w += take * (r["ts"] - lots[0][0]) / 3600; tot += take; lots[0][1] -= take; rem -= take
                if lots[0][1] <= 1e-9: lots.pop(0)
    for ts, q in lots: w += q * max(0.0, now - ts) / 3600; tot += q
    return (w / tot) if tot > 0 else 0.0


def load_cfg(tag, side):
    try: return json.load(open(f"coin_grid_presets/hd_{tag.lower()}_{side}.json", encoding="utf-8"))["cfg"]
    except Exception: return {}


def window(rows_by_side, now, hours, unreal):
    """창 안의 청산 순손익을 시간순으로 합친 곡선 + 마지막에 현재 미실현을 더한 점. 반환: (실현합, 청산 건수, 총손익, MDD)."""
    ev = sorted((r["ts"], r["net"]) for rs in rows_by_side.values() for r in rs if r["kind"] == "out" and now - r["ts"] <= hours * 3600)
    cum = 0.0; curve = []
    for _, n in ev: cum += n; curve.append(cum)
    total = cum + unreal
    return cum, len(ev), total, mdd_of(curve + [total])


def main():
    send = "--check" not in sys.argv
    now = time.time(); stamp = dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    try:
        import coin_grid_screener as sc
        tk = {x["symbol"]: x for x in sc.tickers()}
    except Exception:
        sc, tk = None, {}
    try:
        ex = g.get_exchange(); ex.load_markets()
    except Exception:
        ex = None
    lines = [f"📊 <b>롱숏합동전략 점검 리포트</b> ({stamp} KST) — 12·24시간 · 롱+숏 합계 기준"]
    flagged = []
    for tag, sym in SYMBOLS:
        rows = {}; st = {}; cfg = {}
        for side in ("long", "short"):
            rows[side] = parse_bot_log(f"coin_hd_{tag.lower()}_{side}")
            try: st[side] = json.load(open(f"coin_avg_hd_{tag.lower()}_{side}_state.json", encoding="utf-8"))
            except Exception: st[side] = {}
            cfg[side] = load_cfg(tag, side)
        price = float(tk[sym]["lastPr"]) if sym in tk else 0.0
        cap = float(cfg["long"].get("max_total_usdt", 0) or 0); capital = 2 * cap / 5 if cap else 0.0
        part = {}
        for side in ("long", "short"):
            s_ = st[side]; q = s_.get("qty", 0.0) or 0.0; avg = s_.get("avg", 0.0) or 0.0
            un = q * ((price - avg) if side == "long" else (avg - price)) if (q > 0 and avg and price) else 0.0
            part[side] = dict(qty=q, avg=avg, hold=s_.get("hold", 0.0) or 0.0, unreal=un, cum=s_.get("cum_pnl", 0.0) or 0.0, bs=s_.get("bs", 0), full=bool(s_.get("full")))
        unreal = sum(p["unreal"] for p in part.values()); cum_all = sum(p["cum"] for p in part.values())
        total_all = cum_all + unreal
        r12, c12, t12, m12 = window(rows, now, 12, unreal)
        r24, c24, t24, m24 = window(rows, now, 24, unreal)
        allrows = sorted(rows["long"] + rows["short"], key=lambda r: r["ts"])
        hh = hold_hours(allrows, now)
        net_exp = part["long"]["hold"] - part["short"]["hold"]
        # 장부-거래소 대조
        mism = []
        if ex:
            try:
                pp = {p["side"]: float(p["contracts"]) for p in ex.fetch_positions([g._normalize_symbol(tag)], params=g.UTA) if float(p["contracts"] or 0) > 0}
                for side in ("long", "short"):
                    d = pp.get(side, 0.0) - part[side]["qty"]
                    if abs(d) > max(2.0, part[side]["qty"] * 0.002): mism.append(f"{'롱' if side == 'long' else '숏'} 거래소 {pp.get(side, 0.0):,.0f} vs 장부 {part[side]['qty']:,.0f}")
            except Exception:
                pass
        flags = []
        if t24 < 0: flags.append(f"24h 총손익 {t24:+.1f}")
        if capital:
            for hrs, m in ((12, m12), (24, m24)):
                if m / capital * 100 > MDD_LIMIT: flags.append(f"{hrs}h MDD {m / capital * 100:.0f}%>{MDD_LIMIT:.0f}%")
        if cap and abs(net_exp) > cap * NET_LIMIT: flags.append(f"순노출 {net_exp:+,.0f}USDT(한도의 {abs(net_exp) / cap * 100:.0f}%)")
        if mism: flags.append("장부-거래소 불일치: " + ", ".join(mism))
        if any(p["full"] for p in part.values()): flags.append("사다리 소진·손절 구간 활성")
        info = None
        try: info = sc.evaluate(tk[sym], sc.bars_5m(sym)) if (sc and sym in tk) else None
        except Exception: info = None
        kind = info["kind"] if info and "kind" in info else "?"
        if info and "kind" in info:
            if info["spread"] > 0.2: flags.append(f"스프레드 {info['spread']:.2f}%")
            if info["vol24"] < 20_000_000: flags.append(f"거래대금 {info['vol24'] / 1e6:.1f}M(<20M)")
        verdict = "조정 검토" if len(flags) >= 2 else ("주의" if flags else "유지")
        icon = {"유지": "🟢", "주의": "🟡", "조정 검토": "🔴"}[verdict]
        pct = lambda v: f"{v / capital * 100:+.1f}%" if capital else "-"
        lines.append(f"{icon} <b>{tag} 롱숏합동</b> [{verdict}] · 현재가 {price:,.6g} · 시장 {kind} · 기준 자본 {capital:,.0f}USDT")
        lines.append(f"   12h 실현 {r12:+.2f} ({c12}회) → 총 {t12:+.2f} ({pct(t12)}) / MDD {m12 / capital * 100 if capital else 0:.1f}%"
                     f"  |  24h 실현 {r24:+.2f} ({c24}회) → 총 {t24:+.2f} ({pct(t24)}) / MDD {m24 / capital * 100 if capital else 0:.1f}%")
        lines.append(f"   가동 이후 총 {total_all:+.2f} ({pct(total_all)}) · 평균 보유 {hh:.1f}시간 · 순노출 {net_exp:+,.0f}USDT")
        for side, nm in (("long", "롱"), ("short", "숏")):
            p = part[side]
            lines.append(f"   └ {nm}: 보유 {p['qty']:,.0f}개 ({p['hold']:,.0f}USDT) 평균 {p['avg']:,.6g} · 평가 {p['unreal']:+.2f} · 누적 {p['cum']:+.2f} · 사다리 {p['bs']}단계{' · 손절선 활성' if p['full'] else ''}")
        if flags: lines.append("   ⚠ " + " · ".join(flags))
        if verdict != "유지": flagged.append(tag)
    try:
        a = (ex.privateUtaGetV3AccountAssets({}).get("data") or {}) if ex else {}
        if a: lines.append(f"💼 계좌 총자산 {float(a.get('accountEquity', 0)):,.1f} USDT · 사용 증거금 {float(a.get('imr', 0)):,.1f} USDT")
    except Exception:
        pass
    if flagged: lines.append(f"👉 점검 필요: {', '.join(flagged)} — 한도·설정 조정을 검토하세요(자동 변경 없음)")
    lines.append("※ 누적수익률=총 순손익÷기준 자본, MDD=고점 대비 최대 낙폭(체결 사이 평가손실은 미반영). 통과 기준 MDD≤30%. 과거 결과일 뿐 투자 조언이 아닙니다.")
    msg = "\n".join(lines)
    if send: g.send_discord(msg); print("  ✅ 리포트 전송 완료")
    print(msg.replace("<b>", "").replace("</b>", ""))


if __name__ == "__main__":
    main()
