"""表形式の入出力（app.domain.tabular）の試験。

設計書 5.1 の RULE-INT-001・6.3（入出力ファイルの定義）、要求仕様書 INT-01 の受入基準（往復で一致・行ごとのエラー・1 件でもあれば書かない）、
BR-02（旧 ID の解決）。全体の検査で当てる RULE-WBS-003・RULE-SCH-001・RULE-EFF-005 は、取込の誤りとして返ることだけを見る。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.domain.effort import TimeEntryValue
from app.domain.estimate import ElementKind
from app.domain.tabular import (
    ACTIVITY_COLUMNS,
    COLUMNS,
    TIME_ENTRY_COLUMNS,
    WBS_COLUMNS,
    ActivityRow,
    ChangeAction,
    ImportIssue,
    ImportIssueCode,
    TabularError,
    TabularFileError,
    TabularTarget,
    TimeEntryRow,
    UserEffortContext,
    WbsRow,
    cell_text,
    decode_csv,
    encode_csv,
    export_grid,
    plan_activity_import,
    plan_time_entry_import,
    plan_wbs_import,
)
from app.domain.wbs_code import CodedElement, CodeHistoryEntry
from app.domain.wbs_rules import ActivityRef

D, M, WP = ElementKind.DELIVERABLE, ElementKind.MANAGEMENT, ElementKind.WORK_PACKAGE
T0 = datetime(2026, 11, 10, 1, 0, tzinfo=UTC)

# 今の WBS：1.0 設計（成果物）／1.1 基本設計（WP）／1.2 詳細設計（WP。旧 ID 1.3）／2.0 管理
ELEMENTS = (
    CodedElement(id=1, parent_id=None, wbs_code="1.0", sibling_seq=1, last_child_seq=3),
    CodedElement(id=2, parent_id=1, wbs_code="1.1", sibling_seq=1),
    CodedElement(id=3, parent_id=1, wbs_code="1.2", sibling_seq=3),
    CodedElement(id=4, parent_id=None, wbs_code="2.0", sibling_seq=2),
)
HISTORY = (CodeHistoryEntry(id=1, element_id=3, old_code="1.3", new_code="1.2", renumbered_at=T0),)
WBS_NOW = {
    1: WbsRow("1.0", None, D, "設計"),
    2: WbsRow("1.1", "1.0", WP, "基本設計", "pm1", Decimal("16.00"), "基本設計書", "画面と帳票", "レビュー承認", "要件確定済み"),
    3: WbsRow("1.2", "1.0", WP, "詳細設計", None, None, None, None, "レビュー承認", None),
    4: WbsRow("2.0", None, M, " 管理 "),
}
LOGINS = ("pm1", "u1", "u2")

# 今の活動：10→11→12（12 はマイルストーン）
ACTIVITIES_NOW = {
    10: ActivityRow(10, "1.1", "要件確認", date(2026, 11, 2), 3, False, Decimal("24.00"), (), ("u1",)),
    11: ActivityRow(11, "1.1", "設計書作成", None, 5, False, Decimal("40.00"), (10,), ("u2", "u1")),
    12: ActivityRow(12, "1.2", "設計完了", None, 0, True, None, (11,), ()),
}
WORK_PACKAGES = (2, 3)

# 今の実績工数
ENTRIES_NOW = (
    TimeEntryRow("u1", 10, date(2026, 11, 2), Decimal("8.00"), "初日"),
    TimeEntryRow("u2", 11, date(2026, 11, 3), Decimal("4.00")),
)
USERS = {
    "u1": UserEffortContext(
        user_id=1,
        existing=(
            TimeEntryValue(user_id=1, activity_id=10, work_date=date(2026, 11, 2), hours=Decimal("8.00")),
            TimeEntryValue(user_id=1, activity_id=99, work_date=date(2026, 11, 4), hours=Decimal("20.00")),  # 別のプロジェクト
        ),
        assigned_activity_ids=frozenset({10, 11}),
    ),
    "u2": UserEffortContext(
        user_id=2,
        existing=(TimeEntryValue(user_id=2, activity_id=11, work_date=date(2026, 11, 3), hours=Decimal("4.00")),),
        assigned_activity_ids=frozenset({11}),
    ),
    "pm1": UserEffortContext(user_id=3),
}


def wbs_plan(grid: Sequence[Sequence[object]], **kwargs: object):  # type: ignore[no-untyped-def]
    args: dict[str, object] = {"elements": ELEMENTS, "current": WBS_NOW, "login_ids": LOGINS, "history": HISTORY}
    args.update(kwargs)
    return plan_wbs_import(grid, **args)  # type: ignore[arg-type]


def activity_plan(grid: Sequence[Sequence[object]]):  # type: ignore[no-untyped-def]
    return plan_activity_import(grid, elements=ELEMENTS, work_package_ids=WORK_PACKAGES, current=ACTIVITIES_NOW,
                                login_ids=LOGINS, history=HISTORY)


def entry_plan(grid: Sequence[Sequence[object]], finalized: Sequence[date] = ()):  # type: ignore[no-untyped-def]
    return plan_time_entry_import(grid, current=ENTRIES_NOW, activity_ids=ACTIVITIES_NOW.keys(), users=USERS,
                                  finalized_report_dates=finalized)


def wbs_grid(*rows: Sequence[str]) -> list[list[str]]:
    return [list(WBS_COLUMNS), *[list(r) for r in rows]]


def activity_grid(*rows: Sequence[str]) -> list[list[str]]:
    return [list(ACTIVITY_COLUMNS), *[list(r) for r in rows]]


def entry_grid(*rows: Sequence[str]) -> list[list[str]]:
    return [list(TIME_ENTRY_COLUMNS), *[list(r) for r in rows]]


def codes(plan) -> list[tuple[int, str | None, ImportIssueCode]]:  # type: ignore[no-untyped-def]
    return [(i.row, i.column, i.code) for i in plan.issues]


def wbs_row(code: str = "1.1", parent: str = "1.0", kind: str = "WORK_PACKAGE", name: str = "基本設計",
            login: str = "pm1", hours: str = "16.00", *dictionary: str) -> list[str]:
    tail = list(dictionary) if dictionary else ["基本設計書", "画面と帳票", "レビュー承認", "要件確定済み"]
    return [code, parent, kind, name, login, hours, *tail]


# ---------------------------------------------------------------------------
# 列の定義と CSV
# ---------------------------------------------------------------------------


def test_columns_follow_design_6_3() -> None:
    """RULE-INT-001：3 つの target の列が 6.3 の表の順のとおり。"""
    assert [t.value for t in TabularTarget] == ["wbs", "activities", "time-entries"]
    assert COLUMNS[TabularTarget.WBS] == (
        "WBS ID", "親のWBS ID", "区分", "名称", "責任者のログインID", "自分の見積（人時）", "成果物", "作業範囲", "完了条件", "前提条件",
    )
    assert COLUMNS[TabularTarget.ACTIVITIES] == (
        "活動ID", "WPのWBS ID", "名称", "希望開始日", "期間（稼働日）", "マイルストーン（0/1）", "見積（人時）",
        "先行の活動ID（; 区切り）", "担当者のログインID（; 区切り）",
    )
    assert COLUMNS[TabularTarget.TIME_ENTRIES] == ("ログインID", "活動ID", "作業日", "工数（人時）", "備考")


def test_csv_is_utf8_with_bom_comma_separated_header_first() -> None:
    """RULE-INT-001：CSV は UTF-8（BOM付き）・カンマ区切り・1 行目が見出し、日付は YYYY-MM-DD（6.3）。"""
    data = encode_csv(export_grid("time-entries", ENTRIES_NOW))
    assert data.startswith(b"\xef\xbb\xbf")
    lines = data.decode("utf-8-sig").split("\r\n")
    assert lines[0] == ",".join(TIME_ENTRY_COLUMNS)
    assert lines[1] == "u1,10,2026-11-02,8.00,初日"
    assert lines[2] == "u2,11,2026-11-03,4.00,"


def test_csv_round_trip_keeps_commas_quotes_and_newlines() -> None:
    """RULE-INT-001：カンマ・引用符・改行・前後の空白を含むセルも、CSV の往復で同じ文字になる。"""
    grid = [list(WBS_COLUMNS), ["1.0", "", "DELIVERABLE", ' 名称,"引用"\r\n2 行目 ', "", "", "a\nb", "", "", ""]]
    assert decode_csv(encode_csv(grid)) == grid


def test_csv_escapes_cells_read_as_formulas() -> None:
    """RULE-INT-001：CSV の出力は =・+・-・@・タブ・復帰・' で始まるセルの先頭へ ' を 1 つ足す（表計算ソフトが式として読まない）。
    途中にあるだけ・空のセルには足さない。取り込むと元の文字に戻る（6.3・CR-019）。
    """
    row = ["=1+1", "+1", "-1", "@SUM(A1)", "\tタブ", "\r復帰", "'引用", "''", "'", "a=b", "", "名称"]
    data = encode_csv([row])
    assert data.decode("utf-8-sig") == "'=1+1,'+1,'-1,'@SUM(A1),'\tタブ,\"'\r復帰\",''引用,''','',a=b,,名称\r\n"
    assert decode_csv(data) == [row]


def test_csv_import_removes_one_quote_only_before_formula_characters() -> None:
    """RULE-INT-001：取込で外す ' は、先頭の ' の次の文字が =・+・-・@・タブ・復帰・' のときの 1 つだけ（6.3・CR-019）。
    手で書いたファイルの '=… は =… として読み、次の文字がそれら以外の ' と途中の ' はそのまま読む。
    """
    data = "名称\r\n'=1+1\r\n'abc\r\na'=b\r\n'''x\r\n'\r\n'-\r\n".encode()
    assert decode_csv(data) == [["名称"], ["=1+1"], ["'abc"], ["a'=b"], ["''x"], ["'"], ["-"]]


def test_wbs_names_like_formulas_round_trip_without_changes() -> None:
    """RULE-INT-001：式として読まれる文字で始まる名称・辞書の項目も、CSV の往復で変更は 0 件（6.3・CR-019）。"""
    current = {
        **WBS_NOW,
        2: WbsRow("1.1", "1.0", WP, '=HYPERLINK("x")', "pm1", Decimal("16.00"), "+成果物", "-範囲", "@完了", "'前提"),
        3: WbsRow("1.2", "1.0", WP, "'=ちょうど", None, None, None, None, "\t完了", None),
    }
    text = encode_csv(export_grid(TabularTarget.WBS, current.values())).decode("utf-8-sig")
    assert "'=HYPERLINK" in text and "''=ちょうど" in text
    plan = wbs_plan(decode_csv(text.encode("utf-8-sig")), current=current)
    assert plan.ok and plan.changes == () and plan.unchanged == len(current)


def test_decode_csv_reads_without_bom_and_rejects_non_utf8() -> None:
    """RULE-INT-001：BOM の無い UTF-8 は読み、UTF-8 でないバイト列は読めない行を示して拒む。"""
    assert decode_csv("ログインID,活動ID\r\nu1,10\r\n".encode()) == [["ログインID", "活動ID"], ["u1", "10"]]
    sjis = "ログインID\r\n".encode() + "利用者".encode("cp932")
    with pytest.raises(TabularFileError) as caught:
        decode_csv(sjis)
    assert [(i.row, i.code) for i in caught.value.issues] == [(2, ImportIssueCode.FILE_UNREADABLE)]


def test_cell_text_normalizes_spreadsheet_values() -> None:
    """RULE-INT-001：xlsx のセルの値（数・日付・真偽・空）を 6.3 の文字列に直す。扱えない型は呼び手の誤り。"""
    assert cell_text(None) == ""
    assert cell_text("1.0") == "1.0"
    assert cell_text(12) == "12"
    assert cell_text(8.0) == "8"
    assert cell_text(7.5) == "7.5"
    assert cell_text(Decimal("7.50")) == "7.50"
    assert cell_text(True) == "1" and cell_text(False) == "0"
    assert cell_text(date(2026, 11, 2)) == "2026-11-02"
    assert cell_text(datetime(2026, 11, 2)) == "2026-11-02"
    with pytest.raises(TabularError):
        cell_text(object())
    with pytest.raises(TabularError):
        cell_text(float("nan"))


def test_export_rejects_other_rows_and_unknown_target() -> None:
    """RULE-INT-001：target と行の型が合わない出力・未定義の target は推測せずに拒む。"""
    with pytest.raises(TabularError):
        export_grid("wbs", ENTRIES_NOW)
    with pytest.raises(TabularError):
        export_grid("users", ())


# ---------------------------------------------------------------------------
# 往復の一致（INT-01 の受入基準）
# ---------------------------------------------------------------------------


def test_wbs_export_then_import_has_no_changes() -> None:
    """RULE-INT-001：WBS を出力した直後のファイル（CSV）を取り込むと、変更は 0 件（往復の一致）。"""
    data = encode_csv(export_grid(TabularTarget.WBS, WBS_NOW.values()))
    plan = wbs_plan(decode_csv(data))
    assert plan.ok and plan.changes == () and plan.unchanged == len(WBS_NOW)


def test_activity_export_then_import_has_no_changes() -> None:
    """RULE-INT-001：活動を出力した直後のファイルを取り込むと、変更は 0 件（先行・担当者は昇順で出し、集合で比べる）。"""
    grid = export_grid(TabularTarget.ACTIVITIES, ACTIVITIES_NOW.values())
    assert grid[2][7:] == ["10", "u1;u2"]
    assert grid[3][4:6] == ["0", "1"]
    plan = activity_plan(decode_csv(encode_csv(grid)))
    assert plan.ok and plan.changes == () and plan.unchanged == len(ACTIVITIES_NOW)


def test_time_entry_export_then_import_has_no_changes() -> None:
    """RULE-INT-001：実績工数を出力した直後のファイルを取り込むと、変更は 0 件。"""
    plan = entry_plan(decode_csv(encode_csv(export_grid(TabularTarget.TIME_ENTRIES, ENTRIES_NOW))))
    assert plan.ok and plan.changes == () and plan.unchanged == len(ENTRIES_NOW)


def test_spreadsheet_typed_cells_round_trip() -> None:
    """RULE-INT-001：xlsx が数・日付の型で返したセルでも、同じ内容なら変更は 0 件。"""
    grid: list[list[object]] = [list(TIME_ENTRY_COLUMNS), ["u1", 10, datetime(2026, 11, 2), 8.0, "初日"],
                                ["u2", 11.0, date(2026, 11, 3), 4, None]]
    plan = entry_plan(grid)
    assert plan.ok and plan.changes == () and plan.unchanged == 2


# ---------------------------------------------------------------------------
# 見出し・全件の検査（1 件でもあれば書かない）
# ---------------------------------------------------------------------------


def test_numeric_cells_in_wbs_id_columns_are_rejected() -> None:
    """RULE-INT-001：WBS ID の列が数のセル（xlsx の General 書式）なら INVALID_VALUE。1.10 が 1.1 として別の要素に当たらない。"""
    row: list[object] = list(wbs_row(name="基本設計（改）"))
    row[0] = 1.1  # 利用者は 1.1 か 1.10 を打った。数の表記からはどちらか分からない
    plan = wbs_plan([list(WBS_COLUMNS), row])
    assert plan.changes == () and codes(plan) == [(2, "WBS ID", ImportIssueCode.INVALID_VALUE)]
    parent_row: list[object] = list(wbs_row(name="基本設計（改）"))
    parent_row[1] = 1  # 1.0 が 1 になる
    assert codes(wbs_plan([list(WBS_COLUMNS), parent_row])) == [(2, "親のWBS ID", ImportIssueCode.INVALID_VALUE)]
    activity: list[object] = list(ACTIVITY_COLUMNS)
    numeric_wp: list[object] = ["", 1.1, "設計レビュー", "", "1", "0", "", "", ""]
    assert codes(activity_plan([activity, numeric_wp])) == [(2, "WPのWBS ID", ImportIssueCode.INVALID_VALUE)]
    # 文字のセルの WBS ID はそのまま照合する
    assert wbs_plan(wbs_grid(wbs_row(name="基本設計（改）"))).ok


def test_header_mismatch_reads_no_rows() -> None:
    """RULE-INT-001：見出しが 6.3 の列（この順）と違う・見出しが無いときは、1 行目の誤りだけを返し行を読まない。"""
    swapped = [["活動ID", "ログインID", "作業日", "工数（人時）", "備考"], ["x", "y", "z", "w", ""]]
    plan = entry_plan(swapped)
    assert codes(plan) == [(1, None, ImportIssueCode.HEADER_MISMATCH)]
    assert entry_plan([]).issues[0].code is ImportIssueCode.HEADER_MISMATCH
    assert entry_plan([[*TIME_ENTRY_COLUMNS, "", None]]).ok  # 見出しの右の空のセルは許す


def test_any_error_writes_nothing() -> None:
    """RULE-INT-001：正しい変更の行があっても、誤りが 1 件あれば changes は空（全件の検査が通ったときだけ書く）。"""
    good = wbs_row(name="基本設計（改）")
    bad = ["2.1", "2.0", "COMPONENT", "", "", "", "", "", "", ""]
    alone = wbs_plan(wbs_grid(good))
    assert alone.ok and [c.action for c in alone.changes] == [ChangeAction.UPDATE]
    plan = wbs_plan(wbs_grid(good, bad))
    assert plan.changes == () and plan.unchanged == 0
    assert codes(plan) == [(3, "名称", ImportIssueCode.REQUIRED)]


def test_all_row_errors_are_reported_with_row_column_code_message() -> None:
    """RULE-INT-001：全行を先に検査し、誤りを {行, 列, コード, メッセージ} の配列で全て返す（6.3）。"""
    plan = entry_plan(entry_grid(
        ["u9", "10", "2026-11-05", "1", ""],
        ["u1", "77", "2026/11/05", "-1", ""],
        ["u1", "10", "2026-11-05", "", ""],
    ))
    assert codes(plan) == [
        (2, "ログインID", ImportIssueCode.NOT_FOUND),
        (3, "活動ID", ImportIssueCode.NOT_FOUND),
        (3, "作業日", ImportIssueCode.INVALID_VALUE),
        (3, "工数（人時）", ImportIssueCode.OUT_OF_RANGE),
        (4, "工数（人時）", ImportIssueCode.REQUIRED),
    ]
    first = plan.issues[0].to_dict()
    assert set(first) == {"row", "column", "code", "message"}
    assert first["row"] == 2 and first["code"] == "NOT_FOUND" and "u9" in str(first["message"])


def test_blank_rows_are_skipped_and_extra_cells_rejected() -> None:
    """RULE-INT-001：全てが空の行は読み飛ばし、短い行は空で埋め、見出しより右の値は誤り（行番号はファイルの行）。"""
    grid: list[list[object]] = [list(TIME_ENTRY_COLUMNS), ["", "", "", "", ""], ["u1", "11", "2026-11-05", "2"],
                                ["u1", "11", "2026-11-06", "2", "", "余り"], [None, None]]
    plan = entry_plan(grid)
    assert codes(plan) == [(4, None, ImportIssueCode.TOO_MANY_COLUMNS)]
    plan = entry_plan(grid[:3])
    assert plan.ok and [(c.line, c.action) for c in plan.changes] == [(3, ChangeAction.CREATE)]


# ---------------------------------------------------------------------------
# wbs
# ---------------------------------------------------------------------------


def test_wbs_old_code_resolves_to_current_element() -> None:
    """RULE-INT-001：WBS ID の照合は RULE-WBS-002 で旧 ID を解決する（BR-02）。旧 ID 1.3 の行は要素 3（今 1.2）の更新。"""
    plan = wbs_plan(wbs_grid(["1.3", "1.0", "WORK_PACKAGE", "詳細設計（改）", "", "", "", "", "レビュー承認", ""]))
    assert plan.ok
    (change,) = plan.changes
    assert (change.action, change.element_id, change.row.wbs_code, change.changed) == (
        ChangeAction.UPDATE, 3, "1.2", ("名称",)
    )


def test_wbs_unknown_code_is_added_parents_first() -> None:
    """RULE-INT-001：解決できない WBS ID は追加。同じファイルの追加の行を親にでき、追加は親を先に並べる。"""
    plan = wbs_plan(wbs_grid(
        ["3.1", "3.0", "WORK_PACKAGE", "移行作業", "u1", "8", "", "", "移行完了", ""],
        ["3.0", "", "DELIVERABLE", "移行", "", "", "", "", "", ""],
        ["1.4", "1.0", "WORK_PACKAGE", "設計レビュー", "", "", "", "", "", ""],
    ))
    assert plan.ok and plan.unchanged == 0
    assert [(c.line, c.action, c.element_id, c.parent_id, c.parent_line) for c in plan.changes] == [
        (3, ChangeAction.CREATE, None, None, None),
        (2, ChangeAction.CREATE, None, None, 3),
        (4, ChangeAction.CREATE, None, 1, None),
    ]
    assert plan.changes[1].row.own_estimate_hours == Decimal("8")


def test_wbs_update_reports_changed_columns_and_move() -> None:
    """RULE-INT-001：更新は変わる列だけを返す。親の WBS ID の変更（移動）は親の今の ID で比べる。"""
    plan = wbs_plan(wbs_grid(wbs_row(parent="2.0", login="u1", hours="16")))
    (change,) = plan.changes
    assert change.changed == ("親のWBS ID", "責任者のログインID")
    assert (change.parent_id, change.row.parent_code) == (4, "2.0")


def test_wbs_duplicate_and_missing_references() -> None:
    """RULE-INT-001：同じ要素を指す行（今の ID と旧 ID を含む）の重複、無い親・無いログインID は誤り。"""
    plan = wbs_plan(wbs_grid(
        ["1.2", "1.0", "WORK_PACKAGE", "詳細設計", "", "", "", "", "", ""],
        ["1.3", "1.0", "WORK_PACKAGE", "詳細設計", "", "", "", "", "", ""],
        ["5.0", "", "DELIVERABLE", "新規", "", "", "", "", "", ""],
        ["5.0", "", "DELIVERABLE", "新規", "", "", "", "", "", ""],
        ["6.1", "6.0", "WORK_PACKAGE", "親なし", "nobody", "", "", "", "", ""],
    ))
    assert codes(plan) == [
        (3, "WBS ID", ImportIssueCode.DUPLICATE_KEY),
        (5, "WBS ID", ImportIssueCode.DUPLICATE_KEY),
        (6, "親のWBS ID", ImportIssueCode.NOT_FOUND),
        (6, "責任者のログインID", ImportIssueCode.NOT_FOUND),
    ]


@pytest.mark.parametrize(
    ("column", "value", "code"),
    [
        ("名称", "あ" * 200, None),
        ("名称", "あ" * 201, ImportIssueCode.TOO_LONG),
        ("名称", "   ", ImportIssueCode.REQUIRED),
        ("自分の見積（人時）", "0", None),
        ("自分の見積（人時）", "99999999.99", None),
        ("自分の見積（人時）", "100000000", ImportIssueCode.OUT_OF_RANGE),
        ("自分の見積（人時）", "-0.01", ImportIssueCode.OUT_OF_RANGE),
        ("自分の見積（人時）", "1.005", ImportIssueCode.OUT_OF_RANGE),
        ("自分の見積（人時）", "1e2", ImportIssueCode.INVALID_VALUE),
        ("区分", "WP", ImportIssueCode.INVALID_VALUE),
        ("区分", "", ImportIssueCode.REQUIRED),
        ("WBS ID", "", ImportIssueCode.REQUIRED),
        ("WBS ID", "9." + "1" * 99, ImportIssueCode.TOO_LONG),
    ],
)
def test_wbs_value_boundaries(column: str, value: str, code: ImportIssueCode | None) -> None:
    """RULE-INT-001：行ごとの検査の境界（名称 200 文字、見積は 0〜99999999.99・小数 2 桁、区分は 4 値、WBS ID は 100 文字）。"""
    row = wbs_row()
    row[WBS_COLUMNS.index(column)] = value
    plan = wbs_plan(wbs_grid(row))
    assert codes(plan) == ([] if code is None else [(2, column, code)])


def test_wbs_hierarchy_rules_after_import() -> None:
    """RULE-INT-001：取り込んだ後の木に RULE-WBS-003 を当てる（WP の子・親子の循環・活動のある WP の区分の変更）。"""
    under_wp = wbs_plan(wbs_grid(["1.1.1", "1.1", "WORK_PACKAGE", "WP の下", "", "", "", "", "", ""]))
    assert codes(under_wp) == [(2, "親のWBS ID", ImportIssueCode.WORK_PACKAGE_HAS_CHILDREN)]

    cycle = wbs_plan(wbs_grid(
        ["1.0", "2.0", "DELIVERABLE", "設計", "", "", "", "", "", ""],
        ["2.0", "1.0", "MANAGEMENT", " 管理 ", "", "", "", "", "", ""],
    ))
    assert codes(cycle) == [(2, "親のWBS ID", ImportIssueCode.PARENT_CYCLE)]

    to_component = wbs_row(kind="COMPONENT")
    plan = wbs_plan(wbs_grid(to_component), activities=(ActivityRef(id=10, work_package_id=2),))
    assert codes(plan) == [(2, "区分", ImportIssueCode.ACTIVITY_NOT_ON_WORK_PACKAGE)]
    assert wbs_plan(wbs_grid(to_component)).ok  # 活動が無ければ区分を変えられる


def test_wbs_inputs_must_agree() -> None:
    """RULE-INT-001：elements と current の要素がそろわない呼び出しは呼び手の誤り。"""
    with pytest.raises(TabularError):
        plan_wbs_import(wbs_grid(), elements=ELEMENTS, current={1: WBS_NOW[1]}, login_ids=LOGINS)


# ---------------------------------------------------------------------------
# activities
# ---------------------------------------------------------------------------


def test_activity_empty_id_is_added_and_unknown_id_rejected() -> None:
    """RULE-INT-001：活動ID が空なら追加、今の活動に無い ID は NOT_FOUND、同じ ID の 2 行は DUPLICATE_KEY。"""
    plan = activity_plan(activity_grid(["", "1.3", "結合試験準備", "2026-11-20", "2", "0", "", "12", "u2"]))
    assert plan.ok
    (change,) = plan.changes
    assert (change.action, change.activity_id, change.work_package_id, change.row.work_package_code) == (
        ChangeAction.CREATE, None, 3, "1.2"
    )
    assert change.row.predecessor_ids == (12,) and change.row.assignee_login_ids == ("u2",)

    plan = activity_plan(activity_grid(
        ["99", "1.1", "無い活動", "", "1", "0", "", "", ""],
        ["10", "1.1", "要件確認", "2026-11-02", "3", "0", "24", "", "u1"],
        ["10", "1.1", "要件確認", "2026-11-02", "3", "0", "24", "", "u1"],
    ))
    assert codes(plan) == [(2, "活動ID", ImportIssueCode.NOT_FOUND), (4, "活動ID", ImportIssueCode.DUPLICATE_KEY)]


def test_activity_update_compares_lists_as_sets() -> None:
    """RULE-INT-001：先行・担当者は順を問わずに比べ、変わる列だけを返す。"""
    same = ["11", "1.1", "設計書作成", "", "5", "0", "40", "10", "u2; u1;"]
    assert activity_plan(activity_grid(same)).unchanged == 1
    changed = ["11", "1.2", "設計書作成", "2026-11-05", "6", "0", "40.5", "", "u1"]
    (change,) = activity_plan(activity_grid(changed)).changes
    assert change.action is ChangeAction.UPDATE and change.work_package_id == 3
    assert change.changed == ("WPのWBS ID", "希望開始日", "期間（稼働日）", "見積（人時）", "先行の活動ID（; 区切り）",
                              "担当者のログインID（; 区切り）")


@pytest.mark.parametrize(
    ("duration", "milestone", "ok"),
    [("0", "1", True), ("1", "0", True), ("0", "0", False), ("1", "1", False)],
)
def test_activity_milestone_pairs_with_zero_duration(duration: str, milestone: str, ok: bool) -> None:
    """RULE-INT-001：マイルストーンは期間 0、それ以外は期間 1 以上（4.2）。"""
    plan = activity_plan(activity_grid(["", "1.1", "節目", "", duration, milestone, "", "", ""]))
    assert codes(plan) == ([] if ok else [(2, "マイルストーン（0/1）", ImportIssueCode.INVALID_VALUE)])


@pytest.mark.parametrize(
    ("column", "value", "code"),
    [
        ("希望開始日", "2026/11/02", ImportIssueCode.INVALID_VALUE),
        ("希望開始日", "2026-02-30", ImportIssueCode.INVALID_VALUE),
        ("希望開始日", "", None),
        ("期間（稼働日）", "-1", ImportIssueCode.INVALID_VALUE),
        ("期間（稼働日）", "1.5", ImportIssueCode.INVALID_VALUE),
        ("期間（稼働日）", "2147483648", ImportIssueCode.OUT_OF_RANGE),
        ("マイルストーン（0/1）", "2", ImportIssueCode.INVALID_VALUE),
        ("マイルストーン（0/1）", "", ImportIssueCode.REQUIRED),
        ("WPのWBS ID", "1.0", ImportIssueCode.ACTIVITY_NOT_ON_WORK_PACKAGE),
        ("WPのWBS ID", "9.9", ImportIssueCode.NOT_FOUND),
        ("先行の活動ID（; 区切り）", "77", ImportIssueCode.NOT_FOUND),
        ("先行の活動ID（; 区切り）", "10;10", ImportIssueCode.INVALID_VALUE),
        ("先行の活動ID（; 区切り）", "0", ImportIssueCode.OUT_OF_RANGE),
        ("担当者のログインID（; 区切り）", "u1;nobody", ImportIssueCode.NOT_FOUND),
    ],
)
def test_activity_value_checks(column: str, value: str, code: ImportIssueCode | None) -> None:
    """RULE-INT-001：活動の行ごとの検査（日付・整数・0/1・WP の WBS ID・先行・担当者）。"""
    row = ["", "1.1", "新しい活動", "2026-11-09", "1", "0", "8", "", "u1"]
    row[ACTIVITY_COLUMNS.index(column)] = value
    plan = activity_plan(activity_grid(row))
    assert codes(plan) == ([] if code is None else [(2, column, code)])


def test_activity_dependency_cycle_is_rejected() -> None:
    """RULE-INT-001：取り込んだ後の依存に RULE-SCH-001 を当てる（10→11→12 に 12→10 を足すと循環）。自分を先行にするのも循環。"""
    plan = activity_plan(activity_grid(["10", "1.1", "要件確認", "2026-11-02", "3", "0", "24", "12", "u1"]))
    assert codes(plan) == [(2, "先行の活動ID（; 区切り）", ImportIssueCode.SCHEDULE_CYCLE)]
    plan = activity_plan(activity_grid(["10", "1.1", "要件確認", "2026-11-02", "3", "0", "24", "10", "u1"]))
    assert codes(plan) == [(2, "先行の活動ID（; 区切り）", ImportIssueCode.SCHEDULE_CYCLE)]


# ---------------------------------------------------------------------------
# time-entries
# ---------------------------------------------------------------------------


def test_time_entry_key_create_update_and_zero() -> None:
    """RULE-INT-001：照合キー（ログインID＋活動ID＋作業日）が今の行にあれば更新、無ければ追加。行の無い 0 時間は変更なし。"""
    plan = entry_plan(entry_grid(
        ["u1", "10", "2026-11-02", "6", "初日"],
        ["u1", "11", "2026-11-05", "24", ""],
        ["u2", "11", "2026-11-06", "0", ""],
    ))
    assert plan.ok and plan.unchanged == 1
    assert [(c.line, c.action, c.changed) for c in plan.changes] == [
        (2, ChangeAction.UPDATE, ("工数（人時）",)),
        (3, ChangeAction.CREATE, ()),
    ]


def test_time_entry_value_and_key_checks() -> None:
    """RULE-INT-001：1 行の工数は 0〜24・小数 2 桁、同じキーの 2 行は DUPLICATE_KEY。"""
    plan = entry_plan(entry_grid(
        ["u1", "11", "2026-11-05", "24.01", ""],
        ["u1", "11", "2026-11-06", "1.234", ""],
        ["u1", "11", "2026-11-07", "1", ""],
        ["u1", "11", "2026-11-07", "2", ""],
    ))
    assert codes(plan) == [
        (2, "工数（人時）", ImportIssueCode.OUT_OF_RANGE),
        (3, "工数（人時）", ImportIssueCode.OUT_OF_RANGE),
        (5, None, ImportIssueCode.DUPLICATE_KEY),
    ]


def test_time_entry_effort_rules_are_applied() -> None:
    """RULE-INT-001：書く行に RULE-EFF-005 を当てる（自分の割当・1 日の合計 24 時間以下・確定した報告日以前の日）。"""
    not_assigned = entry_plan(entry_grid(["u2", "10", "2026-11-05", "1", ""]))
    assert codes(not_assigned) == [(2, "活動ID", ImportIssueCode.NOT_ASSIGNED)]

    # u1 は 11/4 に別のプロジェクトで 20 時間。4 時間はちょうど 24、4.01 は超える
    assert entry_plan(entry_grid(["u1", "11", "2026-11-04", "4", ""])).ok
    over = entry_plan(entry_grid(["u1", "11", "2026-11-04", "4.01", ""]))
    assert codes(over) == [(2, "工数（人時）", ImportIssueCode.DAY_TOTAL_EXCEEDED)]

    finalized = [date(2026, 11, 2)]  # 報告日の当日も締める（設計書 1.1・CR-005）
    locked = entry_plan(entry_grid(["u1", "10", "2026-11-02", "6", "初日"]), finalized)
    assert codes(locked) == [(2, "作業日", ImportIssueCode.LOCKED_DATE)]
    note_only = entry_plan(entry_grid(["u1", "10", "2026-11-02", "8", "初日（訂正）"]), finalized)
    assert codes(note_only) == [(2, "備考", ImportIssueCode.LOCKED_DATE)]
    open_day = entry_plan(entry_grid(["u2", "11", "2026-11-03", "4", "メモ"]), finalized)
    assert open_day.ok and [c.changed for c in open_day.changes] == [("備考",)]


def test_import_issue_is_plain_data() -> None:
    """RULE-INT-001：誤りは {行, 列, コード, メッセージ} の値（列の無い誤りは None）。"""
    issue = ImportIssue(1, None, ImportIssueCode.HEADER_MISMATCH, "見出しが違う")
    assert issue.to_dict() == {"row": 1, "column": None, "code": "HEADER_MISMATCH", "message": "見出しが違う"}
