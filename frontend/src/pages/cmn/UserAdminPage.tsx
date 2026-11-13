// UI-CMN-002 ユーザー管理（設計書 7.1 のルート /admin/users。API-CMN-003・004・005・008）。
// 一覧は user.admin か member.manage で見られる。作成・変更・無効化は user.admin だけ（9.1）なので、
// その権限が無ければ操作の部品を出さない（7.2。サーバでも必ず判定する）。
// プロジェクトの作成の許可（can_create_project。9.1 の project.create）もここで設定する。
// ログインのロック中（locked_until が今より後。NFR-04）を示し、パスワードを設定し直すとロックが解ける
import { useEffect, useState, type FormEvent } from "react";

import {
  canAdministerUsers,
  createUser,
  getMe,
  listUsers,
  updateUser,
  type Me,
  type Page,
  type User,
  type UserUpdateInput,
} from "../../api/cmn";
import { describeError, toErrorView, type ErrorView } from "../../api/errors";
import ErrorAlert from "../../components/ErrorAlert";
import Pager from "../../components/Pager";
import { optionalText } from "../../lib/form";
import { formatDateTime, serverTime } from "../../lib/format";
import { DISPLAY_NAME_MAX, EMAIL_MAX, LOGIN_ID_MAX, ORGANIZATION_MAX, PASSWORD_MAX, PASSWORD_MIN } from "./limits";

export const USERS_PAGE_SIZE = 100;

/** パスワードの方針の注記（NFR-04。文字種・有効期限は課さない） */
export const PASSWORD_POLICY_NOTE = `${PASSWORD_MIN} 文字以上（ログイン名と同じものは使えません）`;

/** ログインをロックしているか（locked_until が今より後。NFR-04） */
export function isLocked(user: Pick<User, "locked_until">, now: number = Date.now()): boolean {
  return user.locked_until !== null && serverTime(user.locked_until) > now;
}

function messageError(message: string): ErrorView {
  return { message, details: [] };
}

function UserCreateForm({ onCreated }: { onCreated: (user: User) => void }) {
  const [loginId, setLoginId] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [email, setEmail] = useState("");
  const [organization, setOrganization] = useState("");
  const [isSystemAdmin, setIsSystemAdmin] = useState(false);
  const [canCreateProject, setCanCreateProject] = useState(false);
  const [error, setError] = useState<ErrorView | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const created = await createUser({
        login_id: loginId,
        display_name: displayName,
        password,
        email: optionalText(email),
        organization: optionalText(organization),
        is_system_admin: isSystemAdmin,
        can_create_project: canCreateProject,
      });
      setLoginId("");
      setDisplayName("");
      setPassword("");
      setEmail("");
      setOrganization("");
      setIsSystemAdmin(false);
      setCanCreateProject(false);
      onCreated(created);
    } catch (e: unknown) {
      setError(toErrorView(e));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} aria-label="ユーザーの作成">
      <h3>ユーザーの作成</h3>
      <div>
        <label htmlFor="new-login-id">ログイン名</label>
        <input
          id="new-login-id"
          required
          maxLength={LOGIN_ID_MAX}
          value={loginId}
          onChange={(e) => setLoginId(e.target.value)}
        />
      </div>
      <div>
        <label htmlFor="new-display-name">表示名</label>
        <input
          id="new-display-name"
          required
          maxLength={DISPLAY_NAME_MAX}
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
        />
      </div>
      <div>
        <label htmlFor="new-password">初期パスワード</label>
        <input
          id="new-password"
          type="password"
          autoComplete="new-password"
          required
          maxLength={PASSWORD_MAX}
          aria-describedby="new-password-note"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <small id="new-password-note">{PASSWORD_POLICY_NOTE}</small>
      </div>
      <div>
        <label htmlFor="new-email">メール</label>
        <input
          id="new-email"
          type="email"
          maxLength={EMAIL_MAX}
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
      </div>
      <div>
        <label htmlFor="new-organization">所属</label>
        <input
          id="new-organization"
          maxLength={ORGANIZATION_MAX}
          value={organization}
          onChange={(e) => setOrganization(e.target.value)}
        />
      </div>
      <div>
        <label>
          <input type="checkbox" checked={isSystemAdmin} onChange={(e) => setIsSystemAdmin(e.target.checked)} />
          システム管理者
        </label>
      </div>
      <div>
        <label>
          <input
            type="checkbox"
            checked={canCreateProject}
            onChange={(e) => setCanCreateProject(e.target.checked)}
          />
          プロジェクトを作れる
        </label>
      </div>
      <ErrorAlert error={error} />
      <button type="submit" disabled={submitting}>
        作成する
      </button>
    </form>
  );
}

/** 変えた項目だけを集める（API-CMN-005 は載せた項目だけを変える）。パスワードは入れたときだけ送る */
export function collectChanges(
  user: User,
  form: {
    displayName: string;
    email: string;
    organization: string;
    password: string;
    isSystemAdmin: boolean;
    canCreateProject: boolean;
    isActive: boolean;
  },
): UserUpdateInput {
  const changes: UserUpdateInput = {};
  if (form.displayName !== user.display_name) {
    changes.display_name = form.displayName;
  }
  const email = optionalText(form.email);
  if (email !== user.email) {
    changes.email = email;
  }
  const organization = optionalText(form.organization);
  if (organization !== user.organization) {
    changes.organization = organization;
  }
  if (form.password !== "") {
    changes.password = form.password;
  }
  if (form.isSystemAdmin !== user.is_system_admin) {
    changes.is_system_admin = form.isSystemAdmin;
  }
  if (form.canCreateProject !== user.can_create_project) {
    changes.can_create_project = form.canCreateProject;
  }
  if (form.isActive !== user.is_active) {
    changes.is_active = form.isActive;
  }
  return changes;
}

function UserEditForm({
  user,
  onSaved,
  onCancel,
}: {
  user: User;
  onSaved: (user: User) => void;
  onCancel: () => void;
}) {
  const [displayName, setDisplayName] = useState(user.display_name);
  const [email, setEmail] = useState(user.email ?? "");
  const [organization, setOrganization] = useState(user.organization ?? "");
  const [password, setPassword] = useState("");
  const [isSystemAdmin, setIsSystemAdmin] = useState(user.is_system_admin);
  const [canCreateProject, setCanCreateProject] = useState(user.can_create_project);
  const [isActive, setIsActive] = useState(user.is_active);
  const [error, setError] = useState<ErrorView | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const locked = isLocked(user);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const changes = collectChanges(user, {
      displayName,
      email,
      organization,
      password,
      isSystemAdmin,
      canCreateProject,
      isActive,
    });
    if (Object.keys(changes).length === 0) {
      setError(messageError("変更した項目がありません"));
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      const updated = await updateUser(user.id, changes);
      onSaved(updated);
    } catch (e: unknown) {
      setError(toErrorView(e));
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} aria-label="ユーザーの変更">
      <h3>ユーザーの変更：{user.login_id}</h3>
      {locked && user.locked_until !== null && (
        <p>
          ログインを続けて誤ったため、{formatDateTime(user.locked_until)} までログインできません。
          新しいパスワードを設定するとロックを解きます
        </p>
      )}
      <div>
        <label htmlFor="edit-display-name">表示名</label>
        <input
          id="edit-display-name"
          required
          maxLength={DISPLAY_NAME_MAX}
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
        />
      </div>
      <div>
        <label htmlFor="edit-email">メール</label>
        <input
          id="edit-email"
          type="email"
          maxLength={EMAIL_MAX}
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
      </div>
      <div>
        <label htmlFor="edit-organization">所属</label>
        <input
          id="edit-organization"
          maxLength={ORGANIZATION_MAX}
          value={organization}
          onChange={(e) => setOrganization(e.target.value)}
        />
      </div>
      <div>
        <label htmlFor="edit-password">新しいパスワード（変えないときは空欄）</label>
        <input
          id="edit-password"
          type="password"
          autoComplete="new-password"
          maxLength={PASSWORD_MAX}
          aria-describedby="edit-password-note"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <small id="edit-password-note">{PASSWORD_POLICY_NOTE}</small>
      </div>
      <div>
        <label>
          <input type="checkbox" checked={isSystemAdmin} onChange={(e) => setIsSystemAdmin(e.target.checked)} />
          システム管理者
        </label>
      </div>
      <div>
        <label>
          <input
            type="checkbox"
            checked={canCreateProject}
            onChange={(e) => setCanCreateProject(e.target.checked)}
          />
          プロジェクトを作れる
        </label>
      </div>
      <div>
        <label>
          <input type="checkbox" checked={isActive} onChange={(e) => setIsActive(e.target.checked)} />
          有効
        </label>
      </div>
      <ErrorAlert error={error} />
      <button type="submit" disabled={submitting}>
        保存する
      </button>
      <button type="button" onClick={onCancel} disabled={submitting}>
        取り消す
      </button>
    </form>
  );
}

export default function UserAdminPage() {
  const [me, setMe] = useState<Me | null>(null);
  const [page, setPage] = useState<Page<User> | null>(null);
  const [offset, setOffset] = useState(0);
  const [reloadKey, setReloadKey] = useState(0);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [editing, setEditing] = useState<User | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoadError(null);
    Promise.all([getMe(), listUsers({ limit: USERS_PAGE_SIZE, offset })]).then(
      ([meResult, users]) => {
        if (!cancelled) {
          setMe(meResult);
          setPage(users);
        }
      },
      (e: unknown) => {
        if (!cancelled) {
          setPage(null);
          setLoadError(describeError(e));
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [offset, reloadKey]);

  const canAdmin = me !== null && canAdministerUsers(me);

  function reload() {
    setReloadKey((key) => key + 1);
  }

  return (
    <section data-screen-id="UI-CMN-002" className="user-admin-page">
      <h2>ユーザー管理</h2>
      {notice !== null && <p role="status">{notice}</p>}
      {loadError !== null && <p role="alert">{loadError}</p>}
      {canAdmin && (
        <UserCreateForm
          onCreated={(created) => {
            setNotice(`ユーザー「${created.login_id}」を作成しました`);
            reload();
          }}
        />
      )}
      {canAdmin && editing !== null && (
        <UserEditForm
          key={editing.id}
          user={editing}
          onSaved={(updated) => {
            setEditing(null);
            setNotice(`ユーザー「${updated.login_id}」を変更しました`);
            reload();
          }}
          onCancel={() => setEditing(null)}
        />
      )}
      {page !== null && (
        <>
          <table aria-label="ユーザーの一覧">
            <thead>
              <tr>
                <th scope="col">ログイン名</th>
                <th scope="col">表示名</th>
                <th scope="col">メール</th>
                <th scope="col">所属</th>
                <th scope="col">システム管理者</th>
                <th scope="col">プロジェクトを作れる</th>
                <th scope="col">状態</th>
                {canAdmin && <th scope="col">操作</th>}
              </tr>
            </thead>
            <tbody>
              {page.items.map((user) => (
                <tr key={user.id}>
                  <td>{user.login_id}</td>
                  <td>{user.display_name}</td>
                  <td>{user.email ?? ""}</td>
                  <td>{user.organization ?? ""}</td>
                  <td>{user.is_system_admin ? "○" : ""}</td>
                  <td>{user.can_create_project ? "○" : ""}</td>
                  <td>
                    {user.is_active ? "有効" : "無効"}
                    {isLocked(user) && user.locked_until !== null && (
                      <>・ロック中（{formatDateTime(user.locked_until)} まで）</>
                    )}
                  </td>
                  {canAdmin && (
                    <td>
                      <button
                        type="button"
                        aria-label={`${user.login_id} を変更する`}
                        onClick={() => {
                          setNotice(null);
                          setEditing(user);
                        }}
                      >
                        変更
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
          {page.items.length === 0 && <p>ユーザーがいません</p>}
          <Pager total={page.total} limit={page.limit} offset={page.offset} onChange={setOffset} />
        </>
      )}
    </section>
  );
}
