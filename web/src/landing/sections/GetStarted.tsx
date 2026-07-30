import Reveal from "../components/Reveal";
import { GITHUB_URL } from "../sections";
import { ClaudeIcon, GitHubIcon } from "../components/Icons";

export default function GetStarted() {
  return (
    <section id="start" className="section">
      <Reveal>
        <p className="kicker">
          <span className="num">04</span> Run it
        </p>
        <h2>Two paths. Both start with a fork.</h2>
      </Reveal>

      <div className="paths">
        <Reveal delay={0.05}>
          <div className="path">
            <div className="path-tag">Path A · 15 minutes</div>
            <h3>Interactive only</h3>
            <p className="path-sub">
              No Docker, no proxy, no monthly cost beyond Claude. Build your
              profile once, then paste any job posting for an honest fit
              evaluation (it will tell you when a job is not worth applying
              to) and a tailored CV, drafted, reviewed and compiled to a
              verified two-page PDF.
            </p>
            <ul>
              <li>
                Install <ClaudeIcon size={14} className="inline-icon" /> Claude
                Code and Chrome
              </li>
              <li>
                Fork the repo, run <span className="inline-code">/setup</span>
              </li>
              <li>
                Paste a job: <span className="inline-code">/apply &lt;url&gt;</span>
              </li>
            </ul>
          </div>
        </Reveal>

        <Reveal delay={0.12}>
          <div className="path">
            <div className="path-tag">Path B · The full radar</div>
            <h3>24/7 automated</h3>
            <p className="path-sub">
              The whole onboarding is one command. A manager agent runs each
              specialist setup agent in order (profile, boards, companies,
              scorer), checking its work before moving on. About 1–2 hours of
              guided setup, ~$25–30 a month, plus a one-time cold start.
            </p>
            <div className="terminal">
              <div>
                <span className="prompt">$ </span>
                <span className="cmd-line">claude</span>
              </div>
              <div>
                <span className="prompt">&gt; </span>
                <span className="cmd-line">/setup-all</span>
              </div>
              <div>
                <span className="prompt">$ </span>
                <span className="cmd-line">docker compose up -d --build</span>
              </div>
            </div>
          </div>
        </Reveal>
      </div>

      <Reveal delay={0.15}>
        <p className="lore">
          Cold start included: the model does not exist on day one. It needs
          ~800–1,000 labelled jobs, a day or two collected in cheap batches
          (about $0.09 per 50 jobs), and <em>the watcher comes alive the
          moment the first model is trained</em>. From then on, scoring is
          free and instant.
        </p>
      </Reveal>

      <Reveal delay={0.18}>
        <p className="honest">
          Fair warning, straight from the README: this is not a one-click app.
          It asks for a Claude subscription, Docker, a free Telegram account,
          ideally a residential proxy (~$1/GB; LinkedIn is the only source
          that needs it), and optionally a small VPS so the radar runs while
          your machine is off. What you get back is a radar that never sleeps.
        </p>
        <div className="hero-ctas">
          <a className="btn btn-solid" href={GITHUB_URL} target="_blank" rel="noreferrer">
            <GitHubIcon size={16} /> Get the code on GitHub
          </a>
        </div>
      </Reveal>
    </section>
  );
}
