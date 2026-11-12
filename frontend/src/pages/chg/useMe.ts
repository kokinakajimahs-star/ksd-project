// 自分の権限（GET /api/v1/me）。権限の無い操作のボタンを出さないために読む（7.2）。正の判定はサーバが行う
import { useEffect, useState } from "react";

import { getMe, type Me } from "../../api/cmn";
import { toErrorView, type ErrorView } from "../../api/errors";

export function useMe(projectId: number | null): { me: Me | null; meError: ErrorView | null } {
  const [me, setMe] = useState<Me | null>(null);
  const [meError, setMeError] = useState<ErrorView | null>(null);

  useEffect(() => {
    if (projectId === null) {
      return;
    }
    let cancelled = false;
    getMe().then(
      (result) => {
        if (!cancelled) {
          setMe(result);
        }
      },
      (e: unknown) => {
        if (!cancelled) {
          setMeError(toErrorView(e));
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  return { me, meError };
}
