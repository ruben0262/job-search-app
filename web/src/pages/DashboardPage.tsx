/** The overview. */
import { useEffect, useState } from "react";

import { api, type Dashboard } from "../api";
import { Banner } from "../ui";

type Component = { key: string; label: string; state: "ok" | "warn" | "down" | "off"; detail: string };

/** The state, as a sentence somebody can act on. */
const HEADLINES: Record<string, string> = {
  confirm: "Your profile is ready to review",
  process: "Your profile is not built yet",
  upload: "Add your CV to get started",
};

const ACTIONS: Record<string, { href: string; label: string }> = {
  confirm: { href: "#/process", label: "Review your profile" },
  // Not "process your documents" any more: the step is a conversation.
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
        <p className="kicker">Overview</p>
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
          Telegram is {telegram}.{" "}
          {/* null is unlimited, which is the catalogue default. */}
          {notifications.per_day_cap === null
            ? "No daily limit on messages"
            : `At most ${notifications.per_day_cap} messages a day`}
          . Matches are sent as they are scored.
        </p>

        <StatusList />
      </div>
    </>
  );
}

/** Every moving part, with a dot and a sentence. */
function StatusList() {
  const [components, setComponents] = useState<Component[] | null>(null);

  useEffect(() => {
    api
      .get<{ components: Component[] }>("/status")
      .then((body) => setComponents(body.components))
      .catch(() => setComponents([]));
  }, []);

  if (!components?.length) return null;

  return (
    <div style={{ marginTop: "2.5rem" }}>
      <p className="kicker">Status</p>
      <ul className="status-list">
        {components.map((component) => (
          <li key={component.key} className={`status-row ${component.state}`}>
            <span className="status-dot" aria-hidden="true" />
            <span>
              <span className="status-label">{component.label}</span>
              <span className="status-detail">{component.detail}</span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** One number and what it counts. */
function Stat({ value, label }: { value: number; label: string }) {
  return (
    <div className="stat">
      <dt>{value.toLocaleString()}</dt>
      <dd>{label}</dd>
    </div>
  );
}
