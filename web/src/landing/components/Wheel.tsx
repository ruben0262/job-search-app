import { useEffect, useRef } from "react";
import { m, useSpring, useTransform, type MotionValue } from "framer-motion";
import { SECTIONS } from "../sections";
import WheelSVG from "./WheelSVG";

const N = SECTIONS.length;
const STEP = 360 / N;
// Node anchors live on the spoke ring: SVG r=170 in a 400 viewBox,
// scaled to the CSS wheel size of 380px → 170/400 * 380.
const NODE_RADIUS = (170 / 400) * 380;
// Degrees of pointer travel below which a pointerdown still counts as a click.
const DRAG_THRESHOLD = 3;

interface WheelProps {
  active: number;
  onSelect: (index: number) => void;
}

/**
 * The wheel navigation. Spoke i carries section i; the wheel turns so the
 * active spoke always points left, into the page. Rotation takes the
 * shortest path (cumulative angle, so 5 → 0 clicks forward, not unwinds).
 * The wheel can also be grabbed and spun: on release it snaps to the
 * nearest spoke and navigates to that section.
 */
export default function Wheel({ active, onSelect }: WheelProps) {
  const cumulative = useRef(180 - active * STEP);
  const prev = useRef(active);
  // While a drag-initiated smooth scroll is in flight, the wheel is already
  // snapped; ignore the intermediate active-section updates until it lands.
  const scrollTarget = useRef<number | null>(null);
  const rotation = useSpring(cumulative.current, {
    stiffness: 55,
    damping: 13,
    mass: 1.1,
  });

  useEffect(() => {
    if (scrollTarget.current !== null) {
      prev.current = active;
      if (active === scrollTarget.current) scrollTarget.current = null;
      return;
    }
    let delta = active - prev.current;
    if (delta > N / 2) delta -= N;
    if (delta < -N / 2) delta += N;
    cumulative.current -= delta * STEP;
    prev.current = active;
    rotation.set(cumulative.current);
  }, [active, rotation]);

  const rotorRef = useRef<HTMLDivElement>(null);
  const drag = useRef({ active: false, last: 0, moved: 0 });

  const pointerAngle = (e: React.PointerEvent) => {
    const rect = rotorRef.current!.getBoundingClientRect();
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    return (Math.atan2(e.clientY - cy, e.clientX - cx) * 180) / Math.PI;
  };

  const onPointerDown = (e: React.PointerEvent) => {
    drag.current = { active: true, last: pointerAngle(e), moved: 0 };
    // No pointer capture yet: capturing here would redirect the eventual
    // click away from the spoke buttons and break plain dot-clicks.
  };

  const onPointerMove = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d.active) return;
    const a = pointerAngle(e);
    let delta = a - d.last;
    if (delta > 180) delta -= 360;
    if (delta < -180) delta += 360;
    d.last = a;
    d.moved += Math.abs(delta);
    // Only once it is clearly a drag, capture the pointer so the spin
    // keeps tracking even when the cursor leaves the wheel.
    if (d.moved > DRAG_THRESHOLD && !rotorRef.current?.hasPointerCapture(e.pointerId)) {
      rotorRef.current?.setPointerCapture(e.pointerId);
    }
    cumulative.current += delta;
    rotation.jump(cumulative.current);
  };

  const onPointerUp = () => {
    const d = drag.current;
    if (!d.active) return;
    d.active = false;
    // Snap to the nearest spoke pointing left (180°) and navigate there.
    const idx = Math.round((180 - cumulative.current) / STEP);
    cumulative.current = 180 - idx * STEP;
    rotation.set(cumulative.current);
    const section = ((idx % N) + N) % N;
    if (d.moved > DRAG_THRESHOLD && section !== prev.current) {
      prev.current = section;
      scrollTarget.current = section;
      onSelect(section);
    }
  };

  // A real drag should not also fire the spoke button underneath.
  const onClickCapture = (e: React.MouseEvent) => {
    if (drag.current.moved > DRAG_THRESHOLD) {
      e.preventDefault();
      e.stopPropagation();
    }
  };

  return (
    <nav className="wheel-nav" aria-label="Sections">
      <m.div
        ref={rotorRef}
        className="wheel-rotor"
        style={{ rotate: rotation }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onClickCapture={onClickCapture}
      >
        <WheelSVG spokes={N} />
        {SECTIONS.map((section, i) => (
          <WheelNode
            key={section.id}
            label={section.label}
            angle={i * STEP}
            rotation={rotation}
            isActive={i === active}
            pulseDelay={i * 0.4}
            onClick={() => onSelect(i)}
          />
        ))}
      </m.div>
    </nav>
  );
}

interface WheelNodeProps {
  label: string;
  angle: number;
  rotation: MotionValue<number>;
  isActive: boolean;
  /** Stagger (seconds) for the idle pulse that hints the dots are clickable. */
  pulseDelay: number;
  onClick: () => void;
}

function WheelNode({ label, angle, rotation, isActive, pulseDelay, onClick }: WheelNodeProps) {
  // Counter-rotate so the label stays horizontal whatever the wheel does.
  const counter = useTransform(rotation, (r) => -(r + angle));

  return (
    <div
      className={`wheel-node${isActive ? " is-active" : ""}`}
      style={{ transform: `rotate(${angle}deg) translateX(${NODE_RADIUS}px)` }}
    >
      <m.div style={{ rotate: counter }}>
        <div className="wheel-node-inner">
          <button onClick={onClick} aria-current={isActive ? "true" : undefined}>
            <span className="wheel-dot" style={{ animationDelay: `${pulseDelay}s` }} />
            <span className="wheel-label">{label}</span>
          </button>
        </div>
      </m.div>
    </div>
  );
}
