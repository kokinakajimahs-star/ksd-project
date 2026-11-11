"""遅延活動の抽出と影響（設計書 5.1 の RULE-PRG-006、5.7。要求仕様書 7.9 BR-09、PRG-19・PRG-03）。

1. 抽出：ずれで判定した活動（RULE-PRG-004）のうち ``δ ≤ −delay_extract_days`` のものを、ずれの小さい順（遅れの大きい順）に並べる
2. 見込みの終了：完了見込み日（PRG-03）があれば ``index(完了見込み日) + 1``、無ければ ``L + ceil(d × (1 − 進捗率))``
3. 個別の影響：その活動の EF を ``max(EF, 見込みの終了)`` に固定して前進計算（RULE-SCH-002）をやり直し、変わった後続の活動・
   マイルストーン（ずれの稼働日）・プロジェクトの終了のずれを返す。あわせて、その活動がクリティカルか（RULE-SCH-003）と、
   推移的な後続の一覧を返す
4. 全体の影響：抽出した全ての活動の見込みを同時に当てた前進計算の結果（マイルストーンとプロジェクトの終了のずれ）も返す

- 完了見込み日が非稼働日のときは、終了日の寄せ（BR-04。前の稼働日）をしてから終業の位置にする（稼働日なら
  ``index(完了見込み日) + 1`` と同じ。docs/decisions/ASSUMPTIONS.md A-23）
- 見込みは EF の下限として当てる：``EF' = max(ES' + d, 見込みの終了)``。個別の影響では先行が動かないので ``ES' + d`` は計画の
  EF で、5.7 の 3 の ``max(EF, 見込みの終了)`` に一致する。全体の影響で、抽出した活動どうしが先行・後続の関係にあるとき、
  後続は先行の遅れも受ける（先行の見込みより前には始められない。A-23）
- 位置は稼働日の軸（5.4。原点はプロジェクトの開始日）の上の位置。日付は 5.5 の「結果の日付」（添字 ``EF − 1`` の稼働日）

純粋に保つ（1.3）。DB・HTTP・時刻・乱数に触れない。報告日は引数で受ける。DB の行はサービス層が下のデータクラスへ直して渡す。
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Final

from app.domain.calendar import WorkCalendar, WorkdayAxis
from app.domain.lightning import Judgement
from app.domain.scheduling import (
    DependencyInput,
    ForwardPass,
    ScheduleActivity,
    backward_pass,
    forward_pass,
)

HUNDRED: Final = Decimal(100)
# 遅延の抽出の閾値の既定（BR-09。projects.settings.delay_extract_days の既定。4.2）
DEFAULT_EXTRACT_DAYS: Final = Decimal(2)


class DelayError(ValueError):
    """遅延の抽出・影響の入力が不正（未定義の入力は推測せずに拒む）。"""


@dataclass(frozen=True)
class DelayInput:
    """活動の報告日の判定（RULE-PRG-004 の結果）と進捗の入力（4.1 の ``progress_entries``）のうち、ここで要る値。

    ``percent_complete``：RULE-PRG-001 の進捗率（0〜100。``None`` は未算定）。``forecast_finish``：完了見込み日（PRG-03）。
    """

    activity_id: int
    deviation: Decimal
    judgement: Judgement
    percent_complete: Decimal | None
    forecast_finish: date | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "deviation", _as_decimal(self.deviation, f"活動 {self.activity_id} のずれ"))
        object.__setattr__(self, "judgement", Judgement(self.judgement))
        if self.percent_complete is not None:
            p = _as_decimal(self.percent_complete, f"活動 {self.activity_id} の進捗率")
            if p < 0 or p > HUNDRED:
                raise DelayError(f"活動 {self.activity_id}：進捗率は 0〜100: {p}")
            object.__setattr__(self, "percent_complete", p)


@dataclass(frozen=True)
class Slip:
    """1 つの活動（マイルストーン）の終わりのずれ。``before``・``after`` は軸上の位置（活動は EF、マイルストーンは M）。

    ``slip_days = after − before``（稼働日）。日付は添字 ``位置 − 1`` の稼働日（活動の終了日・マイルストーンの日付）。
    """

    activity_id: int
    is_milestone: bool
    before: int
    after: int
    before_date: date
    after_date: date

    @property
    def slip_days(self) -> int:
        return self.after - self.before


@dataclass(frozen=True)
class FinishSlip:
    """プロジェクトの終了（``E = max(EF)``）のずれ。活動が無ければ全て ``None``。"""

    before: int | None
    after: int | None
    before_date: date | None
    after_date: date | None

    @property
    def slip_days(self) -> int:
        if self.before is None or self.after is None:
            return 0
        return self.after - self.before


@dataclass(frozen=True)
class DelayImpact:
    """抽出した 1 つの遅延活動とその個別の影響（5.7 の 2・3）。

    ``forecast_finish``：見込みの終了（位置）。``fixed_finish``：固定した EF（``max(EF, 見込みの終了)``）。
    ``successor_slips``：変わった後続の活動（マイルストーンを除く）。``milestone_slips``：変わったマイルストーン。
    どちらもトポロジカル順（同順位は活動の id の昇順）。``transitive_successors`` も同じ順。
    """

    activity_id: int
    deviation: Decimal
    percent_complete: Decimal | None
    forecast_finish: int
    forecast_finish_date: date
    fixed_finish: Slip
    is_critical: bool
    transitive_successors: tuple[int, ...]
    successor_slips: tuple[Slip, ...]
    milestone_slips: tuple[Slip, ...]
    project_finish: FinishSlip


@dataclass(frozen=True)
class DelayAnalysis:
    """遅延の抽出と影響（API-PRG-009 が返す）。``delays`` はずれの小さい順（同じなら活動の id の昇順）。

    ``combined_*`` は抽出した全ての活動の見込みを同時に当てた結果（5.7 の 4）。
    """

    report_line: int
    extract_days: Decimal
    delays: tuple[DelayImpact, ...]
    combined_milestone_slips: tuple[Slip, ...]
    combined_project_finish: FinishSlip


def _as_decimal(value: object, what: str) -> Decimal:
    if isinstance(value, bool):
        raise DelayError(f"{what}は数で渡す: {value!r}")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise DelayError(f"{what}は数で渡す: {value!r}") from exc
    if not result.is_finite():
        raise DelayError(f"{what}が有限の数ではない: {value!r}")
    return result


def _propagate(forward: ForwardPass, forecasts: Mapping[int, int]) -> dict[int, int]:
    """前進計算（RULE-SCH-002 と同じ式）をやり直した EF（マイルストーンは M）。``forecasts`` の活動は見込みの終了を EF の下限にする。"""
    finish: dict[int, int] = {}
    for activity_id in forward.order:
        early = forward.early[activity_id]
        start = early.lower_bound
        for p in forward.graph.predecessors(activity_id):
            start = max(start, finish[p])
        end = start + early.duration_days
        if activity_id in forecasts:
            end = max(end, forecasts[activity_id])
        finish[activity_id] = end
    return finish


def _transitive_successors(forward: ForwardPass, activity_id: int) -> set[int]:
    found: set[int] = set()
    queue: deque[int] = deque(forward.graph.successors(activity_id))
    while queue:
        v = queue.popleft()
        if v in found:
            continue
        found.add(v)
        queue.extend(forward.graph.successors(v))
    return found


# RULE-PRG-006 遅延活動の抽出と影響
def analyze_delays(
    judged: Iterable[DelayInput],
    activities: Iterable[ScheduleActivity],
    dependencies: Iterable[DependencyInput],
    *,
    calendar: WorkCalendar,
    project_start: date,
    report_date: date,
    extract_days: Decimal | int | str = DEFAULT_EXTRACT_DAYS,
) -> DelayAnalysis:
    """遅延活動を抽出し、後続・クリティカルパス・マイルストーン・プロジェクトの終了への影響を求める（5.7・PRG-19）。

    - ``judged``：報告日の活動ごとの判定とずれ（RULE-PRG-004）と進捗の入力。``activities``・``dependencies``：最新計画の
      日程計算の入力（RULE-SCH-002 と同じ。循環があれば ``ScheduleCycleError``）
    - 抽出は、ずれで判定した活動（遅れ・予定どおり・前倒し）のうち ``δ ≤ −extract_days`` のもの。完了・未着手（予定内）・
      未算定は報告日線上に置いた活動なので抽出しない
    - 判定の活動が日程計算の入力に無いとき・同じ活動の判定が 2 つあるとき・閾値が負のときは ``DelayError``
    """
    threshold = _as_decimal(extract_days, "遅延の抽出の閾値")
    if threshold < 0:
        raise DelayError(f"遅延の抽出の閾値（稼働日）は 0 以上: {threshold}")
    activity_list = list(activities)
    forward = forward_pass(activity_list, dependencies, calendar=calendar, project_start=project_start)
    backward = backward_pass(forward)
    axis = WorkdayAxis(calendar=calendar, origin=project_start)
    line = axis.report_line(report_date)
    position = {a: i for i, a in enumerate(forward.order)}

    inputs: dict[int, DelayInput] = {}
    for item in judged:
        if item.activity_id not in forward.early:
            raise DelayError(f"判定の活動 {item.activity_id} が日程計算の入力に無い")
        if item.activity_id in inputs:
            raise DelayError(f"活動 {item.activity_id} の判定が 2 つある")
        inputs[item.activity_id] = item

    extracted = sorted(
        (i for i in inputs.values() if i.judgement.judged_by_deviation and i.deviation <= -threshold),
        key=lambda i: (i.deviation, i.activity_id),
    )

    base = {a: forward.early[a].early_finish for a in forward.order}
    base_finish = max(base.values(), default=None)

    def date_at(p: int) -> date:
        return axis.workday_at(p - 1)

    def slip(activity_id: int, after: Mapping[int, int]) -> Slip:
        return Slip(
            activity_id=activity_id,
            is_milestone=forward.early[activity_id].is_milestone,
            before=base[activity_id],
            after=after[activity_id],
            before_date=date_at(base[activity_id]),
            after_date=date_at(after[activity_id]),
        )

    def finish_slip(after: Mapping[int, int]) -> FinishSlip:
        if base_finish is None:
            return FinishSlip(before=None, after=None, before_date=None, after_date=None)
        after_finish = max(after.values())
        return FinishSlip(
            before=base_finish,
            after=after_finish,
            before_date=date_at(base_finish),
            after_date=date_at(after_finish),
        )

    def forecast_end(item: DelayInput) -> int:
        if item.forecast_finish is not None:
            rolled = calendar.roll_backward(item.forecast_finish)  # 終了日の寄せ（BR-04）
            return axis.index(rolled) + 1
        if item.percent_complete is None:
            raise DelayError(f"活動 {item.activity_id}：進捗率が未算定で、見込みの終了を求められない")
        duration = forward.early[item.activity_id].duration_days
        remaining = Decimal(duration) * (1 - item.percent_complete / HUNDRED)
        return line + math.ceil(remaining)

    forecasts: dict[int, int] = {}
    impacts: list[DelayImpact] = []
    for item in extracted:
        a = item.activity_id
        forecast = forecast_end(item)
        forecasts[a] = forecast
        after = _propagate(forward, {a: forecast})  # その活動の EF は max(EF, 見込みの終了) になる
        successors = _transitive_successors(forward, a)
        changed = [s for s in forward.order if s in successors and after[s] != base[s]]
        impacts.append(
            DelayImpact(
                activity_id=a,
                deviation=item.deviation,
                percent_complete=item.percent_complete,
                forecast_finish=forecast,
                forecast_finish_date=date_at(forecast),
                fixed_finish=slip(a, after),
                is_critical=backward.times[a].is_critical,
                transitive_successors=tuple(sorted(successors, key=position.__getitem__)),
                successor_slips=tuple(slip(s, after) for s in changed if not forward.early[s].is_milestone),
                milestone_slips=tuple(slip(s, after) for s in changed if forward.early[s].is_milestone),
                project_finish=finish_slip(after),
            )
        )

    combined = _propagate(forward, forecasts)
    return DelayAnalysis(
        report_line=line,
        extract_days=threshold,
        delays=tuple(impacts),
        combined_milestone_slips=tuple(
            slip(a, combined) for a in forward.order if forward.early[a].is_milestone and combined[a] != base[a]
        ),
        combined_project_finish=finish_slip(combined),
    )
