"""日程計算（app.domain.scheduling）の試験。

設計書 5.1・5.5、要求仕様書 7.5 BR-05・7.4 BR-04、SCH-02・04・06・09・10、NFR-01。
2026-12 の曜日：12/01＝火、12/04＝金、12/05＝土、12/06＝日、12/07＝月。プロジェクトの開始日は 12/01（軸の原点。位置 0）。
既定のカレンダー（土日休み）での位置：12/01＝0、12/02＝1、12/03＝2、12/04＝3、12/07＝4、12/08＝5、12/09＝6、12/10＝7。
"""

from __future__ import annotations

import time
from datetime import date

import pytest

from app.domain.calendar import WorkCalendar
from app.domain.scheduling import (
    UNSET,
    ActivityChange,
    BulkIssueCode,
    BulkUpdateError,
    Dependency,
    DependencyGraph,
    EditableActivity,
    ScheduleActivity,
    ScheduleCycleError,
    SchedulingError,
    backward_pass,
    calculate_schedule,
    check_bulk_update,
    ensure_acyclic,
    forward_pass,
)

CAL = WorkCalendar()
START = date(2026, 12, 1)


def d(month_day: str) -> date:
    """'12/05' → 2026-12-05（'11/30' は 2026 年、'01/04' は 2027 年）。"""
    month, day = (int(x) for x in month_day.split("/"))
    return date(2026 if month >= 11 else 2027, month, day)


def act(activity_id: int, duration: int, requested: str | None = None) -> ScheduleActivity:
    return ScheduleActivity(
        id=activity_id,
        duration_days=duration,
        is_milestone=duration == 0,
        requested_start=None if requested is None else d(requested),
    )


def schedule(activities: list[ScheduleActivity], deps: list[tuple[int, int]], calendar: WorkCalendar = CAL):
    return calculate_schedule(activities, deps, calendar=calendar, project_start=START)


# ---------------------------------------------------------------------------
# RULE-SCH-001 依存の循環検出
# ---------------------------------------------------------------------------


def test_no_cycle_in_chain() -> None:
    """RULE-SCH-001：鎖状の依存には循環が無い（空を返す）。"""
    graph = DependencyGraph([1, 2, 3], [(1, 2), (2, 3)])
    assert graph.cycles() == ()
    assert graph.cycle_activity_ids() == ()
    ensure_acyclic(graph)  # 例外を出さない


def test_two_activity_cycle() -> None:
    """RULE-SCH-001：2 つの活動が互いに先行になる循環は、その 2 つを返す。"""
    graph = DependencyGraph([1, 2, 3], [(1, 2), (2, 1), (2, 3)])
    assert graph.cycles() == ((1, 2),)
    assert graph.cycle_activity_ids() == (1, 2)


def test_cycle_returns_only_activities_in_the_cycle() -> None:
    """RULE-SCH-001：循環の下流・上流の活動、2 つの循環の間の活動は「循環に含まれる活動」に入らない。"""
    # 9 → (1 → 2 → 3 → 1) → 4 → (5 ↔ 6)
    deps = [(9, 1), (1, 2), (2, 3), (3, 1), (3, 4), (4, 5), (5, 6), (6, 5)]
    graph = DependencyGraph([1, 2, 3, 4, 5, 6, 9], deps)
    assert graph.cycles() == ((1, 2, 3), (5, 6))
    assert graph.cycle_activity_ids() == (1, 2, 3, 5, 6)


def test_self_dependency_is_a_cycle() -> None:
    """RULE-SCH-001：自分への依存は 1 つの活動の循環。"""
    graph = DependencyGraph([5, 6], [(5, 5)])
    assert graph.cycles() == ((5,),)


def test_cycle_if_added_before_adding_dependency() -> None:
    """RULE-SCH-001：依存を足す前に、足すと循環になるか判定し、その循環の活動（後続→…→先行）を返す（SCH-04）。"""
    graph = DependencyGraph([1, 2, 3, 4], [(1, 2), (2, 3)])
    assert graph.cycle_if_added(3, 1) == (1, 2, 3)  # 1→2→3 に 3→1 を足す
    assert graph.cycle_if_added(2, 1) == (1, 2)
    assert graph.cycle_if_added(1, 3) == ()  # 推移的な依存は循環ではない
    assert graph.cycle_if_added(4, 1) == ()
    assert graph.cycle_if_added(2, 2) == (2,)  # 自分への依存
    with pytest.raises(SchedulingError):
        graph.cycle_if_added(1, 99)


def test_cycle_if_added_picks_shortest_path_deterministically() -> None:
    """RULE-SCH-001：後続から先行への経路が複数あれば、最短で ID の小さい活動をたどる経路を返す。"""
    graph = DependencyGraph([1, 2, 3, 4, 5], [(1, 3), (1, 2), (2, 5), (3, 5), (1, 4), (4, 3)])
    assert graph.cycle_if_added(5, 1) == (1, 2, 5)


def test_independent_activities_are_identified() -> None:
    """RULE-SCH-001：先行の無い活動を並行可能として識別できる（SCH-04 の受入基準）。"""
    graph = DependencyGraph([1, 2, 3, 4], [(1, 3), (2, 3)])
    assert graph.independent_activity_ids() == (1, 2, 4)


def test_graph_rejects_unknown_and_duplicate_dependencies() -> None:
    """RULE-SCH-001：入力に無い活動への依存と、同じ組の重複は入力の誤り。"""
    with pytest.raises(SchedulingError):
        DependencyGraph([1, 2], [(1, 3)])
    with pytest.raises(SchedulingError):
        DependencyGraph([1, 2], [(1, 2), (1, 2)])
    with pytest.raises(SchedulingError):
        DependencyGraph([1, 1], [])


def test_only_fs_and_zero_lag_in_r1() -> None:
    """RULE-SCH-001：R1 の依存は終了→開始・ラグ 0 だけ（BR-05・OQ-04）。それ以外は拒む。"""
    assert Dependency(1, 2).pair == (1, 2)
    for dep_type in ("SS", "FF", "SF"):
        with pytest.raises(SchedulingError):
            Dependency(1, 2, dep_type=dep_type)
    for lag in (1, -1):
        with pytest.raises(SchedulingError):
            Dependency(1, 2, lag_days=lag)


def test_topological_order_breaks_ties_by_id() -> None:
    """RULE-SCH-001：トポロジカル順の同順位は活動の id の昇順（5.5）。"""
    graph = DependencyGraph([3, 1, 2, 4], [(3, 1), (4, 2)])
    assert graph.topological_order() == (3, 1, 4, 2)


def test_schedule_is_not_calculated_when_cycle_exists() -> None:
    """RULE-SCH-001：循環があれば計算せず、循環の活動の一覧を SCHEDULE_CYCLE で返す（5.5・BR-05）。"""
    with pytest.raises(ScheduleCycleError) as info:
        schedule([act(1, 1), act(2, 1), act(3, 1), act(4, 1)], [(1, 2), (2, 3), (3, 2), (3, 4)])
    assert info.value.code == "SCHEDULE_CYCLE"
    assert info.value.activity_ids == (2, 3)
    with pytest.raises(ScheduleCycleError):
        DependencyGraph([1, 2], [(1, 2), (2, 1)]).topological_order()


def test_cycle_detection_is_fast_for_large_graph() -> None:
    """RULE-SCH-001：再帰を使わないので、長い鎖（活動 5,000）でも再帰の上限で落ちない。"""
    n = 5000
    deps = [(i, i + 1) for i in range(1, n)] + [(n, 1)]
    graph = DependencyGraph(range(1, n + 1), deps)
    assert graph.cycle_activity_ids() == tuple(range(1, n + 1))


# ---------------------------------------------------------------------------
# RULE-SCH-002 前進計算（自動スケジューリング）
# ---------------------------------------------------------------------------


def test_activity_without_predecessor_starts_on_project_start() -> None:
    """RULE-SCH-002：先行も希望開始日も無い活動はプロジェクトの開始日に始まる。終了日は d 番目の稼働日（BR-04・BR-05）。"""
    result = schedule([act(1, 3)], [])
    a = result.activities[1]
    assert (a.times.early_start, a.times.early_finish) == (0, 3)
    assert (a.start_date, a.finish_date) == (d("12/01"), d("12/03"))


def test_successor_starts_on_next_working_day_after_predecessor() -> None:
    """RULE-SCH-002：後続の最早開始は先行の終了日の次の稼働日（週末を飛ばす。BR-05）。"""
    result = schedule([act(1, 4), act(2, 2)], [(1, 2)])
    first, second = result.activities[1], result.activities[2]
    assert (first.start_date, first.finish_date) == (d("12/01"), d("12/04"))  # 火〜金
    assert (second.times.early_start, second.times.early_finish) == (4, 6)
    assert (second.start_date, second.finish_date) == (d("12/07"), d("12/08"))  # 月〜火


def test_successor_waits_for_latest_predecessor() -> None:
    """RULE-SCH-002：先行が複数あれば、全ての先行の終了日の次の稼働日のうち最も遅い日（BR-05）。"""
    result = schedule([act(1, 2), act(2, 5), act(3, 1)], [(1, 3), (2, 3)])
    assert result.activities[3].times.early_start == 5
    assert result.activities[3].start_date == d("12/08")


def test_requested_start_is_lower_bound() -> None:
    """RULE-SCH-002：開始日は最早開始日と希望開始日の遅いほう（BR-05）。"""
    later = schedule([act(1, 2), act(2, 1, "12/09")], [(1, 2)])
    assert later.activities[2].start_date == d("12/09")  # 希望開始日のほうが遅い
    earlier = schedule([act(1, 3), act(2, 1, "12/02")], [(1, 2)])
    assert earlier.activities[2].start_date == d("12/04")  # 先行の終了の翌稼働日のほうが遅い
    same = schedule([act(1, 3), act(2, 1, "12/04")], [(1, 2)])
    assert same.activities[2].start_date == d("12/04")  # 境目：同じ日


def test_requested_start_on_non_working_day_rolls_forward() -> None:
    """RULE-SCH-002：非稼働日の希望開始日は次の稼働日の始業（index の定義。BR-04 の開始日の寄せ）。"""
    result = schedule([act(1, 2, "12/05")], [])  # 土曜
    assert result.activities[1].times.early_start == 4
    assert (result.activities[1].start_date, result.activities[1].finish_date) == (d("12/07"), d("12/08"))


def test_requested_start_with_holiday() -> None:
    """RULE-SCH-002：休日を飛ばして数える（BR-04）。"""
    cal = WorkCalendar.of(holidays=[d("12/03")])
    result = schedule([act(1, 3), act(2, 1)], [(1, 2)], calendar=cal)
    assert (result.activities[1].start_date, result.activities[1].finish_date) == (d("12/01"), d("12/04"))
    assert result.activities[2].start_date == d("12/07")


def test_requested_start_before_project_start_follows_formula() -> None:
    """RULE-SCH-002：下限は index(希望開始日 ?? 開始日)。原点より前の希望開始日は負の位置になる（5.5 の式どおり）。"""
    result = schedule([act(1, 2, "11/30")], [])
    assert result.activities[1].times.early_start == -1
    assert (result.activities[1].start_date, result.activities[1].finish_date) == (d("11/30"), d("12/01"))


def test_milestone_without_predecessor_is_end_of_project_start_day() -> None:
    """RULE-SCH-002：マイルストーンの下限はその日の終業（index＋1）。日付は添字 M−1 の稼働日（5.5）。"""
    result = schedule([act(1, 0)], [])
    m = result.activities[1]
    assert (m.times.early_start, m.times.early_finish) == (1, 1)
    assert m.start_date == m.finish_date == d("12/01")


def test_milestone_after_activity_is_on_predecessor_finish_date() -> None:
    """RULE-SCH-002：後続がマイルストーンなら、全ての先行の終了日のうち最も遅い日がマイルストーンの日付（BR-05）。"""
    result = schedule([act(1, 3), act(2, 4), act(3, 0)], [(1, 3), (2, 3)])
    m = result.activities[3]
    assert m.times.early_start == 4
    assert m.start_date == m.finish_date == d("12/04")


def test_activity_after_milestone_starts_next_working_day() -> None:
    """RULE-SCH-002：先行がマイルストーンなら、マイルストーンの日付の次の稼働日に始まる（BR-05）。"""
    result = schedule([act(1, 4), act(2, 0), act(3, 2)], [(1, 2), (2, 3)])
    assert result.activities[2].start_date == d("12/04")  # 金
    assert result.activities[3].times.early_start == 4
    assert result.activities[3].start_date == d("12/07")  # 次の稼働日（月）


def test_milestone_with_requested_date() -> None:
    """RULE-SCH-002：マイルストーンの希望日は index(希望日)＋1（その日の終業）。先行が早く終われば希望日のまま。"""
    result = schedule([act(1, 2), act(2, 0, "12/09")], [(1, 2)])
    m = result.activities[2]
    assert m.times.early_start == 7
    assert m.start_date == d("12/09")


def test_milestone_to_milestone_is_same_day() -> None:
    """RULE-SCH-002：マイルストーンの後続のマイルストーンは同じ日（EF(p) = M(p)）。"""
    result = schedule([act(1, 3), act(2, 0), act(3, 0)], [(1, 2), (2, 3)])
    assert result.activities[2].start_date == result.activities[3].start_date == d("12/03")


def test_forward_pass_is_deterministic() -> None:
    """RULE-SCH-002：同じ入力から同じ結果（入力の並びに依らない。SCH-06）。"""
    activities = [act(1, 2), act(2, 3), act(3, 1, "12/08"), act(4, 0), act(5, 2)]
    deps = [(1, 3), (2, 3), (3, 4), (2, 5)]
    a = schedule(activities, deps)
    b = schedule(list(reversed(activities)), list(reversed(deps)))
    assert a == b
    assert forward_pass(activities, deps, calendar=CAL, project_start=START).order == (1, 2, 3, 4, 5)


def test_schedule_activity_rejects_inconsistent_milestone() -> None:
    """RULE-SCH-002：期間 0 がマイルストーン、マイルストーンの期間は 0、期間は 0 以上（BR-04・4.2）。"""
    with pytest.raises(SchedulingError):
        ScheduleActivity(id=1, duration_days=0, is_milestone=False)
    with pytest.raises(SchedulingError):
        ScheduleActivity(id=1, duration_days=1, is_milestone=True)
    with pytest.raises(SchedulingError):
        ScheduleActivity(id=1, duration_days=-1)
    with pytest.raises(SchedulingError):
        schedule([act(1, 1), act(1, 2)], [])  # ID の重複


# ---------------------------------------------------------------------------
# RULE-SCH-003 後退計算とクリティカルパス
# ---------------------------------------------------------------------------


def test_backward_pass_float_and_critical_path() -> None:
    """RULE-SCH-003：E＝max(EF)、LF＝min(後続の LS)、LS＝LF−d、TF＝LS−ES、TF≦0 がクリティカル（5.5・BR-05）。

    A(3)→C(2)、B(1)→C、D(1) は単独。E＝5。
    """
    result = schedule([act(1, 3), act(2, 1), act(3, 2), act(4, 1)], [(1, 3), (2, 3)])
    times = {i: result.activities[i].times for i in (1, 2, 3, 4)}
    assert result.project_finish == 5
    assert result.project_finish_date == d("12/07")
    assert (times[3].late_start, times[3].late_finish, times[3].total_float) == (3, 5, 0)
    assert (times[1].late_start, times[1].late_finish, times[1].total_float) == (0, 3, 0)
    assert (times[2].late_start, times[2].late_finish, times[2].total_float) == (2, 3, 2)
    assert (times[4].late_start, times[4].late_finish, times[4].total_float) == (4, 5, 4)  # 後続なし：LF＝E
    assert [i for i in (1, 2, 3, 4) if times[i].is_critical] == [1, 3]
    assert result.critical_path == (1, 3)
    b = result.activities[2]
    assert (b.late_start, b.late_finish) == (d("12/03"), d("12/03"))  # 添字 LS、添字 LF−1


def test_critical_boundary_float_zero_vs_one() -> None:
    """RULE-SCH-003：フロート 0 はクリティカル、フロート 1 はクリティカルでない（境界値）。"""
    result = schedule([act(1, 2), act(2, 1), act(3, 1)], [(1, 3), (2, 3)])
    assert result.activities[1].total_float == 0 and result.activities[1].is_critical
    assert result.activities[2].total_float == 1 and not result.activities[2].is_critical


def test_milestone_late_dates_and_float() -> None:
    """RULE-SCH-003：マイルストーンは LM＝min(後続の LS)（後続が無ければ E）。日付は添字 LM−1。"""
    result = schedule([act(1, 3), act(2, 0), act(3, 5)], [(1, 2)])
    m = result.activities[2]
    assert result.project_finish == 5
    assert (m.times.late_start, m.times.late_finish, m.total_float) == (5, 5, 2)
    assert m.late_start == m.late_finish == d("12/07")
    assert m.early_start == m.early_finish == d("12/03")
    assert not m.is_critical


def test_final_milestone_on_requested_date_defines_project_finish() -> None:
    """RULE-SCH-003：E はマイルストーンの M も含む。希望日の遅い最終マイルストーンの前の活動にはフロートが出る。"""
    result = schedule([act(1, 3), act(2, 0, "12/10")], [(1, 2)])
    assert result.project_finish == 8
    assert result.project_finish_date == d("12/10")
    assert result.activities[2].total_float == 0 and result.activities[2].is_critical
    assert result.activities[1].total_float == 5
    assert result.activities[1].late_finish == d("12/10")


def test_milestone_in_middle_of_critical_chain() -> None:
    """RULE-SCH-003：最長経路の途中のマイルストーンはクリティカル。"""
    result = schedule([act(1, 2), act(2, 0), act(3, 3), act(4, 1)], [(1, 2), (2, 3)])
    assert result.critical_path == (1, 2, 3)
    assert result.activities[2].total_float == 0


def test_empty_project_schedule() -> None:
    """RULE-SCH-003：活動が無ければ E は無い。"""
    result = schedule([], [])
    assert result.project_finish is None and result.project_finish_date is None
    assert result.critical_path == () and dict(result.activities) == {}
    forward = forward_pass([], [], calendar=CAL, project_start=START)
    assert backward_pass(forward).project_finish is None


def test_schedule_performance_nfr01() -> None:
    """RULE-SCH-003：活動 2,000・依存 5,000 の前進・後退計算と日付への変換が 1 秒以内（NFR-01）。"""
    n = 2000
    activities = [act(i, 0 if i % 50 == 0 else 1 + i % 4, "12/08" if i % 97 == 0 else None) for i in range(1, n + 1)]
    deps: list[tuple[int, int]] = []
    for step in (1, 3, 7):
        deps.extend((i, i + step) for i in range(1, n + 1 - step))
    deps = deps[:5000]
    assert len(deps) == 5000
    begin = time.perf_counter()
    result = schedule(activities, deps)
    elapsed = time.perf_counter() - begin
    assert len(result.activities) == n
    assert elapsed < 1.0, f"{elapsed:.3f} 秒"


# ---------------------------------------------------------------------------
# RULE-SCH-006 一括更新の検査
# ---------------------------------------------------------------------------


def editable(activity_id: int, duration: int = 2, preds: tuple[int, ...] = (), assignees: tuple[int, ...] = ()):
    return EditableActivity(
        id=activity_id,
        name=f"活動{activity_id}",
        requested_start=None,
        duration_days=duration,
        predecessor_ids=frozenset(preds),
        assignee_user_ids=frozenset(assignees),
    )


PROJECT = [editable(1), editable(2, preds=(1,)), editable(3, preds=(2,), assignees=(100,)), editable(4)]
MEMBERS = {100, 101}


def test_bulk_update_applies_all_valid_changes() -> None:
    """RULE-SCH-006：日付・期間・先行・担当・名称の変更を全て検査してから、適用後の状態を返す（SCH-02）。"""
    plan = check_bulk_update(
        PROJECT,
        [
            ActivityChange(1, requested_start=d("12/08"), duration_days=5, name="設計"),
            ActivityChange(3, predecessor_ids=[1, 4], assignee_user_ids=[101]),
            ActivityChange(4, duration_days=0),
        ],
        assignable_user_ids=MEMBERS,
    )
    first, third, fourth = plan.activities
    assert (first.id, first.requested_start, first.duration_days, first.name) == (1, d("12/08"), 5, "設計")
    assert third.predecessor_ids == frozenset({1, 4}) and third.assignee_user_ids == frozenset({101})
    assert fourth.is_milestone  # 期間 0 がマイルストーン（BR-04）
    assert plan.dependencies == ((1, 2), (1, 3), (4, 3))
    assert plan.added_dependencies == ((1, 3), (4, 3))
    assert plan.removed_dependencies == ((2, 3),)


def test_bulk_update_unset_keeps_values_and_none_clears_requested_start() -> None:
    """RULE-SCH-006：UNSET の項目は変えない。希望開始日の None は空にする。"""
    project = [EditableActivity(1, "A", d("12/09"), 3, frozenset(), frozenset({100}))]
    plan = check_bulk_update(project, [ActivityChange(1, requested_start=None)], assignable_user_ids=MEMBERS)
    (only,) = plan.activities
    assert only.requested_start is None
    assert (only.name, only.duration_days, only.assignee_user_ids) == ("A", 3, frozenset({100}))
    assert ActivityChange(1).name is UNSET and not UNSET
    assert check_bulk_update(project, [], assignable_user_ids=MEMBERS).activities == ()


def test_bulk_update_rejects_whole_batch_and_reports_every_issue() -> None:
    """RULE-SCH-006：1 件でも不正なら全体を拒む。最初の 1 件で止めず、全ての不正を返す（SCH-02）。"""
    changes = [
        ActivityChange(1, name="正しい変更"),  # これだけなら通る
        ActivityChange(99, duration_days=1),
        ActivityChange(2, duration_days=-1),
        ActivityChange(3, assignee_user_ids=[100, 555]),
        ActivityChange(4, name="  "),
        ActivityChange(2, name="重複"),
    ]
    with pytest.raises(BulkUpdateError) as info:
        check_bulk_update(PROJECT, changes, assignable_user_ids=MEMBERS)
    found = [(i.code, i.index, i.activity_id, i.field) for i in info.value.issues]
    assert found == [
        (BulkIssueCode.ACTIVITY_NOT_FOUND, 1, 99, "activity_id"),
        (BulkIssueCode.INVALID_DURATION, 2, 2, "duration_days"),
        (BulkIssueCode.ASSIGNEE_NOT_ASSIGNABLE, 3, 3, "assignee_user_ids"),
        (BulkIssueCode.INVALID_NAME, 4, 4, "name"),
        (BulkIssueCode.DUPLICATE_ACTIVITY, 5, 2, "activity_id"),
    ]


def test_bulk_update_predecessor_checks() -> None:
    """RULE-SCH-006：先行の存在・自分・重複、担当の重複を検査する。"""
    with pytest.raises(BulkUpdateError) as info:
        check_bulk_update(
            PROJECT,
            [ActivityChange(2, predecessor_ids=[2, 1, 1, 77]), ActivityChange(4, assignee_user_ids=[100, 100])],
            assignable_user_ids=MEMBERS,
        )
    assert [i.code for i in info.value.issues] == [
        BulkIssueCode.SELF_DEPENDENCY,
        BulkIssueCode.DUPLICATE_PREDECESSOR,
        BulkIssueCode.PREDECESSOR_NOT_FOUND,
        BulkIssueCode.DUPLICATE_ASSIGNEE,
    ]


def test_bulk_update_rejects_cycle_made_by_combination_of_changes() -> None:
    """RULE-SCH-006：1 件ずつは正しくても、変更後の依存の全体が循環するなら全体を拒む（RULE-SCH-001 で判定）。"""
    with pytest.raises(BulkUpdateError) as info:
        check_bulk_update(
            PROJECT,
            [ActivityChange(4, predecessor_ids=[3]), ActivityChange(1, predecessor_ids=[4])],
            assignable_user_ids=MEMBERS,
        )
    (issue,) = info.value.issues
    assert issue.code == BulkIssueCode.SCHEDULE_CYCLE
    assert issue.related_ids == (1, 2, 3, 4)
    assert issue.index is None


def test_bulk_update_rejects_cycle_with_existing_dependencies() -> None:
    """RULE-SCH-006：今の依存（1→2→3）と変更（1 の先行を 3 に）で循環になれば拒む。"""
    with pytest.raises(BulkUpdateError) as info:
        check_bulk_update(PROJECT, [ActivityChange(1, predecessor_ids=[3])], assignable_user_ids=MEMBERS)
    assert [i.related_ids for i in info.value.issues] == [(1, 2, 3)]


def test_bulk_update_can_break_cycle_by_replacing_predecessors() -> None:
    """RULE-SCH-006：先行の置き換えで依存を外せる（外した依存は removed に出る）。"""
    plan = check_bulk_update(
        PROJECT,
        [ActivityChange(2, predecessor_ids=[]), ActivityChange(1, predecessor_ids=[3])],
        assignable_user_ids=MEMBERS,
    )
    assert plan.dependencies == ((2, 3), (3, 1))
    assert plan.removed_dependencies == ((1, 2),)
    assert plan.added_dependencies == ((3, 1),)
