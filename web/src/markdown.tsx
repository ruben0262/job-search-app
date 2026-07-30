/**
 * Markdown-lite, for chat messages. The assistant writes prose with bold, bullets, headings and
 * the occasional inline code — this renders that and nothing more.
 *
 * Hand-rolled rather than a library: the repo's two-runtime-dependency rule, and the full grammar
 * is not wanted — tables and images in a chat bubble are a rendering surface nobody asked for.
 * Everything is built as React nodes, never injected as HTML, so a message containing `<script>`
 * is text like any other.
 */
import type { ReactNode } from "react";

/** Inline spans: **bold**, *italic*, `code`. One pass, longest marker first. */
function inline(text: string, keyBase: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const pattern = /(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)/g;
  let last = 0;
  let index = 0;
  for (const match of text.matchAll(pattern)) {
    if (match.index > last) nodes.push(text.slice(last, match.index));
    const token = match[0];
    const key = `${keyBase}-${index++}`;
    if (token.startsWith("**")) nodes.push(<strong key={key}>{token.slice(2, -2)}</strong>);
    else if (token.startsWith("`")) nodes.push(<code key={key}>{token.slice(1, -1)}</code>);
    else nodes.push(<em key={key}>{token.slice(1, -1)}</em>);
    last = match.index + token.length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

export function Markdown({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  const lines = text.split("\n");
  let index = 0;
  let key = 0;

  while (index < lines.length) {
    const line = lines[index] ?? "";

    if (!line.trim()) {
      index += 1;
      continue;
    }

    // Fenced code: kept verbatim, one block.
    if (line.trimStart().startsWith("```")) {
      const body: string[] = [];
      index += 1;
      while (index < lines.length && !(lines[index] ?? "").trimStart().startsWith("```")) {
        body.push(lines[index] ?? "");
        index += 1;
      }
      index += 1; // the closing fence
      blocks.push(
        <pre key={key++} className="md-code">
          <code>{body.join("\n")}</code>
        </pre>,
      );
      continue;
    }

    // Bullets: consecutive `- ` / `* ` lines become one list.
    if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (index < lines.length && /^\s*[-*]\s+/.test(lines[index] ?? "")) {
        items.push((lines[index] ?? "").replace(/^\s*[-*]\s+/, ""));
        index += 1;
      }
      blocks.push(
        <ul key={key++} className="md-list">
          {items.map((item, position) => (
            <li key={position}>{inline(item, `${key}-${position}`)}</li>
          ))}
        </ul>,
      );
      continue;
    }

    // Headings: any level renders as one weight — a chat message is not a document.
    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (heading) {
      blocks.push(
        <p key={key++} className="md-heading">
          {inline(heading[2] ?? "", `h${key}`)}
        </p>,
      );
      index += 1;
      continue;
    }

    // A paragraph: consecutive plain lines, joined the way prose reads.
    const paragraph: string[] = [line];
    index += 1;
    while (
      index < lines.length &&
      (lines[index] ?? "").trim() &&
      !/^\s*[-*]\s+|^#{1,4}\s|^```/.test(lines[index] ?? "")
    ) {
      paragraph.push(lines[index] ?? "");
      index += 1;
    }
    blocks.push(<p key={key++}>{inline(paragraph.join(" "), `p${key}`)}</p>);
  }

  return <>{blocks}</>;
}
