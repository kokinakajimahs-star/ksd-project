// 入力欄の値の扱い

/** 空欄（空白だけを含む）は「値なし」（null）として送る */
export function optionalText(value: string): string | null {
  return value.trim() === "" ? null : value;
}
