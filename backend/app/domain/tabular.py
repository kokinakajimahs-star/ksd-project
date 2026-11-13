"""表形式の入出力（設計書 5.1 の RULE-INT-001、6.3。要求仕様書 INT-01、BR-02）。

- 列の定義（6.3）：target ごとの列（この順）を ``COLUMNS`` に持つ。1 行目が見出し、2 行目からがデータ。
  日付は ``YYYY-MM-DD``、工数は人時の小数（2 桁まで）、マイルストーンは ``0``/``1``、複数の値は ``;`` 区切り
- 変換：6.3 の 1 行（``WbsRow``・``ActivityRow``・``TimeEntryRow``）↔ 文字列のセルの並び（``export_grid``・取込の各関数）。
  CSV（UTF-8 の BOM 付き・カンマ区切り）は ``encode_csv``・``decode_csv``。表計算ソフトが式として読まないよう、CSV の出力では
  ``=``・``+``・``-``・``@``・タブ・復帰・``'`` で始まるセルの先頭へ ``'`` を 1 つ足し、取込では先頭の ``'`` の次の文字がそれらの
  ときに ``'`` を 1 つ外す（往復で一致する。6.3・CR-019）。xlsx のセルの値（数・日付・真偽）は ``cell_text`` で
  同じ文字列に直す（xlsx のファイルの読み書きは外部のライブラリが要るので、この層では行わない。1.3 の「標準ライブラリのみ」）
- 行ごとの検査：見出し、必須、形式（日付・整数・数・0/1・区分）、範囲（負の値・上限・小数 3 桁）、文字数、
  照合キーの重複、参照先（WBS ID・ログインID・活動ID）の有無。誤りは ``{行, 列, コード, メッセージ}``（``ImportIssue``）で全件を返す
- 全件の検査：行ごとの検査が全て通った後に、取り込んだ後の全体へ RULE-WBS-003（階層と区分）・RULE-SCH-001（依存の循環）・
  実績工数の入力検査（RULE-EFF-005）を当てる
- 全件の検査が通ったときだけ書く：誤りが 1 件でもあれば ``ImportPlan.changes`` は空（何も書かない）。書くのは呼び手（サービス層）
- 往復の一致：出力した直後のファイルを取り込むと、変更は 0 件（``changes`` が空・全ての行が ``unchanged``）

照合キー（6.3）：``wbs`` は WBS ID（旧 ID は RULE-WBS-002 の ``resolve_code`` で解決。無ければ追加）、
``activities`` は活動ID（空なら追加）、``time-entries`` はログインID＋活動ID＋作業日。
ファイルに無い要素・活動・実績は消さない（6.3 に削除の定めが無い。docs/decisions/ASSUMPTIONS.md A-34）。

純粋に保つ（1.3）。DB・HTTP・時刻・乱数に触れない。DB の行はサービス層が下のデータクラスへ直して渡す。
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
import math
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Generic, TypeVar

from app.domain.effort import TimeEntryChange, TimeEntryProblemCode, TimeEntryValue, check_time_entries
from app.domain.estimate import HOURS_QUANTUM, MAX_HOURS, MAX_HOURS_PER_DAY, ElementKind, WbsNode
from app.domain.scheduling import DependencyGraph
from app.domain.wbs_code import MAX_CODE_LENGTH, CodedElement, CodeHistoryEntry, resolve_code
from app.domain.wbs_rules import ActivityRef, HierarchyIssue, HierarchyIssueCode, check_hierarchy

# 6.3：CSV は UTF-8（BOM付き）・カンマ区切り・1行目が見出し
CSV_ENCODING: Final = "utf-8-sig"
CSV_DELIMITER: Final = ","
CSV_LINE_TERMINATOR: Final = "\r\n"
# 6.3（CR-019）：表計算ソフトが式として読む先頭の文字。CSV の出力ではこれで始まるセルの先頭へ CSV_FORMULA_ESCAPE を足す
CSV_FORMULA_TRIGGERS: Final = frozenset({"=", "+", "-", "@", "\t", "\r", "'"})
CSV_FORMULA_ESCAPE: Final = "'"
# 6.3：先行の活動ID・担当者のログインIDの区切り
LIST_SEPARATOR: Final = ";"
# 見出しの行の行番号（データは次の行から）
HEADER_ROW: Final = 1
# 列の長さ（4.1 の wbs_elements.name・activities.name は String(200)、users.login_id は String(100)）
MAX_NAME_LENGTH: Final = 200
MAX_LOGIN_ID_LENGTH: Final = 100
# 整数の列（活動ID・期間）の上限（DB の Integer）
MAX_INTEGER: Final = 2**31 - 1

_ZERO = Decimal(0)
_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")
_INTEGER_PATTERN = re.compile(r"[0-9]+")
_DECIMAL_PATTERN = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)")


class TabularError(ValueError):
    """呼び手の誤り（未定義の target・今の値の不整合など）。利用者のファイルの誤りは ``ImportIssue`` で返す。"""


class TabularTarget(StrEnum):
    """入出力の対象（6.2 の API-INT-001・002 の ``target``）。"""

    WBS = "wbs"
    ACTIVITIES = "activities"
    TIME_ENTRIES = "time-entries"


# 6.3 の列（この順）。見出しの文字は 6.3 の表の列の名前（Markdown の ` を外したもの。A-34）
WBS_COLUMNS: Final = (
    "WBS ID",
    "親のWBS ID",
    "区分",
    "名称",
    "責任者のログインID",
    "自分の見積（人時）",
    "成果物",
    "作業範囲",
    "完了条件",
    "前提条件",
)
ACTIVITY_COLUMNS: Final = (
    "活動ID",
    "WPのWBS ID",
    "名称",
    "希望開始日",
    "期間（稼働日）",
    "マイルストーン（0/1）",
    "見積（人時）",
    "先行の活動ID（; 区切り）",
    "担当者のログインID（; 区切り）",
)
TIME_ENTRY_COLUMNS: Final = ("ログインID", "活動ID", "作業日", "工数（人時）", "備考")
# WBS ID の列。xlsx の数のセルは表記を失う（1.10 → 1.1、1.0 → 1）ので、文字のセルだけを受ける（A-47）
CODE_COLUMNS: Final = frozenset({"WBS ID", "親のWBS ID", "WPのWBS ID"})

COLUMNS: Final[Mapping[TabularTarget, tuple[str, ...]]] = MappingProxyType(
    {
        TabularTarget.WBS: WBS_COLUMNS,
        TabularTarget.ACTIVITIES: ACTIVITY_COLUMNS,
        TabularTarget.TIME_ENTRIES: TIME_ENTRY_COLUMNS,
    }
)


class ImportIssueCode(StrEnum):
    """取込の誤りの種類（6.3 の ``{行, 列, コード, メッセージ}`` のコード）。"""

    FILE_UNREADABLE = "FILE_UNREADABLE"  # UTF-8 の CSV として読めない
    HEADER_MISMATCH = "HEADER_MISMATCH"  # 1 行目の見出しが 6.3 の列（この順）と違う
    TOO_MANY_COLUMNS = "TOO_MANY_COLUMNS"  # 見出しより右のセルに値がある
    REQUIRED = "REQUIRED"  # 必須の列が空
    INVALID_VALUE = "INVALID_VALUE"  # 形式の誤り（日付・整数・数・0/1・区分・区切りの値の重複）
    OUT_OF_RANGE = "OUT_OF_RANGE"  # 負の値・上限を超える値・小数 3 桁以降
    TOO_LONG = "TOO_LONG"  # 文字数が列の長さを超える
    DUPLICATE_KEY = "DUPLICATE_KEY"  # 照合キーがファイルの中で重複している
    NOT_FOUND = "NOT_FOUND"  # 参照先（WBS ID・ログインID・活動ID）が無い
    # 階層と区分の整合（RULE-WBS-003）
    PARENT_CYCLE = "PARENT_CYCLE"
    WORK_PACKAGE_HAS_CHILDREN = "WORK_PACKAGE_HAS_CHILDREN"
    ACTIVITY_NOT_ON_WORK_PACKAGE = "ACTIVITY_NOT_ON_WORK_PACKAGE"
    # 依存の循環（RULE-SCH-001）
    SCHEDULE_CYCLE = "SCHEDULE_CYCLE"
    # 希望開始日がプロジェクトの開始日より前（5.5・CR-018。開始日はサービス層が DB から読んで判定する）
    BEFORE_PROJECT_START = "BEFORE_PROJECT_START"
    # 実績工数の入力検査（RULE-EFF-005）
    NOT_ASSIGNED = "NOT_ASSIGNED"
    LOCKED_DATE = "LOCKED_DATE"
    DAY_TOTAL_EXCEEDED = "DAY_TOTAL_EXCEEDED"


@dataclass(frozen=True)
class ImportIssue:
    """取込の誤り 1 件。``row`` はファイルの行（見出しが 1、データは 2 から。xlsx の行番号と同じ）、
    ``column`` は列の見出し（行やファイルの全体の誤りは ``None``）。"""

    row: int
    column: str | None
    code: ImportIssueCode
    message: str

    def to_dict(self) -> dict[str, object]:
        """6.3 の ``{行, 列, コード, メッセージ}`` の形。"""
        return {"row": self.row, "column": self.column, "code": self.code.value, "message": self.message}


class TabularFileError(ValueError):
    """ファイルを表として読めない（文字コード・CSV の引用符の誤り）。``issues`` は取込の誤りと同じ形。"""

    def __init__(self, issues: Sequence[ImportIssue]) -> None:
        self.issues: tuple[ImportIssue, ...] = tuple(issues)
        super().__init__("; ".join(issue.message for issue in self.issues))


# ---------------------------------------------------------------------------
# 6.3 の 1 行
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WbsRow:
    """``wbs`` の 1 行。``parent_code`` が ``None`` は最上位、文字の列の空は ``None``。"""

    wbs_code: str
    parent_code: str | None
    kind: ElementKind
    name: str
    responsible_login_id: str | None = None
    own_estimate_hours: Decimal | None = None
    deliverable: str | None = None
    work_scope: str | None = None
    completion_criteria: str | None = None
    assumptions: str | None = None


@dataclass(frozen=True)
class ActivityRow:
    """``activities`` の 1 行。``activity_id`` が ``None`` は追加。先行・担当者は順を問わない（出力は昇順）。"""

    activity_id: int | None
    work_package_code: str
    name: str
    requested_start: date | None
    duration_days: int
    is_milestone: bool
    estimate_hours: Decimal | None = None
    predecessor_ids: tuple[int, ...] = ()
    assignee_login_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class TimeEntryRow:
    """``time-entries`` の 1 行。"""

    login_id: str
    activity_id: int
    work_date: date
    hours: Decimal
    note: str | None = None


# ---------------------------------------------------------------------------
# セルの値の変換
# ---------------------------------------------------------------------------


def cell_text(value: object) -> str:
    """セルの値を 6.3 の文字列に直す（CSV は文字列のまま、xlsx の読み出した値は型ごとに直す）。

    ``None`` は空、真偽は ``1``/``0``、整数の値の数は整数の表記、日付（時刻が 0 時ちょうどの日時を含む）は ``YYYY-MM-DD``。
    それ以外の型は呼び手の誤りとして ``TabularError``。
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TabularError(f"セルの数が有限でない: {value!r}")
        return str(int(value)) if value.is_integer() else repr(value)
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        if value.time() == datetime.min.time() and value.tzinfo is None:
            return value.date().isoformat()
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raise TabularError(f"セルの値の型を扱えない: {type(value).__name__}")


def _format_hours(value: Decimal | None) -> str:
    if value is None:
        return ""
    return format(Decimal(value).quantize(HOURS_QUANTUM), "f")


def _format_text(value: str | None) -> str:
    return "" if value is None else value


def _format_date(value: date | None) -> str:
    return "" if value is None else value.isoformat()


def _blank_to_none(value: str | None) -> str | None:
    """空・空白だけの文字は「無い」とみなす（取込の ``_Cells.text`` と同じ）。"""
    return None if value is None or value.strip() == "" else value


def format_row(row: WbsRow | ActivityRow | TimeEntryRow) -> tuple[str, ...]:
    """6.3 の 1 行を、列の順の文字列にする。"""
    if isinstance(row, WbsRow):
        return (
            row.wbs_code,
            _format_text(row.parent_code),
            ElementKind(row.kind).value,
            row.name,
            _format_text(row.responsible_login_id),
            _format_hours(row.own_estimate_hours),
            _format_text(row.deliverable),
            _format_text(row.work_scope),
            _format_text(row.completion_criteria),
            _format_text(row.assumptions),
        )
    if isinstance(row, ActivityRow):
        return (
            "" if row.activity_id is None else str(row.activity_id),
            row.work_package_code,
            row.name,
            _format_date(row.requested_start),
            str(row.duration_days),
            "1" if row.is_milestone else "0",
            _format_hours(row.estimate_hours),
            LIST_SEPARATOR.join(str(i) for i in sorted(set(row.predecessor_ids))),
            LIST_SEPARATOR.join(sorted(set(row.assignee_login_ids))),
        )
    if isinstance(row, TimeEntryRow):
        return (
            row.login_id,
            str(row.activity_id),
            row.work_date.isoformat(),
            _format_hours(row.hours),
            _format_text(row.note),
        )
    raise TabularError(f"6.3 の行でない: {type(row).__name__}")


_ROW_TYPES: Final[Mapping[TabularTarget, type]] = MappingProxyType(
    {TabularTarget.WBS: WbsRow, TabularTarget.ACTIVITIES: ActivityRow, TabularTarget.TIME_ENTRIES: TimeEntryRow}
)


# RULE-INT-001 表形式の入出力
def export_grid(target: TabularTarget | str, rows: Iterable[WbsRow | ActivityRow | TimeEntryRow]) -> list[list[str]]:
    """出力する表（1 行目が 6.3 の見出し、2 行目から渡した順の行）を作る。xlsx は同じ表を 1 枚目のシートに置く。"""
    resolved = _target(target)
    expected = _ROW_TYPES[resolved]
    grid: list[list[str]] = [list(COLUMNS[resolved])]
    for row in rows:
        if not isinstance(row, expected):
            raise TabularError(f"{resolved.value} の出力に {type(row).__name__} は置けない")
        grid.append(list(format_row(row)))
    return grid


def _escape_formula(cell: str) -> str:
    """式として読まれる文字で始まるセルの先頭へ ``'`` を 1 つ足す（6.3・CR-019）。"""
    return CSV_FORMULA_ESCAPE + cell if cell[:1] in CSV_FORMULA_TRIGGERS else cell


def _unescape_formula(cell: str) -> str:
    """先頭の ``'`` の次の文字が式として読まれる文字なら、``'`` を 1 つ外す（``_escape_formula`` の逆。6.3・CR-019）。"""
    return cell[1:] if cell[:1] == CSV_FORMULA_ESCAPE and cell[1:2] in CSV_FORMULA_TRIGGERS else cell


def encode_csv(grid: Iterable[Sequence[str]]) -> bytes:
    """表を CSV（UTF-8 の BOM 付き・カンマ区切り・改行は CRLF・必要なセルだけ引用符）のバイト列にする（6.3）。

    ``=``・``+``・``-``・``@``・タブ・復帰・``'`` で始まるセルは先頭へ ``'`` を 1 つ足す（式にしない。CR-019）。
    """
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, delimiter=CSV_DELIMITER, lineterminator=CSV_LINE_TERMINATOR, quoting=csv.QUOTE_MINIMAL)
    writer.writerows([_escape_formula(cell) for cell in row] for row in grid)
    return buffer.getvalue().encode(CSV_ENCODING)


def decode_csv(data: bytes) -> list[list[str]]:
    """CSV のバイト列を表にする。先頭の BOM は外す（BOM の無い UTF-8 も読む。A-34）。

    UTF-8 として読めない・引用符が閉じていないときは ``TabularFileError``（行は誤りのあった行）。
    先頭の ``'`` の次の文字が ``=``・``+``・``-``・``@``・タブ・復帰・``'`` のセルは ``'`` を 1 つ外す（``encode_csv`` の逆。CR-019）。
    """
    return list(iter_csv(data))


def iter_csv(data: bytes) -> Iterator[list[str]]:
    """``decode_csv`` と同じ表を 1 行ずつ返す（呼び手が行の数の上限で読むのを止められる。6.3・CR-019）。

    UTF-8 として読めなければ最初の行を返す前に、引用符が閉じていなければその行に来たときに ``TabularFileError``。
    """
    try:
        text = data.decode(CSV_ENCODING)
    except UnicodeDecodeError as exc:
        row = data[: exc.start].count(b"\n") + 1
        raise TabularFileError(
            [ImportIssue(row, None, ImportIssueCode.FILE_UNREADABLE, f"{row} 行目が UTF-8 として読めない（CSV は UTF-8 で保存する）")]
        ) from exc
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=CSV_DELIMITER, strict=True)
    count = 0
    try:
        for record in reader:
            count += 1
            yield [_unescape_formula(cell) for cell in record]
    except csv.Error as exc:
        row = count + 1
        raise TabularFileError(
            [ImportIssue(row, None, ImportIssueCode.FILE_UNREADABLE, f"{row} 行目を CSV として読めない: {exc}")]
        ) from exc


def _target(target: TabularTarget | str) -> TabularTarget:
    try:
        return TabularTarget(target)
    except ValueError:
        allowed = "・".join(t.value for t in TabularTarget)
        raise TabularError(f"target は {allowed} のいずれか: {target!r}") from None


# ---------------------------------------------------------------------------
# 行ごとの検査
# ---------------------------------------------------------------------------


class _Issues:
    """見つけた誤りを集める（行の順・列の順）。"""

    def __init__(self) -> None:
        self.items: list[ImportIssue] = []

    def add(self, row: int, column: str | None, code: ImportIssueCode, message: str) -> None:
        self.items.append(ImportIssue(row, column, code, message))


class _Cells:
    """データの 1 行のセルを、列の見出しで読み、形式の誤りを集める。誤りの列は ``None`` を返し ``failed`` を立てる。"""

    def __init__(self, line: int, columns: Sequence[str], cells: Sequence[str], issues: _Issues) -> None:
        self.line = line
        self._index = {name: i for i, name in enumerate(columns)}
        self._cells = cells
        self._issues = issues
        self.failed = False

    def fail(self, column: str | None, code: ImportIssueCode, message: str) -> None:
        self.failed = True
        self._issues.add(self.line, column, code, message)

    def raw(self, column: str) -> str:
        index = self._index[column]
        return self._cells[index] if index < len(self._cells) else ""

    def text(self, column: str, *, required: bool = False, max_length: int | None = None) -> str | None:
        """自由な文字（名称・辞書・備考）。値は前後の空白も含めてそのまま持つ（往復で一致させるため）。"""
        value = self.raw(column)
        if value.strip() == "":
            if required:
                self.fail(column, ImportIssueCode.REQUIRED, f"{column} は必須")
            return None
        if max_length is not None and len(value) > max_length:
            self.fail(column, ImportIssueCode.TOO_LONG, f"{column} は {max_length} 文字まで（{len(value)} 文字）")
            return None
        return value

    def token(self, column: str, *, required: bool = False, max_length: int | None = None) -> str | None:
        """識別子（WBS ID・ログインID）。前後の空白は外す。"""
        value = self.raw(column).strip()
        if value == "":
            if required:
                self.fail(column, ImportIssueCode.REQUIRED, f"{column} は必須")
            return None
        if max_length is not None and len(value) > max_length:
            self.fail(column, ImportIssueCode.TOO_LONG, f"{column} は {max_length} 文字まで（{len(value)} 文字）")
            return None
        return value

    def integer(self, column: str, *, required: bool = False, minimum: int = 0) -> int | None:
        value = self.raw(column).strip()
        if value == "":
            if required:
                self.fail(column, ImportIssueCode.REQUIRED, f"{column} は必須")
            return None
        return _parse_integer(value, column, minimum, self.fail)

    def hours(self, column: str, *, required: bool = False, maximum: Decimal = MAX_HOURS) -> Decimal | None:
        """工数（人時）：0 以上・``maximum`` 以下・小数 2 桁まで（2.4 の ``Numeric(10,2)``）。"""
        value = self.raw(column).strip()
        if value == "":
            if required:
                self.fail(column, ImportIssueCode.REQUIRED, f"{column} は必須")
            return None
        if not _DECIMAL_PATTERN.fullmatch(value):
            self.fail(column, ImportIssueCode.INVALID_VALUE, f"{column} は数（人時）で書く: {value!r}")
            return None
        hours = Decimal(value)  # 正規表現を通った表記は Decimal で読める
        if hours < 0:
            self.fail(column, ImportIssueCode.OUT_OF_RANGE, f"{column} に負の値は入れられない: {value}")
            return None
        if hours > maximum:
            self.fail(column, ImportIssueCode.OUT_OF_RANGE, f"{column} は {maximum} 以下: {value}")
            return None
        if hours != hours.quantize(HOURS_QUANTUM):
            self.fail(column, ImportIssueCode.OUT_OF_RANGE, f"{column} は小数 2 桁まで: {value}")
            return None
        return hours

    def day(self, column: str, *, required: bool = False) -> date | None:
        value = self.raw(column).strip()
        if value == "":
            if required:
                self.fail(column, ImportIssueCode.REQUIRED, f"{column} は必須")
            return None
        if not _DATE_PATTERN.fullmatch(value):
            self.fail(column, ImportIssueCode.INVALID_VALUE, f"{column} は YYYY-MM-DD で書く: {value!r}")
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            self.fail(column, ImportIssueCode.INVALID_VALUE, f"{column} が暦に無い日付: {value!r}")
            return None

    def flag(self, column: str) -> bool | None:
        value = self.raw(column).strip()
        if value == "":
            self.fail(column, ImportIssueCode.REQUIRED, f"{column} は必須")
            return None
        if value not in ("0", "1"):
            self.fail(column, ImportIssueCode.INVALID_VALUE, f"{column} は 0 か 1: {value!r}")
            return None
        return value == "1"

    def kind(self, column: str) -> ElementKind | None:
        value = self.raw(column).strip()
        if value == "":
            self.fail(column, ImportIssueCode.REQUIRED, f"{column} は必須")
            return None
        try:
            return ElementKind(value)
        except ValueError:
            allowed = "・".join(kind.value for kind in ElementKind)
            self.fail(column, ImportIssueCode.INVALID_VALUE, f"{column} は {allowed} のいずれか: {value!r}")
            return None

    def _items(self, column: str) -> list[str]:
        return [item.strip() for item in self.raw(column).split(LIST_SEPARATOR) if item.strip() != ""]

    def integer_list(self, column: str) -> tuple[int, ...] | None:
        """``;`` 区切りの整数（空の要素は無視）。重複は誤り。"""
        values: list[int] = []
        for item in self._items(column):
            parsed = _parse_integer(item, column, 1, self.fail)
            if parsed is None:
                return None
            if parsed in values:
                self.fail(column, ImportIssueCode.INVALID_VALUE, f"{column} に {parsed} が重複している")
                return None
            values.append(parsed)
        return tuple(sorted(values))

    def token_list(self, column: str, *, max_length: int) -> tuple[str, ...] | None:
        """``;`` 区切りの識別子（空の要素は無視）。重複は誤り。"""
        values: list[str] = []
        for item in self._items(column):
            if len(item) > max_length:
                self.fail(column, ImportIssueCode.TOO_LONG, f"{column} の {item!r} は {max_length} 文字まで")
                return None
            if item in values:
                self.fail(column, ImportIssueCode.INVALID_VALUE, f"{column} に {item!r} が重複している")
                return None
            values.append(item)
        return tuple(sorted(values))


def _parse_integer(
    value: str, column: str, minimum: int, fail: Callable[[str | None, ImportIssueCode, str], None]
) -> int | None:
    if not _INTEGER_PATTERN.fullmatch(value):
        fail(column, ImportIssueCode.INVALID_VALUE, f"{column} は {minimum} 以上の整数で書く: {value!r}")
        return None
    parsed = int(value)
    if parsed < minimum or parsed > MAX_INTEGER:
        fail(column, ImportIssueCode.OUT_OF_RANGE, f"{column} は {minimum} 以上 {MAX_INTEGER} 以下: {value}")
        return None
    return parsed


def _data_rows(
    target: TabularTarget, grid: Sequence[Sequence[object]], issues: _Issues
) -> list[tuple[int, list[str]]] | None:
    """見出しを照らし、データの行（行番号, 列の数にそろえたセル）を返す。見出しが違えば ``None``（行は読まない）。

    全てのセルが空の行は読み飛ばす（表計算ソフトが末尾に残す空の行）。見出しより右のセルに値があれば ``TOO_MANY_COLUMNS``。
    WBS ID の列（``CODE_COLUMNS``）が数のセルなら ``INVALID_VALUE`` でその行を読まない（BR-02 の照合を誤らせない）。
    """
    columns = COLUMNS[target]
    if not grid:
        issues.add(HEADER_ROW, None, ImportIssueCode.HEADER_MISMATCH, f"見出しの行が無い。1 行目に {list(columns)} を置く")
        return None
    header = [cell_text(cell).strip() for cell in grid[0]]
    while header and header[-1] == "":
        header.pop()
    if tuple(header) != columns:
        issues.add(
            HEADER_ROW,
            None,
            ImportIssueCode.HEADER_MISMATCH,
            f"1 行目の見出しが {target.value} の列（この順）と違う。期待 {list(columns)}、実際 {header}",
        )
        return None
    rows: list[tuple[int, list[str]]] = []
    for offset, raw in enumerate(grid[1:]):
        line = HEADER_ROW + 1 + offset
        cells = [cell_text(cell) for cell in raw]
        if all(cell.strip() == "" for cell in cells):
            continue
        extra = [cell for cell in cells[len(columns) :] if cell.strip() != ""]
        if extra:
            issues.add(line, None, ImportIssueCode.TOO_MANY_COLUMNS, f"見出しの {len(columns)} 列より右に値がある: {extra}")
            continue
        numeric = [(column, cell) for column, cell in zip(columns, raw, strict=False)
                   if column in CODE_COLUMNS and isinstance(cell, int | float | Decimal)]
        if numeric:
            # 数のセルの WBS ID は別の要素に当たりうる（1.10 が 1.1 になる）ので、照合せずにこの行を誤りにする
            for column, cell in numeric:
                issues.add(line, column, ImportIssueCode.INVALID_VALUE,
                           f"{column} が数のセル（{cell!r}）。表記が変わるので、文字の書式のセルで入れる（例 '1.10'）")
            continue
        rows.append((line, (cells + [""] * len(columns))[: len(columns)]))
    return rows


# ---------------------------------------------------------------------------
# 取込の計画
# ---------------------------------------------------------------------------


class ChangeAction(StrEnum):
    """取り込む行が書くもの。"""

    CREATE = "CREATE"
    UPDATE = "UPDATE"


@dataclass(frozen=True)
class WbsChange:
    """``wbs`` の取り込む 1 行。

    - ``row.wbs_code``：更新は要素の今の ID（旧 ID で書かれていても今の ID に直す）、追加はファイルの値（仮の ID。
      実際の ID はサービス層が RULE-WBS-002 の ``new_element_code`` で振る）
    - 親：既存の要素なら ``parent_id``（``row.parent_code`` は親の今の ID）、同じファイルで追加する行なら ``parent_line``
      （``row.parent_code`` はその行の仮の ID）。最上位はどちらも ``None``
    - ``changed``：更新で変わる列の見出し（追加は空）
    """

    line: int
    action: ChangeAction
    row: WbsRow
    element_id: int | None
    parent_id: int | None
    parent_line: int | None
    changed: tuple[str, ...]


@dataclass(frozen=True)
class ActivityChange:
    """``activities`` の取り込む 1 行。``row.work_package_code`` は WP の今の ID、``work_package_id`` はその要素の ID。"""

    line: int
    action: ChangeAction
    row: ActivityRow
    activity_id: int | None
    work_package_id: int
    changed: tuple[str, ...]


@dataclass(frozen=True)
class TimeEntryImportChange:
    """``time-entries`` の取り込む 1 行（ログインID＋活動ID＋作業日の値を置き換える）。"""

    line: int
    action: ChangeAction
    row: TimeEntryRow
    changed: tuple[str, ...]


C = TypeVar("C", WbsChange, ActivityChange, TimeEntryImportChange)


@dataclass(frozen=True)
class ImportPlan(Generic[C]):
    """取込の検査の結果。

    - ``issues``：誤りの全て（行の順。全件の検査の誤りはその後）
    - ``changes``：書く行（追加・更新）。**誤りが 1 件でもあれば空**（何も書かない）。``dry_run`` は書かずにこれを見せる
    - ``unchanged``：今の値と同じで書かない行の数（出力した直後のファイルなら全ての行）
    """

    target: TabularTarget
    issues: tuple[ImportIssue, ...]
    changes: tuple[C, ...]
    unchanged: int

    @property
    def ok(self) -> bool:
        return not self.issues


def _plan(target: TabularTarget, issues: _Issues, changes: Sequence[C], unchanged: int) -> ImportPlan[C]:
    """誤りがあれば、行の順（同じ行は列の順。列の無い誤りが先）に並べ、書く行を空にする。"""
    if issues.items:
        position = {name: i for i, name in enumerate(COLUMNS[target])}
        ordered = sorted(issues.items, key=lambda i: (i.row, -1 if i.column is None else position[i.column]))
        return ImportPlan(target=target, issues=tuple(ordered), changes=(), unchanged=0)
    return ImportPlan(target=target, issues=(), changes=tuple(changes), unchanged=unchanged)


def _changed(columns: Sequence[str], before: Sequence[object], after: Sequence[object]) -> tuple[str, ...]:
    return tuple(column for column, old, new in zip(columns, before, after, strict=True) if old != new)


# --- wbs -------------------------------------------------------------------


@dataclass(frozen=True)
class _WbsLine:
    line: int
    row: WbsRow
    element_id: int | None  # 既存の要素（追加は None）
    parent_id: int | None = None
    parent_line: int | None = None
    parent_code: str | None = None  # 親の今の ID（既存）か仮の ID（追加の行）


def _wbs_values(row: WbsRow) -> tuple[object, ...]:
    """更新の比べ方：WBS ID は照合キーなので比べない（親は今の ID で比べる）。文字の空は ``None`` にそろえる。"""
    return (
        _blank_to_none(row.parent_code),
        ElementKind(row.kind),
        row.name,
        _blank_to_none(row.responsible_login_id),
        row.own_estimate_hours,
        _blank_to_none(row.deliverable),
        _blank_to_none(row.work_scope),
        _blank_to_none(row.completion_criteria),
        _blank_to_none(row.assumptions),
    )


# WBS の表の取込の計画（RULE-INT-001 の一部）
def plan_wbs_import(
    grid: Sequence[Sequence[object]],
    *,
    elements: Sequence[CodedElement],
    current: Mapping[int, WbsRow],
    login_ids: Iterable[str],
    history: Iterable[CodeHistoryEntry] = (),
    activities: Iterable[ActivityRef] = (),
) -> ImportPlan[WbsChange]:
    """``wbs`` の表を検査し、取り込む行を決める（6.3・INT-01・BR-02）。

    - ``elements``：プロジェクトの今の WBS 要素（WBS ID の解決に使う）。``current`` は要素の ID ごとの今の値（6.3 の形）
    - ``login_ids``：責任者に置けるログインID。``activities``：今の活動の置き場（WP の区分を変える検査に使う）
    - WBS ID が今の ID か旧 ID（``history``）に解決できれば更新、できなければ追加（ファイルの WBS ID は仮の ID）
    - 親の WBS ID は、今の要素（旧 ID を含む）→ 同じファイルの追加の行の順に探す
    - 行ごとの検査が通れば、取り込んだ後の木に RULE-WBS-003 を当てる（循環・WP の子・活動のある WP の区分の変更）
    - 追加は親を先に並べる（同じ親の中はファイルの順）
    """
    if {e.id for e in elements} != set(current):
        raise TabularError("elements と current の要素の ID がそろっていない")
    target = TabularTarget.WBS
    issues = _Issues()
    rows = _data_rows(target, grid, issues)
    if rows is None:
        return _plan(target, issues, [], 0)
    known_logins = frozenset(login_ids)
    entries = tuple(history)
    by_id = {e.id: e for e in elements}

    parsed: list[_WbsLine] = []
    parents: list[tuple[int, str]] = []  # (行, 親の WBS ID)。他の列に誤りのある行も親の有無を見る
    claimed: dict[int, int] = {}  # 要素の ID → それを書いた行
    new_codes: dict[str, int] = {}  # 追加の行の仮の ID → 行
    for line, raw in rows:
        cells = _Cells(line, WBS_COLUMNS, raw, issues)
        code = cells.token("WBS ID", required=True, max_length=MAX_CODE_LENGTH)
        parent_code = cells.token("親のWBS ID", max_length=MAX_CODE_LENGTH)
        kind = cells.kind("区分")
        name = cells.text("名称", required=True, max_length=MAX_NAME_LENGTH)
        login = cells.token("責任者のログインID", max_length=MAX_LOGIN_ID_LENGTH)
        hours = cells.hours("自分の見積（人時）")
        deliverable = cells.text("成果物")
        work_scope = cells.text("作業範囲")
        completion = cells.text("完了条件")
        assumptions = cells.text("前提条件")
        if login is not None and login not in known_logins:
            cells.fail("責任者のログインID", ImportIssueCode.NOT_FOUND, f"ログインID {login!r} の利用者がいない")
        element_id: int | None = None
        if code is not None:
            found = resolve_code(code, elements, entries)
            if found is not None:
                element_id = found.element_id
                if element_id in claimed:
                    cells.fail("WBS ID", ImportIssueCode.DUPLICATE_KEY,
                               f"WBS ID {code!r}（今の ID {found.wbs_code}）は {claimed[element_id]} 行目と同じ要素")
                else:
                    claimed[element_id] = line
            elif code in new_codes:
                cells.fail("WBS ID", ImportIssueCode.DUPLICATE_KEY, f"WBS ID {code!r} は {new_codes[code]} 行目と重複している")
            else:
                new_codes[code] = line
        if parent_code is not None:
            parents.append((line, parent_code))
        if cells.failed or code is None or kind is None or name is None:
            continue
        stored_code = by_id[element_id].wbs_code if element_id is not None else code
        parsed.append(
            _WbsLine(
                line=line,
                row=WbsRow(stored_code, parent_code, kind, name, login, hours, deliverable, work_scope, completion,
                           assumptions),
                element_id=element_id,
            )
        )

    # 親：今の要素（旧 ID を含む）→ 同じファイルの追加の行（仮の ID）の順に探す
    parent_of: dict[int, tuple[int | None, int | None, str]] = {}  # 行 → (親の要素の ID, 親の行, 親の ID)
    for line, parent_code in parents:
        found = resolve_code(parent_code, elements, entries)
        if found is not None:
            parent_of[line] = (found.element_id, None, found.wbs_code)
        elif parent_code in new_codes:
            parent_of[line] = (None, new_codes[parent_code], parent_code)
        else:
            issues.add(line, "親のWBS ID", ImportIssueCode.NOT_FOUND, f"親の WBS ID {parent_code!r} の要素が無い")
    if issues.items:
        return _plan(target, issues, [], 0)
    resolved = [
        _WbsLine(item.line, item.row, item.element_id, *parent_of[item.line]) if item.line in parent_of else item
        for item in parsed
    ]

    _check_wbs_hierarchy(resolved, elements, current, tuple(activities), issues)
    if issues.items:
        return _plan(target, issues, [], 0)

    updates: list[WbsChange] = []
    creates: list[WbsChange] = []
    unchanged = 0
    for item in resolved:
        row = replace(item.row, parent_code=item.parent_code)
        if item.element_id is None:
            creates.append(WbsChange(item.line, ChangeAction.CREATE, row, None, item.parent_id, item.parent_line, ()))
            continue
        before = current[item.element_id]
        changed = _changed(WBS_COLUMNS[1:], _wbs_values(before), _wbs_values(row))
        if changed:
            updates.append(
                WbsChange(item.line, ChangeAction.UPDATE, row, item.element_id, item.parent_id, item.parent_line,
                          changed)
            )
        else:
            unchanged += 1
    return _plan(target, issues, [*updates, *_parents_first(creates)], unchanged)


def _parents_first(creates: Sequence[WbsChange]) -> list[WbsChange]:
    """追加の行を、親（同じファイルの追加の行）が先に来る順に並べる（循環は RULE-WBS-003 の検査で弾いてある）。"""
    by_line = {c.line: c for c in creates}
    ordered: list[WbsChange] = []
    placed: set[int] = set()

    def place(change: WbsChange) -> None:
        if change.line in placed:
            return
        if change.parent_line is not None and change.parent_line in by_line:
            place(by_line[change.parent_line])
        placed.add(change.line)
        ordered.append(change)

    for change in creates:
        place(change)
    return ordered


def _check_wbs_hierarchy(
    lines: Sequence[_WbsLine],
    elements: Sequence[CodedElement],
    current: Mapping[int, WbsRow],
    activities: Sequence[ActivityRef],
    issues: _Issues,
) -> None:
    """取り込んだ後の木に RULE-WBS-003 を当て、ファイルの行に関わる不整合を誤りにする。追加の行は仮の ID（−行番号）で置く。"""
    in_file = {item.element_id: item for item in lines if item.element_id is not None}
    line_of: dict[int, int] = {}
    nodes: list[WbsNode] = []
    for element in elements:
        item = in_file.get(element.id)
        if item is None:
            kind = ElementKind(current[element.id].kind)
            nodes.append(WbsNode(id=element.id, parent_id=element.parent_id, kind=kind))
            continue
        line_of[element.id] = item.line
        nodes.append(WbsNode(id=element.id, parent_id=_node_parent(item), kind=item.row.kind))
    for item in lines:
        if item.element_id is None:
            line_of[-item.line] = item.line
            nodes.append(WbsNode(id=-item.line, parent_id=_node_parent(item), kind=item.row.kind))
    for problem in check_hierarchy(nodes, activities):
        _add_hierarchy_issue(problem, line_of, issues)


def _node_parent(item: _WbsLine) -> int | None:
    if item.parent_id is not None:
        return item.parent_id
    if item.parent_line is not None:
        return -item.parent_line
    return None


_HIERARCHY_CODES: Final[Mapping[HierarchyIssueCode, ImportIssueCode]] = MappingProxyType(
    {
        HierarchyIssueCode.PARENT_CYCLE: ImportIssueCode.PARENT_CYCLE,
        HierarchyIssueCode.WORK_PACKAGE_HAS_CHILDREN: ImportIssueCode.WORK_PACKAGE_HAS_CHILDREN,
        HierarchyIssueCode.ACTIVITY_NOT_ON_WORK_PACKAGE: ImportIssueCode.ACTIVITY_NOT_ON_WORK_PACKAGE,
    }
)


def _add_hierarchy_issue(problem: HierarchyIssue, line_of: Mapping[int, int], issues: _Issues) -> None:
    involved = sorted(line_of[i] for i in problem.element_ids if i in line_of)
    if not involved:
        return  # ファイルの行に関わらない（今の木のまま）不整合は、この取込の誤りにしない
    code = _HIERARCHY_CODES.get(problem.code)
    if code is None:
        raise TabularError(f"取込の木の組み立ての誤り（{problem.code.value}）: {problem.message}")
    first = involved[0]
    # 区分を置いた行（WP 自身・活動のある要素）か、親を置いた行（子・循環）か
    owner = problem.element_ids[0] if problem.code is not HierarchyIssueCode.PARENT_CYCLE else None
    column = "区分" if owner is not None and line_of.get(owner) == first else "親のWBS ID"
    lines = "・".join(str(n) for n in involved)
    messages = {
        ImportIssueCode.PARENT_CYCLE: f"親子が循環する（{lines} 行目）",
        ImportIssueCode.WORK_PACKAGE_HAS_CHILDREN: f"ワークパッケージは子を持てない（{lines} 行目）",
        ImportIssueCode.ACTIVITY_NOT_ON_WORK_PACKAGE: "活動のある要素の区分は WORK_PACKAGE から変えられない",
    }
    issues.add(first, column, code, messages[code])


# --- activities ------------------------------------------------------------


def _activity_values(row: ActivityRow) -> tuple[object, ...]:
    """更新の比べ方：活動ID は照合キーなので比べない。先行・担当者は集合で比べる。"""
    return (
        row.work_package_code,
        row.name,
        row.requested_start,
        row.duration_days,
        bool(row.is_milestone),
        row.estimate_hours,
        frozenset(row.predecessor_ids),
        frozenset(row.assignee_login_ids),
    )


# 活動の表の取込の計画（RULE-INT-001 の一部）
def plan_activity_import(
    grid: Sequence[Sequence[object]],
    *,
    elements: Sequence[CodedElement],
    work_package_ids: Iterable[int],
    current: Mapping[int, ActivityRow],
    login_ids: Iterable[str],
    history: Iterable[CodeHistoryEntry] = (),
) -> ImportPlan[ActivityChange]:
    """``activities`` の表を検査し、取り込む行を決める（6.3・INT-01）。

    - ``current``：活動の ID ごとの今の値（6.3 の形。``work_package_code`` は WP の今の ID、先行は今の依存の全て）
    - ``work_package_ids``：区分が WORK_PACKAGE の要素の ID（活動は WP にだけ置ける。RULE-WBS-003）
    - 活動ID が空なら追加、今の活動に無い ID は ``NOT_FOUND``。WP の WBS ID は旧 ID も解決する（RULE-WBS-002）
    - 期間 0 とマイルストーン 1 は組でだけ置ける（4.2）。先行は今の活動の ID だけを書ける（追加する活動は ID が無い）
    - 行ごとの検査が通れば、取り込んだ後の依存に RULE-SCH-001（循環）を当てる（ファイルの行の先行は、その活動の先行の全て）
    """
    for activity_id, row in current.items():
        if row.activity_id != activity_id:
            raise TabularError(f"current の鍵 {activity_id} と行の活動ID {row.activity_id} が違う")
    target = TabularTarget.ACTIVITIES
    issues = _Issues()
    rows = _data_rows(target, grid, issues)
    if rows is None:
        return _plan(target, issues, [], 0)
    known_logins = frozenset(login_ids)
    wp_ids = frozenset(work_package_ids)
    entries = tuple(history)

    accepted: list[tuple[int, ActivityRow, int]] = []  # (行, 行の値, WP の要素の ID)
    claimed: dict[int, int] = {}
    for line, raw in rows:
        cells = _Cells(line, ACTIVITY_COLUMNS, raw, issues)
        activity_id = cells.integer("活動ID", minimum=1)
        wp_code = cells.token("WPのWBS ID", required=True, max_length=MAX_CODE_LENGTH)
        name = cells.text("名称", required=True, max_length=MAX_NAME_LENGTH)
        requested_start = cells.day("希望開始日")
        duration = cells.integer("期間（稼働日）", required=True, minimum=0)
        milestone = cells.flag("マイルストーン（0/1）")
        estimate = cells.hours("見積（人時）")
        predecessors = cells.integer_list("先行の活動ID（; 区切り）")
        assignees = cells.token_list("担当者のログインID（; 区切り）", max_length=MAX_LOGIN_ID_LENGTH)

        if activity_id is not None:
            if activity_id not in current:
                cells.fail("活動ID", ImportIssueCode.NOT_FOUND, f"活動ID {activity_id} の活動が無い（追加は活動ID を空にする）")
            elif activity_id in claimed:
                cells.fail("活動ID", ImportIssueCode.DUPLICATE_KEY,
                           f"活動ID {activity_id} は {claimed[activity_id]} 行目と重複している")
            else:
                claimed[activity_id] = line
        wp_id: int | None = None
        wp_current_code: str | None = None
        if wp_code is not None:
            found = resolve_code(wp_code, elements, entries)
            if found is None:
                cells.fail("WPのWBS ID", ImportIssueCode.NOT_FOUND, f"WBS ID {wp_code!r} の要素が無い")
            elif found.element_id not in wp_ids:
                cells.fail("WPのWBS ID", ImportIssueCode.ACTIVITY_NOT_ON_WORK_PACKAGE,
                           f"活動はワークパッケージにだけ置ける。{found.wbs_code} は WORK_PACKAGE でない")
            else:
                wp_id, wp_current_code = found.element_id, found.wbs_code
        if duration is not None and milestone is not None and (duration == 0) != milestone:
            cells.fail("マイルストーン（0/1）", ImportIssueCode.INVALID_VALUE,
                       f"マイルストーンは期間 0、それ以外は期間 1 以上（マイルストーン {int(milestone)}・期間 {duration}）")
        for predecessor in predecessors or ():
            if predecessor == activity_id:
                cells.fail("先行の活動ID（; 区切り）", ImportIssueCode.SCHEDULE_CYCLE, f"活動 {predecessor} は自分の先行にできない")
            elif predecessor not in current:
                cells.fail("先行の活動ID（; 区切り）", ImportIssueCode.NOT_FOUND, f"先行の活動ID {predecessor} の活動が無い")
        for login in assignees or ():
            if login not in known_logins:
                cells.fail("担当者のログインID（; 区切り）", ImportIssueCode.NOT_FOUND, f"ログインID {login!r} の利用者がいない")
        if cells.failed:
            continue
        assert wp_id is not None and wp_current_code is not None and name is not None
        assert duration is not None and milestone is not None and predecessors is not None and assignees is not None
        row = ActivityRow(activity_id, wp_current_code, name, requested_start, duration, milestone, estimate,
                          predecessors, assignees)
        accepted.append((line, row, wp_id))
    if issues.items:
        return _plan(target, issues, [], 0)

    _check_activity_cycles(accepted, current, issues)
    if issues.items:
        return _plan(target, issues, [], 0)

    changes: list[ActivityChange] = []
    unchanged = 0
    for line, row, wp_id in accepted:
        if row.activity_id is None:
            changes.append(ActivityChange(line, ChangeAction.CREATE, row, None, wp_id, ()))
            continue
        changed = _changed(ACTIVITY_COLUMNS[1:], _activity_values(current[row.activity_id]), _activity_values(row))
        if changed:
            changes.append(ActivityChange(line, ChangeAction.UPDATE, row, row.activity_id, wp_id, changed))
        else:
            unchanged += 1
    return _plan(target, issues, changes, unchanged)


def _check_activity_cycles(
    accepted: Sequence[tuple[int, ActivityRow, int]], current: Mapping[int, ActivityRow], issues: _Issues
) -> None:
    """取り込んだ後の依存に RULE-SCH-001 を当てる。追加の行は仮の ID（−行番号）で置く。"""
    predecessors: dict[int, tuple[int, ...]] = {i: tuple(r.predecessor_ids) for i, r in current.items()}
    line_of: dict[int, int] = {}
    for line, row, _ in accepted:
        node = row.activity_id if row.activity_id is not None else -line
        predecessors[node] = row.predecessor_ids
        line_of[node] = line
    deps = [(p, s) for s, preds in predecessors.items() for p in preds]
    graph = DependencyGraph(predecessors.keys(), deps)
    for cycle in graph.cycles():
        involved = sorted(line_of[i] for i in cycle if i in line_of)
        if not involved:
            continue  # ファイルの行に関わらない（今の依存のまま）循環は、この取込の誤りにしない
        ids = "・".join(str(i) for i in cycle)
        issues.add(involved[0], "先行の活動ID（; 区切り）", ImportIssueCode.SCHEDULE_CYCLE,
                   f"依存が循環する（活動 {ids}。{'・'.join(str(n) for n in involved)} 行目）")


# --- time-entries ----------------------------------------------------------


@dataclass(frozen=True)
class UserEffortContext:
    """実績工数の入力検査（RULE-EFF-005）を当てるのに要る、利用者 1 人の今の値。

    - ``user_id``：``existing`` の行の利用者
    - ``existing``：その利用者の今の実績工数（プロジェクトをまたいで渡す。1 日の合計の検査に使う）
    - ``assigned_activity_ids``：その利用者の割当の活動
    """

    user_id: int
    existing: tuple[TimeEntryValue, ...] = ()
    assigned_activity_ids: frozenset[int] = frozenset()


_EFFORT_CODES: Final[Mapping[TimeEntryProblemCode, ImportIssueCode]] = MappingProxyType(
    {
        TimeEntryProblemCode.NOT_ASSIGNED: ImportIssueCode.NOT_ASSIGNED,
        TimeEntryProblemCode.LOCKED_DATE: ImportIssueCode.LOCKED_DATE,
        TimeEntryProblemCode.DAY_TOTAL_EXCEEDED: ImportIssueCode.DAY_TOTAL_EXCEEDED,
        TimeEntryProblemCode.INVALID_HOURS: ImportIssueCode.OUT_OF_RANGE,
        TimeEntryProblemCode.NEGATIVE_HOURS: ImportIssueCode.OUT_OF_RANGE,
        TimeEntryProblemCode.DUPLICATE: ImportIssueCode.DUPLICATE_KEY,
    }
)


# 実績工数の表の取込の計画（RULE-INT-001 の一部）
def plan_time_entry_import(
    grid: Sequence[Sequence[object]],
    *,
    current: Iterable[TimeEntryRow],
    activity_ids: Iterable[int],
    users: Mapping[str, UserEffortContext],
    finalized_report_dates: Iterable[date] = (),
) -> ImportPlan[TimeEntryImportChange]:
    """``time-entries`` の表を検査し、取り込む行を決める（6.3・INT-01・EFF-02）。

    - ``current``：プロジェクトの今の実績工数（6.3 の形）。``activity_ids``：プロジェクトの活動
    - ``users``：ログインID ごとの利用者（無いログインID は ``NOT_FOUND``）。``finalized_report_dates``：確定した報告日
    - 照合キー（ログインID＋活動ID＋作業日）が今の行にあれば更新、無ければ追加。今の行が無いキーは 0 時間・備考なしとみなす
    - 行ごとの検査が通れば、利用者ごとに RULE-EFF-005（自分の割当・1 日 24 時間以下・確定した報告日以前の日）を当てる
    """
    target = TabularTarget.TIME_ENTRIES
    issues = _Issues()
    rows = _data_rows(target, grid, issues)
    if rows is None:
        return _plan(target, issues, [], 0)
    project_activities = frozenset(activity_ids)
    now: dict[tuple[str, int, date], TimeEntryRow] = {}
    for entry in current:
        key = (entry.login_id, entry.activity_id, entry.work_date)
        if key in now:
            raise TabularError(f"今の実績工数が重複している: {key}")
        now[key] = entry

    accepted: list[tuple[int, TimeEntryRow]] = []
    claimed: dict[tuple[str, int, date], int] = {}
    for line, raw in rows:
        cells = _Cells(line, TIME_ENTRY_COLUMNS, raw, issues)
        login = cells.token("ログインID", required=True, max_length=MAX_LOGIN_ID_LENGTH)
        activity_id = cells.integer("活動ID", required=True, minimum=1)
        work_date = cells.day("作業日", required=True)
        hours = cells.hours("工数（人時）", required=True, maximum=MAX_HOURS_PER_DAY)
        note = cells.text("備考")
        if login is not None and login not in users:
            cells.fail("ログインID", ImportIssueCode.NOT_FOUND, f"ログインID {login!r} の利用者がいない")
        if activity_id is not None and activity_id not in project_activities:
            cells.fail("活動ID", ImportIssueCode.NOT_FOUND, f"活動ID {activity_id} の活動がこのプロジェクトに無い")
        if login is not None and activity_id is not None and work_date is not None:
            key = (login, activity_id, work_date)
            if key in claimed:
                cells.fail(None, ImportIssueCode.DUPLICATE_KEY,
                           f"{login}・活動 {activity_id}・{work_date} は {claimed[key]} 行目と重複している")
            else:
                claimed[key] = line
        if cells.failed:
            continue
        assert login is not None and activity_id is not None and work_date is not None and hours is not None
        accepted.append((line, TimeEntryRow(login, activity_id, work_date, hours, note)))
    if issues.items:
        return _plan(target, issues, [], 0)

    changes: list[TimeEntryImportChange] = []
    unchanged = 0
    for line, row in accepted:
        before = now.get((row.login_id, row.activity_id, row.work_date))
        old = (before.hours, _blank_to_none(before.note)) if before is not None else (_ZERO, None)
        new = (row.hours, _blank_to_none(row.note))
        changed = _changed(("工数（人時）", "備考"), old, new)
        if not changed:
            unchanged += 1
            continue
        action = ChangeAction.UPDATE if before is not None else ChangeAction.CREATE
        changes.append(TimeEntryImportChange(line, action, row, changed if before is not None else ()))

    _check_effort_rules(changes, users, tuple(finalized_report_dates), issues)
    return _plan(target, issues, changes, unchanged)


def _check_effort_rules(
    changes: Sequence[TimeEntryImportChange],
    users: Mapping[str, UserEffortContext],
    finalized_report_dates: Sequence[date],
    issues: _Issues,
) -> None:
    """利用者ごとに、書く行へ RULE-EFF-005 を当てる。

    工数が今の値と同じ行（備考だけの変更）は RULE-EFF-005 の「変えない」マスなので割当・1 日の合計を当てないが、
    確定した報告日以前の日の備考は変えさせない（確定した日の行を書き換えないため。A-34・設計書 1.1 の RULE-EFF-005）。
    """
    locked_through = max(finalized_report_dates, default=None)
    by_login: dict[str, list[TimeEntryImportChange]] = {}
    for change in changes:
        by_login.setdefault(change.row.login_id, []).append(change)
    for login, items in by_login.items():
        context = users[login]
        line_of = {(c.row.activity_id, c.row.work_date): c.line for c in items}
        check = check_time_entries(
            (TimeEntryChange(c.row.activity_id, c.row.work_date, c.row.hours) for c in items),
            existing=(e for e in context.existing if e.user_id == context.user_id),
            assigned_activity_ids=context.assigned_activity_ids,
            finalized_report_dates=finalized_report_dates,
        )
        hours_changed = {(c.activity_id, c.work_date) for c in check.changes}
        flagged = {(p.activity_id, p.work_date) for p in check.problems if p.activity_id is not None}
        for c in items:
            cell = (c.row.activity_id, c.row.work_date)
            if locked_through is not None and c.row.work_date <= locked_through and cell not in hours_changed | flagged:
                issues.add(c.line, "備考", ImportIssueCode.LOCKED_DATE,
                           f"{login}: 確定した報告日 {locked_through} 以前の日 {c.row.work_date} の備考は変えられない")
        for problem in check.problems:
            code = _EFFORT_CODES[problem.code]
            if problem.activity_id is not None:
                line = line_of[(problem.activity_id, problem.work_date)]
                column: str | None = "活動ID" if code is ImportIssueCode.NOT_ASSIGNED else "作業日"
            else:  # 1 日の合計：その日の最初の行
                line = min(n for (_, d), n in line_of.items() if d == problem.work_date)
                column = "工数（人時）"
            issues.add(line, column, code, f"{login}: {problem.message}")
