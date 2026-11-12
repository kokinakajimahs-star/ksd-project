// 認証・ユーザー・承認待ちの API の型と呼び出し（設計書 6.2 の API-CMN-001〜005・008、API-WBS-019・020）。
// 形はサーバの schemas（backend/app/schemas/auth.py・users.py・wbs.py）に合わせる
import { apiFetch } from "./client";

export interface User {
  id: number;
  login_id: string;
  display_name: string;
  email: string | null;
  organization: string | null;
  is_system_admin: boolean;
  is_active: boolean;
}

export interface LoginResponse {
  access_token: string;
  token_type: "bearer";
  expires_at: string;
  user: User;
}

export interface ProjectPermissions {
  project_id: number;
  roles: string[];
  permissions: string[];
  view_level: string;
}

export interface Me {
  user: User;
  system_permissions: string[];
  projects: ProjectPermissions[];
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface UserCreateInput {
  login_id: string;
  display_name: string;
  password: string;
  email?: string | null;
  organization?: string | null;
  is_system_admin?: boolean;
}

/** 載せた項目だけを変える（API-CMN-005）。ログイン名は変えない */
export interface UserUpdateInput {
  display_name?: string;
  email?: string | null;
  organization?: string | null;
  password?: string;
  is_system_admin?: boolean;
  is_active?: boolean;
}

export type ApprovalTargetType = "WBS" | "CHANGE_REQUEST";

export interface PendingApproval {
  id: number;
  project_id: number;
  target_type: ApprovalTargetType;
  target_id: number;
  status: string;
  requested_by: number;
  requested_at: string;
  decided_by: number | null;
  decided_at: string | null;
  comment: string | null;
  project_code: string;
  project_name: string;
  requested_by_name: string;
}

export type Decision = "APPROVE" | "REJECT";

export interface PageQuery {
  limit?: number;
  offset?: number;
}

export function login(loginId: string, password: string): Promise<LoginResponse> {
  return apiFetch<LoginResponse>("/auth/login", { method: "POST", body: { login_id: loginId, password } });
}

export async function logout(): Promise<void> {
  await apiFetch<null>("/auth/logout", { method: "POST" });
}

export function getMe(): Promise<Me> {
  return apiFetch<Me>("/me");
}

export function listUsers(query: PageQuery = {}): Promise<Page<User>> {
  return apiFetch<Page<User>>("/users", { query: { limit: query.limit, offset: query.offset } });
}

export function createUser(input: UserCreateInput): Promise<User> {
  return apiFetch<User>("/users", { method: "POST", body: input });
}

export function updateUser(userId: number, input: UserUpdateInput): Promise<User> {
  return apiFetch<User>(`/users/${userId}`, { method: "PATCH", body: input });
}

export function listPendingApprovals(query: PageQuery = {}): Promise<Page<PendingApproval>> {
  return apiFetch<Page<PendingApproval>>("/approval-requests", {
    query: { limit: query.limit, offset: query.offset },
  });
}

export function decideApproval(approvalId: number, decision: Decision, comment: string | null): Promise<unknown> {
  return apiFetch<unknown>(`/approval-requests/${approvalId}/decision`, {
    method: "POST",
    body: { decision, comment },
  });
}

/** システムの権限 user.admin（9.1）を持つか。システム管理者は全ての権限を持つ（9.2） */
export function canAdministerUsers(me: Me): boolean {
  return me.user.is_system_admin || me.system_permissions.includes("user.admin");
}

/** システムの権限 project.create（9.1）を持つか。システム管理者は全ての権限を持つ（9.2） */
export function canCreateProject(me: Me): boolean {
  return me.user.is_system_admin || me.system_permissions.includes("project.create");
}

/** そのプロジェクトで権限を持つか（/me のプロジェクトごとの権限。システム管理者は全ての権限を持つ。9.2） */
export function hasProjectPermission(me: Me, projectId: number, permission: string): boolean {
  if (me.user.is_system_admin) {
    return true;
  }
  const project = me.projects.find((p) => p.project_id === projectId);
  return project !== undefined && project.permissions.includes(permission);
}

/** その申請を判断する権限（WBS は wbs.approve、変更要求は change.approve）をプロジェクトで持つか */
export function canDecide(me: Me, approval: Pick<PendingApproval, "project_id" | "target_type">): boolean {
  const required = approval.target_type === "WBS" ? "wbs.approve" : "change.approve";
  return hasProjectPermission(me, approval.project_id, required);
}
