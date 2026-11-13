// UI-PRJ-002 の権限（CMN-01。API-CMN-006・007）。プロジェクトのロール×権限の表（9.2 の既定を写したもの）を見せ、置き換える。
// 変えられるのは permission.manage を持つ人だけ（9.1）。置き換えは載せなかったロールの権限を全て外すので、全てのロールを送る。
// permission.manage を持つロールが 1 つも無い表は、サーバが 422 NO_PERMISSION_MANAGER で拒む（9.2 の注記）
import { useEffect, useState } from "react";

import { ApiError } from "../../../api/client";
import { toErrorView, type ErrorView } from "../../../api/errors";
import { getRolePermissions, replaceRolePermissions, type RoleGrants, type RolePermissions } from "../../../api/prj";
import ErrorAlert from "../../../components/ErrorAlert";
import { PROJECT_PERMISSIONS, ROLES } from "../labels";

/** ロール → 持つ権限。サーバの表にあって画面の一覧（9.1）に無い権限も保つ（置き換えで黙って外さない） */
export type GrantTable = Record<string, string[]>;

export function tableOf(value: RolePermissions): GrantTable {
  const table: GrantTable = {};
  for (const role of ROLES) {
    table[role.code] = [];
  }
  for (const grant of value.roles) {
    table[grant.role] = [...grant.permissions];
  }
  return table;
}

/** 422 NO_PERMISSION_MANAGER のときに、どう直せばよいかを添える文言 */
export const NO_PERMISSION_MANAGER_HINT =
  "「ロール×権限の表を変える（permission.manage）」を、少なくとも 1 つのロールに残してください";

/** 保存の失敗を画面に出す形にする。permission.manage を持つロールが無くなる表（422）には直し方を添える */
export function describeSaveError(error: unknown): ErrorView {
  const view = toErrorView(error);
  if (error instanceof ApiError && error.code === "NO_PERMISSION_MANAGER") {
    return { message: view.message, details: [NO_PERMISSION_MANAGER_HINT] };
  }
  return view;
}

/** 表を置き換えの入力にする。3 章のロールの順、各ロールの権限は 9.1 の順（一覧に無いものは後ろ） */
export function toRoleGrants(table: GrantTable): RoleGrants[] {
  const order = PROJECT_PERMISSIONS.map((permission) => permission.code);
  const rank = (code: string) => {
    const index = order.indexOf(code);
    return index === -1 ? order.length : index;
  };
  return ROLES.map((role) => role.code).map((role) => ({
    role,
    permissions: [...(table[role] ?? [])].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b)),
  }));
}

export default function RolePermissionsSection({
  projectId,
  canEdit,
  onChanged,
}: {
  projectId: number;
  canEdit: boolean;
  /** 表を変えた後に呼ぶ（自分の権限が変わると、画面の出し分けも変わる） */
  onChanged: () => void;
}) {
  const [table, setTable] = useState<GrantTable | null>(null);
  const [loadError, setLoadError] = useState<ErrorView | null>(null);
  const [error, setError] = useState<ErrorView | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getRolePermissions(projectId).then(
      (value) => {
        if (!cancelled) {
          setTable(tableOf(value));
        }
      },
      (e: unknown) => {
        if (!cancelled) {
          setLoadError(toErrorView(e));
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  function toggle(role: string, permission: string, checked: boolean) {
    setTable((current) => {
      if (current === null) {
        return current;
      }
      const granted = current[role] ?? [];
      const next = checked
        ? granted.includes(permission)
          ? granted
          : [...granted, permission]
        : granted.filter((p) => p !== permission);
      return { ...current, [role]: next };
    });
  }

  async function handleSave() {
    if (table === null) {
      return;
    }
    setNotice(null);
    setError(null);
    setSubmitting(true);
    try {
      const saved = await replaceRolePermissions(projectId, toRoleGrants(table));
      setTable(tableOf(saved));
      setNotice("ロール×権限の表を保存しました");
      onChanged();
    } catch (e: unknown) {
      setError(describeSaveError(e));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section aria-labelledby="project-permissions-heading">
      <h3 id="project-permissions-heading">権限</h3>
      <ErrorAlert error={loadError} />
      {table !== null && (
        <>
          <table aria-label="ロール×権限の表">
            <thead>
              <tr>
                <th scope="col">権限</th>
                {ROLES.map((role) => (
                  <th scope="col" key={role.code}>
                    {role.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {PROJECT_PERMISSIONS.map((permission) => (
                <tr key={permission.code}>
                  <th scope="row">
                    {permission.label}（{permission.code}）
                  </th>
                  {ROLES.map((role) => (
                    <td key={role.code}>
                      <input
                        type="checkbox"
                        aria-label={`${role.label}：${permission.code}`}
                        checked={(table[role.code] ?? []).includes(permission.code)}
                        disabled={!canEdit || submitting}
                        onChange={(e) => toggle(role.code, permission.code, e.target.checked)}
                      />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {notice !== null && <p role="status">{notice}</p>}
          <ErrorAlert error={error} />
          {canEdit && (
            <button type="button" disabled={submitting} onClick={() => void handleSave()}>
              権限の表を保存する
            </button>
          )}
        </>
      )}
    </section>
  );
}
