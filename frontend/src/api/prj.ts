// プロジェクト基盤の API の型と呼び出し（設計書 6.2 の API-PRJ-001〜011、API-CMN-006・007）。
// 形はサーバの schemas（backend/app/schemas/projects.py・auth.py）に合わせる
import { apiFetch } from "./client";
import type { Page, PageQuery } from "./cmn";

/** 進捗率の算定方式のコード（要求仕様書 7.6 BR-06） */
export type ProgressMethod =
  | "DIRECT"
  | "DELIVERABLE_RATIO"
  | "APPROVED_WORK_RATIO"
  | "CHECK_ITEM_RATIO"
  | "WEIGHTED_MILESTONE"
  | "ZERO_HUNDRED"
  | "FIFTY_FIFTY";

/** プロジェクトの設定（設計書 4.2 の projects.settings） */
export interface ProjectSettings {
  progress_method_default: ProgressMethod;
  delay_tolerance_days: number;
  delay_extract_days: number;
  variance_rate: number;
  variance_hours: number;
  hundred_percent_tolerance: number;
}

export interface UserSummary {
  id: number;
  login_id: string;
  display_name: string;
}

export interface Project {
  id: number;
  code: string;
  name: string;
  purpose: string | null;
  requirements: string | null;
  key_deliverables: string | null;
  /** YYYY-MM-DD */
  start_date: string;
  target_finish_date: string | null;
  owner_user_id: number;
  owner: UserSummary;
  wbs_status: string;
  settings: ProjectSettings;
}

export interface ProjectCreateInput {
  code: string;
  name: string;
  purpose?: string | null;
  requirements?: string | null;
  key_deliverables?: string | null;
  start_date: string;
  target_finish_date?: string | null;
  owner_user_id: number;
}

/** 載せた項目だけを変える（API-PRJ-004）。settings も載せた項目だけを今の設定に重ねる */
export interface ProjectUpdateInput {
  code?: string;
  name?: string;
  purpose?: string | null;
  requirements?: string | null;
  key_deliverables?: string | null;
  start_date?: string;
  target_finish_date?: string | null;
  owner_user_id?: number;
  settings?: Partial<ProjectSettings>;
}

export type ScopeItemKind = "IN" | "OUT";

export interface ScopeItem {
  id: number;
  kind: ScopeItemKind;
  title: string;
  description: string | null;
  sort_order: number;
}

/** 置き換えの 1 項目。id を書けば既存の項目を変え、書かなければ足す（API-PRJ-006） */
export interface ScopeItemInput {
  id?: number;
  kind: ScopeItemKind;
  title: string;
  description: string | null;
}

export interface ScopeItemList extends Page<ScopeItem> {
  project_id: number;
}

export interface CalendarHoliday {
  /** YYYY-MM-DD */
  holiday_date: string;
  name: string | null;
}

export interface WorkCalendar {
  project_id: number;
  /** 月曜から日曜の順の 7 文字。1 が稼働日（設計書 4.1） */
  working_weekdays: string;
  hours_per_day: number;
  holidays: CalendarHoliday[];
}

export interface CalendarInput {
  working_weekdays: string;
  hours_per_day: number;
  holidays: CalendarHoliday[];
}

export interface Member {
  user_id: number;
  login_id: string;
  display_name: string;
  is_active: boolean;
  roles: string[];
  organization: string | null;
  team: string | null;
}

export interface MemberList extends Page<Member> {
  project_id: number;
}

export interface MemberInput {
  roles: string[];
  organization: string | null;
  team: string | null;
}

export interface RoleGrants {
  role: string;
  permissions: string[];
}

export interface RolePermissions {
  project_id: number;
  roles: RoleGrants[];
}

export function createProject(input: ProjectCreateInput): Promise<Project> {
  return apiFetch<Project>("/projects", { method: "POST", body: input });
}

export function listProjects(query: PageQuery = {}): Promise<Page<Project>> {
  return apiFetch<Page<Project>>("/projects", { query: { limit: query.limit, offset: query.offset } });
}

export function getProject(projectId: number): Promise<Project> {
  return apiFetch<Project>(`/projects/${projectId}`);
}

export function updateProject(projectId: number, input: ProjectUpdateInput): Promise<Project> {
  return apiFetch<Project>(`/projects/${projectId}`, { method: "PATCH", body: input });
}

export function listScopeItems(projectId: number, query: PageQuery = {}): Promise<ScopeItemList> {
  return apiFetch<ScopeItemList>(`/projects/${projectId}/scope-items`, {
    query: { limit: query.limit, offset: query.offset },
  });
}

export function replaceScopeItems(projectId: number, items: ScopeItemInput[]): Promise<ScopeItemList> {
  return apiFetch<ScopeItemList>(`/projects/${projectId}/scope-items`, { method: "PUT", body: { items } });
}

export function getCalendar(projectId: number): Promise<WorkCalendar> {
  return apiFetch<WorkCalendar>(`/projects/${projectId}/calendar`);
}

export function replaceCalendar(projectId: number, input: CalendarInput): Promise<WorkCalendar> {
  return apiFetch<WorkCalendar>(`/projects/${projectId}/calendar`, { method: "PUT", body: input });
}

export function listMembers(projectId: number, query: PageQuery = {}): Promise<MemberList> {
  return apiFetch<MemberList>(`/projects/${projectId}/members`, {
    query: { limit: query.limit, offset: query.offset },
  });
}

export function upsertMember(projectId: number, userId: number, input: MemberInput): Promise<Member> {
  return apiFetch<Member>(`/projects/${projectId}/members/${userId}`, { method: "PUT", body: input });
}

export async function removeMember(projectId: number, userId: number): Promise<void> {
  await apiFetch<null>(`/projects/${projectId}/members/${userId}`, { method: "DELETE" });
}

export function getRolePermissions(projectId: number): Promise<RolePermissions> {
  return apiFetch<RolePermissions>(`/projects/${projectId}/role-permissions`);
}

export function replaceRolePermissions(projectId: number, roles: RoleGrants[]): Promise<RolePermissions> {
  return apiFetch<RolePermissions>(`/projects/${projectId}/role-permissions`, { method: "PUT", body: { roles } });
}
