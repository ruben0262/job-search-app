/** A CV or cover letter for one job: ask for it, show it being made, hand over the download.
 *
 * The only place that knows the three calls: `POST /documents/build` starts one, `GET
 * /documents/build/{id}` says where it is, and its `download_url` is the PDF. Every button asks here.
 * The buttons show for everyone (owner, 2026-11-10); pressed without an AI account in use, or without
 * the CV or cover letter it is written from, the server says what is missing (`libs.documents.needs`),
 * and a toast says it, with the way to each. The server puts it in the bell too.
 */
import { useEffect, useState } from "react";

import { ApiError, api, type Schema } from "./api";
import { notificationsChanged } from "./notifications";
import { toast } from "./toast";

type Build = Schema<"BuildStatus">;
type Ask = Schema<"BuildRequest">;

/** How often a document being made is asked about. Under a second, so "ready" shows as it happens. */
const POLL_MS = 800;

/** A refusal said in plain words, where the server's answer is not written for the person. */
const REFUSED: Record<number, string> = {
  403: "AI writing sends your profile to the AI that writes it, so it needs your agreement first.",
  429: "That is all the documents you can make today. You can make more tomorrow.",
  503: "Making documents is paused for a moment. Try again soon.",
};

export function useBuild(jobId: number, kind: Ask["kind"]) {
  const [build, setBuild] = useState<Build | null>(null);
  const [refused, setRefused] = useState<ApiError | null>(null);
  const making = build?.state === "queued" || build?.state === "building";

  useEffect(() => {
    if (!build || !making) return;
    const timer = window.setTimeout(() => {
      api.get<Build>(`/documents/build/${build.id}`).then((next) => {
        setBuild(next);
        // Refused, or kept with no room for it: the server says so in the bell as well.
        if (next.state === "failed" || next.saved === false) notificationsChanged();
      }).catch(() => {
        setBuild(null);
        setRefused(new ApiError(0, ""));
      });
    }, POLL_MS);
    return () => window.clearTimeout(timer);
  }, [build, making]);

  const start = async () => {
    setRefused(null);
    try {
      setBuild(await api.post<Build>("/documents/build", { job_id: jobId, kind, mode: "ai" } satisfies Ask));
    } catch (caught) {
      setBuild(null);
      const refusal = caught instanceof ApiError ? caught : new ApiError(0, "");
      const missing = refusal.status === 409 ? (refusal.data as { missing?: string[] } | undefined)?.missing : null;
      if (missing) {
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
  return { build, making, start, error, needsConsent: refused?.status === 403 };
}
