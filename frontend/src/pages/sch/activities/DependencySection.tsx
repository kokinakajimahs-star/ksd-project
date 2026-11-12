// UI-SCH-001 の依存関係（SCH-04。API-SCH-006・007）。終了→開始（FS）の依存を足す・消す。
// 循環する依存はサーバが 422（SCHEDULE_CYCLE）で拒む。その理由をそのまま出す
import { useState, type FormEvent } from "react";

import { toErrorView, type ErrorView } from "../../../api/errors";
import { createDependency, deleteDependency, type Activity, type Dependency } from "../../../api/sch";
import ErrorAlert from "../../../components/ErrorAlert";

export interface DependencySectionProps {
  projectId: number;
  activities: Activity[];
  dependencies: Dependency[];
  canEdit: boolean;
  onChanged: () => void;
}

function nameOf(byId: Map<number, Activity>, id: number): string {
  const activity = byId.get(id);
  return activity === undefined ? `活動 ${id}` : `${activity.id} ${activity.name}`;
}

export default function DependencySection({
  projectId,
  activities,
  dependencies,
  canEdit,
  onChanged,
}: DependencySectionProps) {
  const [predecessorId, setPredecessorId] = useState("");
  const [successorId, setSuccessorId] = useState("");
  const [removing, setRemoving] = useState<Dependency | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<ErrorView | null>(null);
  const byId = new Map(activities.map((a) => [a.id, a]));

  async function handleAdd(event: FormEvent) {
    event.preventDefault();
    setError(null);
    if (predecessorId === "" || successorId === "") {
      setError({ message: "先行と後続の活動を選んでください", details: [] });
      return;
    }
    if (predecessorId === successorId) {
      setError({ message: "先行と後続に同じ活動は選べません", details: [] });
      return;
    }
    setSubmitting(true);
    try {
      await createDependency(projectId, { predecessor_id: Number(predecessorId), successor_id: Number(successorId) });
      setPredecessorId("");
      setSuccessorId("");
      onChanged();
    } catch (e) {
      setError(toErrorView(e));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleRemove(dependency: Dependency) {
    setError(null);
    setSubmitting(true);
    try {
      await deleteDependency(projectId, dependency.id);
      setRemoving(null);
      onChanged();
    } catch (e) {
      setError(toErrorView(e));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section aria-labelledby="dependency-heading" className="dependency-section">
      <h3 id="dependency-heading">依存関係（終了→開始）</h3>
      {dependencies.length === 0 ? (
        <p>依存はまだありません</p>
      ) : (
        <table aria-label="依存の一覧">
          <thead>
            <tr>
              <th scope="col">先行</th>
              <th scope="col">後続</th>
              <th scope="col">種類</th>
              {canEdit && <th scope="col">操作</th>}
            </tr>
          </thead>
          <tbody>
            {dependencies.map((d) => (
              <tr key={d.id}>
                <td>{nameOf(byId, d.predecessor_id)}</td>
                <td>{nameOf(byId, d.successor_id)}</td>
                <td>{d.dep_type}</td>
                {canEdit && (
                  <td>
                    <button
                      type="button"
                      aria-label={`${d.predecessor_id}→${d.successor_id} の依存を消す`}
                      onClick={() => setRemoving(d)}
                      disabled={submitting}
                    >
                      消す
                    </button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {canEdit && removing !== null && (
        <div role="group" aria-label="依存を消すことの確かめ">
          <p>
            {nameOf(byId, removing.predecessor_id)} → {nameOf(byId, removing.successor_id)} の依存を消します。日程は計算し直されます。
          </p>
          <button type="button" onClick={() => void handleRemove(removing)} disabled={submitting}>
            消す
          </button>
          <button type="button" onClick={() => setRemoving(null)}>
            やめる
          </button>
        </div>
      )}

      {canEdit && (
        <form aria-label="依存を足す" onSubmit={(e) => void handleAdd(e)}>
          <label>
            先行
            <select value={predecessorId} onChange={(e) => setPredecessorId(e.target.value)}>
              <option value="">（選ぶ）</option>
              {activities.map((a) => (
                <option key={a.id} value={a.id}>
                  {`${a.id} ${a.name}`}
                </option>
              ))}
            </select>
          </label>
          <label>
            後続
            <select value={successorId} onChange={(e) => setSuccessorId(e.target.value)}>
              <option value="">（選ぶ）</option>
              {activities.map((a) => (
                <option key={a.id} value={a.id}>
                  {`${a.id} ${a.name}`}
                </option>
              ))}
            </select>
          </label>
          <button type="submit" disabled={submitting}>
            依存を足す
          </button>
        </form>
      )}
      <ErrorAlert error={error} />
    </section>
  );
}
