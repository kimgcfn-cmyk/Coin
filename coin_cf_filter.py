"""
수익곡선 필터 (2026-10-28, 사용자 지시) — 기본은 롱·숏 각각 따로 판정
================================================================================
CurveFilter(sides=("long",)) / CurveFilter(sides=("short",))  : 그 봇 하나의 수익곡선(누적 순손익 + 평가손익, 적용 시점 기준 0)으로 판정한다.
  ① 곡선이 직전 고점 대비 기준 자본(한도÷5)의 drop_pct%(기본 5%)만큼 내려가면 그 봇을 정지하고 보유량을 keep USDT(기본 100)만 남기고 시장가(reduce-only)로 정리한다.
     정지된 봇은 반대쪽 봇에 사다리 단계 0으로 보인다. 반대쪽 봇은 자기 곡선으로 따로 판단한다(같이 멈추지 않는다).
  ② 정지 시점의 장부로 '그림자 봇'(모의)이 같은 전략을 계속 돌며 그림자 곡선을 추적한다.
  ③ 그림자 곡선이 자기 저점 대비 rise_pct%(기본 8%) 오르면(min_stop_min분 이상 정지했을 때) 그 봇만 재가동한다.
  정지·재가동 모두 confirm_min분 확인 대기: 조건을 처음 만족한 뒤 그 시간이 지난 판정에서 다시 만족해야 실행(가짜 신호 거르기, 2026-10-28 사용자 지시 1분)(시세 기록은 그림자 것을 이어받음).
CurveFilter(sides=("long","short")) 로 만들면 롱+숏 합산 곡선으로 한 쌍을 같이 판정하는 방식(합산 모드)도 쓸 수 있다.
두 가지로 쓴다: (1) 관찰 모드/백테스트 — external=False, 이 클래스가 담당 봇 장부를 직접 step 한다.
              (2) 실전 컨트롤러(coin_cf_run.py) — external=True, 실봇 프로세스가 각자 step 하고 이 클래스는 판정·정지·잔량 손절·그림자·재가동만 한다
                  (정지는 장부의 cf_halt 플래그로 봇에 전달: 봇은 cf_halt가 켜져 있으면 거래하지 않는다).
주의: 과거 재생 결과일 뿐 투자 조언이 아니다.
"""
import copy
import coin_avg_ladder_bot as b

SIDE_KO = {"long": "롱", "short": "숏"}


class CurveFilter:
    def __init__(self, cfgs, states, order_fn, sides=("long",), drop_pct=5.0, rise_pct=8.0, min_stop_min=0, keep=100.0, cap_div=5.0, confirm_min=0,
                 external=False, prepare=None, notify=None, log=None, name=""):
        """cfgs/states: {'long': ..., 'short': ...} 둘 다 넘기고, sides로 이 필터가 맡을 봇을 정한다. order_fn: 실봇 주문 함수(side, usdt, price, reduce_only, qty) — 관찰 모드에서는 모의.
        prepare(sd): 실주문 직전에 해당 봇의 CFG를 적용하는 훅(실전용). notify(msg): 디스코드 알림. log(msg): 로그 출력."""
        self.sides = tuple(sides); self.cfgs, self.S, self.order_fn = cfgs, states, order_fn
        cap = sum(float(cfgs[sd]["max_total_usdt"]) / cap_div for sd in self.sides)
        self.D = cap * drop_pct / 100.0; self.R = cap * rise_pct / 100.0; self.min_stop = float(min_stop_min) * 60; self.keep = keep
        self.confirm = float(confirm_min) * 60; self.pend = None; self.rpend = None      # 확인 대기: 조건을 처음 만족한 뒤 confirm_min분 뒤에 다시 만족해야 실행
        self.external = external; self.prepare = prepare; self.notify = notify; self.log = log or (lambda *a: None)
        self.label = "·".join(SIDE_KO[sd] for sd in self.sides) if len(self.sides) == 1 else "롱+숏 합산"
        self.name = f"{name} {self.label}".strip()
        self.mode = "RUN"; self.peak = 0.0; self.trough = 0.0; self.since = 0.0; self.SH = None; self.base = None
        self.pending = {sd: False for sd in self.sides}
        self.stops = 0; self.restarts = 0; self.stop_ticks = 0; self.events = []

    # ── 저장·복원(실전 컨트롤러용) ──
    def dump(self):
        return dict(pend=self.pend, rpend=self.rpend, mode=self.mode, peak=self.peak, trough=self.trough, since=self.since, SH=self.SH, base=self.base, pending=self.pending,
                    stops=self.stops, restarts=self.restarts, stop_ticks=self.stop_ticks, events=self.events[-40:])

    def load(self, d):
        for k in ("mode", "peak", "trough", "since", "SH", "base", "pending", "stops", "restarts", "stop_ticks", "events", "pend", "rpend"):
            if k in d: setattr(self, k, d[k])

    def _opp(self, sd):
        return "short" if sd == "long" else "long"

    def _call(self, state, sd, p, now, opp_bs, order_fn):
        b.CFG.clear(); b.CFG.update(self.cfgs[sd]); b.CFG["opp_bs"] = opp_bs
        return b.step(state, p, now, order_fn, lambda *a: None)

    def equity(self, states, p):
        return sum(b.cf_equity(states[sd], p, sd == "long") for sd in self.sides)

    def _note(self, msg):
        if self.notify:
            try: self.notify(msg)
            except Exception: pass

    def _confirmed(self, hit, now, attr):
        """확인 대기 판정: confirm=0이면 hit 그대로. 아니면 처음 hit 때 대기 시작, confirm 시간이 지난 첫 판정에서 다시 hit이면 True(아니면 대기 취소)."""
        if not self.confirm: return hit
        pend = getattr(self, attr)
        if pend is None:
            if hit:
                setattr(self, attr, now); self.log(f"  ⏳ {self.name} {'정지' if attr == 'pend' else '재가동'} 조건 충족 — {self.confirm / 60:g}분 뒤 다시 확인")
            return False
        if now - pend < self.confirm - 5: return False
        setattr(self, attr, None)
        if not hit: self.log(f"  ↩️ {self.name} {'정지' if attr == 'pend' else '재가동'} 확인 실패 — 조건이 풀려 취소")
        return hit

    def _cut(self, sd, p):
        if self.prepare: self.prepare(sd)
        ok = b.cf_cut(self.S[sd], p, self.order_fn, sd == "long", self.keep, self.log, self.notify)
        self.pending[sd] = not ok

    def step(self, p, now):
        """한 번 판정. 반환: 이 필터가 맡은 봇들의 현재 절대 곡선 값(누적 순손익 + 평가손익)."""
        S = self.S
        if self.base is None: self.base = self.equity(S, p)
        if self.mode == "RUN":
            if not self.external:
                for sd in self.sides: self._call(S[sd], sd, p, now, S[self._opp(sd)].get("bs", 0), self.order_fn)
            e = self.equity(S, p) - self.base; self.peak = max(self.peak, e)
            self.log(f"  📈 {self.name} 수익곡선: 현재 {e:+.2f} / 고점 {self.peak:+.2f} / 하락 {self.peak - e:.2f} (정지 기준 {self.D:g})")
            if self._confirmed(self.peak - e >= self.D, now, "pend"):        # ① 수익곡선 하락(확인 후) → 정지 + 잔량 손절
                self.SH = {sd: {k: copy.deepcopy(v) for k, v in S[sd].items() if k != "cf_halt"} for sd in self.sides}
                self.rpend = None; self.mode = "STOP"; self.since = now; self.stops += 1; self.trough = self.equity(self.SH, p) - self.base
                self.events.append((now, "정지", round(e, 2)))
                self._note(f"🛑 <b>{self.name} 수익곡선 정지</b>\n곡선 {e:+.2f} (고점 {self.peak:+.2f} 대비 −{self.peak - e:.2f} ≥ {self.D:g}) — 정지하고 잔량 {self.keep:g}USDT만 남기고 정리")
                self.log(f"  🛑 {self.name} 수익곡선 하락 — 정지, 잔량 손절 진행")
                for sd in self.sides:
                    self._cut(sd, p); S[sd].update(bs=0, ss=0, cf_halt=True)
        else:
            self.stop_ticks += 1
            for sd in self.sides:
                if self.pending[sd]: self._cut(sd, p)
            for sd in self.sides:
                opp = self._opp(sd); opp_bs = self.SH[opp].get("bs", 0) if opp in self.SH else S[opp].get("bs", 0)
                self._call(self.SH[sd], sd, p, now, opp_bs, b.cf_paper_order)
            es = self.equity(self.SH, p) - self.base; self.trough = min(self.trough, es)
            waited = (now - self.since) / 60
            self.log(f"  ⏸ {self.name} 정지 중 {waited:.0f}분 — 그림자 곡선 {es:+.2f} (저점 {self.trough:+.2f}, 재가동 +{self.R:g} / 최소 {self.min_stop / 60:.0f}분)")
            if self._confirmed(es - self.trough >= self.R and now - self.since >= self.min_stop and not any(self.pending.values()), now, "rpend"):      # ③ 그림자 곡선 회복(확인 후) → 재가동
                for sd in self.sides:
                    S[sd]["ref_hist"] = copy.deepcopy(self.SH[sd]["ref_hist"]); S[sd].update(bs=0, ss=0, full=False, stopped=0, started=True, cf_halt=False)
                    for k_ in ("trend", "t_peak", "rest_until"): S[sd].pop(k_, None)
                self.pend = None; self.mode = "RUN"; self.restarts += 1; self.SH = None; self.peak = self.equity(S, p) - self.base
                self.events.append((now, "재가동", round(es, 2)))
                self._note(f"▶️ <b>{self.name} 재가동</b>\n그림자 곡선 {es:+.2f} (저점 대비 +{es - self.trough:.2f} ≥ {self.R:g}) · {waited:.0f}분 정지")
                self.log(f"  ▶️ {self.name} 그림자 곡선 회복 — 재가동")
        return self.equity(S, p)


def make_filters(cfgs, states, order_fn, mode="side", **kw):
    """mode 'side'(기본): 롱·숏 각각 한 개씩 / 'pair': 롱+숏 합산 한 개."""
    if mode == "pair": return [CurveFilter(cfgs, states, order_fn, sides=("long", "short"), **kw)]
    return [CurveFilter(cfgs, states, order_fn, sides=(sd,), **kw) for sd in ("long", "short")]
