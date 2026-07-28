import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The API is reached through a Cloudflare Tunnel in production (D7), so the browser talks to the
// same origin and there is no CORS story to get wrong. In development the API runs on 8000, and
// this proxy reproduces the same-origin arrangement rather than papering over a different one with
// permissive CORS headers that would then be untested in production.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: Object.fromEntries(
      ["/auth", "/account", "/legal", "/credentials", "/documents", "/onboarding", "/process",
       "/dashboard", "/feed", "/health"].map((path) => [
        path,
        { target: "http://127.0.0.1:8000", changeOrigin: false },
      ]),
    ),
  },
});
