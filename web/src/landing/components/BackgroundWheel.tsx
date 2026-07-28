import { m, useScroll, useSpring, useTransform } from "framer-motion";
import WheelSVG from "./WheelSVG";

/**
 * The giant faint wheel behind the content. Rotation is bound to scroll
 * progress, so every scroll turns it — the page itself drives the wheel.
 */
export default function BackgroundWheel() {
  const { scrollYProgress } = useScroll();
  const raw = useTransform(scrollYProgress, [0, 1], [0, 480]);
  const rotate = useSpring(raw, { stiffness: 60, damping: 20 });

  return (
    <m.div className="bg-wheel" style={{ rotate }}>
      <WheelSVG spokes={8} offset={22.5} />
    </m.div>
  );
}
