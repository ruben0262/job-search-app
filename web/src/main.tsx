import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

// Served with the site rather than from a font host: no third-party request on any page.
import "@fontsource-variable/inter";
import "@fontsource-variable/sora";
import { App } from "./App";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
