import { useCallback, useEffect, useState } from "react";
import { SECTIONS } from "./sections";
import Wheel from "./components/Wheel";
import BackgroundWheel from "./components/BackgroundWheel";
import Hero from "./sections/Hero";
import WhatItIs from "./sections/WhatItIs";
import HowItWorks from "./sections/HowItWorks";
import LiveFeed from "./sections/LiveFeed";
import GetStarted from "./sections/GetStarted";
import Footer from "./sections/Footer";

export default function App() {
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
        // `entries()` rather than an index, so the element is narrowed rather than asserted —
        // this tsconfig has `noUncheckedIndexedAccess`, which the landing page's own did not.
        for (const [index, section] of SECTIONS.entries()) {
          const el = document.getElementById(section.id);
          if (el && el.offsetTop <= marker) current = index;
        }
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
    <>
      <div className="backdrop" aria-hidden="true" />
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
    </>
  );
}
