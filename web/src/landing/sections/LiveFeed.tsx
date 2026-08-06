import Reveal from "../components/Reveal";
import { TelegramIcon } from "../components/Icons";

const COMMANDS = [
  { cmd: "/top", desc: "high scorers with no CV built yet" },
  { cmd: "/stats", desc: "pipeline health and daily volumes" },
  { cmd: "/detail <co>", desc: "full posting, score breakdown" },
  { cmd: "/outreach <co>", desc: "cold-email draft, the highest-converting channel" },
  { cmd: "/interview <co>", desc: "a per-job interview crib sheet" },
  { cmd: "/sources", desc: "what every source found this cycle" },
  { cmd: "/rate", desc: "teach the model directly" },
];

// Fictional jobs (never real pipeline output), but laid out exactly like the real bot's stream messages.
const STREAM_MESSAGES = [
  {
    title: "🟢 91 · Senior Backend Engineer",
    meta: ["Acme Robotics · linkedin · 👥 12 🟢 · <1h", "🌍 Remote", "💰 not listed · 🎯 ask 75k–90k EUR/yr"],
    link: "linkedin.com/jobs/view/4412…",
  },
  {
    title: "🟢 84 · Python Engineer, Data Platform",
    meta: ["Northwind · greenhouse · 👥 25 🟡 · 2h", "📍 London, England, UK", "💰 £65–80k listed · 🎯 ask £70k"],
    link: "boards.greenhouse.io/northwind/…",
  },
];

export default function LiveFeed() {
  return (
    <section id="live" className="section">
      <Reveal>
        <p className="kicker">
          <span className="num">03</span> Live feed
        </p>
        <h2>Everything arrives in Telegram. Everything is controlled from Telegram.</h2>
        <p className="lede">
          Two surfaces, one free bot: a <strong>streams channel</strong> where
          scored jobs land as they are found, and a <strong>private
          chat</strong> that is your control plane for stats, on-demand
          documents, outreach drafts and interview prep.
        </p>
        <p className="lede">
          <strong>Why Telegram?</strong> The bot API is completely free,
          notifications are real push on every device you own, inline buttons
          make one-tap actions possible, and the finished PDF is delivered
          into the same chat. There is nothing extra to install or host; the
          whole transport is one bot token.
        </p>
      </Reveal>

      <div className="tg-grid">
        <Reveal delay={0.1}>
          <div className="tg-phone" aria-label="Example Telegram streams channel">
            <div className="tg-head">
              <TelegramIcon size={15} /> Job_Streams · channel
            </div>
            {STREAM_MESSAGES.map((msg) => (
              <div className="tg-msg" key={msg.title}>
                <div className="tg-chan">Job_Streams</div>
                <div className="tg-title">{msg.title}</div>
                <div className="tg-meta">
                  {msg.meta.map((line, i) => (
                    <span key={line}>
                      {i > 0 && <br />}
                      {line}
                    </span>
                  ))}
                </div>
                <div className="tg-link">{msg.link}</div>
                <div className="tg-buttons">
                  <span className="tg-btn">📄 Build CV</span>
                  <span className="tg-btn">✉️ Build Cover</span>
                </div>
              </div>
            ))}
          </div>
        </Reveal>

        <Reveal delay={0.2}>
          <p className="lede">
            When something looks good, one tap builds the document and the PDF
            lands in your chat. Apply. That is the whole loop, and every build
            teaches the model what to look for next.
          </p>
          <div className="cmd-list">
            {COMMANDS.map((c) => (
              <div className="cmd" key={c.cmd}>
                <code>{c.cmd}</code>
                <span>{c.desc}</span>
              </div>
            ))}
          </div>
          <div className="tg-phone tg-phone-bot" aria-label="Example private bot chat">
            <div className="tg-head">
              <TelegramIcon size={15} /> Mahoraga_Bot · private chat
            </div>
            <div className="tg-msg">
              <div className="tg-meta">
                📄 CV: Northwind started (1 building now). It will arrive here
                when ready; you can start more in the meantime.
              </div>
            </div>
            <div className="tg-msg tg-doc">
              <span className="tg-doc-icon">📄</span>
              <span>
                <span className="tg-title">Your_Name_CV.pdf</span>
                <span className="tg-meta">
                  64.2 KB · Python Engineer @ Northwind
                </span>
              </span>
            </div>
          </div>
        </Reveal>
      </div>
    </section>
  );
}
