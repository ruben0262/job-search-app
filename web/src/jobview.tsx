/** A job, whole, over the page: its text laid out as the posting had it, and whatever the page that
 * opened it puts beside it. The × or a tap outside closes it, as does Escape. The jobs page and Applied
 * both open it. */
import { Fragment, useEffect, useState } from "react";
import type { ReactNode } from "react";

import { api, type Schema } from "./api";
import { Sheet } from "./ui";

export function JobView({ id, title, text: known, onClose, children }: {
  id: number;
  title: string;
  /** Its whole text, when the opener already has it; fetched otherwise. */
  text?: string | null;
  onClose: () => void;
  children: ReactNode;
}) {
  const [text, setText] = useState<string | null | undefined>(known);
  useEffect(() => {
    if (text !== undefined) return;
    api.get<Schema<"JobDescription">>(`/jobs/${id}/description`)
      .then((body) => setText(body.text)).catch(() => setText(null));
  }, [id, text]);

  return (
    <Sheet title={title} onClose={onClose}>
      {children}
      {text === undefined ? <p className="muted small">Loading the whole job…</p>
        : text ? <Description text={text} />
          : <p className="muted small">The board did not give this job's text. Open it to read it there.</p>}
    </Sheet>
  );
}

/** A posting's text as it was laid out: paragraphs, "•" lines as a list, a short line ending in a
 * colon as the heading it is. */
function Description({ text }: { text: string }) {
  return (
    <div className="description">
      {text.split(/\n{2,}/).map((block, index) => {
        const lines = block.split("\n");
        if (lines.length === 1 && block.length <= 60 && /:$/.test(block)) return <h4 key={index}>{block}</h4>;
        // Runs of list lines and of other lines, in order.
        const runs: { list: boolean; lines: string[] }[] = [];
        for (const line of lines) {
          const list = line.startsWith("• ");
          const last = runs[runs.length - 1];
          if (last?.list === list) last.lines.push(line);
          else runs.push({ list, lines: [line] });
        }
        return (
          <Fragment key={index}>
            {runs.map((run, at) => run.list ? (
              <ul key={at}>{run.lines.map((line, n) => <li key={n}>{line.slice(2)}</li>)}</ul>
            ) : (
              <p key={at}>{run.lines.map((line, n) => <Fragment key={n}>{n ? <br /> : null}{line}</Fragment>)}</p>
            ))}
          </Fragment>
        );
      })}
    </div>
  );
}

