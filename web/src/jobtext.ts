/** A job's whole text, here before anybody opens the job. A card carries none of it (owner, 2026-11-21: "remove
 * the jobs descriptions ... there should be no waiting to open a job"), so the page that lists jobs asks for the
 * texts of those it shows, in one request, once the list is drawn; and a card asks for its own as the pointer or
 * a finger reaches it, for a list longer than one request. Opened, a job is drawn from what is here. */
import { api, type Schema } from "./api";
import { keep, peek } from "./kept";

/** Where one job's text is kept (`kept.ts`), so it is forgotten with everything else when the session ends:
 * a job somebody wrote in themselves is theirs alone. */
const held = (id: number) => `/jobs/descriptions?ids=${id}`;
/** As many as a page of Jobs lists. */
const AT_ONCE = 25;
/** The texts on their way, by job: whoever opens one of them waits for that answer, not for a request of its own. */
const asking = new Map<number, Promise<void>>();

/** A job's text; "" where its board gave none; `null` while it has not been read. */
export const textOf = (id: number) => peek<string>(held(id));

/** Have the texts of these jobs here: those missing are asked for, the first `AT_ONCE` of them, in one request. */
export function readTexts(ids: number[]): Promise<void> {
  const missing = ids.filter((id) => textOf(id) === null && !asking.has(id)).slice(0, AT_ONCE);
  if (missing.length) {
    const asked = api.get<Schema<"JobTexts">>(`/jobs/descriptions?${missing.map((id) => `ids=${id}`).join("&")}`)
      .then((body) => missing.forEach((id) => keep(held(id), body.texts[id] ?? "")))
      .finally(() => missing.forEach((id) => asking.delete(id)));
    missing.forEach((id) => asking.set(id, asked));
  }
  return Promise.all(ids.map((id) => asking.get(id))).then(() => undefined);
}

/** The same, for a page that only wants them on their way: one that fails is asked for again when its job opens. */
export function textsAhead(ids: number[]): void {
  void readTexts(ids).catch(() => undefined);
}
