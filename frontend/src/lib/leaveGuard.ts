// 保存していない編集があるときに、画面を離れる前に確かめる（docs/decisions/ASSUMPTIONS.md A-49 の 13・OQ-42 (21) の c）。
// ルータは BrowserRouter（data router でない）なので React Router の useBlocker は使えない。代わりに
// リンク（<a href>）のクリックを document の捕捉段階で確かめ、ブラウザを閉じる・再読み込みは beforeunload で確かめる。
// 限界：ブラウザの戻る・進む、コードからの移動（navigate()。例：ログアウト）は確かめられない
import { useEffect } from "react";

/** 保存していない編集を捨てて移るかを確かめる文言（window.confirm） */
export const UNSAVED_EDITS_MESSAGE = "保存していない編集があります。移ると編集は消えます。よろしいですか";

/** このクリックで今のタブの画面を離れるリンク。新しいタブ・保存（download）・修飾キー付きのクリックは離れないので null */
function leavingAnchor(event: MouseEvent): HTMLAnchorElement | null {
  if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
    return null;
  }
  const anchor = event.target instanceof Element ? event.target.closest("a[href]") : null;
  if (!(anchor instanceof HTMLAnchorElement)) {
    return null;
  }
  if ((anchor.target !== "" && anchor.target !== "_self") || anchor.hasAttribute("download")) {
    return null;
  }
  return anchor;
}

/**
 * active の間、リンクで画面を離れる前に message で確かめ（やめればリンクを辿らない）、ブラウザを閉じる・再読み込みは
 * beforeunload でブラウザの確かめを出す。頁送りなど画面の中の操作は、呼ぶ側で confirmLeave を使って確かめる
 */
export function useLeaveGuard(active: boolean, message: string = UNSAVED_EDITS_MESSAGE): void {
  useEffect(() => {
    if (!active) {
      return;
    }
    function handleClick(event: MouseEvent) {
      if (leavingAnchor(event) !== null && !window.confirm(message)) {
        // React Router の Link は既定の動作が止められたクリックでは移らない
        event.preventDefault();
      }
    }
    function handleBeforeUnload(event: BeforeUnloadEvent) {
      event.preventDefault();
      // 古いブラウザは returnValue に値を入れたときだけ確かめを出す
      event.returnValue = "";
    }
    document.addEventListener("click", handleClick, true);
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => {
      document.removeEventListener("click", handleClick, true);
      window.removeEventListener("beforeunload", handleBeforeUnload);
    };
  }, [active, message]);
}

/** 画面の中で編集を捨てる操作（頁送りなど）の前に確かめる。編集が無ければ確かめずに true */
export function confirmLeave(active: boolean, message: string = UNSAVED_EDITS_MESSAGE): boolean {
  return !active || window.confirm(message);
}
