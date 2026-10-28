"""
노출 축소 E안 실시간 모의(paper) A/B 검증 도구 (2026-10-28 사용자 지시)
================================================================================
A = 실전 CAP·Q 롱/숏 봇(hd_<종목>_<방향>), E = 같은 규칙에서 노출 축소만 바꾼 모의 복사본(hd_<종목>_<방향>_e, 실주문 없음).
  E안: derisk_underwater_pct 3→5 (평균단가 대비 5% 이상 불리할 때만), derisk_pct 1→2 (직전 5분 평균 대비 2% 이상 되돌림일 때만). TAG는 대상 아님.
사용(서버, 프로젝트 폴더에서):
  python coin_derisk_ab.py init            # 실전 장부를 모의 장부로 복사(같은 출발점)하고 시작 시각을 기록한다(잠금·백업 안에서). 모의 봇을 켜기 전에 한 번.
  python coin_derisk_ab.py snap            # (cron 1분) 시세와 A·E 장부를 derisk_ab_<종목>.jsonl 에 한 줄씩 기록
  python coin_derisk_ab.py report [--hours 6|12|24]   # 시작부터 N시간(또는 지금까지) 누적 기준 A vs E 비교표
  python coin_derisk_ab.py json            # 비교 결과와 분 단위 곡선을 JSON 으로 출력(맥 엑셀 생성용)
  python coin_derisk_ab.py mark 6|12|24    # 해당 시점 보고를 마쳤다고 기록 / verdict : 24시간 판정(실전 반영 권고 예·아니오)
정의: 곡선 = 누적 순손익 + 평가손익(반대 슬리피지 0.3%·청산 수수료 반영, A·E 같은 식), 시작 시점을 0으로 한 값. 모두 기준 자본(봇당 한도÷5 = 100 USDT, 종목 합 200) 대비 %.
      MDD = 곡선의 고점 대비 최대 하락폭. 평균 보유시간 = 체결 물량 FIFO(시작 때 보유분은 시작 시각부터).
주의: 과거 결과일 뿐 투자 조언이 아니다.
"""
import sys, os, re, json, time, shutil, fcntl
import datetime as dt
import coin_grid_bot as g

HERE = os.path.dirname(os.path.abspath(__file__))
START = os.path.join(HERE, "derisk_ab_start.json")
SYMS = ("cap", "q"); SIDES = ("long", "short")
SLIP = 0.003; FEE = 0.0004
KST = dt.timezone(dt.timedelta(hours=9))


def sfile(sym, sd, var): return os.path.join(HERE, f"coin_avg_hd_{sym}_{sd}{'_e' if var == 'E' else ''}_state.json")
def jfile(sym): return os.path.join(HERE, f"derisk_ab_{sym}.jsonl")
def blog(sym, sd, var): return os.path.join(HERE, "logs", f"coin_hd_{sym}_{sd}{'_e_모의' if var == 'E' else ''}.log")
def clog(sym, var): return os.path.join(HERE, "logs", f"coin_cf_run_{sym}{'_e_모의' if var == 'E' else ''}.log")
def capital(sym): return json.load(open(os.path.join(HERE, "coin_grid_presets", f"hd_{sym}_long.json"), encoding="utf-8"))["cfg"]["max_total_usdt"] / 5.0


def equity(st, price, long_):
    q = float(st.get("qty") or 0); cum = float(st.get("cum_pnl") or 0); avg = float(st.get("avg") or 0)
    if q <= 0: return cum
    un = q * ((price * (1 - SLIP) - avg) if long_ else (avg - price * (1 + SLIP))) - q * price * FEE
    return cum + un


def kst_utc(t):   # t: UTC naive datetime → "HH:MM KST(HH:MM UTC)"
    return f"{(t + dt.timedelta(hours=9)):%m/%d %H:%M} KST({t:%H:%M} UTC)"


def lock_all(paths):
    fs = []
    for p in paths:
        f = open(p, "w"); fcntl.flock(f, fcntl.LOCK_EX); fs.append(f)
    return fs


def init():
    if os.path.exists(START) and "--force" not in sys.argv:
        print("이미 시작됨:", open(START).read()); return
    paths = [f"/tmp/coin_hd_{sym}_{sd}.lock" for sym in SYMS for sd in SIDES] + [f"/tmp/coin_cf_run_{sym}.lock" for sym in SYMS]
    locks = lock_all(paths)                              # 봇·필터 컨트롤러가 장부를 만지는 동안은 복사하지 않도록 모두 잠근다
    now = time.time(); stamp = time.strftime("%m%d%H%M"); halted = []
    for sym in SYMS:
        for sd in SIDES:
            a = sfile(sym, sd, "A"); e = sfile(sym, sd, "E")
            if os.path.exists(e): shutil.copy(e, e + ".bak_" + stamp)
            st = json.load(open(a, encoding="utf-8")); json.dump(st, open(e, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            if st.get("cf_halt"): halted.append(f"{sym} {sd}")
        ca = os.path.join(HERE, f"coin_cf_curve_{sym}.json"); ce = os.path.join(HERE, f"coin_cf_curve_{sym}_e.json")   # 필터 판정 상태(그림자 포함)도 같이 복사 → 같은 출발점
        if os.path.exists(ca):
            if os.path.exists(ce): shutil.copy(ce, ce + ".bak_" + stamp)
            shutil.copy(ca, ce)
    json.dump(dict(start=now, start_utc=dt.datetime.utcfromtimestamp(now).strftime("%Y-%m-%d %H:%M"), note="E안(derisk_underwater_pct 5, derisk_pct 2) vs 실전 A",
                   halted_at_start=halted, reported={"6": False, "12": False, "24": False}), open(START, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("초기화 완료 — 실전 장부·필터 상태를 모의로 복사(필터 정지 중이던 봇: " + (", ".join(halted) or "없음") + "), 시작", dt.datetime.utcfromtimestamp(now).strftime("%Y-%m-%d %H:%M UTC"))


def snap():
    if not os.path.exists(START): return
    for sym in SYMS:
        price = g.fetch_price(f"{sym.upper()}/USDT:USDT")
        if not price or price <= 0: continue
        row = dict(t=int(time.time()), p=price)
        try:
            for var in ("A", "E"):
                row[var] = {}
                for sd in SIDES:
                    st = json.load(open(sfile(sym, sd, var), encoding="utf-8"))
                    row[var][sd] = {k: st.get(k) for k in ("qty", "avg", "hold", "cum_pnl", "cum_fees", "n_out", "n_stop", "cf_halt", "wt_mult")}
        except Exception:
            continue
        with open(jfile(sym), "a", encoding="utf-8") as f: f.write(json.dumps(row, ensure_ascii=False) + "\n")


# ── 로그 파싱 ──
RE_T = re.compile(r"(\d{4}-\d\d-\d\d \d\d:\d\d) UTC")
RE_FILL = re.compile(r"(?P<ic>\S+)\s+(?P<lab>[^:]*?): 체결 완료 \(주문ID (?P<id>\d+), 금액 (?P<amt>[\d.]+)USDT, 체결가 [\d.]+, 수량 (?P<q>[\d.]+)\)(?: \| (?P<rest>.*))?$")


def fills(sym, sd, var, t0):
    """[(t, kind('in'/'out'), why, qty, price, net)] — 봇 로그 + 컨트롤러 잔량 손절(🟠), 시작 이후만."""
    out = []; seen = set()
    for path, kindcheck in ((blog(sym, sd, var), None), (clog(sym, var), sd)):
        if not os.path.exists(path): continue
        t = None
        for l in open(path, encoding="utf-8", errors="ignore"):
            m = RE_T.search(l)
            if m and ("사다리 봇" in l or "컨트롤러" in l): t = dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M"); continue
            if t is None or t < t0: continue
            m = RE_FILL.search(l.strip())
            if not m or m["id"] in seen: continue
            lab = m["lab"]
            if kindcheck:                                   # 컨트롤러 로그: 잔량 손절 줄만, 방향이 맞는 것만
                if "잔량 손절" not in lab: continue
                if ("롱" in lab) != (sd == "long") and ("롱" in lab or "숏" in lab): continue
            seen.add(m["id"]); amt = float(m["amt"]); q = float(m["q"]); rest = m["rest"] or ""
            net = None; mm = re.search(r"순손익 ([+-]?[\d.]+) USDT", rest)
            if mm: net = float(mm.group(1))
            kind = "in" if any(k in lab for k in ("매수(", "숏 진입(")) else "out"
            if kindcheck: kind = "out"
            out.append((t, kind, lab, q, amt / q if q else 0.0, net))
    return sorted(out, key=lambda x: x[0])


def avg_hold_hours(evs, t0, t_end, start_qty):
    """FIFO 평균 보유시간(시간): 청산분은 실제 보유시간, 끝까지 보유분은 t_end까지 경과. 시작 때 보유분은 t0부터."""
    lots = [[t0, start_qty]] if start_qty > 0 else []; w = tot = 0.0
    for t, kind, lab, q, price, net in evs:
        if t > t_end: break
        if kind == "in": lots.append([t, q])
        else:
            rem = q
            while rem > 1e-9 and lots:
                take = min(rem, lots[0][1]); w += take * (t - lots[0][0]).total_seconds() / 3600; tot += take; lots[0][1] -= take; rem -= take
                if lots[0][1] <= 1e-9: lots.pop(0)
    for t, q in lots: w += q * (t_end - t).total_seconds() / 3600; tot += q
    return (w / tot) if tot else 0.0


def load_rows(sym):
    rows = []
    if os.path.exists(jfile(sym)):
        for l in open(jfile(sym), encoding="utf-8"):
            try: rows.append(json.loads(l))
            except Exception: pass
    return rows


def compute(hours=None):
    st = json.load(open(START, encoding="utf-8")); t0s = st["start"]; t0 = dt.datetime.utcfromtimestamp(t0s)
    res = {"start": t0s, "start_str": kst_utc(t0), "symbols": {}}
    for sym in SYMS:
        rows = [r for r in load_rows(sym) if r["t"] >= t0s - 5]
        if hours: rows = [r for r in rows if r["t"] <= t0s + hours * 3600 + 30]
        if len(rows) < 2: continue
        cap = capital(sym); r0 = rows[0]; end = rows[-1]; t_end = dt.datetime.utcfromtimestamp(end["t"])
        base = {var: {sd: equity(r0[var][sd], r0["p"], sd == "long") for sd in SIDES} for var in ("A", "E")}
        d = {"elapsed_h": (end["t"] - r0["t"]) / 3600, "cap": cap, "price": dict(p0=r0["p"], p1=end["p"], hi=max(r["p"] for r in rows), lo=min(r["p"] for r in rows)), "var": {}, "curve": []}
        for var in ("A", "E"):
            ser = {sd: [equity(r[var][sd], r["p"], sd == "long") - base[var][sd] for r in rows] for sd in SIDES}
            ser["합계"] = [a + b for a, b in zip(ser["long"], ser["short"])]
            v = {}
            for k, s in ser.items():
                pk = mdd = 0.0
                for x in s: pk = max(pk, x); mdd = max(mdd, pk - x)
                capk = cap * (2 if k == "합계" else 1)
                v[k] = dict(ret=s[-1] / capk * 100, mdd=mdd / capk * 100, usdt=s[-1], mdd_usdt=mdd)
            ev = {}
            for sd in SIDES:
                f = fills(sym, sd, var, t0); dr = [x for x in f if "노출 축소" in x[2]]
                n0 = (r0[var][sd].get("n_stop") or 0); n1 = (end[var][sd].get("n_stop") or 0)
                cut_n = len([x for x in f if "잔량 손절" in x[2]])
                maxh = max(float(r[var][sd].get("hold") or 0) for r in rows)
                ev[sd] = dict(derisk_n=len(dr), derisk_net=sum(x[5] or 0 for x in dr), stop_n=n1 - n0, filter_cut_n=cut_n, maxhold=maxh / cap * 100, maxhold_usdt=maxh,
                              avg_hold_h=avg_hold_hours(f, t0, t_end, float(r0[var][sd].get("qty") or 0)), fills=f)
            v["ev"] = ev; d["var"][var] = v
        # E안: 노출 축소로 손실 확정 후 같은 가격대 재매수 반복 점검(30분 안, 가격 ±0.5% 안)
        rep = {}
        for var in ("A", "E"):
            for sd in SIDES:
                f = d["var"][var]["ev"][sd]["fills"]; cnt = []
                for i, x in enumerate(f):
                    if "노출 축소" in x[2] and (x[5] or 0) < 0:
                        for y in f[i + 1:]:
                            if (y[0] - x[0]).total_seconds() > 1800: break
                            if y[1] == "in" and x[4] and abs(y[4] / x[4] - 1) <= 0.005: cnt.append((x[0], y[0])); break
                rep[(var, sd)] = cnt
        d["rebuy"] = {f"{k[0]}_{k[1]}": [(kst_utc(a), kst_utc(b)) for a, b in v] for k, v in rep.items()}
        step = max(1, len(rows) // 400)
        d["curve"] = [[r["t"]] + [round((equity(r[var][sd], r["p"], sd == "long") - base[var][sd]), 3) for var in ("A", "E") for sd in SIDES] + [r["p"]] for r in rows[::step]]
        res["symbols"][sym] = d
    return res


def fmt_report(res, hours):
    L = []
    L.append(f"노출 축소 E안 모의 vs 실전 — 가동 시작 {res['start_str']} · 누적 기준 · 기준 자본 대비 %(수익률/MDD)")
    L.append("| 종목 | 구분 | 시점 | 현재(A) 수익률/MDD | E안 수익률/MDD | 노출 축소 횟수(A→E) |")
    L.append("|---|---|---|---|---|---|")
    flags = []
    for sym, d in res["symbols"].items():
        for k, kn in (("long", "롱"), ("short", "숏"), ("합계", "합계")):
            a = d["var"]["A"][k]; e = d["var"]["E"][k]
            if k == "합계": dn = (sum(d["var"]["A"]["ev"][s]["derisk_n"] for s in SIDES), sum(d["var"]["E"]["ev"][s]["derisk_n"] for s in SIDES))
            else: dn = (d["var"]["A"]["ev"][k]["derisk_n"], d["var"]["E"]["ev"][k]["derisk_n"])
            L.append(f"| {sym.upper()} | {kn} | {(str(hours) + 'h') if hours else '누적'}(경과 {d['elapsed_h']:.1f}h) | {a['ret']:+.1f}% / {a['mdd']:.1f}% ({a['usdt']:+.1f}) | {e['ret']:+.1f}% / {e['mdd']:.1f}% ({e['usdt']:+.1f}) | {dn[0]} → {dn[1]} |")
        if d["var"]["E"]["합계"]["mdd"] > 30 and d["var"]["E"]["합계"]["mdd"] > d["var"]["A"]["합계"]["mdd"]:
            flags.append(f"⚠️ {sym.upper()}: E안 합계 MDD {d['var']['E']['합계']['mdd']:.1f}% > 30% 이고 현재안({d['var']['A']['합계']['mdd']:.1f}%)보다 큼 → 실전 반영 후보에서 제외 표시")
    L.append("")
    for sym, d in res["symbols"].items():
        p = d["price"]
        L.append(f"[{sym.upper()}] 가격 {p['p0']:.6g}→{p['p1']:.6g} ({(p['p1']/p['p0']-1)*100:+.1f}%), 고 {p['hi']:.6g} 저 {p['lo']:.6g} (폭 {(p['hi']/p['lo']-1)*100:.1f}%)")
        for var in ("A", "E"):
            for sd, kn in (("long", "롱"), ("short", "숏")):
                e = d["var"][var]["ev"][sd]
                L.append(f"  {var} {kn}: 노출 축소 {e['derisk_n']}회(손익 {e['derisk_net']:+.2f} USDT) · 손절 {e['stop_n']}회 · 필터 잔량 정리 {e['filter_cut_n']}회 · 최대 보유 {e['maxhold']:.0f}%({e['maxhold_usdt']:.0f} USDT) · 평균 보유시간 {e['avg_hold_h']:.1f}시간")
        for key, lst in d["rebuy"].items():
            if lst: L.append(f"  재매수 점검 {key}: 노출 축소 손실 직후 30분 안 같은 가격대(±0.5%) 재진입 {len(lst)}건 — " + "; ".join(f"{a}→{b}" for a, b in lst[:4]))
    L += [""] + flags
    return "\n".join(L)


def cf_events(sym, var):
    """필터 컨트롤러의 정지·재가동 이벤트 {side: [(ts, kind, curve)]} (A=실전, E=모의)."""
    f = os.path.join(HERE, f"coin_cf_curve_{sym}{'_e' if var == 'E' else ''}.json")
    try: d = json.load(open(f, encoding="utf-8"))
    except Exception: return {sd: [] for sd in SIDES}
    return {sd: [(float(t), k, c) for t, k, c in (d.get(sd, {}).get("events") or [])] for sd in SIDES}


def _stat(ser, cap):
    pk = mdd = 0.0
    for x in ser: pk = max(pk, x); mdd = max(mdd, pk - x)
    return (ser[-1] / cap * 100 if ser else 0.0), (mdd / cap * 100)


def stop_section(hours=None, inject=None):
    """필터 정지 구간 표시와 '재가동 이후 구간만' 비교. 시작 때 정지 중이던 봇은 정지 동안 A·E가 같으므로 따로 보여 준다.
    inject: 시험용 {(sym,var,sd): restart_ts} (없으면 컨트롤러 이벤트 사용)."""
    st = json.load(open(START, encoding="utf-8")); t0s = st["start"]; halted0 = st.get("halted_at_start") or []
    L = []
    for sym in SYMS:
        ev = {var: cf_events(sym, var) for var in ("A", "E")}
        hs = [h for h in halted0 if h.startswith(sym + " ")]
        stops = {}
        for sd in SIDES:
            for var in ("A", "E"):
                e = ev[var][sd]; stops[(var, sd)] = ([x for x in e if x[1] == "정지"][-1:] or [None])[0]
        if not hs and not any(x[0] > t0s for e in ev.values() for v in e.values() for x in v): continue
        L.append(f"[{sym.upper()} 필터 정지·재가동] " + (f"시작 때 정지 중이던 봇: {', '.join(hs)} — 정지 동안은 A·E가 같아 수익률 차이 0이 정상(E안 효과가 없다는 뜻이 아님)" if hs else "시작 때 정지 중이던 봇 없음"))
        rows = [r for r in load_rows(sym) if r["t"] >= t0s - 5 and (not hours or r["t"] <= t0s + hours * 3600 + 30)]
        cap = capital(sym); post = {}
        for sd, kn in (("long", "롱"), ("short", "숏")):
            parts = []
            for var in ("A", "E"):
                e = ev[var][sd]; last_stop = stops[(var, sd)]
                rst = [x for x in e if x[1] == "재가동" and last_stop and x[0] > last_stop[0]]
                if inject and (sym, var, sd) in inject: rst = [(inject[(sym, var, sd)], "재가동", 0)]
                ts = f"정지 {kst_utc(dt.datetime.utcfromtimestamp(last_stop[0]))}" if last_stop else "정지 기록 없음"
                rs = f"재가동 {kst_utc(dt.datetime.utcfromtimestamp(rst[-1][0]))}" if rst else "재가동 전(아직 정지 중)" if (last_stop and (var, sd) not in post) else "재가동 전"
                if not last_stop and not rst: rs = "정지 없음(가동 중)"
                parts.append(f"{var}: {ts} → {rs}"); post[(var, sd)] = rst[-1][0] if rst else None
            L.append(f"  {kn}: " + " | ".join(parts))
        # 재가동 이후 구간만 — A·E 둘 다 재가동한 뒤(둘 중 늦은 시각)부터
        def series(t_from, var, sdl):
            rr = [r for r in rows if r["t"] >= t_from]
            if len(rr) < 2: return None
            b0 = {sd: equity(rr[0][var][sd], rr[0]["p"], sd == "long") for sd in sdl}
            return [sum(equity(r[var][sd], r["p"], sd == "long") - b0[sd] for sd in sdl) for r in rr], rr
        res_lines = []
        for sd, kn in (("long", "롱"), ("short", "숏")):
            if post[("A", sd)] is None or post[("E", sd)] is None:
                if any(x is not None for x in (post[("A", sd)], post[("E", sd)])) or sd in [h.split()[1] for h in hs]:
                    res_lines.append(f"  {kn}: 재가동 이후 구간 없음(A·E 중 아직 재가동 전)"); continue
                continue
            tf = max(post[("A", sd)], post[("E", sd)]); a = series(tf, "A", (sd,)); e = series(tf, "E", (sd,))
            if not a or not e: res_lines.append(f"  {kn}: 재가동 직후라 데이터 부족"); continue
            ra, ma = _stat(a[0], cap); re_, me = _stat(e[0], cap)
            res_lines.append(f"  {kn}: {kst_utc(dt.datetime.utcfromtimestamp(tf))}부터 {(a[1][-1]['t'] - a[1][0]['t']) / 3600:.1f}시간 — A {ra:+.1f}% / {ma:.1f}%  vs  E {re_:+.1f}% / {me:.1f}%")
        if all(post[(v, sd)] is not None for v in ("A", "E") for sd in SIDES):
            tf = max(post.values()); a = series(tf, "A", SIDES); e = series(tf, "E", SIDES)
            if a and e:
                ra, ma = _stat(a[0], cap * 2); re_, me = _stat(e[0], cap * 2)
                res_lines.append(f"  합계: {kst_utc(dt.datetime.utcfromtimestamp(tf))}부터 {(a[1][-1]['t'] - a[1][0]['t']) / 3600:.1f}시간 — A {ra:+.1f}% / {ma:.1f}%  vs  E {re_:+.1f}% / {me:.1f}%")
        L.append("  재가동 이후 구간만 비교 (수익률 / MDD, 기준 자본 대비 %):" if res_lines else "  재가동 이후 구간: 아직 없음 — 재가동 전에는 A·E 차이가 0이어도 E안 효과가 없다는 뜻이 아님")
        L += res_lines
    return "\n".join(L)


def verdict():
    """24시간 판정 — 사용자 지정 실전 반영 권고 기준. 반환: (권고 bool, 이유 문자열). 6·12·24시간 세 시점 데이터가 모두 있어야 한다."""
    R = {h: compute(h) for h in (6, 12, 24)}
    ok = True; why = []
    for sym in SYMS:
        wins = 0; mdd_ok = True; ret24 = None
        for h in (6, 12, 24):
            d = R[h]["symbols"].get(sym)
            if not d or d["elapsed_h"] < h - 0.2: return False, f"{sym.upper()} {h}시간 시점 데이터 부족(경과 {d['elapsed_h']:.1f}h)" if d else f"{sym.upper()} 데이터 없음"
            a = d["var"]["A"]["합계"]; e = d["var"]["E"]["합계"]
            if e["ret"] >= a["ret"]: wins += 1
            if not (e["mdd"] <= 30 or e["mdd"] <= a["mdd"]): mdd_ok = False
            if h == 24: ret24 = (e["ret"], a["ret"])
        c1 = wins >= 2 and ret24[0] >= ret24[1]
        if not c1: ok = False; why.append(f"{sym.upper()} 수익률 조건 미달(E≥A {wins}/3, 24h E {ret24[0]:+.1f}% vs A {ret24[1]:+.1f}%)")
        if not mdd_ok: ok = False; why.append(f"{sym.upper()} MDD 조건 미달")
        rb = R[24]["symbols"][sym]["rebuy"]; nE = sum(len(v) for k, v in rb.items() if k.startswith("E_")); nA = sum(len(v) for k, v in rb.items() if k.startswith("A_"))
        if nE > nA + 1: ok = False; why.append(f"{sym.upper()} 노출 축소 손실 후 같은 가격대 재매수 반복(E {nE}건 vs A {nA}건)")
    return ok, ("모든 조건 충족" if ok else "; ".join(why))


def mark(h):
    st = json.load(open(START, encoding="utf-8")); st.setdefault("reported", {})[str(h)] = True
    json.dump(st, open(START, "w", encoding="utf-8"), ensure_ascii=False, indent=2); print("기록:", st["reported"])


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    if cmd == "init": init()
    elif cmd == "snap": snap()
    elif cmd == "mark": mark(int(sys.argv[2]))
    elif cmd == "verdict": ok, why = verdict(); print("실전 반영 권고: " + ("예" if ok else "아니오") + " — " + why)
    elif cmd == "json":
        res = compute(); out = {"start": res["start"], "start_str": res["start_str"], "symbols": {}}
        for sym, d in res["symbols"].items():
            dd = {k: v for k, v in d.items() if k != "var"}
            dd["var"] = {var: {k: ({kk: vv for kk, vv in v.items() if kk != "fills"} if k == "ev" else v) for k, v in vv_.items() if k != "ev"} | {"ev": {sd: {kk: vv for kk, vv in e.items() if kk != "fills"} for sd, e in vv_["ev"].items()}} for var, vv_ in d["var"].items()}
            out["symbols"][sym] = dd
        print(json.dumps(out, ensure_ascii=False))
    else:
        h = int(sys.argv[sys.argv.index("--hours") + 1]) if "--hours" in sys.argv else None
        res = compute(h); print(fmt_report(res, h or "누적"))
        sec = stop_section(h)
        if sec: print("\n" + sec)


if __name__ == "__main__":
    main()
