"""状態遷移（app.domain.workflow）の試験。

設計書 4.1（projects.wbs_status・approval_requests・scope_baselines）・5.1・5.10、要求仕様書 WBS-13・16・20・21・22。
"""

from __future__ import annotations

import pytest

from app.domain.workflow import (
    ApprovalDecision,
    ApprovalStatus,
    BaselinePlan,
    ScopeBaselineReason,
    WbsAction,
    WbsReadiness,
    WbsStatus,
    WorkflowError,
    WorkflowErrorCode,
    decide_approval,
    transition_wbs,
)

S = WbsStatus
A = WbsAction
READY = WbsReadiness(missing_completion=0, open_comments=0, hundred_percent_errors=0)

# 5.10 の WBS の遷移の表（EDIT を除く）
ALLOWED = {
    (S.DRAFT, A.START_REVIEW): S.IN_REVIEW,
    (S.IN_REVIEW, A.CLOSE_REVIEW): S.DRAFT,
    (S.DRAFT, A.SUBMIT): S.PENDING_APPROVAL,
    (S.IN_REVIEW, A.SUBMIT): S.PENDING_APPROVAL,
    (S.PENDING_APPROVAL, A.APPROVE): S.BASELINED,
    (S.PENDING_APPROVAL, A.SEND_BACK): S.DRAFT,
}
DECISION_ARGS = {"readiness": READY, "requested_by": 1, "decided_by": 2}


@pytest.mark.parametrize(("status", "action"), list(ALLOWED))
def test_transitions_in_design_5_10(status: WbsStatus, action: WbsAction) -> None:
    """RULE-WBS-006：5.10 の遷移の表のとおりに動く（WBS-21 の受入基準「設計書の状態遷移に従う」）。"""
    result = transition_wbs(status, action, **DECISION_ARGS)
    assert (result.from_status, result.action, result.to_status) == (status, action, ALLOWED[(status, action)])


@pytest.mark.parametrize(
    ("status", "action"),
    [(s, a) for s in S for a in A if a is not A.EDIT and (s, a) not in ALLOWED],
)
def test_other_transitions_are_invalid_state(status: WbsStatus, action: WbsAction) -> None:
    """RULE-WBS-006：表に無い遷移（例：BASELINED からの申請・レビュー、承認待ちでない承認）は INVALID_STATE。"""
    with pytest.raises(WorkflowError) as excinfo:
        transition_wbs(status, action, **DECISION_ARGS)
    assert excinfo.value.code is WorkflowErrorCode.INVALID_STATE


@pytest.mark.parametrize(
    ("status", "after"),
    [(S.DRAFT, S.DRAFT), (S.IN_REVIEW, S.IN_REVIEW), (S.BASELINED, S.DRAFT)],
)
def test_edit_returns_baselined_to_draft(status: WbsStatus, after: WbsStatus) -> None:
    """RULE-WBS-006：基準化の後に WBS を変えると DRAFT に戻る（版は残る。5.10）。下書き・レビュー中は状態を変えない。"""
    result = transition_wbs(status, A.EDIT)
    assert result.to_status is after
    assert result.baseline is None


def test_edit_while_pending_approval_is_rejected() -> None:
    """RULE-WBS-006：承認待ちの間は WBS を変えられない（5.10 に遷移が無い。OQ-17）。"""
    with pytest.raises(WorkflowError) as excinfo:
        transition_wbs(S.PENDING_APPROVAL, A.EDIT)
    assert excinfo.value.code is WorkflowErrorCode.INVALID_STATE


@pytest.mark.parametrize(
    ("readiness", "unmet"),
    [
        (WbsReadiness(1, 0, 0), ("missing_completion",)),
        (WbsReadiness(0, 1, 0), ("open_comments",)),
        (WbsReadiness(0, 0, 1), ("hundred_percent_errors",)),
        (WbsReadiness(2, 3, 4), ("missing_completion", "open_comments", "hundred_percent_errors")),
    ],
)
def test_submit_requires_preconditions(readiness: WbsReadiness, unmet: tuple[str, ...]) -> None:
    """RULE-WBS-006：申請の前提（完了条件の欠け・未対応の指摘・100% ルールの ERROR が 0 件）。1 件でもあれば WBS_NOT_READY（WBS-13・WBS-20 の受入基準）。"""
    for status in (S.DRAFT, S.IN_REVIEW):
        with pytest.raises(WorkflowError) as excinfo:
            transition_wbs(status, A.SUBMIT, readiness=readiness)
        assert excinfo.value.code is WorkflowErrorCode.WBS_NOT_READY
        assert excinfo.value.details == unmet


def test_submit_boundary_zero_is_ready_and_readiness_is_required() -> None:
    """RULE-WBS-006：全て 0 件なら申請できる（境界）。前提の件数を渡さない申請・負の件数は入力の誤り。"""
    assert transition_wbs(S.DRAFT, A.SUBMIT, readiness=READY).to_status is S.PENDING_APPROVAL
    with pytest.raises(ValueError):
        transition_wbs(S.DRAFT, A.SUBMIT)
    with pytest.raises(ValueError):
        transition_wbs(S.DRAFT, A.SUBMIT, readiness=WbsReadiness(-1, 0, 0))


@pytest.mark.parametrize("action", [A.APPROVE, A.SEND_BACK])
def test_requester_cannot_decide_own_request(action: WbsAction) -> None:
    """RULE-WBS-006：申請者は自分の申請を承認・差戻しできない（WBS-21 の受入基準・SELF_APPROVAL_FORBIDDEN）。"""
    with pytest.raises(WorkflowError) as excinfo:
        transition_wbs(S.PENDING_APPROVAL, action, requested_by=7, decided_by=7)
    assert excinfo.value.code is WorkflowErrorCode.SELF_APPROVAL_FORBIDDEN
    with pytest.raises(ValueError):
        transition_wbs(S.PENDING_APPROVAL, action, requested_by=7)


def test_state_is_checked_before_self_approval() -> None:
    """RULE-WBS-006：状態が合わなければ、本人の判定より先に INVALID_STATE。"""
    with pytest.raises(WorkflowError) as excinfo:
        transition_wbs(S.DRAFT, A.APPROVE, requested_by=7, decided_by=7)
    assert excinfo.value.code is WorkflowErrorCode.INVALID_STATE


def test_approval_creates_initial_then_re_approval_versions() -> None:
    """RULE-WBS-006：承認でスコープ基準の版を作る。初回は版 1・INITIAL_APPROVAL、版があれば次の版・RE_APPROVAL（WBS-22・5.10）。"""
    first = transition_wbs(S.PENDING_APPROVAL, A.APPROVE, requested_by=1, decided_by=2)
    assert first.baseline == BaselinePlan(version=1, reason=ScopeBaselineReason.INITIAL_APPROVAL)
    again = transition_wbs(S.PENDING_APPROVAL, A.APPROVE, requested_by=1, decided_by=2, latest_baseline_version=3)
    assert again.baseline == BaselinePlan(version=4, reason=ScopeBaselineReason.RE_APPROVAL)
    sent_back = transition_wbs(S.PENDING_APPROVAL, A.SEND_BACK, requested_by=1, decided_by=2, latest_baseline_version=3)
    assert sent_back.baseline is None
    with pytest.raises(ValueError):
        transition_wbs(S.PENDING_APPROVAL, A.APPROVE, requested_by=1, decided_by=2, latest_baseline_version=0)


def test_full_cycle_review_submit_send_back_approve_edit_reapprove() -> None:
    """RULE-WBS-006：レビュー→申請→差戻し→申請→承認（版 1）→変更で DRAFT→申請→承認（版 2・RE_APPROVAL）の一巡。"""
    status = S.DRAFT
    latest: int | None = None
    steps = [A.START_REVIEW, A.SUBMIT, A.SEND_BACK, A.SUBMIT, A.APPROVE, A.EDIT, A.SUBMIT, A.APPROVE]
    reasons = []
    for action in steps:
        result = transition_wbs(status, action, latest_baseline_version=latest, **DECISION_ARGS)
        status = result.to_status
        if result.baseline is not None:
            latest = result.baseline.version
            reasons.append(result.baseline.reason)
    assert status is S.BASELINED
    assert latest == 2
    assert reasons == [ScopeBaselineReason.INITIAL_APPROVAL, ScopeBaselineReason.RE_APPROVAL]


def test_strings_from_db_are_accepted_and_unknown_values_rejected() -> None:
    """RULE-WBS-006：DB の値（文字列）でも受ける。値の一覧に無い状態・操作は入力の誤り（ValueError）。"""
    assert transition_wbs("DRAFT", "START_REVIEW").to_status is S.IN_REVIEW
    with pytest.raises(ValueError):
        transition_wbs("ARCHIVED", A.EDIT)
    with pytest.raises(ValueError):
        transition_wbs(S.DRAFT, "PUBLISH")


@pytest.mark.parametrize(
    ("decision", "after"),
    [(ApprovalDecision.APPROVE, ApprovalStatus.APPROVED), (ApprovalDecision.REJECT, ApprovalStatus.REJECTED)],
)
def test_approval_request_decision(decision: ApprovalDecision, after: ApprovalStatus) -> None:
    """RULE-WBS-006：承認申請は PENDING → APPROVED / REJECTED。判断した後は動かせない。申請者は判断できない（5.10）。"""
    assert decide_approval(ApprovalStatus.PENDING, decision, requested_by=1, decided_by=2) is after
    for done in (ApprovalStatus.APPROVED, ApprovalStatus.REJECTED):
        with pytest.raises(WorkflowError) as excinfo:
            decide_approval(done, decision, requested_by=1, decided_by=2)
        assert excinfo.value.code is WorkflowErrorCode.INVALID_STATE
    with pytest.raises(WorkflowError) as excinfo:
        decide_approval("PENDING", decision.value, requested_by=5, decided_by=5)
    assert excinfo.value.code is WorkflowErrorCode.SELF_APPROVAL_FORBIDDEN
