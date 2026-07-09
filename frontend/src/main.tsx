import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { applySiteBackground } from "./shared/lib/siteBackground";
import "./shared/styles/global.css";

// 启动时恢复用户自定义背景
applySiteBackground();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
