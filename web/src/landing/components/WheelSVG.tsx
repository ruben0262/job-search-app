interface WheelSVGProps {
  spokes: number;
  /** Rotate the whole geometry so spokes line up with node anchors. */
  offset?: number;
  className?: string;
}

const C = 200; // viewBox centre
const R_OUTER = 196;
const R_RIM = 186;
const R_SPOKE = 170;
const R_HUB = 36;

/**
 * The Mahoraga wheel — a spoked wheel drawn once and reused by both the
 * nav wheel (one spoke per section) and the background wheel (8 spokes).
 */
export default function WheelSVG({ spokes, offset = 0, className }: WheelSVGProps) {
  const spokeAngles = Array.from({ length: spokes }, (_, i) => offset + (360 / spokes) * i);
  const ticks = Array.from({ length: 48 }, (_, i) => (360 / 48) * i);

  const point = (angleDeg: number, r: number) => {
    const a = (angleDeg * Math.PI) / 180;
    return { x: C + r * Math.cos(a), y: C + r * Math.sin(a) };
  };

  return (
    <svg viewBox="0 0 400 400" className={className} fill="none" aria-hidden="true">
      <circle cx={C} cy={C} r={R_OUTER} stroke="var(--faint)" strokeWidth="1" />
      <circle cx={C} cy={C} r={R_RIM} stroke="var(--faint)" strokeWidth="1" />
      <circle cx={C} cy={C} r={R_SPOKE} stroke="var(--hairline)" strokeWidth="1" />

      {ticks.map((a) => {
        const p1 = point(a, R_RIM);
        const p2 = point(a, R_RIM + 5);
        return (
          <line
            key={`t${a}`}
            x1={p1.x}
            y1={p1.y}
            x2={p2.x}
            y2={p2.y}
            stroke="var(--hairline)"
            strokeWidth="1"
          />
        );
      })}

      {spokeAngles.map((a) => {
        const p1 = point(a, R_HUB);
        const p2 = point(a, R_SPOKE);
        const mid = point(a, R_HUB + (R_SPOKE - R_HUB) * 0.45);
        return (
          <g key={`s${a}`}>
            <line
              x1={p1.x}
              y1={p1.y}
              x2={p2.x}
              y2={p2.y}
              stroke="var(--faint)"
              strokeWidth="1.5"
            />
            {/* small diamond detail on each spoke */}
            <rect
              x={mid.x - 3.2}
              y={mid.y - 3.2}
              width="6.4"
              height="6.4"
              stroke="var(--faint)"
              strokeWidth="1"
              transform={`rotate(${a + 45} ${mid.x} ${mid.y})`}
            />
          </g>
        );
      })}

      <circle cx={C} cy={C} r={R_HUB} stroke="var(--faint)" strokeWidth="1.5" />
      <circle cx={C} cy={C} r={R_HUB - 10} stroke="var(--hairline)" strokeWidth="1" />
      <circle cx={C} cy={C} r={4} fill="var(--dim)" />
    </svg>
  );
}
