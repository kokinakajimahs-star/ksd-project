"""状態遷移（設計書 5.1 の RULE-WBS-006、5.10。要求仕様書 WBS-20・21・22、WBS-13・16）。

WBS の状態（``projects.wbs_status``）::

    [*] → DRAFT
    DRAFT → IN_REVIEW              レビューを始める
    IN_REVIEW → DRAFT              レビューを閉じる
    DRAFT → PENDING_APPROVAL       承認を申請する（前提を満たすとき）
    IN_REVIEW → PENDING_APPROVAL   承認を申請する（前提を満たすとき）
    PENDING_APPROVAL → BASELINED   承認（スコープ基準の新しい版を作る）
    PENDING_APPROVAL → DRAFT       差戻し
    BASELINED → DRAFT              基準の後に WBS を変えた（版は残る）

- 申請の前提：完了条件の欠け 0 件（RULE-WBS-005）・未対応の指摘 0 件・100% ルールの ERROR 0 件（RULE-WBS-004）。満たさなければ ``WBS_NOT_READY``
- 承認・差戻しは申請者と別の人だけ（``SELF_APPROVAL_FORBIDDEN``）
- 承認で作る版の理由は、初めてなら ``INITIAL_APPROVAL``、既に版があれば ``RE_APPROVAL``（5.10）
- 承認申請（``approval_requests.status``）：``PENDING → APPROVED / REJECTED``。申請者と同じ人は判断できない

表に無い遷移は ``INVALID_STATE``。純粋に保つ（1.3）。DB・HTTP・時刻・乱数に触れない。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum


class WorkflowErrorCode(StrEnum):
    """状態遷移の拒否の理由（6.1 のエラーのコード）。"""

    INVALID_STATE = "INVALID_STATE"  # 409：今の状態ではできない操作
    WBS_NOT_READY = "WBS_NOT_READY"  # 422：申請の前提を満たしていない
    SELF_APPROVAL_FORBIDDEN = "SELF_APPROVAL_FORBIDDEN"  # 申請者が自分の申請を判断しようとした（5.10）


class WorkflowError(ValueError):
    """状態遷移の拒否。``code`` は拒否の理由、``details`` は満たしていない前提などの一覧。"""

    def __init__(self, code: WorkflowErrorCode, message: str, *, details: Sequence[object] = ()) -> None:
        super().__init__(message)
        self.code = code
        self.details = tuple(details)


class WbsStatus(StrEnum):
    """WBS の状態（``projects.wbs_status``。3 章・5.10）。"""

    DRAFT = "DRAFT"
    IN_REVIEW = "IN_REVIEW"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    BASELINED = "BASELINED"


class WbsAction(StrEnum):
    """WBS の状態を動かす操作（5.10 の遷移の名前）。"""

    START_REVIEW = "START_REVIEW"  # レビューを始める
    CLOSE_REVIEW = "CLOSE_REVIEW"  # レビューを閉じる
    SUBMIT = "SUBMIT"  # 承認を申請する
    APPROVE = "APPROVE"  # 承認
    SEND_BACK = "SEND_BACK"  # 差戻し
    EDIT = "EDIT"  # WBS（要素・辞書・紐付けなど）を変えた


class ScopeBaselineReason(StrEnum):
    """スコープベースラインの版の理由（4.1 の ``scope_baselines.reason``）。"""

    INITIAL_APPROVAL = "INITIAL_APPROVAL"
    RE_APPROVAL = "RE_APPROVAL"
    CHANGE_REQUEST = "CHANGE_REQUEST"


class ApprovalStatus(StrEnum):
    """承認申請の状態（4.1 の ``approval_requests.status``）。"""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ApprovalDecision(StrEnum):
    """承認申請への判断。"""

    APPROVE = "APPROVE"
    REJECT = "REJECT"


# 5.10 の WBS の遷移の表（今の状態, 操作）→ 次の状態。EDIT は別に扱う
_WBS_TRANSITIONS: dict[tuple[WbsStatus, WbsAction], WbsStatus] = {
    (WbsStatus.DRAFT, WbsAction.START_REVIEW): WbsStatus.IN_REVIEW,
    (WbsStatus.IN_REVIEW, WbsAction.CLOSE_REVIEW): WbsStatus.DRAFT,
    (WbsStatus.DRAFT, WbsAction.SUBMIT): WbsStatus.PENDING_APPROVAL,
    (WbsStatus.IN_REVIEW, WbsAction.SUBMIT): WbsStatus.PENDING_APPROVAL,
    (WbsStatus.PENDING_APPROVAL, WbsAction.APPROVE): WbsStatus.BASELINED,
    (WbsStatus.PENDING_APPROVAL, WbsAction.SEND_BACK): WbsStatus.DRAFT,
}

# WBS を変えたときの状態。基準化の後に変えると DRAFT に戻る（5.10）。承認待ちの間の変更は 5.10 に無いので拒む（OQ-17）
_WBS_AFTER_EDIT: dict[WbsStatus, WbsStatus] = {
    WbsStatus.DRAFT: WbsStatus.DRAFT,
    WbsStatus.IN_REVIEW: WbsStatus.IN_REVIEW,
    WbsStatus.BASELINED: WbsStatus.DRAFT,
}


@dataclass(frozen=True)
class WbsReadiness:
    """承認申請の前提の件数（5.10）。

    - ``missing_completion``：成果物か完了条件が空の WP の数（RULE-WBS-005）
    - ``open_comments``：未対応（OPEN）のレビューの指摘の数（WBS-20）
    - ``hundred_percent_errors``：100% ルールの ERROR の数（RULE-WBS-004）
    """

    missing_completion: int
    open_comments: int
    hundred_percent_errors: int

    def unmet(self) -> tuple[str, ...]:
        """満たしていない前提の名前（0 件でない項目。上の並び）。負・整数でない件数は ``ValueError``。"""
        unmet: list[str] = []
        for name in ("missing_completion", "open_comments", "hundred_percent_errors"):
            count = getattr(self, name)
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError(f"申請の前提の件数 {name} は 0 以上の整数: {count!r}")
            if count > 0:
                unmet.append(name)
        return tuple(unmet)


@dataclass(frozen=True)
class BaselinePlan:
    """承認で作るスコープベースラインの版（4.1 の ``scope_baselines`` の ``version``・``reason``）。"""

    version: int
    reason: ScopeBaselineReason


@dataclass(frozen=True)
class WbsTransition:
    """WBS の状態遷移の結果。``baseline`` は承認のときだけ（サービス層がその時点の WBS と辞書を写す）。"""

    from_status: WbsStatus
    action: WbsAction
    to_status: WbsStatus
    baseline: BaselinePlan | None = None


def _as_member(enum: type[StrEnum], value: object, what: str) -> StrEnum:
    """値の一覧に無い状態・操作は入力の誤り（``ValueError``。状態の不一致の ``INVALID_STATE`` とは分ける）。"""
    try:
        return enum(value)
    except ValueError:
        allowed = "・".join(member.value for member in enum)
        raise ValueError(f"{what} は {allowed} のいずれか: {value!r}") from None


def ensure_not_self_approval(requested_by: int | None, decided_by: int | None) -> None:
    """申請者と判断する人が同じなら ``SELF_APPROVAL_FORBIDDEN``（5.10・WBS-21）。どちらかが無ければ判定できないので ``ValueError``。"""
    if requested_by is None or decided_by is None:
        raise ValueError("承認・差戻しには申請者（requested_by）と判断する人（decided_by）が要る")
    if requested_by == decided_by:
        raise WorkflowError(
            WorkflowErrorCode.SELF_APPROVAL_FORBIDDEN, f"申請者 {requested_by} は自分の申請を承認・差戻しできない"
        )


# RULE-WBS-006 WBSの承認の状態遷移
def transition_wbs(
    status: WbsStatus | str,
    action: WbsAction | str,
    *,
    readiness: WbsReadiness | None = None,
    requested_by: int | None = None,
    decided_by: int | None = None,
    latest_baseline_version: int | None = None,
) -> WbsTransition:
    """WBS の状態を 5.10 の遷移に従って動かす（WBS-20・21・22）。

    - ``SUBMIT``：``readiness`` が要る。前提（完了条件の欠け・未対応の指摘・100% ルールの ERROR が全て 0 件）を
      満たさなければ ``WBS_NOT_READY``（``details`` は満たしていない前提の名前）
    - ``APPROVE``・``SEND_BACK``：``requested_by``（申請者）と ``decided_by``（判断する人）が要る。同じ人なら ``SELF_APPROVAL_FORBIDDEN``。
      承認は、``latest_baseline_version`` が無ければ版 1（``INITIAL_APPROVAL``）、あれば次の版（``RE_APPROVAL``）を作る
    - ``EDIT``：DRAFT・IN_REVIEW はそのまま、BASELINED は DRAFT へ戻る（版は残る）。PENDING_APPROVAL では拒む
    - 表に無い遷移（例：BASELINED からレビュー・申請、承認待ちでない承認）は ``INVALID_STATE``

    状態を先に見て（``INVALID_STATE``）、次に前提・本人の判定を行う。
    """
    current = WbsStatus(_as_member(WbsStatus, status, "WBS の状態"))
    act = WbsAction(_as_member(WbsAction, action, "WBS の操作"))

    if act is WbsAction.EDIT:
        after = _WBS_AFTER_EDIT.get(current)
        if after is None:
            raise WorkflowError(WorkflowErrorCode.INVALID_STATE, f"WBS が {current} の間は変えられない（承認待ち）")
        return WbsTransition(from_status=current, action=act, to_status=after)

    target = _WBS_TRANSITIONS.get((current, act))
    if target is None:
        raise WorkflowError(WorkflowErrorCode.INVALID_STATE, f"WBS が {current} のときは {act} できない")

    if act is WbsAction.SUBMIT:
        if readiness is None:
            raise ValueError("承認の申請には前提の件数（readiness）が要る")
        unmet = readiness.unmet()
        if unmet:
            raise WorkflowError(
                WorkflowErrorCode.WBS_NOT_READY, f"承認を申請する前提を満たしていない: {', '.join(unmet)}", details=unmet
            )
        return WbsTransition(from_status=current, action=act, to_status=target)

    if act in (WbsAction.APPROVE, WbsAction.SEND_BACK):
        ensure_not_self_approval(requested_by, decided_by)
    if act is WbsAction.APPROVE:
        if latest_baseline_version is None:
            plan = BaselinePlan(version=1, reason=ScopeBaselineReason.INITIAL_APPROVAL)
        else:
            if isinstance(latest_baseline_version, bool) or not isinstance(latest_baseline_version, int):
                raise ValueError(f"最新の版は整数で渡す: {latest_baseline_version!r}")
            if latest_baseline_version < 1:
                raise ValueError(f"最新の版は 1 以上: {latest_baseline_version}")
            plan = BaselinePlan(version=latest_baseline_version + 1, reason=ScopeBaselineReason.RE_APPROVAL)
        return WbsTransition(from_status=current, action=act, to_status=target, baseline=plan)
    return WbsTransition(from_status=current, action=act, to_status=target)


def decide_approval(
    status: ApprovalStatus | str,
    decision: ApprovalDecision | str,
    *,
    requested_by: int,
    decided_by: int,
) -> ApprovalStatus:
    """承認申請を判断する（5.10 の「承認申請：PENDING → APPROVED / REJECTED」・WBS-21）。

    PENDING でなければ ``INVALID_STATE``、申請者と同じ人なら ``SELF_APPROVAL_FORBIDDEN``。WBS と変更要求の申請で共通の判定。
    """
    current = ApprovalStatus(_as_member(ApprovalStatus, status, "承認申請の状態"))
    chosen = ApprovalDecision(_as_member(ApprovalDecision, decision, "承認申請への判断"))
    if current is not ApprovalStatus.PENDING:
        raise WorkflowError(WorkflowErrorCode.INVALID_STATE, f"承認申請は {current} で、もう判断できない")
    ensure_not_self_approval(requested_by, decided_by)
    return ApprovalStatus.APPROVED if chosen is ApprovalDecision.APPROVE else ApprovalStatus.REJECTED
