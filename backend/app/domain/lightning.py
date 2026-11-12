"""進捗ポイント・特殊な描画ルール・遅れ／前倒しの判定・イナズマ線の組み立て
（設計書 5.1 の RULE-PRG-002〜005、5.4・5.6・7.3。要求仕様書 7.7 BR-07・7.8 BR-08・7.9 BR-09・7.13 BR-13、PRG-08〜13・18）。

報告日 r、報告日線 ``L = line(r)``（5.4・RULE-CAL-002）。進捗ポイント ``P(a)`` と判定（5.6 の表。上の行から当てる）::

    完了（進捗率 100）かつ 終了予定日 ≦ r                       P = L                 完了
    未着手（実績開始日なし・進捗率 0）かつ 開始予定日 ＞ r      P = L                 未着手（予定内）
    マイルストーン                                              P = M(m)（その日の終業）  ずれで判定
    上記以外の活動                                              P = S_a + d_a × 進捗率    ずれで判定

- ずれ ``δ = P − L``。``δ < −許容幅`` は遅れ、``δ > 許容幅`` は前倒し、それ以外は予定どおり。完了・未着手（予定内）は判定の
  上では予定どおりに数え、表示で区別する（``Judgement.counted_as``）
- 進捗率が未算定（``None``）の活動は判定を「未算定」とし、線の上では L に置く（線を途切れさせない）
- 計算の基準は最新計画（``activities.start_date``・``duration_days``。OQ-03）
- イナズマ線（RULE-PRG-005）：ガントの行の順（RULE-SCH-005）にポイントを並べた折れ線と報告日線。過去の線はスナップショットに
  記録した値だけから描き（BR-13）、複数の報告日の線を線の種類を変えて重ねる（PRG-18・7.3 の ⑧）

判定の名前は 4.1 の ``progress_snapshot_items.judgement`` の値（docs/decisions/ASSUMPTIONS.md A-22・OQ-23）と同じにする。
位置は稼働日の軸の上の位置（``Decimal``。4.1 の ``Numeric(12,4)`` に誤差なく入る）と、その暦日上の座標（7.3）を両方返す。
純粋に保つ（1.3）。DB・HTTP・時刻・乱数に触れない。報告日は引数で受ける。DB の行はサービス層が下のデータクラスへ直して渡す。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Final

from app.domain.calendar import WorkCalendar, WorkdayAxis
from app.domain.gantt import CalendarPoint, end_coordinate

HUNDRED: Final = Decimal(100)
# 許容幅の既定（BR-09。projects.settings.delay_tolerance_days の既定。4.2）
DEFAULT_TOLERANCE_DAYS: Final = Decimal("0.5")


class LightningError(ValueError):
    """進捗ポイント・イナズマ線の入力が不正（未定義の入力は推測せずに拒む）。"""


class Judgement(StrEnum):
    """活動ごとの判定（5.6・BR-08・BR-09 の 6 つ）。"""

    COMPLETED = "COMPLETED"  # 完了（BR-08 の 1。報告日線上）
    NOT_STARTED = "NOT_STARTED"  # 未着手（予定内）（BR-08 の 2。報告日線上）
    BEHIND = "BEHIND"  # 遅れ
    AHEAD = "AHEAD"  # 前倒し
    ON_SCHEDULE = "ON_SCHEDULE"  # 予定どおり
    NOT_COMPUTED = "NOT_COMPUTED"  # 未算定（線の上では報告日線に置く）

    @property
    def counted_as(self) -> Judgement:
        """判定の上で数える値（完了・未着手（予定内）は予定どおりに数える。BR-09）。"""
        if self in (Judgement.COMPLETED, Judgement.NOT_STARTED):
            return Judgement.ON_SCHEDULE
        return self

    @property
    def judged_by_deviation(self) -> bool:
        """ずれで判定した（遅れ・予定どおり・前倒しのどれか）か。"""
        return self in (Judgement.BEHIND, Judgement.ON_SCHEDULE, Judgement.AHEAD)


def _as_decimal(value: object, what: str) -> Decimal:
    if isinstance(value, bool):
        raise LightningError(f"{what}は数で渡す: {value!r}")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise LightningError(f"{what}は数で渡す: {value!r}") from exc
    if not result.is_finite():
        raise LightningError(f"{what}が有限の数ではない: {value!r}")
    return result


def _tolerance(value: object) -> Decimal:
    tolerance = _as_decimal(value, "許容幅")
    if tolerance < 0:
        raise LightningError(f"許容幅（稼働日）は 0 以上: {tolerance}")
    return tolerance


# ---------------------------------------------------------------------------
# 入力
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LightningActivity:
    """進捗ポイントの計算に要る活動の値（4.1 の ``activities`` の最新計画と、報告日の進捗）。

    ``start_date``・``finish_date``：日程計算（RULE-SCH-002）の開始予定日・終了予定日。``duration_days``：期間（稼働日）。
    マイルストーンは期間 0 で開始日＝終了日。``percent_complete``：RULE-PRG-001 の進捗率（0〜100。``None`` は未算定）。
    """

    activity_id: int
    start_date: date
    finish_date: date
    duration_days: int
    is_milestone: bool
    percent_complete: Decimal | None
    actual_start: date | None = None
    actual_finish: date | None = None

    def __post_init__(self) -> None:
        if self.start_date is None or self.finish_date is None:
            raise LightningError(f"活動 {self.activity_id}：開始予定日と終了予定日が要る（日程計算の後に渡す）")
        d = self.duration_days
        if isinstance(d, bool) or not isinstance(d, int) or d < 0:
            raise LightningError(f"活動 {self.activity_id}：期間（稼働日）は 0 以上の整数: {d!r}")
        if bool(self.is_milestone) != (d == 0):
            raise LightningError(f"活動 {self.activity_id}：期間 0 の活動がマイルストーンで、マイルストーンの期間は 0")
        if self.is_milestone and self.start_date != self.finish_date:
            raise LightningError(f"活動 {self.activity_id}：マイルストーンの日付は 1 つ（開始日＝終了日）")
        if self.finish_date < self.start_date:
            raise LightningError(f"活動 {self.activity_id}：終了予定日 {self.finish_date} が開始予定日 {self.start_date} より前")
        if self.percent_complete is not None:
            p = _as_decimal(self.percent_complete, "進捗率")
            if p < 0 or p > HUNDRED:
                raise LightningError(f"活動 {self.activity_id}：進捗率は 0〜100: {p}")
            object.__setattr__(self, "percent_complete", p)


# ---------------------------------------------------------------------------
# 出力
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProgressPoint:
    """1 つの活動の進捗ポイント・ずれ・判定（位置は稼働日の軸の上。``deviation = point − 報告日線``）。"""

    activity_id: int
    point: Decimal
    deviation: Decimal
    judgement: Judgement


@dataclass(frozen=True)
class LightningVertex:
    """イナズマ線の頂点（ガントの 1 行）。``coordinate`` は暦日上の座標、``display_date`` は表示の日付（5.4）。"""

    row_index: int
    activity_id: int | None
    point: Decimal
    deviation: Decimal
    judgement: Judgement
    coordinate: CalendarPoint
    display_date: date


@dataclass(frozen=True)
class LightningLine:
    """1 つの報告日のイナズマ線：報告日線（位置と暦日上の座標）と、行の順の頂点（7.3 の ⑥・⑦）。"""

    report_date: date
    report_line: int
    report_line_coordinate: CalendarPoint
    tolerance_days: Decimal
    vertices: tuple[LightningVertex, ...]


@dataclass(frozen=True)
class OverlaidLine:
    """重ねて描く 1 本の線（PRG-18）。``style_index`` は線の種類（報告日の古い順に 0, 1, …）。

    頂点は今のガントの行へ置き直したもの（今の行の順）。今のガントに無い活動（消した活動など）の頂点は外し、
    その活動の ID（消して紐付けの無い写しは ``None``）を ``missing_activity_ids`` に返す。
    """

    style_index: int
    line: LightningLine
    missing_activity_ids: tuple[int | None, ...]


# ---------------------------------------------------------------------------
# 進捗ポイントの計算
# ---------------------------------------------------------------------------


def _end_of_day(axis: WorkdayAxis, d: date) -> int:
    """日 d の終業の位置（非稼働日なら前の稼働日の終業。5.4 の ``line(d)`` と同じ式）。"""
    return axis.index(d) + (1 if axis.calendar.is_working_day(d) else 0)


# RULE-PRG-002 進捗ポイントの計算
def progress_point(axis: WorkdayAxis, activity: LightningActivity) -> Decimal:
    """式による進捗ポイント（5.6・BR-07。PRG-09・PRG-10）。

    - 活動：``S_a + d_a × 進捗率``（``S_a`` は開始予定日の軸の上の位置＝その日の始業、進捗率は 0〜1 に直す）。
      稼働日の軸で数えるので、期間中の休日は飛ばされる（PRG-10）
    - マイルストーン：``M(m)``（その日の終業の位置）
    - 未算定（進捗率 ``None``）の活動は式で置けないので ``LightningError``（線の上では報告日線に置く。RULE-PRG-004 が扱う）
    """
    if activity.is_milestone:
        return Decimal(_end_of_day(axis, activity.start_date))
    if activity.percent_complete is None:
        raise LightningError(f"活動 {activity.activity_id}：進捗率が未算定で、進捗ポイントを式で求められない")
    start = axis.index(activity.start_date)
    return Decimal(start) + Decimal(activity.duration_days) * activity.percent_complete / HUNDRED


# ---------------------------------------------------------------------------
# 特殊な描画ルール
# ---------------------------------------------------------------------------


# RULE-PRG-003 特殊な描画ルール
def special_placement(activity: LightningActivity, report_date: date) -> Judgement | None:
    """報告日線の上に置く活動なら、その判定を返す（BR-08。PRG-11）。式で置く活動は ``None``。

    1. 完了（進捗率 100）かつ 終了予定日 ≦ 報告日 → ``COMPLETED``
    2. 未着手（実績開始日なし・進捗率 0）かつ 開始予定日 ＞ 報告日 → ``NOT_STARTED``（未着手（予定内））

    報告日より後に終了予定で完了した活動は 1 に当たらず、式のとおり報告日線より右（前倒し）になる（BR-08 の 3）。
    未算定（``None``）の活動はどちらにも当たらない。
    """
    p = activity.percent_complete
    if p is None:
        return None
    if p == HUNDRED and activity.finish_date <= report_date:
        return Judgement.COMPLETED
    if p == 0 and activity.actual_start is None and activity.start_date > report_date:
        return Judgement.NOT_STARTED
    return None


# ---------------------------------------------------------------------------
# 遅れ・前倒しの判定
# ---------------------------------------------------------------------------


def classify_deviation(deviation: Decimal, tolerance_days: Decimal | int | str) -> Judgement:
    """ずれの 3 値の判定（BR-09）：``δ < −許容幅`` は遅れ、``δ > 許容幅`` は前倒し、それ以外は予定どおり。"""
    tolerance = _tolerance(tolerance_days)
    if deviation < -tolerance:
        return Judgement.BEHIND
    if deviation > tolerance:
        return Judgement.AHEAD
    return Judgement.ON_SCHEDULE


# RULE-PRG-004 遅れ・前倒しの判定
def judge_progress(
    axis: WorkdayAxis,
    activity: LightningActivity,
    report_date: date,
    tolerance_days: Decimal | int | str = DEFAULT_TOLERANCE_DAYS,
) -> ProgressPoint:
    """1 つの活動の進捗ポイント・ずれ・判定を求める（5.6 の表を上の行から当てる。BR-08・BR-09。PRG-13）。

    - 未算定 → 報告日線に置き、``NOT_COMPUTED``（ずれ 0）
    - 特殊な描画ルール（RULE-PRG-003）に当たる → 報告日線に置き、``COMPLETED`` か ``NOT_STARTED``（ずれ 0）
    - それ以外 → 進捗ポイント（RULE-PRG-002）と ``δ = P − L`` を、許容幅で遅れ・予定どおり・前倒しに分ける
    """
    tolerance = _tolerance(tolerance_days)
    line = Decimal(axis.report_line(report_date))
    if activity.percent_complete is None:
        return ProgressPoint(activity.activity_id, line, Decimal(0), Judgement.NOT_COMPUTED)
    special = special_placement(activity, report_date)
    if special is not None:
        return ProgressPoint(activity.activity_id, line, Decimal(0), special)
    point = progress_point(axis, activity)
    deviation = point - line
    return ProgressPoint(activity.activity_id, point, deviation, classify_deviation(deviation, tolerance))


# ---------------------------------------------------------------------------
# イナズマ線の組み立て
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RecordedPoint:
    """スナップショットに記録した 1 行（4.1 の ``progress_snapshot_items`` の行の順・ポイント・ずれ・判定）。

    写した活動が後から消えたときは ``activity_id`` が ``None``（A-22）。
    """

    activity_id: int | None
    row_order: int
    point: Decimal
    deviation: Decimal
    judgement: Judgement


def _vertex(
    axis: WorkdayAxis,
    row_index: int,
    activity_id: int | None,
    point: Decimal,
    deviation: Decimal,
    judgement: Judgement,
) -> LightningVertex:
    return LightningVertex(
        row_index=row_index,
        activity_id=activity_id,
        point=point,
        deviation=deviation,
        judgement=judgement,
        coordinate=end_coordinate(axis, point),
        display_date=axis.display_date(point),
    )


def _report_line_coordinate(axis: WorkdayAxis, line: int) -> CalendarPoint:
    """報告日線の暦日上の座標（報告日（非稼働日なら前の稼働日）の終業）。"""
    return end_coordinate(axis, line)


# RULE-PRG-005 イナズマ線の組み立て
def build_lightning_line(
    rows: Sequence[LightningActivity],
    *,
    calendar: WorkCalendar,
    project_start: date,
    report_date: date,
    tolerance_days: Decimal | int | str = DEFAULT_TOLERANCE_DAYS,
) -> LightningLine:
    """報告日のイナズマ線を組み立てる（7.3 の ⑥・⑦。PRG-08・PRG-12）。

    - ``rows``：ガントの行の順（RULE-SCH-005）に並べた活動の全て。頂点はこの順に並べ、``row_index`` は 0 から
    - 報告日線は ``line(r)``（RULE-CAL-002）。各行のポイント・判定は RULE-PRG-004 で求める
    - 軸の原点はプロジェクトの開始日。同じ活動が 2 行に現れる入力は拒む
    """
    tolerance = _tolerance(tolerance_days)
    axis = WorkdayAxis(calendar=calendar, origin=project_start)
    seen: set[int] = set()
    vertices: list[LightningVertex] = []
    for row_index, activity in enumerate(rows):
        if activity.activity_id in seen:
            raise LightningError(f"活動 {activity.activity_id} が 2 つの行にある")
        seen.add(activity.activity_id)
        judged = judge_progress(axis, activity, report_date, tolerance)
        vertices.append(_vertex(axis, row_index, judged.activity_id, judged.point, judged.deviation, judged.judgement))
    line = axis.report_line(report_date)
    return LightningLine(
        report_date=report_date,
        report_line=line,
        report_line_coordinate=_report_line_coordinate(axis, line),
        tolerance_days=tolerance,
        vertices=tuple(vertices),
    )


def restore_lightning_line(
    *,
    report_date: date,
    report_line: int,
    tolerance_days: Decimal | int | str,
    points: Iterable[RecordedPoint],
    calendar: WorkCalendar,
    project_start: date,
) -> LightningLine:
    """スナップショットに記録した値だけから過去の線を作る（BR-13。今の計画・進捗から計算し直さない）。

    頂点は記録した行の順（``row_order``）に並べる。暦日上の座標は記録した位置を渡されたカレンダーの軸で直す
    （位置そのものは記録の値のまま。docs/open-questions.md OQ-24）。
    """
    axis = WorkdayAxis(calendar=calendar, origin=project_start)
    recorded = sorted(points, key=lambda p: p.row_order)
    orders = [p.row_order for p in recorded]
    if len(set(orders)) != len(orders):
        raise LightningError("記録の行の順（row_order）が重複している")
    vertices = tuple(
        _vertex(axis, i, p.activity_id, _as_decimal(p.point, "ポイント"), _as_decimal(p.deviation, "ずれ"), Judgement(p.judgement))
        for i, p in enumerate(recorded)
    )
    return LightningLine(
        report_date=report_date,
        report_line=report_line,
        report_line_coordinate=_report_line_coordinate(axis, report_line),
        tolerance_days=_tolerance(tolerance_days),
        vertices=vertices,
    )


def overlay_lightning_lines(lines: Iterable[LightningLine], row_of: Mapping[int, int]) -> tuple[OverlaidLine, ...]:
    """複数の報告日の線を、今のガントの行へ置き直して重ねる（PRG-18・7.3 の ⑧）。

    - ``row_of``：今のガントの行（活動の ID → 行の番号。RULE-SCH-005 の順）
    - 線は報告日の古い順に並べ、線の種類 ``style_index`` を 0 から振る。同じ報告日の線が 2 本あれば拒む
    - 各線の頂点は今の行の順に並べ直し、``row_index`` を今の行の番号にする。今のガントに無い活動は外して返す
    """
    ordered = sorted(lines, key=lambda line: line.report_date)
    dates = [line.report_date for line in ordered]
    if len(set(dates)) != len(dates):
        raise LightningError("同じ報告日の線が 2 本ある")
    result: list[OverlaidLine] = []
    for style_index, line in enumerate(ordered):
        kept: list[LightningVertex] = []
        missing: list[int | None] = []
        for vertex in line.vertices:
            row = None if vertex.activity_id is None else row_of.get(vertex.activity_id)
            if row is None:
                missing.append(vertex.activity_id)
                continue
            kept.append(
                LightningVertex(
                    row_index=row,
                    activity_id=vertex.activity_id,
                    point=vertex.point,
                    deviation=vertex.deviation,
                    judgement=vertex.judgement,
                    coordinate=vertex.coordinate,
                    display_date=vertex.display_date,
                )
            )
        kept.sort(key=lambda v: v.row_index)
        result.append(
            OverlaidLine(
                style_index=style_index,
                line=LightningLine(
                    report_date=line.report_date,
                    report_line=line.report_line,
                    report_line_coordinate=line.report_line_coordinate,
                    tolerance_days=line.tolerance_days,
                    vertices=tuple(kept),
                ),
                missing_activity_ids=tuple(missing),
            )
        )
    return tuple(result)
