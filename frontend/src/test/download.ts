// 試験用：ブラウザの保存（URL.createObjectURL と <a download> の click）を差し替え、保存したファイルを記録する。
// jsdom は URL.createObjectURL を持たず、<a> の click で遷移しようとするため
import { vi } from "vitest";

export interface SavedFile {
  blob: Blob;
  filename: string;
  /** 保存の後に一時的な URL を解放したか */
  revoked: boolean;
}

export function stubDownloads() {
  const saved: SavedFile[] = [];
  const urls = new Map<string, Blob>();
  const had = { create: "createObjectURL" in URL, revoke: "revokeObjectURL" in URL };
  const original = { create: URL.createObjectURL, revoke: URL.revokeObjectURL };

  Object.defineProperty(URL, "createObjectURL", {
    configurable: true,
    writable: true,
    value: (blob: Blob) => {
      const url = `blob:test-${urls.size + 1}`;
      urls.set(url, blob);
      return url;
    },
  });
  Object.defineProperty(URL, "revokeObjectURL", {
    configurable: true,
    writable: true,
    value: (url: string) => {
      for (const file of saved) {
        if (urls.get(url) === file.blob) {
          file.revoked = true;
        }
      }
      urls.delete(url);
    },
  });
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
    const blob = urls.get(this.href);
    if (blob !== undefined && this.download !== "") {
      saved.push({ blob, filename: this.download, revoked: false });
    }
  });

  function restore() {
    click.mockRestore();
    for (const [name, existed, value] of [
      ["createObjectURL", had.create, original.create],
      ["revokeObjectURL", had.revoke, original.revoke],
    ] as const) {
      if (existed) {
        Object.defineProperty(URL, name, { configurable: true, writable: true, value });
      } else {
        Reflect.deleteProperty(URL, name);
      }
    }
  }

  return { saved, restore };
}

/** Blob を文字列で読む（jsdom の Blob は text() を持たないため FileReader で読む） */
export function readBlobText(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error ?? new Error("Blob を読めません"));
    reader.readAsText(blob);
  });
}
