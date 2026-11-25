import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

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
// A page's address and the data behind it are often the same words (`/applied`, `/alerts`). A browser going
// to one is given the page and the page's own code asking is given the data, told apart by what is asked for,
// as the API itself tells them apart (`services/api/guard.py`): the same rule, read off the same contract. Any
// other address is a page whatever is asked for, and the API's own addresses that answer a browser (a file to
// download, the way back from Discord) go to it.
type Answers = Record<string, { content?: Record<string, unknown> }>;
const gets = Object.entries(openapi.paths as Record<string, { get?: { responses: Answers } }>)
  .flatMap(([path, item]) => (item.get ? [{
    pattern: new RegExp(`^${path.replace(/\{[^/]+\}/g, "[^/]+")}$`),
    own: !Object.entries(item.get.responses)
      .some(([code, answer]) => /^[23]/.test(code) && "application/json" in (answer.content ?? {})),
  }] : []));
const pageAsked = (method: string | undefined, accept: string | undefined, url: string | undefined) => {
  const path = (url ?? "").split("?")[0] ?? "";
  if (method !== "GET" || (path.split("/").pop() ?? "").includes(".")) return false;
  const api = gets.filter(({ pattern }) => pattern.test(path));
  return api.length === 0 || ((accept ?? "").includes("text/html") && !api.some(({ own }) => own));
};

// What a page would only find late, said in its head, each named after what is in it and so renamed by
// every build.
// The landing page is a file of its own: the page says where it is (`<meta name="landing">`), and
// `public/theme.js`, which runs before anything is drawn, asks for it there and then for a visitor: beside the
// site's script, not a round trip after it.
// The two fonts Latin text is set in (Inter, and Sora for titles) were asked for only once the first words
// were drawn, in another font, and the page was drawn again in its own when they came. Asked for from the
// page itself, a visitor's headline is there in its font 0.3 s sooner on a slow connection (3.73 s to 3.42)
// and nothing moves when it comes (layout shift 0.021 to 0; 2026-11-21).
const hints = (): Plugin => ({
  name: "hints",
  transformIndexHtml: {
    order: "post",
    handler(html, { bundle }) {
      const files = Object.values(bundle ?? {});
      const landing = files.find((each) => each.type === "chunk"
        && each.facadeModuleId?.endsWith("/landing/Landing.tsx"));
      const fonts = files.filter((each) => /-latin-wght-normal-[^/]*\.woff2$/.test(each.fileName));
      return html.replace("<head>", ["<head>",
        ...(landing ? [`<meta name="landing" content="/${landing.fileName}" />`] : []),
        ...fonts.map((font) =>
          `<link rel="preload" as="font" type="font/woff2" crossorigin href="/${font.fileName}" />`),
      ].join("\n    "));
    },
  },
});

export default defineConfig({
  plugins: [react(), hints()],
  server: {
    proxy: Object.fromEntries(
      [...prefixes].map((prefix) => [`/${prefix}`, {
        target: "http://127.0.0.1:8000",
        changeOrigin: false,
        bypass: (request: { method?: string; url?: string; headers: { accept?: string } }) =>
          (pageAsked(request.method, request.headers.accept, request.url) ? "/index.html" : undefined),
      }]),
    ),
  },
});
