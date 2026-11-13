// UI-PRJ-002 のメンバー（PRJ-06。API-PRJ-009〜011）。メンバーに 3 章のロールと所属を割り当て、足す・変える・外す。
// 変えられるのは member.manage を持つ人だけ（9.1）。足す人の候補はユーザーの一覧（API-CMN-003。member.manage で読める）から、
// 全ての頁のメンバー（API-PRJ-009 を total まで読み足す。A-49 の 2）を除いたもの
import { useEffect, useState, type FormEvent } from "react";

import type { User } from "../../../api/cmn";
import { toErrorView, type ErrorView } from "../../../api/errors";
import { fetchAllPages } from "../../../api/pages";
import { listMembers, removeMember, upsertMember, type Member, type MemberList } from "../../../api/prj";
import ErrorAlert from "../../../components/ErrorAlert";
import Pager from "../../../components/Pager";
import { optionalText } from "../../../lib/form";
import { loadActiveUsers } from "../activeUsers";
import { ROLES, roleLabel } from "../labels";
import { MEMBER_ORGANIZATION_MAX, MEMBER_TEAM_MAX } from "../limits";

export const MEMBERS_PAGE_SIZE = 100;

/** 3 章の順に並べたロール（サーバも 3 章の順で持つ） */
export function orderRoles(roles: Iterable<string>): string[] {
  const chosen = new Set(roles);
  return ROLES.map((role) => role.code).filter((code) => chosen.has(code));
}

function MemberForm({
  member,
  candidates,
  onSubmit,
  onCancel,
}: {
  /** 変えるメンバー（足すときは null） */
  member: Member | null;
  candidates: User[];
  onSubmit: (userId: number, roles: string[], organization: string | null, team: string | null) => Promise<void>;
  onCancel: () => void;
}) {
  const [userId, setUserId] = useState<number | null>(member?.user_id ?? candidates[0]?.id ?? null);
  const [roles, setRoles] = useState<string[]>(member?.roles ?? []);
  const [organization, setOrganization] = useState(member?.organization ?? "");
  const [team, setTeam] = useState(member?.team ?? "");
  const [error, setError] = useState<ErrorView | null>(null);
  const [submitting, setSubmitting] = useState(false);

  function toggleRole(code: string, checked: boolean) {
    setRoles((current) => orderRoles(checked ? [...current, code] : current.filter((role) => role !== code)));
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (userId === null) {
      setError({ message: "足すユーザーを選びます", details: [] });
      return;
    }
    if (roles.length === 0) {
      setError({ message: "ロールを 1 つ以上選びます", details: [] });
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      await onSubmit(userId, roles, optionalText(organization), optionalText(team));
    } catch (e: unknown) {
      setError(toErrorView(e));
      setSubmitting(false);
    }
  }

  const title = member === null ? "メンバーを足す" : `メンバーの変更：${member.login_id}`;
  return (
    <form onSubmit={handleSubmit} aria-label={member === null ? "メンバーを足す" : "メンバーの変更"}>
      <h4>{title}</h4>
      {member === null && (
        <div>
          <label htmlFor="member-user">ユーザー</label>
          <select
            id="member-user"
            value={userId ?? ""}
            onChange={(e) => setUserId(e.target.value === "" ? null : Number(e.target.value))}
          >
            {candidates.map((user) => (
              <option key={user.id} value={user.id}>
                {user.display_name}（{user.login_id}）
              </option>
            ))}
          </select>
        </div>
      )}
      <fieldset>
        <legend>ロール</legend>
        {ROLES.map((role) => (
          <label key={role.code}>
            <input
              type="checkbox"
              checked={roles.includes(role.code)}
              onChange={(e) => toggleRole(role.code, e.target.checked)}
            />
            {role.label}
          </label>
        ))}
      </fieldset>
      <div>
        <label htmlFor="member-organization">所属</label>
        <input
          id="member-organization"
          maxLength={MEMBER_ORGANIZATION_MAX}
          value={organization}
          onChange={(e) => setOrganization(e.target.value)}
        />
      </div>
      <div>
        <label htmlFor="member-team">チーム</label>
        <input id="member-team" maxLength={MEMBER_TEAM_MAX} value={team} onChange={(e) => setTeam(e.target.value)} />
      </div>
      <ErrorAlert error={error} />
      <button type="submit" disabled={submitting}>
        {member === null ? "足す" : "保存する"}
      </button>
      <button type="button" onClick={onCancel} disabled={submitting}>
        取り消す
      </button>
    </form>
  );
}

export default function MembersSection({
  projectId,
  canEdit,
  onChanged,
}: {
  projectId: number;
  canEdit: boolean;
  /** メンバーを変えた後に呼ぶ（自分のロールが変わると、画面の出し分けも変わる） */
  onChanged: () => void;
}) {
  const [page, setPage] = useState<MemberList | null>(null);
  const [offset, setOffset] = useState(0);
  const [reloadKey, setReloadKey] = useState(0);
  const [loadError, setLoadError] = useState<ErrorView | null>(null);
  const [error, setError] = useState<ErrorView | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [candidates, setCandidates] = useState<User[] | null>(null);
  /** 全ての頁のメンバーの利用者 ID（足す候補から除く）。読むまで・読めなければ null */
  const [allMemberIds, setAllMemberIds] = useState<ReadonlySet<number> | null>(null);
  const [allMembersError, setAllMembersError] = useState<ErrorView | null>(null);
  /** 開いている部品：足す（"add"）・変える（メンバー）・外すことの確かめ */
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<Member | null>(null);
  const [removing, setRemoving] = useState<Member | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoadError(null);
    listMembers(projectId, { limit: MEMBERS_PAGE_SIZE, offset }).then(
      (members) => {
        if (!cancelled) {
          setPage(members);
        }
      },
      (e: unknown) => {
        if (!cancelled) {
          setPage(null);
          setLoadError(toErrorView(e));
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [projectId, offset, reloadKey]);

  useEffect(() => {
    if (!canEdit) {
      return;
    }
    let cancelled = false;
    void loadActiveUsers().then((users) => {
      if (!cancelled) {
        setCandidates(users);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [canEdit]);

  // 足す候補から除くメンバーは、今の頁でなく全ての頁から（変えたら読み直す）
  useEffect(() => {
    if (!canEdit) {
      return;
    }
    let cancelled = false;
    setAllMemberIds(null);
    setAllMembersError(null);
    fetchAllPages((query) => listMembers(projectId, query)).then(
      (members) => {
        if (!cancelled) {
          setAllMemberIds(new Set(members.map((member) => member.user_id)));
        }
      },
      (e: unknown) => {
        if (!cancelled) {
          setAllMembersError(toErrorView(e));
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [projectId, canEdit, reloadKey]);

  function closeForms() {
    setAdding(false);
    setEditing(null);
    setRemoving(null);
  }

  function afterChange(message: string) {
    closeForms();
    setNotice(message);
    setReloadKey((key) => key + 1);
    onChanged();
  }

  async function submitMember(userId: number, roles: string[], organization: string | null, team: string | null) {
    const saved = await upsertMember(projectId, userId, { roles, organization, team });
    afterChange(
      editing === null ? `「${saved.login_id}」をメンバーに足しました` : `「${saved.login_id}」を変更しました`,
    );
  }

  async function handleRemove(member: Member) {
    setError(null);
    setSubmitting(true);
    try {
      await removeMember(projectId, member.user_id);
      afterChange(`「${member.login_id}」をメンバーから外しました`);
    } catch (e: unknown) {
      setError(toErrorView(e));
    } finally {
      setSubmitting(false);
    }
  }

  // 足す候補は、どの頁のメンバーでもない有効なユーザー（メンバーを選ぶと、そのロール・所属を置き換えてしまう）
  const addCandidates =
    allMemberIds === null ? [] : (candidates ?? []).filter((user) => !allMemberIds.has(user.id));

  return (
    <section aria-labelledby="project-members-heading">
      <h3 id="project-members-heading">メンバー</h3>
      <ErrorAlert error={loadError} />
      {notice !== null && <p role="status">{notice}</p>}
      <ErrorAlert error={error} />
      {canEdit && !adding && editing === null && (
        <button
          type="button"
          disabled={candidates === null || allMemberIds === null || addCandidates.length === 0}
          onClick={() => {
            setNotice(null);
            setRemoving(null);
            setAdding(true);
          }}
        >
          メンバーを足す
        </button>
      )}
      {canEdit && candidates === null && <p>ユーザーの一覧を読めないため、メンバーを足せません</p>}
      {canEdit && allMembersError !== null && (
        <>
          <p>メンバーの全件を読めないため、メンバーを足せません</p>
          <ErrorAlert error={allMembersError} />
        </>
      )}
      {canEdit && adding && (
        <MemberForm member={null} candidates={addCandidates} onSubmit={submitMember} onCancel={closeForms} />
      )}
      {canEdit && editing !== null && (
        <MemberForm
          key={editing.user_id}
          member={editing}
          candidates={[]}
          onSubmit={submitMember}
          onCancel={closeForms}
        />
      )}
      {canEdit && removing !== null && (
        <div role="group" aria-label="メンバーを外すことの確かめ">
          <p>
            「{removing.display_name}（{removing.login_id}）」をメンバーから外しますか。ロールと所属の割り当ても消えます。
          </p>
          <button type="button" disabled={submitting} onClick={() => void handleRemove(removing)}>
            外す
          </button>
          <button type="button" disabled={submitting} onClick={() => setRemoving(null)}>
            取り消す
          </button>
        </div>
      )}
      {page !== null && (
        <>
          <table aria-label="メンバーの一覧">
            <thead>
              <tr>
                <th scope="col">ログイン名</th>
                <th scope="col">表示名</th>
                <th scope="col">ロール</th>
                <th scope="col">所属</th>
                <th scope="col">チーム</th>
                <th scope="col">状態</th>
                {canEdit && <th scope="col">操作</th>}
              </tr>
            </thead>
            <tbody>
              {page.items.map((member) => (
                <tr key={member.user_id}>
                  <td>{member.login_id}</td>
                  <td>{member.display_name}</td>
                  <td>{member.roles.map(roleLabel).join("・")}</td>
                  <td>{member.organization ?? ""}</td>
                  <td>{member.team ?? ""}</td>
                  <td>{member.is_active ? "有効" : "無効"}</td>
                  {canEdit && (
                    <td>
                      <button
                        type="button"
                        aria-label={`${member.login_id} を変更する`}
                        onClick={() => {
                          setNotice(null);
                          setAdding(false);
                          setRemoving(null);
                          setEditing(member);
                        }}
                      >
                        変更
                      </button>
                      <button
                        type="button"
                        aria-label={`${member.login_id} を外す`}
                        onClick={() => {
                          setNotice(null);
                          setAdding(false);
                          setEditing(null);
                          setRemoving(member);
                        }}
                      >
                        外す
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
          {page.items.length === 0 && <p>メンバーがいません</p>}
          <Pager total={page.total} limit={page.limit} offset={page.offset} onChange={setOffset} />
        </>
      )}
    </section>
  );
}
