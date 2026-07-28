/**
 * The overview. **It says what to do next; it does not offer four buttons and hope.**
 *
 * The order is not guessable — processing invalidates every label, relabelling costs real money,
 * training is free — so the server decides which action is next and this renders that one. A user
 * who presses them in the wrong order pays for the mistake, which is the whole argument against a
 * toolbar.
 */
import type { Dashboard } from "../api";
import { Banner, Card } from "../ui";

export function DashboardPage({ dashboard }: { dashboard: Dashboard; onChanged: () => void }) {
  if (dashboard.stage === "onboarding") return null;
  const { next, model, feed, banners, notifications, telegram } = dashboard;

  return (
    <>
      <h1>Overview</h1>

      {banners.map((banner) => (
        <Banner key={banner.key} level={banner.level}>
          {banner.text}
        </Banner>
      ))}

      <Card title="What is next">
        <p>{next.message}</p>
        {next.action === "confirm" ? (
          <a href="#/process">
            <button>Review your profile</button>
          </a>
        ) : next.action === "process" ? (
          <a href="#/process">
            <button>Process your documents</button>
          </a>
        ) : next.action === "upload" ? (
          <a href="#/settings">
            <button>Upload your CV</button>
          </a>
        ) : (
          <p className="muted small">Nothing needs your attention.</p>
        )}
      </Card>

      <Card title="Your model">
        {model.servable ? (
          <p>
            Version {model.version}, trained on your own judgement
            {model.trained_at ? ` on ${new Date(model.trained_at).toLocaleDateString()}` : ""}.
          </p>
        ) : (
          <p className="muted">{model.stale_reason}</p>
        )}
        <table>
          <tbody>
            <tr>
              <td>Jobs scored for you</td>
              <td>{feed.scored.toLocaleString()}</td>
            </tr>
            <tr>
              <td>Labels at this profile version</td>
              <td>{feed.labels.toLocaleString()}</td>
            </tr>
            <tr>
              <td>Applications recorded</td>
              <td>{feed.applied.toLocaleString()}</td>
            </tr>
          </tbody>
        </table>
      </Card>

      <Card title="Delivery">
        <p className="small muted">
          Telegram is {telegram}. At most {notifications.per_day_cap} messages a day, checked every{" "}
          {notifications.interval_min} minutes.
        </p>
      </Card>
    </>
  );
}
