import Reveal from "../components/Reveal";

export default function WhatItIs() {
  return (
    <section id="what" className="section">
      <Reveal>
        <p className="kicker">
          <span className="num">01</span> What it is
        </p>
        <h2>A 24/7 radar for the freshest slice of the job market.</h2>
      </Reveal>
      <Reveal delay={0.1}>
        <p className="lede">
          Mahoraga is a <strong>job-hunting radar that works for you alone</strong>. It scans
          LinkedIn, Indeed, Google, ~940 company career boards (Greenhouse, Ashby, Lever, Workday,
          Personio, Workable and more), hiring.cafe, VC portfolio boards and RSS feeds every 15
          minutes, all day, every day.
        </p>
        <p className="lede">
          Every new posting is deduplicated three ways, filtered for
          seniority, language and workplace mode, then scored by{" "}
          <strong>a model that belongs to you</strong>: sentence embeddings,
          engineered features and a classifier trained on your own decisions.
          Scoring costs nothing per job, and nobody else's model ever ranks
          your search.
        </p>
        <p className="lede">
          The result: jobs land on your phone{" "}
          <strong>minutes after they are posted</strong>, while a posting has
          5 applicants instead of 500. Each arrives scored against your
          profile, with the posted salary next to the salary you should ask
          for, and a tailored two-page CV one tap away. Nothing is fabricated;
          every claim traces back to your real profile. The name comes from
          the wheel that adapts to whatever it faces: the longer it runs, the
          more precisely it is shaped to you.
        </p>
      </Reveal>
    </section>
  );
}
