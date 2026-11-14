// ガントの編集の右クリックのメニュー（設計書 7.3 の「編集」。CR-025）。右クリック・Shift+F10・メニューキーで開き、
// 上下の矢印・Home・End で項目を選んで Enter（Space）で行う。Esc・Tab・外のクリック・図や画面を送ったときに閉じる
// （Esc・Tab は入力の的を図へ戻す）
import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";

import type { MenuEntry, OpenMenu } from "./useGanttEditor";

/** 画面の端から離す幅 */
const MARGIN = 4;

export function GanttMenu({
  menu,
  onChoose,
  onClose,
}: {
  menu: OpenMenu;
  onChoose: (entry: MenuEntry) => void;
  onClose: (restoreFocus: boolean) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState({ left: menu.x, top: menu.y });

  // 画面の外へはみ出すなら内側へ寄せる
  useLayoutEffect(() => {
    const element = ref.current;
    if (element === null) {
      return;
    }
    const { width, height } = element.getBoundingClientRect();
    const left = Math.max(MARGIN, Math.min(menu.x, window.innerWidth - width - MARGIN));
    const top = Math.max(MARGIN, Math.min(menu.y, window.innerHeight - height - MARGIN));
    setPosition({ left, top });
  }, [menu.x, menu.y]);

  // 開いたら最初の項目へ（行える項目が無ければメニューそのものへ。Esc で閉じられるように）
  useEffect(() => {
    (items()[0] ?? ref.current)?.focus();
  }, [menu.x, menu.y]);

  // 外を押したら閉じる（押した先が的を受ける）
  const close = useRef(onClose);
  useEffect(() => {
    close.current = onClose;
  });
  useEffect(() => {
    function onDown(event: MouseEvent) {
      if (ref.current !== null && event.target instanceof Node && !ref.current.contains(event.target)) {
        close.current(false);
      }
    }
    function onResize() {
      close.current(false);
    }
    // 図や画面を送ったら閉じる（メニューは画面に固定なので、行から離れて残さない）。メニューの中を送るのは除く
    function onScroll(event: Event) {
      if (!(event.target instanceof Node && ref.current?.contains(event.target))) {
        close.current(false);
      }
    }
    document.addEventListener("mousedown", onDown, true);
    document.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onResize);
    return () => {
      document.removeEventListener("mousedown", onDown, true);
      document.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onResize);
    };
  }, []);

  function items(): HTMLElement[] {
    return [...(ref.current?.querySelectorAll<HTMLElement>('[role^="menuitem"]:not([aria-disabled="true"])') ?? [])];
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const list = items();
    const index = list.findIndex((e) => e === document.activeElement);
    const go = (next: number) => {
      event.preventDefault();
      list[(next + list.length) % list.length]?.focus();
    };
    switch (event.key) {
      case "ArrowDown":
        go(index + 1);
        return;
      case "ArrowUp":
        go(index < 0 ? list.length - 1 : index - 1);
        return;
      case "Home":
        go(0);
        return;
      case "End":
        go(list.length - 1);
        return;
      case " ":
        // ボタンの項目は Space で押せる。リンクの項目も同じにする
        if (document.activeElement instanceof HTMLAnchorElement) {
          event.preventDefault();
          document.activeElement.click();
        }
        return;
      case "Escape":
      case "Tab":
        event.preventDefault();
        event.stopPropagation();
        onClose(true);
        return;
    }
  }

  return createPortal(
    <div
      ref={ref}
      role="menu"
      aria-label={menu.label}
      tabIndex={-1}
      className="gantt-menu"
      style={{ left: position.left, top: position.top }}
      onKeyDown={onKeyDown}
      onContextMenu={(e) => e.preventDefault()}
    >
      {menu.entries.length === 0 && (
        <div role="menuitem" aria-disabled="true" className="gantt-menu-item">
          ここで行える操作の権限がありません
        </div>
      )}
      {menu.entries.map((entry) => {
        switch (entry.kind) {
          case "separator":
            return <div key={entry.id} role="separator" className="gantt-menu-separator" />;
          case "link":
            return (
              <Link key={entry.id} role="menuitem" className="gantt-menu-item" to={entry.to} onClick={() => onChoose(entry)}>
                {entry.label}
              </Link>
            );
          case "radio":
            return (
              <button
                key={entry.id}
                type="button"
                role="menuitemradio"
                aria-checked={entry.checked}
                className="gantt-menu-item"
                onClick={() => onChoose(entry)}
              >
                <span aria-hidden="true">{entry.checked ? "●" : "○"}</span>
                {entry.label}
              </button>
            );
          case "item":
            return (
              <button
                key={entry.id}
                type="button"
                role="menuitem"
                className="gantt-menu-item"
                onClick={() => onChoose(entry)}
              >
                {entry.label}
                {entry.shortcut !== undefined && <kbd aria-hidden="true">{entry.shortcut}</kbd>}
              </button>
            );
        }
      })}
    </div>,
    document.body,
  );
}
