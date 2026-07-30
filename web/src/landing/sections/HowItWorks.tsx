import { useRef, type ReactNode } from "react";
import { m, useInView, useScroll, useSpring } from "framer-motion";
import Reveal from "../components/Reveal";
import { ClaudeIcon } from "../components/Icons";

const STAGES = [
  {
    name: "Scrape",
    body: (
      <>
        LinkedIn, Indeed and Google via JobSpy (through a residential proxy),
        ~940 company ATS boards on a self-growing company list, hiring.cafe,
        VC portfolio boards, RSS boards and optional country-specific sources.
        Around 30 in total, scanned every 15 minutes.
      </>
    ),
  },
  {
    name: "Filter",
    body: (
      <>
        Three-layer deduplication means you never see the same job twice.
        Seniority, language, workplace-mode and applicant-glut filters cut the
        noise before anything is scored.
      </>
    ),
  },
  {
    name: "Score",
    body: (
      <>
        Your own free local model scores every survivor against your profile:
        sentence embeddings, engineered features and a classifier. Scoring
        costs nothing per job and runs entirely on your machine.
      </>
    ),
  },
  {
    name: "Notify",
    body: (
      <>
        Good ones stream into your Telegram channel: score, location,
        remote/hybrid/on-site mode, and the posted salary next to the salary
        you should ask for.
      </>
    ),
  },
  {
    name: "Build",
    body: (
      <>
        Tap <code>📄 Build CV</code> or <code>✉️ Build Cover</code> and one{" "}
        <ClaudeIcon size={14} className="inline-icon" /> Claude call tailors
        the document (ATS keywords, the 6-second test, the bullet formula)
        into a verified two-page PDF delivered straight to your chat.
      </>
    ),
  },
  {
    name: "Adapt",
    body: (
      <>
        Every CV you build becomes a positive training label at 5× weight;
        every skip a negative. The model retrains on your real decisions, so
        the feed keeps sharpening the more you use it.
      </>
    ),
  },
];

export default function HowItWorks() {
  const pipelineRef = useRef<HTMLDivElement>(null);
  // The rail fills as the pipeline moves through the middle of the viewport,
  // and empties again when scrolling back up.
  const { scrollYProgress } = useScroll({
    target: pipelineRef,
    offset: ["start 0.72", "end 0.55"],
  });
  const fill = useSpring(scrollYProgress, { stiffness: 90, damping: 24 });

  return (
    <section id="how" className="section">
      <Reveal>
        <p className="kicker">
          <span className="num">02</span> How it works
        </p>
        <h2>Six stages, four Docker services, one loop.</h2>
        <p className="lede">
          <strong>watcher</strong> (the radar) · <strong>notifier</strong> (the
          Telegram bot) · <strong>scorer-service</strong> (your model,
          hot-swappable) · an on-demand <strong>pipeline</strong> container.
        </p>
      </Reveal>

      <div className="pipeline" ref={pipelineRef}>
        <m.span
          className="pipeline-progress"
          style={{ scaleY: fill }}
          aria-hidden="true"
        />
        {STAGES.map((stage, i) => (
          <Reveal key={stage.name} delay={i * 0.05}>
            <Stage num={i + 1} name={stage.name}>
              {stage.body}
            </Stage>
          </Reveal>
        ))}
      </div>
    </section>
  );
}

interface StageProps {
  num: number;
  name: string;
  children: ReactNode;
}

/** One pipeline stage; its milestone dot lights up (and dims again on the
 *  way back up) as the progress rail sweeps past it. */
function Stage({ num, name, children }: StageProps) {
  const ref = useRef<HTMLDivElement>(null);
  const passed = useInView(ref, { margin: "0px 0px -42% 0px" });

  return (
    <div ref={ref} className={`stage${passed ? " is-passed" : ""}`}>
      <div className="stage-head">
        <span className="stage-num">0{num}</span>
        <h3>{name}</h3>
      </div>
      <p>{children}</p>
    </div>
  );
}
