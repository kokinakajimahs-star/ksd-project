// プロジェクトのメンバー（API-PRJ-009。権限は project.view）。作成者・担当・変更者の名前を出し、担当を選ぶのに使う
import { useEffect, useMemo, useState } from "react";

import { listMembers, type Member } from "../../api/prj";
import { LIST_LIMIT_MAX } from "./labels";

export interface ProjectMembers {
  members: Member[];
  /** 利用者の ID → 表示名 */
  names: ReadonlyMap<number, string>;
}

/** メンバーを先頭の 1000 件まで読む。読めない（接続できない）ときは空にする（名前は #ID で出す） */
export function useProjectMembers(projectId: number | null): ProjectMembers {
  const [members, setMembers] = useState<Member[]>([]);

  useEffect(() => {
    if (projectId === null) {
      return;
    }
    let cancelled = false;
    listMembers(projectId, { limit: LIST_LIMIT_MAX, offset: 0 }).then(
      (page) => {
        if (!cancelled) {
          setMembers(page.items);
        }
      },
      () => {
        if (!cancelled) {
          setMembers([]);
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  const names = useMemo(() => new Map(members.map((m) => [m.user_id, m.display_name])), [members]);
  return { members, names };
}
