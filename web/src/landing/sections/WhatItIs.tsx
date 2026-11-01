import Reveal from "../components/Reveal";

export default function WhatItIs() {
  return (
    <section id="what" className="section">
      <Reveal>
        <p className="kicker">
          <span className="num">01</span> What it is
        </p>
        <h2>Your job search, on autopilot.</h2>
      </Reveal>
      <Reveal delay={0.1}>
        <p className="lede">
          Mahoraga collects new jobs <strong>every 15 minutes, all day, every day</strong>: from
          job boards, from company career pages, and from LinkedIn.
        </p>
        <p className="lede">
          <strong>You decide what you see.</strong> Green words find jobs, like "nurse" or
          "backend engineer". Red words hide them, like "senior" or "unpaid". We suggest both
          from your CV, and you tap to keep the ones you like. Nothing is hidden: every job
          says which of your words it matched.
        </p>
        <p className="lede">
          When a job looks right, <strong>one tap makes a CV and a cover letter</strong> for
          it: built from your own CV, with the parts that fit that job first, ready to download
          in seconds. Nothing is made up; every line comes from what you wrote. The name comes
          from the wheel that adapts to whatever it faces. Here, it is shaped by your words.
        </p>
      </Reveal>
    </section>
  );
}
