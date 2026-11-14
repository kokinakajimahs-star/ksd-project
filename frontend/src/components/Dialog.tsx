// 画面の上に重ねるダイアログ（CR-027。ガントの表示の設定・活動の詳細と進捗）。
// 開くと本文の最初の入力（無ければダイアログ）へ入力の的を移し、Tab は中で回す。Esc・「閉じる」・外側のクリックで閉じ、
// 閉じたら開く前に的があった所（図・ボタン）へ戻す。中の部品が Esc を自分で使ったとき（行の中の入力の取り消しなど）・
// 文字の変換中の Esc では閉じない。開いている間は的を外へ出さない：中の部品が作り直されて的のあった要素が消えたとき
// （保存の後の読み直し）・外へ的が移ったときはダイアログへ戻し、的がどこにも無いときの Esc・Tab も受ける
import { useEffect, useId, useRef, type KeyboardEvent, type ReactNode } from "react";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export interface DialogProps {
  title: string;
  onClose: () => void;
  children: ReactNode;
  /** 本体の要素に付ける class（幅などを変える） */
  className?: string;
}

export default function Dialog({ title, onClose, children, className }: DialogProps) {
  const titleId = useId();
  const ref = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  });

  // 開いたときに的を中へ移し、開いている間は中に留め、閉じたときに元の所へ戻す
  useEffect(() => {
    const before = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const node = ref.current;
    if (node === null) {
      return;
    }
    const first = node.querySelector(".dialog-body")?.querySelector<HTMLElement>(FOCUSABLE) ?? null;
    (first ?? node).focus();
    const inside = (target: EventTarget | null) => target instanceof Node && node.contains(target);
    const refocus = () => node.focus({ preventScroll: true });
    // 的のあった要素が消えた・押せなくなった（保存の後に入力欄を作り直す・保存中のボタン）
    const observer = new MutationObserver(() => {
      if (!inside(document.activeElement)) {
        refocus();
      }
    });
    observer.observe(node, { childList: true, subtree: true, attributes: true, attributeFilter: ["disabled"] });
    // 背景の画面へ的が移った
    function onFocusIn(event: FocusEvent) {
      if (!inside(event.target)) {
        refocus();
      }
    }
    // 的がダイアログの外（どこにも無い）ときのキー
    function onDocumentKeyDown(event: globalThis.KeyboardEvent) {
      if (inside(event.target) || event.defaultPrevented) {
        return;
      }
      if (event.key === "Escape" && !event.isComposing) {
        event.preventDefault();
        onCloseRef.current();
      } else if (event.key === "Tab") {
        event.preventDefault();
        refocus();
      }
    }
    document.addEventListener("focusin", onFocusIn);
    document.addEventListener("keydown", onDocumentKeyDown);
    return () => {
      observer.disconnect();
      document.removeEventListener("focusin", onFocusIn);
      document.removeEventListener("keydown", onDocumentKeyDown);
      if (before !== null && before.isConnected) {
        before.focus({ preventScroll: true });
      }
    };
  }, []);

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      // 文字の変換中の Esc は変換の取り消し（閉じない）
      if (!event.defaultPrevented && !event.nativeEvent.isComposing) {
        event.preventDefault();
        event.stopPropagation();
        onClose();
      }
      return;
    }
    if (event.key !== "Tab" || ref.current === null) {
      return;
    }
    const items = Array.from(ref.current.querySelectorAll<HTMLElement>(FOCUSABLE));
    if (items.length === 0) {
      event.preventDefault();
      return;
    }
    const first = items[0]!;
    const last = items[items.length - 1]!;
    if (event.shiftKey && (document.activeElement === first || document.activeElement === ref.current)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  return (
    <div
      className="dialog-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) {
          onClose();
        }
      }}
    >
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className={className === undefined ? "dialog" : `dialog ${className}`}
        onKeyDown={onKeyDown}
      >
        <div className="dialog-header">
          <h3 id={titleId}>{title}</h3>
          <button type="button" onClick={() => onClose()}>
            閉じる
          </button>
        </div>
        <div className="dialog-body">{children}</div>
      </div>
    </div>
  );
}
