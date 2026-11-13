"""WBS の API（設計書 6.2 の API-WBS-001〜023）：木・要素・採番規則・改番・新旧対応・辞書・責任分担表・検査・スコープの紐付け・
レビュー・承認・スコープベースライン・横断照会。"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_project_access
from app.schemas.wbs import (
    ApprovalOut,
    ApprovalSubmitOut,
    BaselineElementOut,
    BaselineList,
    BaselineOut,
    BaselineSummary,
    CommentCreate,
    CommentOut,
    CommentUpdate,
    DecisionIn,
    DecisionOut,
    MatrixCellOut,
    MatrixRowOut,
    MatrixUserOut,
    PendingApprovalList,
    PendingApprovalOut,
    ResponsibilityMatrixOut,
    ReviewCreate,
    ReviewList,
    ReviewSummary,
    TraceElementOut,
    WbsTraceOut,
    WithoutResponsibleOut,
    ReviewOut,
    CodeChangeOut,
    CodeHistoryList,
    CodeHistoryOut,
    DictionaryOut,
    DictionaryPut,
    ElementCreate,
    ElementMove,
    ElementOut,
    ElementUpdate,
    FindingOut,
    MissingCompletionOut,
    NumberingRuleIO,
    RenumberOut,
    ScopeLinksOut,
    ScopeLinksPut,
    ValidationCounts,
    ValidationOut,
    WbsTree,
)
from app.services import get_session
from app.services import wbs as wbs_service
from app.services.auth import User
from app.services.clock import get_now
from app.services.project_access import ProjectAccess
from app.services.wbs import (
    ActivitiesReader,
    ChangeRequestApprovals,
    CrossGroupReaders,
    MatrixView,
    ScopeBaseline,
    ValidationResult,
    WbsDictionaryEntry,
    WbsElement,
    WbsNumberingRule,
    WbsReview,
    WbsReviewComment,
)

router = APIRouter(tags=["wbs"])


def get_wbs_activities(request: Request) -> ActivitiesReader:
    """アプリに載せた活動を読む口（``app.services.wbs``）。"""
    reader: ActivitiesReader = request.app.state.wbs_activities
    return reader


def get_wbs_readers(request: Request) -> CrossGroupReaders:
    """アプリに載せた別の群の表（割当・見積の記録・実績・変更要求・是正措置・EAC）を読む口（``app.services.wbs``）。"""
    readers: CrossGroupReaders = request.app.state.wbs_readers
    return readers


def get_change_request_approvals(request: Request) -> ChangeRequestApprovals:
    """アプリに載せた、変更要求の承認申請への判断を変更要求へ反映する口（``app.services.wbs``）。"""
    approvals: ChangeRequestApprovals = request.app.state.change_request_approvals
    return approvals


def _element_out(element: WbsElement, depth: int, rollup_hours: float, rollup_source: str) -> ElementOut:
    return ElementOut(
        id=element.id,
        parent_id=element.parent_id,
        kind=element.kind,
        name=element.name,
        wbs_code=element.wbs_code,
        sibling_seq=element.sibling_seq,
        sort_order=element.sort_order,
        depth=depth,
        own_estimate_hours=wbs_service._hours(element.own_estimate_hours),
        rollup_hours=rollup_hours,
        rollup_source=rollup_source,
        responsible_user_id=element.responsible_user_id,
        responsible_team=element.responsible_team,
        needs_renumber=element.needs_renumber,
    )


def _single_out(session: Session, activities: ActivitiesReader, element: WbsElement) -> ElementOut:
    # 1 件の応答の深さと集計値は、木の応答（API-WBS-001）と同じ求め方にする
    depth, hours, source = wbs_service.element_summary(session, activities, element)
    return _element_out(element, depth, float(hours), source)


def _rule_out(row: WbsNumberingRule) -> NumberingRuleIO:
    return NumberingRuleIO(
        separator=row.separator, min_digits=row.min_digits, prefix=row.prefix, pad_first_level=row.pad_first_level
    )


def _counts(result: ValidationResult) -> ValidationCounts:
    return ValidationCounts(errors=result.errors, warnings=result.warnings, missing_completion=len(result.missing))


def _dictionary_out(element: WbsElement, entry: WbsDictionaryEntry | None) -> DictionaryOut:
    return DictionaryOut(
        element_id=element.id,
        wbs_code=element.wbs_code,
        name=element.name,
        kind=element.kind,
        responsible_user_id=element.responsible_user_id,
        responsible_team=element.responsible_team,
        own_estimate_hours=wbs_service._hours(element.own_estimate_hours),
        deliverable=entry.deliverable if entry else None,
        work_scope=entry.work_scope if entry else None,
        completion_criteria=entry.completion_criteria if entry else None,
        assumptions=entry.assumptions if entry else None,
    )


@router.get("/projects/{project_id}/wbs", response_model=WbsTree)
def get_wbs(
    project_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
    activities: ActivitiesReader = Depends(get_wbs_activities),
) -> WbsTree:
    """API-WBS-001 WBSの木（集計値・改番待ち・検証の件数付き）。

    権限は project.view（委託先は自分の割当の経路だけ。``restricted`` が真）。要素は先行順（兄弟は表示順）。
    集計値は RULE-EST-001、検査の件数は RULE-WBS-004・005（API-WBS-012 と同じ）。
    """
    view = wbs_service.get_tree(session, access, user, activities, project_id)
    elements = [
        _element_out(e, depth, float(view.rollup.elements[e.id].hours), view.rollup.elements[e.id].source.value)
        for e, depth in view.rows
    ]
    return WbsTree(
        project_id=project_id,
        wbs_status=view.context.project.wbs_status,
        numbering_rule=_rule_out(view.context.rule_row),
        project_estimate_hours=float(view.rollup.project_hours) if not view.restricted
        else sum((x.rollup_hours for x in elements if x.parent_id is None), 0.0),
        renumber_pending=sum(1 for e, _ in view.rows if e.needs_renumber),
        validation=_counts(view.validation),
        restricted=view.restricted,
        elements=elements,
    )


@router.post("/projects/{project_id}/wbs/elements", response_model=ElementOut, status_code=201)
def create_element(
    project_id: int,
    body: ElementCreate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
    activities: ActivitiesReader = Depends(get_wbs_activities),
) -> ElementOut:
    """API-WBS-002 要素を足す（採番する）。

    権限は wbs.edit。順番は親の last_child_seq + 1、WBS ID は採番規則から作る（RULE-WBS-001・002）。
    WP の下へは 422 ``WORK_PACKAGE_HAS_CHILDREN``、承認待ちの間は 409 ``INVALID_STATE``。
    """
    element = wbs_service.add_element(
        session, access, user, activities, project_id,
        parent_id=body.parent_id, kind=body.kind, name=body.name, own_estimate_hours=body.own_estimate_hours,
        responsible_user_id=body.responsible_user_id, responsible_team=body.responsible_team,
    )
    return _single_out(session, activities, element)


@router.patch("/projects/{project_id}/wbs/elements/{element_id}", response_model=ElementOut)
def patch_element(
    project_id: int,
    element_id: int,
    body: ElementUpdate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
    activities: ActivitiesReader = Depends(get_wbs_activities),
) -> ElementOut:
    """API-WBS-003 要素の名称・区分・責任者・自分の見積を変える。

    権限は wbs.edit。載せた項目だけを変える（WBS ID は変えない）。区分の変更が RULE-WBS-003 に合わなければ 422。
    """
    element = wbs_service.update_element(
        session, access, user, activities, project_id, element_id, body.model_dump(exclude_unset=True)
    )
    return _single_out(session, activities, element)


@router.post("/projects/{project_id}/wbs/elements/{element_id}/move", response_model=ElementOut)
def move_element(
    project_id: int,
    element_id: int,
    body: ElementMove,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
    activities: ActivitiesReader = Depends(get_wbs_activities),
) -> ElementOut:
    """API-WBS-004 要素を移動する（IDは変えない）。

    権限は wbs.edit。親の ID と形が合わない要素と配下は改番待ち（``needs_renumber``）になる。
    自分か配下の下へは 422 ``PARENT_CYCLE``、WP の下へは 422 ``WORK_PACKAGE_HAS_CHILDREN``。
    """
    element = wbs_service.move(
        session, access, user, activities, project_id, element_id, parent_id=body.parent_id, position=body.position
    )
    return _single_out(session, activities, element)


@router.delete("/projects/{project_id}/wbs/elements/{element_id}", status_code=204)
def delete_element(
    project_id: int,
    element_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
    activities: ActivitiesReader = Depends(get_wbs_activities),
) -> Response:
    """API-WBS-005 要素を配下ごと消す。

    権限は wbs.edit。他の要素の ID は変えない。基準の版の写しは変えない（設計書 1.1 の 4.2。CR-002）。
    """
    wbs_service.remove_element(session, access, user, activities, project_id, element_id)
    return Response(status_code=204)


@router.put("/projects/{project_id}/wbs/numbering-rule", response_model=NumberingRuleIO)
def put_numbering_rule(
    project_id: int,
    body: NumberingRuleIO,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
) -> NumberingRuleIO:
    """API-WBS-006 採番規則を変える。

    権限は wbs.edit。今の ID は変えない（改番 API-WBS-007 で新しい規則の ID になる）。形の誤りは 400。
    """
    row = wbs_service.replace_numbering_rule(
        session, access, user, project_id, separator=body.separator, min_digits=body.min_digits, prefix=body.prefix,
        pad_first_level=body.pad_first_level,
    )
    return _rule_out(row)


@router.post("/projects/{project_id}/wbs/renumber", response_model=RenumberOut)
def post_renumber(
    project_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
    activities: ActivitiesReader = Depends(get_wbs_activities),
    now: datetime = Depends(get_now),
) -> RenumberOut:
    """API-WBS-007 改番する（新旧の対応を返す）。

    権限は wbs.edit。兄弟の表示順に 1 から振り直し、変わった要素の新旧を ``wbs_code_history`` に残す（RULE-WBS-002）。
    """
    view = wbs_service.renumber_wbs(session, access, user, activities, project_id, now=now)
    return RenumberOut(
        renumbered_at=view.renumbered_at,
        changes=[CodeChangeOut(element_id=i, old_code=o, new_code=n) for i, o, n in view.changes],
    )


@router.get("/projects/{project_id}/wbs/code-history", response_model=CodeHistoryList)
def get_code_history(
    project_id: int,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
    activities: ActivitiesReader = Depends(get_wbs_activities),
) -> CodeHistoryList:
    """API-WBS-008 WBS IDの新旧対応の一覧。

    権限は project.view。新しい順。要素の今の ID（``current_code``）を添える。``limit``・``offset``（6.1）。
    """
    rows, total = wbs_service.list_code_history(
        session, access, user, activities, project_id, limit=limit, offset=offset
    )
    items = [
        CodeHistoryOut(id=row.id, element_id=row.element_id, current_code=current, old_code=row.old_code,
                       new_code=row.new_code, renumbered_at=row.renumbered_at, renumbered_by=row.renumbered_by)
        for row, current in rows
    ]
    return CodeHistoryList(project_id=project_id, items=items, total=total, limit=limit, offset=offset)


@router.get("/projects/{project_id}/wbs/elements/{element_id}/dictionary", response_model=DictionaryOut)
def get_dictionary(
    project_id: int,
    element_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
    activities: ActivitiesReader = Depends(get_wbs_activities),
) -> DictionaryOut:
    """API-WBS-009 WBS辞書を読む。

    権限は project.view。WBS ID・名称・責任者・見積は要素の値。辞書が無ければ 4 項目は空。
    """
    element, entry = wbs_service.get_dictionary(session, access, user, activities, project_id, element_id)
    return _dictionary_out(element, entry)


@router.put("/projects/{project_id}/wbs/elements/{element_id}/dictionary", response_model=DictionaryOut)
def put_dictionary(
    project_id: int,
    element_id: int,
    body: DictionaryPut,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
    activities: ActivitiesReader = Depends(get_wbs_activities),
) -> DictionaryOut:
    """API-WBS-010 WBS辞書を書く。

    権限は wbs.edit。成果物・作業範囲・完了条件・前提条件を載せた値で置き換える。
    """
    element, entry = wbs_service.put_dictionary(
        session, access, user, activities, project_id, element_id, body.model_dump()
    )
    return _dictionary_out(element, entry)


@router.get("/projects/{project_id}/wbs/validation", response_model=ValidationOut)
def get_validation(
    project_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
    activities: ActivitiesReader = Depends(get_wbs_activities),
) -> ValidationOut:
    """API-WBS-012 100%ルールと完了条件の検査の結果。

    権限は project.view。100% ルール（RULE-WBS-004。許容幅は ``settings.hundred_percent_tolerance``）と、
    成果物か完了条件が空の WP（RULE-WBS-005）。
    """
    context, result, _visible = wbs_service.get_validation(session, access, user, activities, project_id)
    session.commit()
    codes = {e.id: e.wbs_code for e in context.elements}
    return ValidationOut(
        project_id=project_id,
        tolerance=float(result.tolerance),
        counts=_counts(result),
        findings=[
            FindingOut(
                level=f.level.value, code=f.code.value, element_id=f.element_id,
                wbs_code=codes.get(f.element_id) if f.element_id is not None else None,
                scope_item_id=f.scope_item_id, own_estimate_hours=wbs_service._hours(f.own_estimate_hours),
                lower_hours=wbs_service._hours(f.lower_hours),
            )
            for f in result.findings
        ],
        missing_completion=[
            MissingCompletionOut(element_id=m.element_id, wbs_code=codes[m.element_id], missing=[x.value for x in m.missing])
            for m in result.missing
        ],
    )


@router.get("/projects/{project_id}/wbs/elements/{element_id}/scope-links", response_model=ScopeLinksOut)
def get_scope_links(
    project_id: int,
    element_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
    activities: ActivitiesReader = Depends(get_wbs_activities),
) -> ScopeLinksOut:
    """API-WBS-026 要素とスコープ項目の紐付けを読む。

    権限は project.view。項目の ID の順。無い要素・委託先に見えない要素は 404（設計書 1.1・CR-004）。
    """
    ids = wbs_service.get_scope_links(session, access, user, activities, project_id, element_id)
    return ScopeLinksOut(element_id=element_id, scope_item_ids=ids)


@router.put("/projects/{project_id}/wbs/elements/{element_id}/scope-links", response_model=ScopeLinksOut)
def put_scope_links(
    project_id: int,
    element_id: int,
    body: ScopeLinksPut,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
    activities: ActivitiesReader = Depends(get_wbs_activities),
) -> ScopeLinksOut:
    """API-WBS-013 要素とスコープ項目の紐付けを置き換える。

    権限は wbs.edit。他のプロジェクトの・無い項目、重複は 400。
    """
    ids = wbs_service.replace_scope_links(session, access, user, activities, project_id, element_id, body.scope_item_ids)
    return ScopeLinksOut(element_id=element_id, scope_item_ids=ids)


# ---------------------------------------------------------------------------
# レビュー（API-WBS-014〜017）
# ---------------------------------------------------------------------------


def _review_out(review: WbsReview, comments: list[WbsReviewComment]) -> ReviewOut:
    return ReviewOut(
        id=review.id, project_id=review.project_id, status=review.status, requested_by=review.requested_by,
        requested_at=review.requested_at, reviewer_ids=list(review.reviewer_ids), closed_at=review.closed_at,
        comments=[CommentOut.model_validate(c) for c in comments],
    )


@router.post("/projects/{project_id}/wbs/reviews", response_model=ReviewOut, status_code=201)
def post_review(
    project_id: int,
    body: ReviewCreate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
    now: datetime = Depends(get_now),
) -> ReviewOut:
    """API-WBS-014 レビューを始める。

    権限は wbs.edit。WBS は DRAFT → IN_REVIEW（RULE-WBS-006。他の状態は 409 ``INVALID_STATE``）。頼む人はメンバー（400）。
    """
    review = wbs_service.start_review(session, access, user, project_id, body.reviewer_ids, now=now)
    return _review_out(review, [])


@router.get("/projects/{project_id}/wbs/reviews", response_model=ReviewList)
def list_reviews(
    project_id: int,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
) -> ReviewList:
    """API-WBS-025 レビューの一覧。

    権限は project.view。新しい順（ID の降順）。指摘は API-WBS-015 で読む。``limit``・``offset``（6.1。設計書 1.1・CR-004）。
    """
    reviews, total = wbs_service.list_reviews(session, access, user, project_id, limit=limit, offset=offset)
    return ReviewList(project_id=project_id, items=[ReviewSummary.model_validate(r) for r in reviews],
                      total=total, limit=limit, offset=offset)


@router.post("/projects/{project_id}/wbs/reviews/{review_id}/close", response_model=ReviewOut)
def close_review(
    project_id: int,
    review_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
    activities: ActivitiesReader = Depends(get_wbs_activities),
    now: datetime = Depends(get_now),
) -> ReviewOut:
    """API-WBS-024 レビューを閉じる（IN_REVIEW → DRAFT）。

    権限は wbs.edit。OPEN のレビューを CLOSED にし（``closed_at``）、WBS を DRAFT に戻す。指摘はそのまま残る（5.10）。
    閉じたレビュー・WBS が IN_REVIEW でなければ 409 ``INVALID_STATE``、無いレビューは 404（設計書 1.1・CR-004）。
    応答はレビューと指摘（API-WBS-015 と同じ形で project.view が要る）。PM がロールの表を変えて wbs.edit だけを持つロールが
    できても、閉じてから 403 を返さないよう、読めることを閉じる前に確かめる。
    """
    wbs_service.get_review(session, access, user, activities, project_id, review_id)
    wbs_service.close_review(session, access, user, project_id, review_id, now=now)
    review, comments = wbs_service.get_review(session, access, user, activities, project_id, review_id)
    return _review_out(review, comments)


@router.get("/projects/{project_id}/wbs/reviews/{review_id}", response_model=ReviewOut)
def get_review(
    project_id: int,
    review_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
    activities: ActivitiesReader = Depends(get_wbs_activities),
) -> ReviewOut:
    """API-WBS-015 レビューと指摘を読む。

    権限は project.view。指摘は ID の順。無いレビューは 404。
    """
    review, comments = wbs_service.get_review(session, access, user, activities, project_id, review_id)
    return _review_out(review, comments)


@router.post("/projects/{project_id}/wbs/reviews/{review_id}/comments", response_model=CommentOut, status_code=201)
def post_comment(
    project_id: int,
    review_id: int,
    body: CommentCreate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
) -> CommentOut:
    """API-WBS-016 指摘を書く。

    権限は wbs.review。分類は漏れ・重複・曖昧・その他。閉じたレビューは 409 ``INVALID_STATE``。
    """
    comment = wbs_service.add_comment(
        session, access, user, project_id, review_id, element_id=body.element_id, category=body.category, body=body.body
    )
    return CommentOut.model_validate(comment)


@router.patch("/projects/{project_id}/wbs/reviews/{review_id}/comments/{comment_id}", response_model=CommentOut)
def patch_comment(
    project_id: int,
    review_id: int,
    comment_id: int,
    body: CommentUpdate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
) -> CommentOut:
    """API-WBS-017 指摘の状態・対応を更新する。

    権限は wbs.edit。状態は OPEN・RESOLVED・REJECTED。閉じたレビューの指摘も更新できる（5.10：指摘は残り、申請の前提に数える）。
    無い指摘は 404。
    """
    comment = wbs_service.update_comment(
        session, access, user, project_id, review_id, comment_id, body.model_dump(exclude_unset=True)
    )
    return CommentOut.model_validate(comment)


# ---------------------------------------------------------------------------
# スコープベースライン（API-WBS-021・022）
# ---------------------------------------------------------------------------


def _baseline_summary(baseline: ScopeBaseline, count: int) -> dict[str, object]:
    return {
        "id": baseline.id, "version": baseline.version, "reason": baseline.reason,
        "approval_request_id": baseline.approval_request_id, "change_request_id": baseline.change_request_id,
        "created_by": baseline.created_by, "created_at": baseline.created_at, "element_count": count,
    }


@router.get("/projects/{project_id}/scope-baselines", response_model=BaselineList)
def list_scope_baselines(
    project_id: int,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
) -> BaselineList:
    """API-WBS-021 スコープベースラインの版の一覧。

    権限は project.view。版の順。版は上書きされず増える（WBS-22）。``limit``・``offset``（6.1）。
    """
    rows, total = wbs_service.list_baselines(session, access, user, project_id, limit=limit, offset=offset)
    return BaselineList(
        project_id=project_id,
        items=[BaselineSummary.model_validate(_baseline_summary(b, n)) for b, n in rows],
        total=total, limit=limit, offset=offset,
    )


@router.get("/projects/{project_id}/scope-baselines/{baseline_id}", response_model=BaselineOut)
def get_scope_baseline(
    project_id: int,
    baseline_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
    activities: ActivitiesReader = Depends(get_wbs_activities),
) -> BaselineOut:
    """API-WBS-022 スコープベースラインの1版（要素と辞書）。

    権限は project.view。写した時点の WBS ID・区分・名称・見積・辞書（後から変わらない）。無い版は 404。
    """
    baseline, elements, restricted = wbs_service.get_baseline(session, access, user, activities, project_id, baseline_id)
    return BaselineOut.model_validate({
        **_baseline_summary(baseline, len(elements)),
        "restricted": restricted,
        "elements": [
            BaselineElementOut(
                element_id=e.element_id, parent_element_id=e.parent_element_id, wbs_code=e.wbs_code, kind=e.kind,
                name=e.name, own_estimate_hours=wbs_service._hours(e.own_estimate_hours), dictionary=e.dictionary,
            )
            for e in elements
        ],
    })


# ---------------------------------------------------------------------------
# 責任分担表（API-WBS-011）
# ---------------------------------------------------------------------------

_MATRIX_CSV_HEADER = ["WBS ID", "名称", "責任者のログインID", "担当チーム", "割当工数の合計（人時）"]


def _hours_text(value: Decimal) -> str:
    return f"{value:.2f}"


def _matrix_csv(view: MatrixView) -> bytes:
    """責任分担表の CSV（6.3 の形：UTF-8（BOM 付き）・カンマ区切り・1 行目が見出し）。

    利用者の列の見出しはログイン ID（メンバーでない人は「（メンバー外）」を付ける）。マスは「責任者」「担当 <人時>」
    「責任者・担当 <人時>」のどれかか空。
    """
    matrix = view.matrix
    users = view.users
    non_members = set(matrix.non_member_user_ids)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow([
        *_MATRIX_CSV_HEADER,
        *(users[u].login_id + ("（メンバー外）" if u in non_members else "") for u in matrix.user_ids),
    ])
    for row in matrix.rows:
        element = view.elements[row.element_id]
        responsible = users[row.responsible_user_id].login_id if row.responsible_user_id is not None else ""
        cells = []
        for cell in row.cells:
            if cell.is_responsible and cell.is_assigned:
                cells.append(f"責任者・担当 {_hours_text(cell.planned_hours)}")
            elif cell.is_assigned:
                cells.append(f"担当 {_hours_text(cell.planned_hours)}")
            else:
                cells.append("責任者" if cell.is_responsible else "")
        writer.writerow([element.wbs_code, element.name, responsible, row.responsible_team or "",
                         _hours_text(row.planned_hours), *cells])
    return ("﻿" + buffer.getvalue()).encode("utf-8")


@router.get("/projects/{project_id}/wbs/responsibility-matrix", response_model=ResponsibilityMatrixOut)
def get_responsibility_matrix(
    project_id: int,
    output_format: Literal["json", "csv"] = Query("json", alias="format"),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
    activities: ActivitiesReader = Depends(get_wbs_activities),
    readers: CrossGroupReaders = Depends(get_wbs_readers),
) -> ResponsibilityMatrixOut | Response:
    """API-WBS-011 責任分担表（`format=json` か `csv`）。

    権限は project.view。WP × メンバーの表（責任者・担当・割当工数）と責任者の無い WP の一覧（RULE-WBS-007）。
    ``format=csv`` は 6.3 の形の CSV（UTF-8・BOM 付き）。それ以外の値は 400。委託先には見える WP の行だけ。
    """
    view = wbs_service.responsibility_matrix(session, access, user, activities, readers, project_id)
    if output_format == "csv":
        return Response(
            content=_matrix_csv(view),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="responsibility-matrix-{project_id}.csv"'},
        )
    matrix = view.matrix
    non_members = set(matrix.non_member_user_ids)
    return ResponsibilityMatrixOut(
        project_id=project_id,
        restricted=view.restricted,
        users=[
            MatrixUserOut(user_id=u, login_id=view.users[u].login_id, display_name=view.users[u].display_name,
                          is_member=u not in non_members)
            for u in matrix.user_ids
        ],
        rows=[
            MatrixRowOut(
                element_id=row.element_id, wbs_code=view.elements[row.element_id].wbs_code,
                name=view.elements[row.element_id].name, responsible_user_id=row.responsible_user_id,
                responsible_team=row.responsible_team, planned_hours=float(row.planned_hours),
                cells=[
                    MatrixCellOut(user_id=c.user_id, is_responsible=c.is_responsible, is_assigned=c.is_assigned,
                                  planned_hours=float(c.planned_hours))
                    for c in row.cells
                ],
            )
            for row in matrix.rows
        ],
        without_responsible=[
            WithoutResponsibleOut(element_id=i, wbs_code=view.elements[i].wbs_code, name=view.elements[i].name,
                                  responsible_team=view.elements[i].responsible_team)
            for i in matrix.without_responsible
        ],
    )


# ---------------------------------------------------------------------------
# 承認の申請・判断・承認待ちの一覧（API-WBS-018〜020）
# ---------------------------------------------------------------------------


@router.post("/projects/{project_id}/wbs/approval-requests", response_model=ApprovalSubmitOut, status_code=201)
def post_approval_request(
    project_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
    activities: ActivitiesReader = Depends(get_wbs_activities),
    now: datetime = Depends(get_now),
) -> ApprovalSubmitOut:
    """API-WBS-018 WBSの承認を申請する（前提を確かめる）。

    権限は wbs.edit。DRAFT・IN_REVIEW → PENDING_APPROVAL（RULE-WBS-006。他の状態は 409 ``INVALID_STATE``）。
    前提（完了条件の欠け・未対応の指摘・100% ルールの ERROR が 0 件）を満たさなければ 422 ``WBS_NOT_READY``。
    開いているレビューは閉じる（A-18）。
    """
    approval, wbs_status = wbs_service.submit_wbs(session, access, user, activities, project_id, now=now)
    return ApprovalSubmitOut.model_validate({**ApprovalOut.model_validate(approval).model_dump(), "wbs_status": wbs_status})


@router.post("/approval-requests/{approval_id}/decision", response_model=DecisionOut)
def post_decision(
    approval_id: int,
    body: DecisionIn,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
    activities: ActivitiesReader = Depends(get_wbs_activities),
    change_requests: ChangeRequestApprovals = Depends(get_change_request_approvals),
    now: datetime = Depends(get_now),
) -> DecisionOut:
    """API-WBS-019 承認・差戻し（WBSと変更要求で共通）。

    権限は WBS の申請なら wbs.approve、変更要求の申請なら change.approve。PENDING でなければ 409 ``INVALID_STATE``、
    申請者本人は 403 ``SELF_APPROVAL_FORBIDDEN``（5.10・9.2）。WBS の承認はスコープベースラインの新しい版を作る（WBS-22）。
    """
    view = wbs_service.decide(
        session, access, user, activities, change_requests, approval_id,
        decision=body.decision, comment=body.comment, now=now,
    )
    baseline = view.baseline
    return DecisionOut.model_validate({
        **ApprovalOut.model_validate(view.approval).model_dump(),
        "wbs_status": view.wbs_status,
        "scope_baseline": None if baseline is None
        else BaselineSummary.model_validate(_baseline_summary(baseline, view.baseline_element_count)),
    })


@router.get("/approval-requests", response_model=PendingApprovalList)
def list_approval_requests(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
) -> PendingApprovalList:
    """API-WBS-020 自分が判断できる承認待ちの一覧。

    権限はログインだけ。PENDING の申請のうち、判断の権限（wbs.approve・change.approve）を持つプロジェクトの、自分の申請でないもの。
    申請の古い順。``limit``・``offset``（6.1）。
    """
    rows, total = wbs_service.list_pending_approvals(session, access, user, limit=limit, offset=offset)
    return PendingApprovalList(
        items=[
            PendingApprovalOut.model_validate({
                **ApprovalOut.model_validate(row.approval).model_dump(), "project_code": row.project.code,
                "project_name": row.project.name, "requested_by_name": row.requester.display_name,
            })
            for row in rows
        ],
        total=total, limit=limit, offset=offset,
    )


# ---------------------------------------------------------------------------
# 横断照会（API-WBS-023）
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/wbs-codes/{wbs_code}/trace", response_model=WbsTraceOut)
def get_wbs_code_trace(
    project_id: int,
    wbs_code: str,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    access: ProjectAccess = Depends(get_project_access),
    activities: ActivitiesReader = Depends(get_wbs_activities),
    readers: CrossGroupReaders = Depends(get_wbs_readers),
) -> WbsTraceOut:
    """API-WBS-023 WBS IDによる横断照会。

    権限は project.view。今の ID か旧 ID（RULE-WBS-002 で解決）から、配下の要素・活動・見積・実績・EAC・変更要求・是正措置を
    集める（RULE-WBS-008）。解決できない ID は 404。委託先には見える範囲だけ（EAC は返さない）。
    """
    view = wbs_service.trace_code(session, access, user, activities, readers, project_id, wbs_code)
    trace = view.trace
    return WbsTraceOut(
        project_id=project_id,
        query=trace.resolution.query,
        wbs_code=trace.resolution.wbs_code,
        element_id=trace.resolution.element_id,
        resolved_by=trace.resolution.resolved_by.value,
        restricted=view.restricted,
        elements=[
            TraceElementOut(id=e.id, parent_id=e.parent_id, kind=e.kind, name=e.name, wbs_code=e.wbs_code)
            for e in view.elements
        ],
        activity_ids=list(trace.activity_ids),
        estimate_record_ids=list(trace.estimate_record_ids),
        time_entry_ids=list(trace.time_entry_ids),
        actual_hours=float(trace.actual_hours),
        eac_hours=None if trace.eac_hours is None else float(trace.eac_hours),
        change_request_ids=list(trace.change_request_ids),
        corrective_action_ids=list(trace.corrective_action_ids),
    )
