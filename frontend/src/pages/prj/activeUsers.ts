// 責任者・メンバーに選べる有効なユーザー（API-CMN-003）。
// 一覧は user.admin か member.manage が無いと読めない（9.1）
import { listUsers, type UserListItem } from "../../api/cmn";

/** API-CMN-003 の上限（6.1：limit の上限 1000） */
export const ACTIVE_USERS_LIMIT = 1000;

/** 有効なユーザー（先頭の 1000 件まで）。一覧を読めない（権限が無い・接続できない）ときは null */
export async function loadActiveUsers(): Promise<UserListItem[] | null> {
  try {
    const page = await listUsers({ limit: ACTIVE_USERS_LIMIT, offset: 0 });
    return page.items.filter((user) => user.is_active);
  } catch {
    return null;
  }
}
