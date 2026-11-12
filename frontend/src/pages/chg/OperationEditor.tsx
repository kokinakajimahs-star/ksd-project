// 変更内容（5.9 の操作の配列）の編集。操作は足した順に当たる（5.9）
import { useState } from "react";

import type { ChangeOperation } from "../../api/chg";
import type { ElementKind } from "../../api/wbs";
import { ELEMENT_KIND_LABELS, ELEMENT_KINDS, WBS_CODE_MAX } from "./labels";
import {
  buildOperation,
  describeOperation,
  emptyDraft,
  OPERATION_LABELS,
  OPERATION_NAMES,
  type OperationDraft,
  type OperationName,
} from "./operations";

export interface OperationEditorProps {
  operations: ChangeOperation[];
  onChange: (operations: ChangeOperation[]) => void;
  disabled?: boolean;
}

export default function OperationEditor({ operations, onChange, disabled = false }: OperationEditorProps) {
  const [draft, setDraft] = useState<OperationDraft>(() => emptyDraft());
  const [message, setMessage] = useState<string | null>(null);

  function set<K extends keyof OperationDraft>(key: K, value: OperationDraft[K]) {
    setDraft((d) => ({ ...d, [key]: value }));
  }

  function handleAdd() {
    const result = buildOperation(draft);
    if (!result.ok) {
      setMessage(result.message);
      return;
    }
    setMessage(null);
    onChange([...operations, result.operation]);
    setDraft(emptyDraft(draft.op));
  }

  function handleRemove(index: number) {
    onChange(operations.filter((_, i) => i !== index));
  }

  const op = draft.op;
  const isUpdate = op === "update_element" || op === "update_activity";
  const kindSelect = (
    <label>
      区分{op === "update_element" && "（空なら変えない）"}
      <select value={draft.kind} onChange={(e) => set("kind", e.target.value as ElementKind | "")}>
        <option value="">{op === "add_element" ? "選んでください" : "変えない"}</option>
        {ELEMENT_KINDS.map((k) => (
          <option key={k} value={k}>
            {ELEMENT_KIND_LABELS[k]}
          </option>
        ))}
      </select>
    </label>
  );
  const nameInput = (
    <label>
      名称{isUpdate && "（空なら変えない）"}
      <input value={draft.name} onChange={(e) => set("name", e.target.value)} />
    </label>
  );
  const refInput = (
    <label>
      仮の名前（同じ変更要求の中で後の操作から引く名前）
      <input value={draft.ref} onChange={(e) => set("ref", e.target.value)} />
    </label>
  );
  const codeInput = (
    <label>
      対象の WBS ID
      <input value={draft.code} maxLength={WBS_CODE_MAX} onChange={(e) => set("code", e.target.value)} />
    </label>
  );
  const activityIdInput = (
    <label>
      対象の活動の ID
      <input inputMode="numeric" value={draft.activityId} onChange={(e) => set("activityId", e.target.value)} />
    </label>
  );
  const activityFields = (
    <>
      <label>
        期間（稼働日）{isUpdate && "（空なら変えない）"}
        <input inputMode="numeric" value={draft.durationDays} onChange={(e) => set("durationDays", e.target.value)} />
      </label>
      <label>
        見積（人時）{isUpdate ? "（空なら変えない）" : "（空なら未見積）"}
        <input inputMode="decimal" value={draft.estimateHours} onChange={(e) => set("estimateHours", e.target.value)} />
      </label>
      <label>
        希望開始日（任意）
        <input type="date" value={draft.requestedStart} onChange={(e) => set("requestedStart", e.target.value)} />
      </label>
    </>
  );

  return (
    <fieldset className="operation-editor" disabled={disabled}>
      <legend>変更内容（上から順に当てる）</legend>
      {operations.length === 0 ? (
        <p>操作はまだありません</p>
      ) : (
        <ol aria-label="変更内容の操作">
          {operations.map((operation, index) => (
            <li key={index}>
              <span>{describeOperation(operation)}</span>{" "}
              <button
                type="button"
                aria-label={`操作 ${index + 1} を外す`}
                onClick={() => handleRemove(index)}
              >
                外す
              </button>
            </li>
          ))}
        </ol>
      )}

      <div className="operation-draft" role="group" aria-label="操作を組み立てる">
        <label>
          操作
          <select
            value={op}
            onChange={(e) => {
              setMessage(null);
              setDraft(emptyDraft(e.target.value as OperationName));
            }}
          >
            {OPERATION_NAMES.map((name) => (
              <option key={name} value={name}>
                {OPERATION_LABELS[name]}
              </option>
            ))}
          </select>
        </label>

        {op === "add_element" && (
          <>
            <label>
              親の WBS ID（空ならルート）
              <input
                value={draft.parentCode}
                maxLength={WBS_CODE_MAX}
                onChange={(e) => set("parentCode", e.target.value)}
              />
            </label>
            {kindSelect}
            {nameInput}
            {refInput}
          </>
        )}
        {op === "update_element" && (
          <>
            {codeInput}
            {nameInput}
            {kindSelect}
            <label>
              自分の見積（人時。空なら変えない）
              <input
                inputMode="decimal"
                value={draft.ownEstimateHours}
                onChange={(e) => set("ownEstimateHours", e.target.value)}
              />
            </label>
            <label>
              責任者の利用者 ID（空なら変えない）
              <input
                inputMode="numeric"
                value={draft.responsibleUserId}
                onChange={(e) => set("responsibleUserId", e.target.value)}
              />
            </label>
          </>
        )}
        {op === "remove_element" && codeInput}
        {op === "add_activity" && (
          <>
            <label>
              置き場の指定
              <select value={draft.wpBy} onChange={(e) => set("wpBy", e.target.value as "code" | "ref")}>
                <option value="code">既にある WP の WBS ID</option>
                <option value="ref">この変更要求で足す WP の仮の名前</option>
              </select>
            </label>
            {draft.wpBy === "code" ? (
              <label>
                置き場の WP の WBS ID
                <input value={draft.wpCode} maxLength={WBS_CODE_MAX} onChange={(e) => set("wpCode", e.target.value)} />
              </label>
            ) : (
              <label>
                置き場の WP の仮の名前
                <input value={draft.wpRef} onChange={(e) => set("wpRef", e.target.value)} />
              </label>
            )}
            {nameInput}
            {activityFields}
            {refInput}
          </>
        )}
        {op === "update_activity" && (
          <>
            {activityIdInput}
            {nameInput}
            {activityFields}
          </>
        )}
        {op === "remove_activity" && activityIdInput}
        {(op === "add_dependency" || op === "remove_dependency") && (
          <>
            <p className="note">
              活動は ID（数字）か、この変更要求で足す活動の仮の名前で指す（数字だけの入力は ID とみなす。OQ-38）。
            </p>
            <label>
              先行
              <input value={draft.predecessor} onChange={(e) => set("predecessor", e.target.value)} />
            </label>
            <label>
              後続
              <input value={draft.successor} onChange={(e) => set("successor", e.target.value)} />
            </label>
          </>
        )}

        {message !== null && <p role="alert">{message}</p>}
        <button type="button" onClick={handleAdd}>
          操作を足す
        </button>
      </div>
    </fieldset>
  );
}
