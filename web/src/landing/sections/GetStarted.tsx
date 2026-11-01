import Reveal from "../components/Reveal";

export default function GetStarted() {
  return (
    <section id="start" className="section">
      <Reveal>
        <p className="kicker">
          <span className="num">04</span> Get started
        </p>
        <h2>A few minutes to set up. Then it searches while you sleep.</h2>
      </Reveal>

      <div className="paths">
        <Reveal delay={0.05}>
          <div className="path">
            <div className="path-tag">Step 1 · a few minutes</div>
            <h3>Set it up once</h3>
            <p className="path-sub">
              Create an account and upload your CV. We read it and suggest the words to search
              with; you check them and tap Looks good. That is all.
            </p>
            <ul>
              <li>Your CV (a cover letter and an "about you" note are optional)</li>
              <li>Words to find jobs with, and words to hide</li>
              <li>Where you can work, and in which languages</li>
            </ul>
          </div>
        </Reveal>

        <Reveal delay={0.12}>
          <div className="path">
            <div className="path-tag">Step 2 · It runs itself</div>
            <h3>Then it works for you</h3>
            <p className="path-sub">
              New jobs are matched to your words the moment they are found. Each one is a tap
              away from a CV and a cover letter made for it.
            </p>
            <ul>
              <li>Every match says why it is there</li>
              <li>One tap makes a CV or a cover letter</li>
              <li>Alerts on your phone, if you want them</li>
            </ul>
          </div>
        </Reveal>
      </div>

      <Reveal delay={0.15}>
        <p className="lore">
          No waiting: your first matches are there as soon as you finish, from jobs already
          collected.
        </p>
      </Reveal>

      <Reveal delay={0.18}>
        <p className="honest">
          Honest by design: there is no hidden score. Every job is shown because of words you
          chose, and every line of your CV comes from what you wrote. Alerts come by Telegram, by
          your browser's notifications, or in one email each morning.
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
