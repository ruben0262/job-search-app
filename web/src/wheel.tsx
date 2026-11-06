/** The Mahoraga wheel, drawn once: the landing page's halo, the sign-in art, and the brand's mark. */

const round = (n: number) => Math.round(n * 100) / 100;
const at = (deg: number, r: number) => {
  const a = (deg * Math.PI) / 180;
  return [round(200 + r * Math.cos(a)), round(200 + r * Math.sin(a))] as const;
};

/** Rims, ticks, spokes with a diamond each, a hub. Spoke 0 points up and `lit` is lit; every part has a
 * class, so each place that draws the wheel colours it in its own CSS. */
export function Wheel({ spokes = 8, ticks = 48, lit, className }: {
  spokes?: number;
  ticks?: number;
  lit?: number;
  className?: string;
}) {
  return (
    <svg className={className ? `wheel ${className}` : "wheel"} viewBox="0 0 400 400" fill="none" aria-hidden="true">
      <circle className="w-outer" cx="200" cy="200" r="196" />
      <circle className="w-rim" cx="200" cy="200" r="186" />
      <circle className="w-inner" cx="200" cy="200" r="170" />
      {Array.from({ length: ticks }, (_, i) => {
        const deg = (360 / ticks) * i;
        // The long ticks are the ones a spoke ends on.
        const major = Math.abs((deg + 90) % (360 / spokes)) < 0.01;
        const [x1, y1] = at(deg, 186);
        const [x2, y2] = at(deg, major ? 196 : 191);
        return <line key={i} className={major ? "w-tick major" : "w-tick"} x1={x1} y1={y1} x2={x2} y2={y2} />;
      })}
      {Array.from({ length: spokes }, (_, i) => {
        const deg = -90 + (360 / spokes) * i;
        const [x1, y1] = at(deg, 36);
        const [x2, y2] = at(deg, 170);
        const [mx, my] = at(deg, 98);
        return (
          <g key={i} className={i === lit ? "w-spoke lit" : "w-spoke"}>
            <line x1={x1} y1={y1} x2={x2} y2={y2} />
            <rect className="w-diamond" x={round(mx - 4.5)} y={round(my - 4.5)} width="9" height="9"
                  transform={`rotate(${deg + 45} ${mx} ${my})`} />
          </g>
        );
      })}
      <circle className="w-hub" cx="200" cy="200" r="36" />
      <circle className="w-hub-in" cx="200" cy="200" r="25" />
      <circle className="w-core" cx="200" cy="200" r="5" />
    </svg>
  );
}

/** The wheel at the size of a letter: a rim, eight spokes, a hub. */
export function Mark() {
  return (
    <svg className="mark" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"
         strokeLinecap="round" aria-hidden="true">
      <circle cx="12" cy="12" r="9.6" />
      <circle cx="12" cy="12" r="2.7" />
      <path d="M12 2.4v6.9M12 14.7v6.9M2.4 12h6.9M14.7 12h6.9M5.2 5.2l4.9 4.9M13.9 13.9l4.9 4.9M18.8 5.2l-4.9 4.9M10.1 13.9l-4.9 4.9" />
    </svg>
  );
}

/** The wordmark: a link home, or only the name where there is nowhere else to go yet (onboarding). */
export function Brand({ href }: { href?: string }) {
  return href ? (
    <a className="brand" href={href}><Mark />Mahoraga</a>
  ) : (
    <span className="brand"><Mark />Mahoraga</span>
  );
}
