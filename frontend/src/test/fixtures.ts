// 試験用のデータ（サーバの応答の形。backend/app/schemas の UserOut・MeOut・PendingApprovalOut・ProjectOut・MemberOut）
import type { LoginResponse, Me, PendingApproval, User } from "../api/cmn";
import type { Member, Project } from "../api/prj";
import { SESSION_STORAGE_KEY, type Session } from "../auth/session";

export function makeUser(overrides: Partial<User> = {}): User {
  return {
    id: 1,
    login_id: "admin",
    display_name: "管理者",
    email: null,
    organization: null,
    is_system_admin: true,
    is_active: true,
    ...overrides,
  };
}

export function makeMe(overrides: Partial<Me> = {}): Me {
  return {
    user: makeUser(),
    system_permissions: ["project.create", "user.admin"],
    projects: [],
    ...overrides,
  };
}

export function makeLoginResponse(overrides: Partial<LoginResponse> = {}): LoginResponse {
  return {
    access_token: "token-abc",
    token_type: "bearer",
    expires_at: "2999-01-01T00:00:00Z",
    user: makeUser(),
    ...overrides,
  };
}

export function makePendingApproval(overrides: Partial<PendingApproval> = {}): PendingApproval {
  return {
    id: 10,
    project_id: 3,
    target_type: "WBS",
    target_id: 3,
    status: "PENDING",
    requested_by: 7,
    requested_at: "2026-11-10T01:30:00Z",
    decided_by: null,
    decided_at: null,
    comment: null,
    project_code: "P-001",
    project_name: "基幹更改",
    requested_by_name: "計画 太郎",
    ...overrides,
  };
}

/** ログインした状態を作る（sessionStorage に覚えさせる。App の SessionProvider が読む） */
export function storeSession(session: Partial<Session> = {}): Session {
  const value: Session = { token: "token-abc", expiresAt: "2999-01-01T00:00:00Z", user: makeUser(), ...session };
  window.sessionStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(value));
  return value;
}

export function makeProject(overrides: Partial<Project> = {}): Project {
  return {
    id: 3,
    code: "P-001",
    name: "基幹更改",
    purpose: "老朽化した基幹系を更改する",
    requirements: null,
    key_deliverables: null,
    start_date: "2026-04-01",
    target_finish_date: "2026-09-30",
    owner_user_id: 1,
    owner: { id: 1, login_id: "admin", display_name: "管理者" },
    wbs_status: "DRAFT",
    settings: {
      progress_method_default: "DIRECT",
      delay_tolerance_days: 0.5,
      delay_extract_days: 2,
      variance_rate: 0.1,
      variance_hours: 8,
      hundred_percent_tolerance: 0,
    },
    ...overrides,
  };
}

export function makeMember(overrides: Partial<Member> = {}): Member {
  return {
    user_id: 2,
    login_id: "planner",
    display_name: "計画 太郎",
    is_active: true,
    roles: ["PLANNER"],
    organization: "開発部",
    team: null,
    ...overrides,
  };
}
