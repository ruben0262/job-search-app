/** About you, asked for until it says enough (owner, 2026-11-22: "motivating and guiding users to fill this
 * section out"): a red dot on My documents in the menu, a note pinned at the top of the bell, and one over the
 * box itself, all from the one count the server keeps (`libs.documents.about`), until it has more than `enough`
 * words. Each says why in a line: everything the site does for them is read from it, with their CV. */
import type { Schema } from "./api";

export type AboutLength = Schema<"AboutLength">;

/** Too little: empty, or `enough` words or fewer. */
export function needsMore(about: AboutLength | undefined): about is AboutLength {
  return Boolean(about && about.words <= about.enough);
}

/** Words as the server counts them, while they are typed: what spaces and new lines separate. */
export function countWords(text: string): number {
  return text.split(/\s+/).filter(Boolean).length;
}

/** What About you is for on their plan, after "We use it to": "score every job for you, find more jobs that fit
 * you and write your CVs and cover letters". Only what their plan gives is named. */
export function usesOf(areas: string[]): string {
  const uses = [areas.includes("match_scores") ? "score every job for you" : null, "find more jobs that fit you",
                areas.includes("cvs") ? "write your CVs and cover letters" : null].filter((use) => use !== null);
  const last = uses.pop() ?? "";
  return uses.length ? `${uses.join(", ")} and ${last}` : last;
}

/** What it asks, and why. */
export function nudge(about: AboutLength, areas: string[]): { title: string; body: string } {
  const why = `We use it to ${usesOf(areas)}. Give us more than ${about.enough} words of real detail, `
    + "all of it true.";
  return about.words
    ? { title: "Add more about yourself",
        body: `You have written ${about.words} ${about.words === 1 ? "word" : "words"}. ${why}` }
    : { title: "Tell us about yourself", body: why };
}

/** The note over the box on My documents. */
export function AboutNudge({ about, areas }: { about: AboutLength; areas: string[] }) {
  const { title, body } = nudge(about, areas);
  return (
    <div className="about-nudge">
      <strong>{title}</strong>
      <span>{body}</span>
    </div>
  );
}
