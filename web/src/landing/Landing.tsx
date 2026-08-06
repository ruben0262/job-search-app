/** The landing page, ported from `mahoraga-jobs-web` (mahoraga.rubenlekli.com) on 2026-07-30. */
import { LazyMotion, domAnimation } from "framer-motion";
import { useCallback, useEffect, useState } from "react";

import "./landing.css";
import BackgroundWheel from "./components/BackgroundWheel";
import Wheel from "./components/Wheel";
import { SECTIONS } from "./sections";
import Footer from "./sections/Footer";
import GetStarted from "./sections/GetStarted";
import Hero from "./sections/Hero";
import HowItWorks from "./sections/HowItWorks";
import LiveFeed from "./sections/LiveFeed";
import WhatItIs from "./sections/WhatItIs";

export function Landing() {
  const [active, setActive] = useState(0);

  useEffect(() => {
    let ticking = false;
    const onScroll = () => {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(() => {
        ticking = false;
        const marker = window.scrollY + window.innerHeight * 0.38;
        let current = 0;
        SECTIONS.forEach((section, i) => {
          const el = document.getElementById(section.id);
          if (el && el.offsetTop <= marker) current = i;
        });
        setActive(current);
      });
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  const goTo = useCallback((i: number) => {
    const section = SECTIONS[i];
    if (section) document.getElementById(section.id)?.scrollIntoView({ behavior: "smooth" });
  }, []);

  return (
    <LazyMotion features={domAnimation} strict>
      <div className="landing">
        <div className="landing-backdrop" aria-hidden="true" />
        <BackgroundWheel />
        <Wheel active={active} onSelect={goTo} />
        <main className="page">
          <Hero onExplore={() => goTo(2)} />
          <WhatItIs />
          <HowItWorks />
          <LiveFeed />
          <GetStarted />
          <Footer />
        </main>
      </div>
    </LazyMotion>
  );
}
