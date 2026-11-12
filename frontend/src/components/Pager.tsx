// 一覧の頁送り（設計書 6.1：一覧は limit・offset を受ける）

export interface PagerProps {
  total: number;
  limit: number;
  offset: number;
  onChange: (offset: number) => void;
}

export default function Pager({ total, limit, offset, onChange }: PagerProps) {
  if (total === 0) {
    return null;
  }
  const first = offset + 1;
  const last = Math.min(offset + limit, total);
  return (
    <nav aria-label="頁送り" className="pager">
      <span>
        全 {total} 件中 {first}〜{last} 件
      </span>
      <button type="button" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - limit))}>
        前へ
      </button>
      <button type="button" disabled={offset + limit >= total} onClick={() => onChange(offset + limit)}>
        次へ
      </button>
    </nav>
  );
}
