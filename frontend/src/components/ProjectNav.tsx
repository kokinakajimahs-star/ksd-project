// プロジェクトの左メニュー（A-53。画面全体の枠）。/projects/:projectId/* で、プロジェクトの中の画面を縦に並べ、今の画面を
// aria-current で示す。行き先と出す条件は UI-PRJ-001 の遷移（7.2。DESTINATIONS）と同じで、権限の要る画面はその権限が
// 無ければ出さない（API-CMN-003 の自分の権限で判定する。読めなければ権限の要らない行き先だけを出す）。印刷には出さない
import { useEffect, useState } from "react";
import { NavLink } from "react-router-dom";

import { getMe, hasProjectPermission, type Me } from "../api/cmn";
import { DESTINATIONS } from "../pages/prj/ProjectListPage";

export default function ProjectNav({ projectId, userId }: { projectId: number; userId: number }) {
  const [me, setMe] = useState<Me | null>(null);

  useEffect(() => {
    let cancelled = false;
    setMe(null);
    getMe().then(
      (result) => {
        if (!cancelled) {
          setMe(result);
        }
      },
      () => {
        // 権限を読めない間は、権限の要る行き先を出さない（画面そのものはサーバでも判定する）
      },
    );
    return () => {
      cancelled = true;
    };
  }, [projectId, userId]);

  const visible = DESTINATIONS.filter(
    (d) =>
      d.anyOf === undefined ||
      (me !== null && d.anyOf.some((permission) => hasProjectPermission(me, projectId, permission))),
  );

  return (
    <nav aria-label="プロジェクトのメニュー" className="project-nav no-print">
      <ul>
        {visible.map((d) => (
          <li key={d.label}>
            <NavLink to={d.path(projectId)}>{d.label}</NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}
