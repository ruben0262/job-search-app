/** The four legal pages — Terms, Privacy, Cookies and Data use — as the server fills them in (`libs/legal`): the
 * text and nothing else, a plain document (owner, 2026-11-18: "just really simple text on the page"), with a
 * way back for the installed app, which has no back button. Public: sign-up and the landing page's foot link
 * them before anybody has an account. Agreed to with sign-up's box and nowhere else. */
import { Fragment, useEffect, useMemo, useState, type ReactNode } from "react";

import { api, type Schema } from "../api";

type Legal = Schema<"LegalDocument">;

/** Each page's address (`#/terms` …) and its name in a row of links. */
export const LEGAL_PAGES = [["terms", "Terms"], ["privacy", "Privacy"], ["cookies", "Cookies"], ["data", "Data use"]] as const;
export type LegalName = (typeof LEGAL_PAGES)[number][0];

/** The four, as links: in the landing page's foot and at the end of Settings. */
export function LegalLinks() {
  return (
    <>
      {LEGAL_PAGES.map(([name, label]) => <a key={name} href={`#/${name}`}>{label}</a>)}
    </>
  );
}

export function LegalPage({ name }: { name: LegalName }) {
  const [doc, setDoc] = useState<Legal | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    setDoc(null);
    setFailed(false);
    api.get<Legal>(`/legal/${name}`).then((body) => {
      setDoc(body);
      window.scrollTo(0, 0);
    }).catch(() => setFailed(true));
  }, [name]);
  const parsed = useMemo(() => (doc ? blocks(doc.markdown) : []), [doc]);

  return (
    <main className="legal">
      <p><a href="#/">Back</a></p>
      {failed ? <p>This page can't be loaded right now. Check your connection and try again.</p>
        : !doc ? <p>Loading…</p>
          : <Blocks blocks={parsed} />}
    </main>
  );
}

// ── the Markdown the pages are written in: headings, paragraphs, lists, tables, bold, italics and links ──────

type Block =
  | { kind: "h"; level: number; text: string }
  | { kind: "p"; text: string }
  | { kind: "ul"; items: string[] }
  | { kind: "ol"; items: string[]; start: number }
  | { kind: "table"; head: string[]; rows: string[][] }
  | { kind: "hr" };

const ITEM = /^(- |(\d+)\. )/;
const STARTS_BLOCK = /^(#{1,3} |>|\||- |\d+\. |---$)/;

/** The text, as blocks. Ours, so a subset: what the four files use and nothing they do not. A quoted part (`>`)
 * is read as plain text. Each block takes its lines off the front, the first one always, so no line is read
 * twice and none stops the walk. */
function blocks(markdown: string): Block[] {
  const rest = markdown.split("\n").map((line) => line.replace(/^> ?/, ""));
  const out: Block[] = [];
  const take = (test: (line: string) => boolean): string[] => {
    const taken: string[] = [];
    for (let next = rest[0]; next !== undefined && test(next); next = rest[0]) taken.push(rest.shift() ?? "");
    return taken;
  };
  for (let line = rest.shift(); line !== undefined; line = rest.shift()) {
    const heading = /^(#{1,3}) (.+)$/.exec(line);
    const item = ITEM.exec(line);
    if (!line.trim()) continue;
    if (line.trim() === "---") {
      out.push({ kind: "hr" });
    } else if (heading) {
      const [, marks = "", text = ""] = heading;
      out.push({ kind: "h", level: marks.length, text });
    } else if (line.startsWith("|")) {
      const [head = [], , ...rows] = [line, ...take((next) => next.startsWith("|"))]
        .map((next) => next.slice(1, -1).split("|").map((cell) => cell.trim()));
      out.push({ kind: "table", head, rows });
    } else if (item) {
      // A long item goes on over indented lines.
      const items: string[] = [];
      for (const next of [line, ...take((next) => ITEM.test(next) || /^ {2,}\S/.test(next))]) {
        if (ITEM.test(next)) items.push(next.replace(ITEM, ""));
        else items.push(`${items.pop() ?? ""} ${next.trim()}`);
      }
      out.push(item[2] ? { kind: "ol", items, start: Number(item[2]) } : { kind: "ul", items });
    } else {
      const text = [line, ...take((next) => next.trim() !== "" && !STARTS_BLOCK.test(next))];
      out.push({ kind: "p", text: text.map((next) => next.trim()).join(" ") });
    }
  }
  return out;
}

function Blocks({ blocks: list }: { blocks: Block[] }) {
  return (
    <>
      {list.map((block, index) => {
        switch (block.kind) {
          case "h": {
            const Tag = block.level === 1 ? "h1" : block.level === 2 ? "h2" : "h3";
            return <Tag key={index}>{inline(block.text)}</Tag>;
          }
          case "p":
            return <p key={index}>{inline(block.text)}</p>;
          case "ul":
            return <ul key={index}>{block.items.map((text, n) => <li key={n}>{inline(text)}</li>)}</ul>;
          case "ol":
            return <ol key={index} start={block.start}>{block.items.map((text, n) => <li key={n}>{inline(text)}</li>)}</ol>;
          case "table":
            return (
              <div key={index} className="table-scroll">
                <table>
                  <thead><tr>{block.head.map((cell, n) => <th key={n}>{inline(cell)}</th>)}</tr></thead>
                  <tbody>
                    {block.rows.map((row, r) => <tr key={r}>{row.map((cell, n) => <td key={n}>{inline(cell)}</td>)}</tr>)}
                  </tbody>
                </table>
              </div>
            );
          case "hr":
            return <hr key={index} />;
        }
      })}
    </>
  );
}

/** Bold, italics and links inside a line: as elements, never as HTML, so nothing in a page — an operator's
 * detail included — is ever run. */
function inline(text: string): ReactNode {
  return text.split(/(\*\*.+?\*\*|\*[^*\s][^*]*\*|\[[^\]]+\]\([^)\s]+\))/g).map((part, index) => {
    const bold = /^\*\*(.+)\*\*$/.exec(part);
    if (bold) return <strong key={index}>{inline(bold[1] ?? "")}</strong>;
    const italic = /^\*([^*]+)\*$/.exec(part);
    if (italic) return <em key={index}>{italic[1]}</em>;
    const link = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(part);
    if (link) {
      const [, label, href = ""] = link;
      const away = /^https?:/.test(href);
      return <a key={index} href={href} {...(away ? { target: "_blank", rel: "noreferrer" } : {})}>{label}</a>;
    }
    return <Fragment key={index}>{part}</Fragment>;
  });
}
