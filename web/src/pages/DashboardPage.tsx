/**
 * The overview. **It says what to do next; it does not offer four buttons and hope.**
 *
 * The order is not guessable — processing invalidates every label, relabelling costs real money,
 * training is free — so the server decides which action is next and this renders that one. A user
 * who presses them in the wrong order pays for the mistake, which is the whole argument against a
 * toolbar.
 *
 * **Redesigned to the onboarding pass's rules: no cards, one idea per screen.** It was three
 * bordered panels of equal weight, so the thing to do next looked exactly like the delivery
 * settings. Here the state is the headline, the one action is the only control, and everything else
 * is a quiet line underneath — present for whoever wants it, competing with nothing.
 *
 * The numbers stay because they are the answer to "is this thing working": jobs scored, labels at
 * this profile version, applications recorded. They are set as figures rather than a table for the
 * same reason — a table implies rows worth comparing, and these are three unrelated facts.
 */
import type { Dashboard } from "../api";
import { Banner } from "../ui";

/** The state, as a sentence somebody can act on. The server owns the *explanation* (`next.message`);
 *  this owns the four words above it, the same split the onboarding wizard uses for its titles. */
const HEADLINES: Record<string, string> = {
  confirm: "Your profile is ready to review",
  process: "Your profile is not built yet",
  upload: "Add your CV to get started",
};

const ACTIONS: Record<string, { href: string; label: string }> = {
  confirm: { href: "#/process", label: "Review your profile" },
  // Not "process your documents" any more: the step is a conversation, and the label should say
  // what the user is about to do rather than what the platform does with it.
  process: { href: "#/profile", label: "Build your profile" },
  upload: { href: "#/settings", label: "Upload your CV" },
};

export function DashboardPage({ dashboard }: { dashboard: Dashboard; onChanged: () => void }) {
  if (dashboard.stage === "onboarding") return null;
  const { next, model, feed, banners, notifications, telegram } = dashboard;

  const action = ACTIONS[next.action];
  const headline =
    HEADLINES[next.action] ??
    (model.servable ? "Your feed is running on your own model" : "Your feed is running on your keywords");

  return (
    <>
      {banners.map((banner) => (
        <Banner key={banner.key} level={banner.level}>
          {banner.text}
        </Banner>
      ))}

      <div className="overview">
        <h1>{headline}</h1>
        <p className="lede">{next.message}</p>

        {action ? (
          <a href={action.href}>
            <button>{action.label}</button>
          </a>
        ) : null}

        <dl className="stats">
          <Stat value={feed.scored} label="jobs scored for you" />
          <Stat value={feed.labels} label="labels at this profile" />
          <Stat value={feed.applied} label="applications recorded" />
        </dl>

        <p className="facts">
          {model.servable
            ? `Model version ${model.version}, trained on your own judgement${
                model.trained_at ? ` on ${new Date(model.trained_at).toLocaleDateString()}` : ""
              }.`
            : model.stale_reason}
        </p>
        <p className="facts">
          Telegram is {telegram}. At most {notifications.per_day_cap} messages a day, checked every{" "}
          {notifications.interval_min} minutes.
        </p>
      </div>
    </>
  );
}

/** One number and what it counts. The label sits under the figure, so a column of them reads down
 *  rather than needing a header row to explain itself. */
function Stat({ value, label }: { value: number; label: string }) {
  return (
    <div className="stat">
      <dt>{value.toLocaleString()}</dt>
      <dd>{label}</dd>
    </div>
  );
}
