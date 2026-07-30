import { m } from "framer-motion";

const rise = {
  initial: { opacity: 0, y: 32 },
  animate: { opacity: 1, y: 0 },
};

interface HeroProps {
  onExplore: () => void;
}

export default function Hero({ onExplore }: HeroProps) {
  return (
    <section id="top" className="section hero">
      <m.p
        className="kicker"
        {...rise}
        transition={{ duration: 0.7, delay: 0.05 }}
      >
        The job-search radar
      </m.p>

      <m.h1
        className="hero-title"
        {...rise}
        transition={{ duration: 0.8, delay: 0.15 }}
      >
        Mahoraga
      </m.h1>

      <m.p
        className="hero-tag"
        {...rise}
        transition={{ duration: 0.8, delay: 0.3 }}
      >
        The job-search radar that adapts to you.
      </m.p>

      <m.p
        className="hero-sub"
        {...rise}
        transition={{ duration: 0.8, delay: 0.4 }}
      >
        It watches ~30 job sources around the clock, scores every new posting
        against your profile with a model that learns your taste, and streams
        the good ones to your Telegram. Each one is a tap away from a
        tailored, ATS-ready CV.
      </m.p>

      <m.div
        className="hero-ctas"
        {...rise}
        transition={{ duration: 0.8, delay: 0.5 }}
      >
        <a className="btn btn-solid" href="#/signup">
          Create account
        </a>
        <a className="btn btn-ghost" href="#/signin">
          Sign in
        </a>
        <button className="btn btn-ghost" onClick={onExplore}>
          How it works ↓
        </button>
      </m.div>

      <m.div
        className="hero-stats"
        {...rise}
        transition={{ duration: 0.8, delay: 0.65 }}
      >
        <div className="stat">
          <div className="stat-v">~30</div>
          <div className="stat-k">Sources watched</div>
        </div>
        <div className="stat">
          <div className="stat-v">15 min</div>
          <div className="stat-k">Scan cycle</div>
        </div>
        <div className="stat">
          <div className="stat-v">~940</div>
          <div className="stat-k">Company boards</div>
        </div>
        <div className="stat">
          <div className="stat-v">100%</div>
          <div className="stat-k">Yours alone</div>
        </div>
      </m.div>

      <div className="scroll-hint">Scroll · the wheel turns</div>
    </section>
  );
}
