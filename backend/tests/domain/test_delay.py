"""遅延活動の抽出と影響（app.domain.delay）の試験。

設計書 5.1 の RULE-PRG-006、5.5・5.7、要求仕様書 7.9 BR-09・PRG-19・PRG-03。
全ての曜日を稼働日、原点 2026-12-01、報告日 2026-12-10（L＝10）。位置 p の終わりの日付は 12/p。

計画（前進計算の結果）::

    A(1) 10 日 12/01〜12/10  EF＝10 ─→ B(2) 5 日 12/11〜12/15  EF＝15 ─→ M(3) マイルストーン 12/15  M＝15
    C(4)  3 日 12/01〜12/03  EF＝3（後続なし）
    D(5) 12 日 12/01〜12/12  EF＝12（後続なし）
    プロジェクトの終了 E＝15。クリティカル：A・B・M（フロート 0）。C はフロート 12、D は 3
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from fractions import Fraction

import pytest

from app.domain.calendar import WorkCalendar
from app.domain.delay import (
    DEFAULT_EXTRACT_DAYS,
    DelayError,
    DelayInput,
    analyze_delays,
)
from app.domain.lightning import Judgement
from app.domain.scheduling import ScheduleActivity, ScheduleCycleError

ALL_DAYS = WorkCalendar("1111111")
WEEKDAYS = WorkCalendar()
ORIGIN = date(2026, 12, 1)
REPORT = date(2026, 12, 10)

PLAN = [
    ScheduleActivity(id=1, duration_days=10),
    ScheduleActivity(id=2, duration_days=5),
    ScheduleActivity(id=3, duration_days=0, is_milestone=True),
    ScheduleActivity(id=4, duration_days=3),
    ScheduleActivity(id=5, duration_days=12),
]
DEPS = [(1, 2), (2, 3)]


def d(day: int) -> date:
    return date(2026, 12, day)


def judged(activity_id: int, deviation: str, percent: int | str | None, *, forecast: date | None = None,
           judgement: Judgement | None = None) -> DelayInput:
    dev = Decimal(deviation)
    if judgement is None:
        judgement = Judgement.BEHIND if dev < Decimal("-0.5") else Judgement.ON_SCHEDULE
    return DelayInput(
        activity_id=activity_id,
        deviation=dev,
        judgement=judgement,
        percent_complete=None if percent is None else Decimal(percent),
        forecast_finish=forecast,
    )


def analyze(items: list[DelayInput], **kwargs: object):  # type: ignore[no-untyped-def]
    params: dict[str, object] = {"calendar": ALL_DAYS, "project_start": ORIGIN, "report_date": REPORT}
    params.update(kwargs)
    return analyze_delays(items, PLAN, DEPS, **params)  # type: ignore[arg-type]


def test_extracts_by_threshold_sorted_by_deviation() -> None:
    """RULE-PRG-006：δ ≦ −抽出の閾値（既定 2）の活動を、ずれの小さい順（遅れの大きい順）に並べる。δ＝−2 は入り −1.99 は入らない。"""
    assert DEFAULT_EXTRACT_DAYS == Decimal(2)
    result = analyze([
        judged(1, "-5", 50),
        judged(4, "-2", 0, forecast=d(3)),
        judged(5, "-1.99", Decimal("66.75")),
        judged(2, "-9", 0),
    ])
    assert [i.activity_id for i in result.delays] == [2, 1, 4]
    assert result.report_line == 10 and result.extract_days == Decimal(2)


def test_individual_impact_on_successors_milestone_and_project_finish() -> None:
    """RULE-PRG-006：見込みの終了＝L＋ceil(d×(1−進捗率))。EF を max(EF, 見込み) に固定して前進計算をやり直し、変わった後続・
    マイルストーン（ずれの稼働日）・プロジェクトの終了のずれ、クリティカルか、推移的な後続を返す（PRG-19）。

    A は 50%（P＝5、δ＝−5）。見込み＝10＋ceil(10×0.5)＝15 → B は 15→20、M は 15→20、E は 15→20。
    """
    (impact,) = analyze([judged(1, "-5", 50)]).delays
    assert impact.forecast_finish == 15 and impact.forecast_finish_date == d(15)
    assert (impact.fixed_finish.before, impact.fixed_finish.after, impact.fixed_finish.slip_days) == (10, 15, 5)
    assert impact.is_critical is True
    assert impact.transitive_successors == (2, 3)
    assert [(s.activity_id, s.before_date, s.after_date, s.slip_days) for s in impact.successor_slips] == [
        (2, d(15), d(20), 5)
    ]
    assert [(s.activity_id, s.is_milestone, s.before_date, s.after_date, s.slip_days) for s in impact.milestone_slips] == [
        (3, True, d(15), d(20), 5)
    ]
    assert (impact.project_finish.before_date, impact.project_finish.after_date, impact.project_finish.slip_days) == (
        d(15),
        d(20),
        5,
    )


def test_forecast_finish_date_overrides_formula() -> None:
    """RULE-PRG-006：完了見込み日（PRG-03）があれば見込みの終了は index(完了見込み日)＋1。後続の無い非クリティカルの活動は
    プロジェクトの終了を動かさない。

    C（EF＝3）が 0%・δ＝−9、完了見込み 12/12 → 見込み 12、固定 12。後続なし、E は 15 のまま。
    """
    (impact,) = analyze([judged(4, "-9", 0, forecast=d(12))]).delays
    assert impact.forecast_finish == 12
    assert (impact.fixed_finish.after, impact.fixed_finish.slip_days) == (12, 9)
    assert impact.is_critical is False
    assert impact.transitive_successors == ()
    assert impact.successor_slips == () and impact.milestone_slips == ()
    assert impact.project_finish.slip_days == 0


def test_forecast_earlier_than_plan_keeps_plan_finish() -> None:
    """RULE-PRG-006：EF＝max(EF, 見込みの終了)。見込みが計画の終わりより前なら計画の EF のままで、後続は動かない。"""
    (impact,) = analyze([judged(1, "-3", 70, forecast=d(8))]).delays
    assert impact.forecast_finish == 8
    assert (impact.fixed_finish.after, impact.fixed_finish.slip_days) == (10, 0)
    assert impact.successor_slips == () and impact.milestone_slips == ()
    assert impact.project_finish.slip_days == 0


def test_remaining_uses_ceiling() -> None:
    """RULE-PRG-006：残りの稼働日は切り上げる。A 33.33%：10×0.6667＝6.667 → 7、見込み＝17。"""
    (impact,) = analyze([judged(1, "-6.667", "33.33")]).delays
    assert impact.forecast_finish == 17


def test_forecast_on_non_working_day_rolls_back() -> None:
    """RULE-PRG-006：完了見込み日が非稼働日なら終了日の寄せ（前の稼働日。BR-04）をしてから終業の位置にする（設計書 1.1 の 5.7 の 2）。

    土日休み、原点 12/01（火）。C の完了見込み 12/05（土）→ 12/04（金）の終業＝4。
    """
    result = analyze([judged(4, "-3", 0, forecast=d(5))], calendar=WEEKDAYS)
    (impact,) = result.delays
    assert impact.forecast_finish == 4 and impact.forecast_finish_date == d(4)


def test_combined_impact_applies_all_forecasts_at_once() -> None:
    """RULE-PRG-006：全体の影響は抽出した全ての見込みを同時に当てた前進計算（マイルストーンとプロジェクトの終了のずれ）。

    A（見込み 15）と D（12 日、25%・δ＝−7、見込み＝10＋ceil(9)＝19）を同時に当てる → M は 20、E は max(20, 19)＝20。
    """
    result = analyze([judged(1, "-5", 50), judged(5, "-7", 25)])
    assert [i.activity_id for i in result.delays] == [5, 1]
    d_impact = result.delays[0]
    assert d_impact.forecast_finish == 19
    assert d_impact.project_finish.slip_days == 4  # D だけなら E は 15→19
    assert [(s.activity_id, s.slip_days) for s in result.combined_milestone_slips] == [(3, 5)]
    assert (result.combined_project_finish.before, result.combined_project_finish.after) == (15, 20)
    assert result.combined_project_finish.slip_days == 5


def test_combined_successor_also_waits_for_delayed_predecessor() -> None:
    """RULE-PRG-006：見込みは EF の下限として当てる（A-23）。全体の影響で、抽出した後続（B）は先行（A）の遅れも受ける。

    A 見込み 15。B（5 日・0%）の見込み＝10＋5＝15 で、B だけなら EF は max(15, 15)＝15（個別の影響は動かない）。
    同時に当てると B は A の見込み 15 から始まり EF＝max(15＋5, 15)＝20、M は 20、E は 20。
    """
    result = analyze([judged(1, "-5", 50), judged(2, "-5", 0)])
    b_alone = next(i for i in result.delays if i.activity_id == 2)
    assert (b_alone.fixed_finish.after, b_alone.milestone_slips) == (15, ())
    assert [(s.activity_id, s.slip_days) for s in result.combined_milestone_slips] == [(3, 5)]
    assert result.combined_project_finish.slip_days == 5


@pytest.mark.parametrize("judgement", [Judgement.COMPLETED, Judgement.NOT_STARTED, Judgement.NOT_COMPUTED])
def test_activities_placed_on_line_are_never_extracted(judgement: Judgement) -> None:
    """RULE-PRG-006：完了・未着手（予定内）・未算定は報告日線上に置いた活動なので、閾値 0 でも抽出しない。"""
    result = analyze([judged(4, "0", None if judgement is Judgement.NOT_COMPUTED else 0, judgement=judgement)],
                     extract_days=Decimal(0))
    assert result.delays == ()


def test_no_delays_returns_unchanged_finish() -> None:
    """RULE-PRG-006：抽出が 0 件なら全体の影響も無い（プロジェクトの終了のずれ 0）。"""
    result = analyze([judged(1, "-1", 90)])
    assert result.delays == () and result.combined_milestone_slips == ()
    assert result.combined_project_finish.slip_days == 0
    assert result.combined_project_finish.before_date == d(15)


def test_invalid_inputs_rejected() -> None:
    """RULE-PRG-006：日程計算の入力に無い活動・同じ活動の判定の重複・負の閾値は拒む。循環があれば計算しない。"""
    with pytest.raises(DelayError):
        analyze([judged(99, "-5", 50)])
    with pytest.raises(DelayError):
        analyze([judged(1, "-5", 50), judged(1, "-4", 60)])
    with pytest.raises(DelayError):
        analyze([], extract_days=Decimal(-1))
    with pytest.raises(ScheduleCycleError):
        analyze_delays([], PLAN, [(1, 2), (2, 1)], calendar=ALL_DAYS, project_start=ORIGIN, report_date=REPORT)


def test_remaining_uses_unrounded_percent() -> None:
    """RULE-PRG-006：見込みの終了の残りは丸める前の進捗率から求める（設計書 1.1 の 5.6・5.7・CR-009）。

    活動 5（12 日）が 1/3 なら残りは 12×2/3＝8 ちょうどで見込み＝18。保存した 33.33% なら 8.0004 → 9 で 19 になる。
    """
    exact = DelayInput(activity_id=5, deviation=Decimal(-5), judgement=Judgement.BEHIND, percent_complete=Fraction(100, 3))
    (impact,) = analyze([exact]).delays
    assert impact.forecast_finish == 18
    (rounded,) = analyze([judged(5, "-5", "33.33")]).delays
    assert rounded.forecast_finish == 19
