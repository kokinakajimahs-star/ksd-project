// エラーの文言と、項目ごとの理由（400 の details）を 1 つの alert に出す
import type { ErrorView } from "../api/errors";

export default function ErrorAlert({ error }: { error: ErrorView | null }) {
  if (error === null) {
    return null;
  }
  return (
    <div role="alert" className="error-alert">
      <p>{error.message}</p>
      {error.details.length > 0 && (
        <ul>
          {error.details.map((detail) => (
            <li key={detail}>{detail}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
