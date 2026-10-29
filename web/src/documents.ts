/** A CV or cover letter for one job: ask for it, show it being made, hand over the download.
 *
 * The only place that knows the three calls: `POST /documents/build` starts one, `GET
 * /documents/build/{id}` says where it is, and its `download_url` is the PDF. Every button asks here.
 */
import { useEffect, useState } from "react";

import { ApiError, api, type Schema } from "./api";

type Build = Schema<"BuildStatus">;
type Ask = Schema<"BuildRequest">;

/** How often a document being made is asked about. Under a second, so "ready" shows as it happens. */
const POLL_MS = 800;

/** A refusal, in plain words. The server's own text is written for whoever runs it, never shown. */
const REFUSED: Record<number, string> = {
  403: "AI writing sends your profile to the AI that writes it, so it needs your agreement first.",
  409: "AI cannot write this one for you right now. The instant version is free and ready.",
  429: "That is all the documents you can make today. You can make more tomorrow.",
  503: "Making documents is paused for a moment. Try again soon.",
};

export function useBuild(jobId: number, kind: Ask["kind"]) {
  const [build, setBuild] = useState<Build | null>(null);
  const [refused, setRefused] = useState<number | null>(null);
  const making = build?.state === "queued" || build?.state === "building";

  useEffect(() => {
    if (!build || !making) return;
    const timer = window.setTimeout(() => {
      api.get<Build>(`/documents/build/${build.id}`).then(setBuild).catch(() => {
        setBuild(null);
        setRefused(0);
      });
    }, POLL_MS);
    return () => window.clearTimeout(timer);
  }, [build, making]);

  const start = async (mode: Ask["mode"] = "instant") => {
    setRefused(null);
    try {
      const ask: Ask = { job_id: jobId, kind, mode };
      setBuild(await api.post<Build>("/documents/build", ask));
    } catch (caught) {
      setBuild(null);
      setRefused(caught instanceof ApiError ? caught.status : 0);
    }
  };

  const error = refused !== null ? REFUSED[refused] ?? "That did not work. Try again."
    : build?.state === "failed" ? "We could not make it this time. Try again."
      : build?.state === "expired" ? "It was deleted after a while, as they all are. Make it again." : null;
  return { build, making, start, error, needsConsent: refused === 403 };
}
