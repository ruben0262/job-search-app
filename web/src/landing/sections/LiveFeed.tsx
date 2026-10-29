import Reveal from "../components/Reveal";
import { TelegramIcon } from "../components/Icons";

// Fictional jobs (never real pipeline output), laid out like the real Jobs page and alert cards.
const ALERT = { title: "Python Engineer, Data Platform", meta: "Northwind · London · 2 hours ago",
                why: "python, data platform" };
const JOBS = [
  { title: "Senior Backend Engineer", meta: "Acme Robotics · Remote · 1 hour ago", why: "backend engineer" },
  ALERT,
];

export default function LiveFeed() {
  return (
    <section id="live" className="section">
      <Reveal>
        <p className="kicker">
          <span className="num">03</span> Your jobs
        </p>
        <h2>Every match says why it is there.</h2>
        <p className="lede">
          Your Jobs page lists every match, newest first, with the words that found it. One tap
          makes the CV or the cover letter; another marks the job as applied.
        </p>
      </Reveal>

      <div className="tg-grid">
        <Reveal delay={0.1}>
          <div className="tg-phone" aria-label="Example Jobs page">
            <div className="tg-head">Jobs · 2 match your words</div>
            {JOBS.map((job) => (
              <div className="tg-msg" key={job.title}>
                <div className="tg-title">{job.title}</div>
                <div className="tg-meta">
                  {job.meta}
                  <br />
                  Why: {job.why}
                </div>
                <div className="tg-buttons">
                  <span className="tg-btn">Make my CV</span>
                  <span className="tg-btn">Make my cover letter</span>
                </div>
              </div>
            ))}
          </div>
        </Reveal>

        <Reveal delay={0.2}>
          <p className="lede">
            <strong>On your phone, if you like.</strong> Connect Telegram in one tap, or turn on
            browser notifications, and new matches arrive as they are found, each with a link
            straight to its CV.
          </p>
          <div className="tg-phone tg-phone-bot" aria-label="Example phone alert">
            <div className="tg-head">
              <TelegramIcon size={15} /> Mahoraga_Bot
            </div>
            <div className="tg-msg">
              <div className="tg-title">{ALERT.title}</div>
              <div className="tg-meta">
                {ALERT.meta}
                <br />
                Why: {ALERT.why}
              </div>
              <div className="tg-buttons">
                <span className="tg-btn">Open job</span>
                <span className="tg-btn">Make my CV</span>
              </div>
            </div>
          </div>
        </Reveal>
      </div>
    </section>
  );
}
