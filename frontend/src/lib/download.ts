// サーバから受け取ったファイルを、ブラウザの保存（ダウンロード）に渡す
/** Blob を指定の名前で保存させる。一時的な URL は渡した後に解放する */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  try {
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.rel = "noopener";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } finally {
    URL.revokeObjectURL(url);
  }
}
