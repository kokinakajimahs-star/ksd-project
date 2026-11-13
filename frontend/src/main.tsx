import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import App from "./App";
// 画面の最小の見た目（A-51）。試験は main.tsx を読まないので、各画面の試験には効かない
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
