import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import App from "./App";
// 画面の見た目（A-51・A-53）。Primer の値（CSS 変数だけ。明るい配色は index.html の <html> の data-* で有効にする）を
// 先に読み、styles.css がそれを使う。試験は main.tsx を読まないので、各画面の試験には効かない
import "@primer/primitives/dist/css/primitives.css";
import "@primer/primitives/dist/css/functional/themes/light.css";
import "./styles.css";

const container = document.getElementById("root");
if (!container) {
  throw new Error("#root が index.html に無い");
}

createRoot(container).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
);
