/** A CV or cover letter for one job: ask for it, show it being made, hand over the download.
 *
 * The only place that knows the calls: `POST /documents/build` starts one, `GET /documents/build/{id}`
 * says where it is, `GET /documents/builds` lists the ones still being made, and a ready one's
 * `download_url` is the PDF. Every button asks here.
 * The buttons show for everyone (owner, 2026-11-10); pressed without an AI account in use, or without
 * the CV or cover letter it is written from, the server says what is missing (`libs.documents.needs`),
 * and a toast says it, with the way to each. Pressed by a free account, the toast is the way to a
 * membership, which the documents come with (owner, 2026-11-12). The server puts each in the bell too.
 *
 * One record of the builds for the whole site, not one per button (owner, 2026-11-19: "it doesn't just
 * disappear, otherwise they'll keep clicking it"): a card that leaves the page as its job moves to Applied,
 * Applied itself, and a page loaded again all find a build where it was, and `Making` says it on every page
 * until it is ready. Ready, a notice says so with what the AI account has left.
 */
import { useState, useSyncExternalStore } from "react";

import { InUse } from "./aiaccounts";
import { ApiError, api, type Schema } from "./api";
import { notificationsChanged } from "./notifications";
import { toast } from "./toast";

type Build = Schema<"BuildStatus">;
type Ask = Schema<"BuildRequest">;

/** How often a document being made is asked about. Under a second, so "ready" shows as it happens. */
const POLL_MS = 800;

const NAME: Record<Ask["kind"], string> = { cv: "CV", cover: "cover letter" };

/** A refusal said in plain words, where the server's answer is not written for the person. */
const REFUSED: Record<number, string> = {
  429: "That is all the documents you can make today. You can make more tomorrow.",
  503: "Making documents is paused for a moment. Try again soon.",
};

/** Each build this visit knows, by job and kind, and whoever draws one. */
let builds = new Map<string, Build>();
const listeners = new Set<() => void>();
const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => void listeners.delete(listener);
};
const keyOf = (jobId: number, kind: Ask["kind"]) => `${jobId}:${kind}`;
const making = (build: Build | null | undefined) => build?.state === "queued" || build?.state === "building";

function changed(next: Map<string, Build>): void {
  builds = next;
  listeners.forEach((listener) => listener());
}

/** A build as the server last said it; one that has just stopped being made is said aloud. */
function put(build: Build): void {
  const key = keyOf(build.job_id ?? 0, build.kind);
  const was = builds.get(key);
  changed(new Map(builds).set(key, build));
  if (making(build)) poll();
  else if (making(was) && was?.id === build.id) finished(build);
}

/** One timer asks about every build being made, until none is. */
let timer = 0;
function poll(): void {
  if (timer) return;
  timer = window.setTimeout(() => {
    timer = 0;
    for (const build of builds.values()) {
      if (!making(build)) continue;
      api.get<Build>(`/documents/build/${build.id}`).then(put).catch((caught) => {
        // Gone is gone; a lost connection is asked again.
        if (caught instanceof ApiError && caught.status === 404) put({ ...build, state: "failed", detail: null });
        else poll();
      });
    }
  }, POLL_MS);
}

function finished(build: Build): void {
  // Refused, or kept with no room for it: the server says so in the bell as well.
  if (build.state === "failed" || build.saved === false) notificationsChanged();
  const what = `${NAME[build.kind]} for ${build.title ?? "this job"}`;
  if (build.state === "ready" && build.download_url) {
    toast({ text: <>Your {what} is ready.<InUse tokens={build.tokens} /></>,
            links: [{ href: build.download_url, label: `Download ${NAME[build.kind]}` }] });
  } else {
    toast({ text: `We could not make your ${what}. ${build.detail ?? "Try again."}` });
  }
}

/** The ones still being made, as the server has them: once somebody is signed in, so a page loaded again
 * carries on saying so. */
export function resumeBuilds(): void {
  api.get<Build[]>("/documents/builds").then((list) => list.forEach(put)).catch(() => undefined);
}

/** Signed out: the next account must not see this one's. */
export function forgetBuilds(): void {
  changed(new Map());
}

export function useBuild(jobId: number, kind: Ask["kind"]) {
  const build = useSyncExternalStore(subscribe, () => builds.get(keyOf(jobId, kind)) ?? null);
  const [refused, setRefused] = useState<ApiError | null>(null);

  const start = async () => {
    setRefused(null);
    try {
      put(await api.post<Build>("/documents/build", { job_id: jobId, kind, mode: "ai" } satisfies Ask));
    } catch (caught) {
      const next = new Map(builds);
      next.delete(keyOf(jobId, kind));
      changed(next);
      const refusal = caught instanceof ApiError ? caught : new ApiError(0, "");
      const missing = refusal.status === 409 ? (refusal.data as { missing?: string[] } | undefined)?.missing : null;
      if (refusal.status === 402) {
        notificationsChanged();
        toast({ text: refusal.detail, links: [{ href: "#/upgrade", label: "Become a member" }] });
      } else if (missing) {
        notificationsChanged();
        toast({ text: refusal.detail, links: [
          ...missing.includes("account") ? [{ href: "#/settings", label: "Open Settings" }] : [],
          ...missing.some((each) => each !== "account") ? [{ href: "#/documents", label: "Open My documents" }] : [],
        ] });
      } else {
        setRefused(refusal);
      }
    }
  };

  // A 409 says what their account lacks, in words written for them; so does a failed build's detail.
  const error = refused !== null
    ? REFUSED[refused.status] ?? (refused.status === 409 ? refused.detail : "That did not work. Try again.")
    : build?.state === "failed" ? build.detail ?? "We could not make it this time. Try again."
      : build?.state === "expired" ? "It was deleted. Make it again." : null;
  return { build, making: making(build), start, error };
}

/** Every document being made, on every page, until it is ready. */
export function Making() {
  const all = useSyncExternalStore(subscribe, () => builds);
  const now = [...all.values()].filter(making);
  if (!now.length) return null;
  return (
    <div className="banner making-now" role="status">
      {now.map((build) => (
        <span key={build.id}>Making your {NAME[build.kind]} for <strong>{build.title ?? "a job"}</strong>…</span>
      ))}
    </div>
  );
}
