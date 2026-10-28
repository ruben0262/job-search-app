/** The overview. */
import { useEffect, useState } from "react";

import { api, type Dashboard } from "../api";
import type { components } from "../api-types";
import { Banner } from "../ui";

type Component = components["schemas"]["StatusComponent"];
type StatusReport = components["schemas"]["StatusReport"];

export function DashboardPage({ dashboard }: { dashboard: Dashboard; onChanged: () => void }) {
  if (dashboard.stage === "onboarding") return null;
  const { banners, matching, applied, telegram } = dashboard;

  return (
    <>
      {banners.map((banner) => (
        <Banner key={banner.key} level={banner.level}>
          {banner.text}
        </Banner>
      ))}

      <div className="overview">
        <p className="kicker">Overview</p>
        <h1>{matching ? "Jobs that match your words" : "No jobs match your words yet"}</h1>

        <a href="#/feed">
          <button>See your jobs</button>
        </a>

        <dl className="stats">
          <Stat value={matching} label="jobs matching now" />
          <Stat value={applied} label="applications recorded" />
        </dl>

        <p className="facts">Telegram is {telegram}.</p>

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
      .get<StatusReport>("/status")
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
