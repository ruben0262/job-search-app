import Reveal from "../components/Reveal";
import { ClaudeIcon, TelegramIcon } from "../components/Icons";

export default function GetStarted() {
  return (
    <section id="start" className="section">
      <Reveal>
        <p className="kicker">
          <span className="num">04</span> Get started
        </p>
        <h2>Fifteen minutes of setup. Then it hunts while you sleep.</h2>
      </Reveal>

      <div className="paths">
        <Reveal delay={0.05}>
          <div className="path">
            <div className="path-tag">Step 1 · ~15 minutes</div>
            <h3>Set it up once</h3>
            <p className="path-sub">
              Create an account and a short guided setup walks you through everything: your CV and
              history, a free Telegram bot, your own{" "}
              <ClaudeIcon size={14} className="inline-icon" /> Claude key, and what to search for.
              Nothing technical; every fiddly step has a two-minute walkthrough.
            </p>
            <ul>
              <li>Your CV and a written history</li>
              <li>
                A free <TelegramIcon size={13} className="inline-icon" /> Telegram bot for delivery
              </li>
              <li>Keywords, locations, and how fresh is fresh</li>
            </ul>
          </div>
        </Reveal>

        <Reveal delay={0.12}>
          <div className="path">
            <div className="path-tag">Step 2 · It runs itself</div>
            <h3>The radar takes over</h3>
            <p className="path-sub">
              A conversation with Claude turns your documents into a profile, your first jobs are
              scored against it, and a model of your taste is trained. From then on every new
              posting is ranked the moment it is found, and the good ones land in your Telegram
              with a tailored CV one tap away.
            </p>
            <ul>
              <li>Jobs stream to your channel as they appear</li>
              <li>One tap builds an ATS-ready, two-page CV</li>
              <li>Every decision you make sharpens the model</li>
            </ul>
          </div>
        </Reveal>
      </div>

      <Reveal delay={0.15}>
        <p className="lore">
          Cold start included: the model does not exist on day one. It needs a few hundred labelled
          jobs, collected in cheap batches over a day or two, and <em>the radar comes alive the
          moment the first model is trained</em>. From then on, scoring is free and instant.
        </p>
      </Reveal>

      <Reveal delay={0.18}>
        <p className="honest">
          Fair warning: this is honest software, not magic. It runs on your own Claude key so you
          can see exactly what it costs, delivery needs a free Telegram account, and LinkedIn
          coverage wants a small residential proxy of your own. What you get back is a radar that
          never sleeps.
        </p>
        <div className="hero-ctas">
          <a className="btn btn-solid" href="#/signup">
            Create your account
          </a>
        </div>
      </Reveal>
    </section>
  );
}
