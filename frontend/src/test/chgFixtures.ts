// 試験用のデータ：変更管理の応答の形（backend/app/schemas/change.py の ChangeRequestOut・ImpactOut・AppliedOut・
// CorrectiveActionOut・AuditLogOut）
import type { AuditLog, ChangeRequest, CorrectiveAction, Impact, ImpactBody } from "../api/chg";
import type { Member } from "../api/prj";
import { makeMember } from "./fixtures";

export function makeChangeRequest(overrides: Partial<ChangeRequest> = {}): ChangeRequest {
  return {
    id: 71,
    project_id: 3,
    number: 1,
    title: "設計の作業を足す",
    reason: "顧客の追加要望",
    target_codes: ["1.2"],
    operations: [{ op: "remove_activity", activity_id: 21 }],
    status: "DRAFT",
    impact: null,
    created_by: 2,
    submitted_at: null,
    applied_by: null,
    applied_at: null,
    approval_request_id: null,
    ...overrides,
  };
}

export function makeImpactBody(overrides: Partial<ImpactBody> = {}): ImpactBody {
  return {
    project_finish: { before: "2026-09-30", after: "2026-10-05", shift_days: 3 },
    milestones: [{ activity_id: 29, name: "設計完了", before: "2026-06-30", after: "2026-07-03", shift_days: 3 }],
    critical_path: {
      added: [{ activity_id: null, ref: "a1", name: "追加の設計" }],
      removed: [{ activity_id: 22, ref: null, name: "旧レビュー" }],
    },
    total_estimate_hours: { before: "400.00", after: "424.50" },
    elements: {
      added: [{ element_id: null, ref: "e1", wbs_code: null, name: "追加の WP", kind: "WORK_PACKAGE" }],
      updated: [
        {
          element_id: 102,
          wbs_code: "1.2",
          name: "設計",
          changes: { own_estimate_hours: { before: "8.00", after: "16.00" } },
        },
      ],
      removed: [],
    },
    ...overrides,
  };
}

export function makeImpact(changeRequestId = 71, overrides: Partial<ImpactBody> = {}): Impact {
  return { change_request_id: changeRequestId, ...makeImpactBody(overrides) };
}

export function makeCorrectiveAction(overrides: Partial<CorrectiveAction> = {}): CorrectiveAction {
  return {
    id: 81,
    project_id: 3,
    element_id: 102,
    activity_id: null,
    status_report_id: 41,
    wbs_code: "1.2",
    cause: "仕様の確定が遅れた",
    action: "仕様の確認会を週 2 回にする",
    owner_user_id: 2,
    due_date: "2026-11-20",
    status: "NOT_STARTED",
    effect: null,
    overdue: false,
    ...overrides,
  };
}

export function makeAuditLog(overrides: Partial<AuditLog> = {}): AuditLog {
  return {
    id: 901,
    occurred_at: "2026-11-10T01:30:00Z",
    user_id: 2,
    project_id: 3,
    entity_type: "activities",
    entity_id: 21,
    wbs_code: "1.1",
    action: "UPDATE",
    before: { duration_days: 5 },
    after: { duration_days: 8 },
    ...overrides,
  };
}

/** メンバーの一覧（API-PRJ-009 の応答） */
export function memberPage(members: Member[] = [makeMember()]) {
  return { project_id: 3, items: members, total: members.length, limit: 1000, offset: 0 };
}
