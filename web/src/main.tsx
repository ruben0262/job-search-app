import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

// Served with the site rather than from a font host: no third-party request on any page.
import "@fontsource-variable/inter";
import "@fontsource-variable/sora";
import { App } from "./App";
// First, before any page: the browser offers to install the site once, early, and the offer is kept there.
import "./install";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
