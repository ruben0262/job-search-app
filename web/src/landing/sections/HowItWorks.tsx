import { useRef, type ReactNode } from "react";
import { m, useInView, useScroll, useSpring } from "framer-motion";
import Reveal from "../components/Reveal";

const STAGES = [
  {
    name: "Collect",
    body: (
      <>
        Every 15 minutes, new jobs from job boards, company career pages and LinkedIn.
      </>
    ),
  },
  {
    name: "Clean",
    body: (
      <>
        Duplicates are removed, so you never see the same job twice. Jobs in
        languages you do not work in, or in places you cannot work, are left out.
      </>
    ),
  },
  {
    name: "Match",
    body: (
      <>
        Your green words find jobs and your red words hide them. A word in the
        title counts more than one in the text, and every job shows which of
        your words found it.
      </>
    ),
  },
  {
    name: "Tell",
    body: (
      <>
        Matches wait on your Jobs page. If you want them on your phone too,
        connect Telegram in one tap or turn on browser notifications.
      </>
    ),
  },
  {
    name: "Build",
    body: (
      <>
        Tap <code>Make my CV</code> or <code>Make my cover letter</code>. It is
        built from your own CV with what fits the job first, and ready to
        download in seconds.
      </>
    ),
  },
  {
    name: "Refine",
    body: (
      <>
        Change your words any time. Save a new CV and we suggest new words to
        find jobs with, and new words to hide.
      </>
    ),
  },
];

export default function HowItWorks() {
  const pipelineRef = useRef<HTMLDivElement>(null);
  // The rail fills as the pipeline moves through the middle of the viewport.
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
        <h2>Six steps, always on.</h2>
        <p className="lede">
          It <strong>collects</strong> jobs every 15 minutes, <strong>matches</strong> them to
          your words, and <strong>makes</strong> your documents when you ask.
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

/** One pipeline stage; its milestone dot lights up. */
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
