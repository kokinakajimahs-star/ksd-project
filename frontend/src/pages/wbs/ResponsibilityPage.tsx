// UI-WBS-004 責任分担表（設計書 7.1 のルート /projects/:projectId/responsibility。WBS-10）。
// WP × メンバーの表（責任者・担当・割当工数。API-WBS-011 の json）と、責任者の無い WP の一覧を出し、
// 同じ表を CSV（API-WBS-011 の format=csv。UTF-8・BOM 付き）で保存する。責任者は WBS の画面（UI-WBS-001）で割り当てる
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { toErrorView, type ErrorView } from "../../api/errors";
import { downloadResponsibilityMatrixCsv, getResponsibilityMatrix, type MatrixCell, type ResponsibilityMatrix } from "../../api/wbs";
import ErrorAlert from "../../components/ErrorAlert";
import { saveBlob } from "../../lib/download";
import { parseProjectId } from "../prj/ProjectSettingsPage";
import { hoursText } from "./labels";

/** マスの表示：「責任者」「担当 <人時>」「責任者・担当 <人時>」か空（API-WBS-011 の CSV のマスと同じ言い方） */
export function cellText(cell: MatrixCell): string {
  const hours = `${Math.round(cell.planned_hours * 100) / 100} 人時`;
  if (cell.is_responsible && cell.is_assigned) {
    return `責任者・担当 ${hours}`;
  }
  if (cell.is_assigned) {
    return `担当 ${hours}`;
  }
  return cell.is_responsible ? "責任者" : "";
}

export default function ResponsibilityPage() {
  const params = useParams<{ projectId: string }>();
  const projectId = parseProjectId(params.projectId);
  const [matrix, setMatrix] = useState<ResponsibilityMatrix | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState<ErrorView | null>(null);

  useEffect(() => {
    if (projectId === null) {
      return;
    }
    let cancelled = false;
    getResponsibilityMatrix(projectId).then(
      (result) => {
        if (!cancelled) {
          setMatrix(result);
        }
      },
      (e: unknown) => {
        if (!cancelled) {
          setError(toErrorView(e));
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  if (projectId === null) {
    return (
      <section data-screen-id="UI-WBS-004" className="responsibility-page">
        <h2>責任分担表</h2>
        <p role="alert">プロジェクトが見つかりません</p>
        <p>
          <Link to="/projects">プロジェクト一覧へ</Link>
        </p>
      </section>
    );
  }

  async function handleCsv() {
    if (projectId === null) {
      return;
    }
    setError(null);
    setDownloading(true);
    try {
      const file = await downloadResponsibilityMatrixCsv(projectId);
      saveBlob(file.blob, file.filename ?? `responsibility-matrix-${projectId}.csv`);
    } catch (e) {
      setError(toErrorView(e));
    } finally {
      setDownloading(false);
    }
  }

  const userNames = new Map(matrix?.users.map((u) => [u.user_id, u.display_name]) ?? []);

  return (
    <section data-screen-id="UI-WBS-004" className="responsibility-page">
      <h2>責任分担表</h2>
      <nav aria-label="行き先" className="page-links">
        <Link to={`/projects/${projectId}/wbs`}>WBS へ戻る（責任者を割り当てる）</Link>
      </nav>
      <ErrorAlert error={error} />
      <button type="button" onClick={() => void handleCsv()} disabled={downloading}>
        CSV で保存する
      </button>
      {matrix?.restricted === true && <p className="note">自分の割当の経路のワークパッケージだけを表示している</p>}

      {matrix !== null && matrix.rows.length === 0 && <p>ワークパッケージはまだありません</p>}
      {matrix !== null && matrix.rows.length > 0 && (
        <table aria-label="責任分担表">
          <thead>
            <tr>
              <th scope="col">WBS ID</th>
              <th scope="col">ワークパッケージ</th>
              <th scope="col">責任者</th>
              <th scope="col">担当チーム</th>
              <th scope="col">割当工数の合計</th>
              {matrix.users.map((u) => (
                <th key={u.user_id} scope="col">
                  {u.display_name}
                  {!u.is_member && "（メンバー外）"}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {matrix.rows.map((row) => (
              <tr key={row.element_id}>
                <td>
                  <Link to={`/projects/${projectId}/wbs/${row.element_id}`}>{row.wbs_code}</Link>
                </td>
                <td>{row.name}</td>
                <td>
                  {row.responsible_user_id === null
                    ? "未設定"
                    : (userNames.get(row.responsible_user_id) ?? `#${row.responsible_user_id}`)}
                </td>
                <td>{row.responsible_team ?? "—"}</td>
                <td>{hoursText(row.planned_hours)}</td>
                {row.cells.map((cell) => (
                  <td key={cell.user_id} data-responsible={cell.is_responsible ? "true" : undefined}>
                    {cellText(cell)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {matrix !== null && (
        <section aria-labelledby="without-responsible-heading">
          <h3 id="without-responsible-heading">責任者の無いワークパッケージ</h3>
          {matrix.without_responsible.length === 0 && <p>責任者の無いワークパッケージはありません</p>}
          {matrix.without_responsible.length > 0 && (
            <ul aria-label="責任者の無いワークパッケージ">
              {matrix.without_responsible.map((w) => (
                <li key={w.element_id}>
                  <Link to={`/projects/${projectId}/wbs/${w.element_id}`}>{w.wbs_code}</Link> {w.name}
                  {w.responsible_team !== null && `（担当チーム：${w.responsible_team}）`}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
    </section>
  );
}
