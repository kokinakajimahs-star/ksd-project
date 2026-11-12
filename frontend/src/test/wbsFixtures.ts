// 試験用のデータ：WBS・見積の応答の形（backend/app/schemas/wbs.py の WbsTree・ElementOut・DictionaryOut・ValidationOut・
// ReviewOut・ReviewSummary・ReviewList・CommentOut・BaselineSummary・BaselineOut・ResponsibilityMatrixOut・WbsTraceOut、
// estimate.py の EstimateRecordOut・EstimateHistoryOut）
import type { EstimateHistory, EstimateRecord } from "../api/est";
import type {
  Dictionary,
  Review,
  ReviewComment,
  ReviewList,
  ReviewSummary,
  ResponsibilityMatrix,
  ScopeBaselineSummary,
  Validation,
  WbsElement,
  WbsTrace,
  WbsTree,
} from "../api/wbs";

export function makeElement(overrides: Partial<WbsElement> = {}): WbsElement {
  return {
    id: 100,
    parent_id: null,
    kind: "DELIVERABLE",
    name: "要件定義",
    wbs_code: "1.0",
    sibling_seq: 1,
    sort_order: 1,
    depth: 1,
    own_estimate_hours: null,
    rollup_hours: 0,
    rollup_source: "CHILDREN",
    responsible_user_id: null,
    responsible_team: null,
    needs_renumber: false,
    ...overrides,
  };
}

/** 1.0 要件定義（成果物）─ 1.1 業務整理（WP）・1.2 画面設計（WP）、2.0 管理（管理領域） */
export function sampleElements(): WbsElement[] {
  return [
    makeElement({ id: 100, rollup_hours: 56 }),
    makeElement({
      id: 101,
      parent_id: 100,
      kind: "WORK_PACKAGE",
      name: "業務整理",
      wbs_code: "1.1",
      depth: 2,
      own_estimate_hours: 40,
      rollup_hours: 40,
      rollup_source: "OWN_ESTIMATE",
      responsible_user_id: 2,
    }),
    makeElement({
      id: 102,
      parent_id: 100,
      kind: "WORK_PACKAGE",
      name: "画面設計",
      wbs_code: "1.2",
      sibling_seq: 2,
      sort_order: 2,
      depth: 2,
      rollup_hours: 16,
      rollup_source: "ACTIVITIES",
      responsible_team: "設計チーム",
      needs_renumber: true,
    }),
    makeElement({ id: 103, kind: "MANAGEMENT", name: "管理", wbs_code: "2.0", sibling_seq: 2, sort_order: 2 }),
  ];
}

export function makeTree(elements: WbsElement[] = sampleElements(), overrides: Partial<WbsTree> = {}): WbsTree {
  return {
    project_id: 3,
    wbs_status: "DRAFT",
    numbering_rule: { separator: ".", min_digits: 1, prefix: "", pad_first_level: true },
    project_estimate_hours: 56,
    renumber_pending: elements.filter((e) => e.needs_renumber).length,
    validation: { errors: 1, warnings: 2, missing_completion: 1 },
    restricted: false,
    elements,
    ...overrides,
  };
}

export function makeDictionary(overrides: Partial<Dictionary> = {}): Dictionary {
  return {
    element_id: 101,
    wbs_code: "1.1",
    name: "業務整理",
    kind: "WORK_PACKAGE",
    responsible_user_id: 2,
    responsible_team: null,
    own_estimate_hours: 40,
    deliverable: "業務フロー図",
    work_scope: "現行業務の聞き取り",
    completion_criteria: null,
    assumptions: null,
    ...overrides,
  };
}

export function makeValidation(overrides: Partial<Validation> = {}): Validation {
  return {
    project_id: 3,
    tolerance: 0.05,
    counts: { errors: 1, warnings: 1, missing_completion: 1 },
    findings: [
      {
        level: "WARNING",
        code: "SUM_MISMATCH",
        element_id: 100,
        wbs_code: "1.0",
        scope_item_id: null,
        own_estimate_hours: 80,
        lower_hours: 56,
      },
      {
        level: "ERROR",
        code: "IN_SCOPE_UNCOVERED",
        element_id: null,
        wbs_code: null,
        scope_item_id: 31,
        own_estimate_hours: null,
        lower_hours: null,
      },
    ],
    missing_completion: [{ element_id: 101, wbs_code: "1.1", missing: ["completion_criteria"] }],
    ...overrides,
  };
}

export function makeComment(overrides: Partial<ReviewComment> = {}): ReviewComment {
  return {
    id: 501,
    review_id: 50,
    element_id: 101,
    category: "OMISSION",
    body: "移行の作業が無い",
    status: "OPEN",
    author_id: 4,
    resolution: null,
    updated_at: "2026-11-10T02:00:00Z",
    ...overrides,
  };
}

export function makeReview(overrides: Partial<Review> = {}): Review {
  return {
    id: 50,
    project_id: 3,
    status: "OPEN",
    requested_by: 2,
    requested_at: "2026-11-09T01:00:00Z",
    reviewer_ids: [4],
    closed_at: null,
    comments: [makeComment()],
    ...overrides,
  };
}

/** レビューの一覧の 1 行（makeReview から指摘を除いた形） */
export function makeReviewSummary(overrides: Partial<ReviewSummary> = {}): ReviewSummary {
  const { comments: _comments, ...summary } = makeReview();
  return { ...summary, ...overrides };
}

/** レビューの一覧（新しい順に並べて渡す） */
export function reviewList(items: ReviewSummary[], overrides: Partial<ReviewList> = {}): ReviewList {
  return { project_id: 3, items, total: items.length, limit: 50, offset: 0, ...overrides };
}

export function makeScopeBaselineSummary(overrides: Partial<ScopeBaselineSummary> = {}): ScopeBaselineSummary {
  return {
    id: 81,
    version: 1,
    reason: "INITIAL_APPROVAL",
    approval_request_id: 10,
    change_request_id: null,
    created_by: 4,
    created_at: "2026-11-05T03:00:00Z",
    element_count: 4,
    ...overrides,
  };
}

export function makeMatrix(overrides: Partial<ResponsibilityMatrix> = {}): ResponsibilityMatrix {
  return {
    project_id: 3,
    restricted: false,
    users: [
      { user_id: 2, login_id: "planner", display_name: "計画 太郎", is_member: true },
      { user_id: 9, login_id: "former", display_name: "元 次郎", is_member: false },
    ],
    rows: [
      {
        element_id: 101,
        wbs_code: "1.1",
        name: "業務整理",
        responsible_user_id: 2,
        responsible_team: null,
        planned_hours: 24,
        cells: [
          { user_id: 2, is_responsible: true, is_assigned: true, planned_hours: 16 },
          { user_id: 9, is_responsible: false, is_assigned: true, planned_hours: 8 },
        ],
      },
      {
        element_id: 102,
        wbs_code: "1.2",
        name: "画面設計",
        responsible_user_id: null,
        responsible_team: "設計チーム",
        planned_hours: 0,
        cells: [
          { user_id: 2, is_responsible: false, is_assigned: false, planned_hours: 0 },
          { user_id: 9, is_responsible: false, is_assigned: false, planned_hours: 0 },
        ],
      },
    ],
    without_responsible: [{ element_id: 102, wbs_code: "1.2", name: "画面設計", responsible_team: "設計チーム" }],
    ...overrides,
  };
}

export function makeTrace(overrides: Partial<WbsTrace> = {}): WbsTrace {
  return {
    project_id: 3,
    query: "1.1",
    wbs_code: "1.1",
    element_id: 101,
    resolved_by: "CURRENT",
    restricted: false,
    elements: [{ id: 101, parent_id: 100, kind: "WORK_PACKAGE", name: "業務整理", wbs_code: "1.1" }],
    activity_ids: [21, 22],
    estimate_record_ids: [301],
    time_entry_ids: [701, 702, 703],
    actual_hours: 12.5,
    eac_hours: 44,
    change_request_ids: [71],
    corrective_action_ids: [],
    ...overrides,
  };
}

export function makeEstimateRecord(overrides: Partial<EstimateRecord> = {}): EstimateRecord {
  return {
    id: 301,
    project_id: 3,
    target_type: "ELEMENT",
    target_id: 101,
    hours: 40,
    input_value: 5,
    input_unit: "DAY",
    method: "ANALOGOUS",
    assumptions: "前回の更改と同じ規模",
    references: "前回の実績表",
    entered_by: 2,
    entered_by_name: "計画 太郎",
    entered_at: "2026-11-08T01:00:00Z",
    ...overrides,
  };
}

export function estimateHistory(items: EstimateRecord[], overrides: Partial<EstimateHistory> = {}): EstimateHistory {
  return {
    target_type: "ELEMENT",
    target_id: 101,
    target_exists: true,
    current_hours: 40,
    items,
    total: items.length,
    limit: 50,
    offset: 0,
    ...overrides,
  };
}
