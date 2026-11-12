// 影響分析の結果（5.9：終了日・マイルストーン・クリティカルパス・総工数・要素の差。CHG-02）
import type { ImpactActivity, ImpactBody, ImpactElement } from "../../api/chg";
import { elementKindLabel, shiftLabel, valueText } from "./labels";

function activityText(a: ImpactActivity): string {
  if (a.activity_id !== null) {
    return `${a.name}（#${a.activity_id}）`;
  }
  return a.ref !== null ? `${a.name}（新規 ${a.ref}）` : a.name;
}

function elementText(e: ImpactElement): string {
  const code = e.wbs_code ?? (e.ref !== null ? `新規 ${e.ref}` : "—");
  return `${code} ${e.name}（${elementKindLabel(e.kind)}）`;
}

/** 総工数の差（後 − 前）。読めない値は null */
export function estimateDelta(before: string | number | null, after: string | number | null): number | null {
  if (before === null || after === null) {
    return null;
  }
  const b = Number(before);
  const a = Number(after);
  if (!Number.isFinite(b) || !Number.isFinite(a)) {
    return null;
  }
  return Math.round((a - b) * 100) / 100;
}

export default function ImpactView({ impact, title }: { impact: ImpactBody; title: string }) {
  const delta = estimateDelta(impact.total_estimate_hours.before, impact.total_estimate_hours.after);
  const { added, updated, removed } = impact.elements;
  return (
    <section aria-label={title} className="impact-view">
      <h4>{title}</h4>
      <dl>
        <dt>プロジェクトの終了日</dt>
        <dd>
          {valueText(impact.project_finish.before)} → {valueText(impact.project_finish.after)}（
          {shiftLabel(impact.project_finish.shift_days)}）
        </dd>
        <dt>総工数</dt>
        <dd>
          {valueText(impact.total_estimate_hours.before)} 人時 → {valueText(impact.total_estimate_hours.after)} 人時
          {delta !== null && `（${delta > 0 ? "+" : ""}${delta} 人時）`}
        </dd>
      </dl>

      <h5>ずれるマイルストーン</h5>
      {impact.milestones.length === 0 ? (
        <p>ずれるマイルストーンはありません</p>
      ) : (
        <table aria-label="ずれるマイルストーン">
          <thead>
            <tr>
              <th scope="col">マイルストーン</th>
              <th scope="col">前</th>
              <th scope="col">後</th>
              <th scope="col">ずれ</th>
            </tr>
          </thead>
          <tbody>
            {impact.milestones.map((m) => (
              <tr key={m.activity_id}>
                <td>{m.name}</td>
                <td>{m.before}</td>
                <td>{m.after}</td>
                <td>{shiftLabel(m.shift_days)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h5>クリティカルパス</h5>
      {impact.critical_path.added.length === 0 && impact.critical_path.removed.length === 0 ? (
        <p>クリティカルパスは変わりません</p>
      ) : (
        <ul aria-label="クリティカルパスの変化">
          {impact.critical_path.added.map((a) => (
            <li key={`add-${a.activity_id ?? a.ref ?? a.name}`}>加わる：{activityText(a)}</li>
          ))}
          {impact.critical_path.removed.map((a) => (
            <li key={`remove-${a.activity_id ?? a.ref ?? a.name}`}>外れる：{activityText(a)}</li>
          ))}
        </ul>
      )}

      <h5>WBS の要素</h5>
      {added.length === 0 && updated.length === 0 && removed.length === 0 ? (
        <p>要素は変わりません</p>
      ) : (
        <ul aria-label="要素の変化">
          {added.map((e) => (
            <li key={`add-${e.element_id ?? e.ref ?? e.name}`}>追加：{elementText(e)}</li>
          ))}
          {updated.map((u) => (
            <li key={`upd-${u.element_id}`}>
              変更：{u.wbs_code} {u.name}（
              {Object.entries(u.changes)
                .map(([key, change]) => `${key}: ${valueText(change.before)} → ${valueText(change.after)}`)
                .join("、")}
              ）
            </li>
          ))}
          {removed.map((e) => (
            <li key={`rm-${e.element_id ?? e.ref ?? e.name}`}>削除：{elementText(e)}</li>
          ))}
        </ul>
      )}
    </section>
  );
}
