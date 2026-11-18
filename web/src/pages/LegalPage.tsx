/** The four legal pages — Terms, Privacy, Cookies and Data use — as the server fills them in (`libs/legal`), and
 * the one screen that asks an account made before them to agree. Public: sign-up and the landing page's foot
 * link them before anybody has an account. */
import { Fragment, useEffect, useMemo, useState, type ReactNode } from "react";

import { api, type Schema } from "../api";
import { ThemeToggle } from "../theme";
import { Card, Error, useAction } from "../ui";
import { Brand } from "../wheel";
import { DeleteAccount } from "./SettingsPage";

type Legal = Schema<"LegalDocument">;

/** Each page's address (`#/terms` …) and its name in a row of links. */
export const LEGAL_PAGES = [["terms", "Terms"], ["privacy", "Privacy"], ["cookies", "Cookies"], ["data", "Data use"]] as const;
export type LegalName = (typeof LEGAL_PAGES)[number][0];

/** The four, as links: wherever the site's foot is, and beside every box that agrees to them. */
export function LegalLinks({ current }: { current?: LegalName }) {
  return (
    <>
      {LEGAL_PAGES.map(([name, label]) => (
        <a key={name} href={`#/${name}`} aria-current={name === current ? "page" : undefined}>{label}</a>
      ))}
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
  const sections = parsed.filter((block): block is Heading => block.kind === "h" && block.level === 2);

  return (
    <>
      <div className="backdrop" aria-hidden="true" />
      <main className="wizard legal page-in">
        <header className="shell-top">
          <Brand href="#/" />
          <ThemeToggle />
        </header>
        <nav className="legal-tabs" aria-label="Legal pages">
          <LegalLinks current={name} />
        </nav>
        {failed ? (
          <p className="muted">This page can't be loaded right now. Check your connection and try again.</p>
        ) : !doc ? (
          <p className="muted">Loading…</p>
        ) : (
          <div className="legal-body">
            <nav className="legal-toc" aria-label="On this page">
              <p className="label">On this page</p>
              <ol>
                {sections.map((section) => (
                  <li key={section.id}>
                    <a href={`#/${name}`} onClick={(event) => jump(event, section.id)}>{section.text}</a>
                  </li>
                ))}
              </ol>
              <button type="button" className="secondary" onClick={() => window.print()}>Print or save as PDF</button>
            </nav>
            <article className="legal-doc">
              <Blocks blocks={parsed} />
            </article>
          </div>
        )}
        <p className="legal-back">
          <a href="#/">Back to Mahoraga</a>
        </p>
      </main>
    </>
  );
}

/** An account made before the legal pages read as they do now: the Terms' short version, the four in full a tap
 * away, and one button. Nothing else of the site until it is pressed (`dashboard`'s stage "agree"). */
export function AgreePage({ onDone }: { onDone: () => void }) {
  const [terms, setTerms] = useState<Legal | null>(null);
  useEffect(() => {
    void api.get<Legal>("/legal/terms").then(setTerms).catch(() => undefined);
  }, []);
  // The Terms' own summary, its first box: said once, in the Terms, and shown here as it reads there.
  const summary = useMemo(() => (terms ? blocks(terms.markdown).find((block) => block.kind === "quote") : undefined),
    [terms]);
  const agree = useAction(async () => {
    await api.post("/account/consent", { version: terms?.version });
    onDone();
  });
  const signOut = useAction(async () => {
    await api.post("/auth/logout");
    onDone();
  });

  return (
    <>
      <div className="backdrop" aria-hidden="true" />
      <main className="wizard page-in">
        <header className="shell-top">
          <Brand href="#/" />
          <ThemeToggle />
        </header>
        <h1 style={{ marginTop: "2.5rem" }}>Our terms</h1>
        <p className="muted">Please read the short version, and agree to carry on.</p>
        <Card>
          {summary ? <Blocks blocks={summary.kind === "quote" ? summary.inner : []} /> : <p className="muted">Loading…</p>}
          <p className="small legal-links">
            Read them in full: <LegalLinks />
          </p>
          <Error>{agree.error}</Error>
          <button type="button" disabled={agree.busy || !terms} onClick={() => void agree.run()}>
            {agree.busy ? "Saving…" : "I agree"}
          </button>
        </Card>
        <div className="row legal-refuse">
          <span className="small muted">Rather not?</span>
          <button type="button" className="secondary" disabled={signOut.busy} onClick={() => void signOut.run()}>
            Sign out
          </button>
          <DeleteAccount onDeleted={onDone} />
        </div>
        <Error>{signOut.error}</Error>
      </main>
    </>
  );
}

// ── the Markdown the pages are written in: headings, paragraphs, lists, boxes, tables, bold and links ────────

type Heading = { kind: "h"; level: number; text: string; id: string };
type Block =
  | Heading
  | { kind: "p"; text: string }
  | { kind: "ul"; items: string[] }
  | { kind: "ol"; items: string[]; start: number }
  | { kind: "quote"; inner: Block[] }
  | { kind: "table"; head: string[]; rows: string[][] }
  | { kind: "hr" };

const ITEM = /^(- |(\d+)\. )/;
const STARTS_BLOCK = /^(#{1,3} |>|\||- |\d+\. |---$)/;

/** The text, as blocks. Ours, so a subset: what the four files use and nothing they do not. Each block takes its
 * lines off the front, the first one always, so no line is read twice and none stops the walk. */
function blocks(markdown: string): Block[] {
  const rest = markdown.split("\n");
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
      out.push({ kind: "h", level: marks.length, text, id: slug(text) });
    } else if (line.startsWith(">")) {
      const quoted = [line, ...take((next) => next.startsWith(">"))].map((next) => next.replace(/^> ?/, ""));
      out.push({ kind: "quote", inner: blocks(quoted.join("\n")) });
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
            return <Tag key={index} id={block.id}>{inline(block.text)}</Tag>;
          }
          case "p":
            return <p key={index}>{inline(block.text)}</p>;
          case "ul":
            return <ul key={index}>{block.items.map((text, n) => <li key={n}>{inline(text)}</li>)}</ul>;
          case "ol":
            return <ol key={index} start={block.start}>{block.items.map((text, n) => <li key={n}>{inline(text)}</li>)}</ol>;
          case "quote":
            return <aside key={index} className="legal-callout"><Blocks blocks={block.inner} /></aside>;
          case "table":
            return (
              <div key={index} className="table-scroll">
                <table className="legal-table">
                  <thead><tr>{block.head.map((cell, n) => <th key={n}>{inline(cell)}</th>)}</tr></thead>
                  <tbody>
                    {block.rows.map((row, r) => (
                      <tr key={r}>{row.map((cell, n) => <td key={n} data-label={block.head[n]}>{inline(cell)}</td>)}</tr>
                    ))}
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

function slug(text: string): string {
  return text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}

/** To a section, without touching the address: it is the route (`#/privacy`). */
function jump(event: { preventDefault: () => void }, id: string) {
  event.preventDefault();
  document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
}
