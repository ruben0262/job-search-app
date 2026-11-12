import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

import openapi from "./openapi.json";

// Every first path segment the API answers, read from the contract the gates keep current. A list
// written out here missed `/support`, so in development the help chat got this site's HTML back and
// said "Loading…" forever. Imported, not read from disk: Vite restarts when a file its config imports
// changes, so a path added to the contract reaches this proxy. Read once, a running site kept the
// list it started with, and the plan page said "Loading…" because `/pricing` came back as this page.
const prefixes = new Set(Object.keys(openapi.paths).map((path) => path.split("/")[1]).filter(Boolean));

// The API is reached through a Cloudflare Tunnel in production (D7), so the browser talks to the
// same origin and there is no CORS story to get wrong. In development the API runs on 8000, and
// this proxy reproduces the same-origin arrangement rather than papering over a different one with
// permissive CORS headers that would then be untested in production.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: Object.fromEntries(
      [...prefixes].map((prefix) => [`/${prefix}`, { target: "http://127.0.0.1:8000", changeOrigin: false }]),
    ),
  },
});
