// ルートの定義（設計書 2.1・7.1）。7.1 の画面のルートは全てここに書く。
// 画面は frontend/src/pages/<群>/ に置き、下の <Routes> に 1 行ずつ足す。
import { Link, Route, Routes } from "react-router-dom";

function Home() {
  return (
    <section>
      <h2>ホーム</h2>
      <p>WBS・工数・進捗管理システム</p>
    </section>
  );
}

function NotFound() {
  return (
    <section role="alert">
      <h2>ページが見つかりません</h2>
      <p>
        <Link to="/">ホームへ戻る</Link>
      </p>
    </section>
  );
}

export default function App() {
  return (
    <div className="app">
      <header className="app-header">
        <h1>
          <Link to="/">PMT</Link>
        </h1>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
    </div>
  );
}
