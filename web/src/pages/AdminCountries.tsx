/**
 * Admin → Corpus: new jobs through time, all countries together and each one (owner, 2026-11-04 and -05).
 *
 * One line chart: every country together, and the countries ticked in the table under it, a hue each
 * (styles.css, `--series-*`); any of them, the total too, ticked off. A country keeps its hue while it is
 * drawn, whatever else is added or taken away. The table and the numbers under the chart say everything
 * the lines do, without a pointer.
 */
import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";

import { api, type Schema } from "../api";
import { keep, peek } from "../kept";

type CountryChart = Schema<"CountryChart">;
type Country = CountryChart["countries"][number];
type By = CountryChart["by"];
type Series = { key: string; name: string; color: string; values: number[]; area?: boolean };
type Hover = { at: number; chart: string } | null;

/** The ranges offered, in hours (`libs.ops.countries.RANGES_HOURS`): per run to half a day, per hour to two
 * weeks, per day beyond. */
const RANGES = [[1, "1 hour"], [2, "2 hours"], [4, "4 hours"], [8, "8 hours"], [12, "12 hours"],
                [24, "1 day"], [48, "2 days"], [168, "7 days"], [336, "14 days"], [720, "30 days"]] as const;
/** Countries drawn at once, one hue each in a fixed order: more than six hues cannot be told apart. */
const SLOTS = 6;
/** Countries drawn when the page opens: the busiest. */
const FIRST_DRAWN = 5;
/** Rows of the table before the rest are opened. */
const ROWS_SHOWN = 12;

const keyOf = (country: Country) => country.country ?? "none";

export function JobsByCountry() {
  const [hours, setHours] = useState<number>(24);
  const path = `/admin/countries?hours=${hours}`;
  const [chart, setChart] = useState<CountryChart | null>(() => peek<CountryChart>(path));
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  // Each country drawn, and its hue: kept while it is drawn, so no line changes colour under the reader.
  const [slots, setSlots] = useState<Record<string, number> | null>(null);
  // Every country together, as its own line.
  const [together, setTogether] = useState(true);
  const [hover, setHover] = useState<Hover>(null);
  const [all, setAll] = useState(false);

  useEffect(() => {
    let live = true;
    // The last answer for this range at once, if there is one; otherwise the one shown stays, dimmed.
    const kept = peek<CountryChart>(path);
    if (kept) setChart(kept);
    setBusy(true);
    api.get<CountryChart>(path)
      .then((body) => {
        if (!live) return;
        keep(path, body);
        setChart(body);
        setFailed(false);
      })
      .catch(() => live && setFailed(true))
      .finally(() => live && setBusy(false));
    return () => {
      live = false;
    };
  }, [path]);

  useEffect(() => {
    if (chart && slots === null && chart.countries.length) {
      setSlots(Object.fromEntries(chart.countries.slice(0, FIRST_DRAWN).map((each, slot) => [keyOf(each), slot])));
    }
  }, [chart, slots]);

  const toggle = (key: string) =>
    setSlots((now) => {
      const next = { ...now };
      if (key in next) {
        delete next[key];
        return next;
      }
      const taken = new Set(Object.values(next));
      const free = [...Array(SLOTS).keys()].find((slot) => !taken.has(slot));
      return free === undefined ? now : { ...next, [key]: free };
    });

  const picked = slots ?? {};
  const countries = chart?.countries ?? [];
  const starts = chart?.starts ?? [];
  const by: By = chart?.by ?? "hour";
  const step = chart?.bucket_minutes ?? 60;
  const unit = unitName(by, step);
  const total = starts.map((_, index) => countries.reduce((sum, each) => sum + (each.counts[index] ?? 0), 0));
  const sum = total.reduce((a, b) => a + b, 0);
  const drawn: Series[] = countries
    .filter((each) => keyOf(each) in picked)
    .sort((a, b) => picked[keyOf(a)]! - picked[keyOf(b)]!)
    .map((each) => ({ key: keyOf(each), name: each.name, color: hue(picked[keyOf(each)]!), values: each.counts }));
  const lines: Series[] = [
    ...(together ? [{ key: "all", name: "All countries", color: "var(--ink)", values: total, area: true }] : []),
    ...drawn,
  ];
  const busiest = total.indexOf(Math.max(...total));
  const full = Object.keys(picked).length >= SLOTS;
  const rows = all ? countries : countries.slice(0, ROWS_SHOWN);
  const trimmed = chart?.since && starts.length < Math.floor((hours * 60) / step);

  return (
    <section style={{ marginBottom: "2.5rem" }}>
      <h2 style={{ marginTop: 0 }}>Jobs found by country</h2>
      <p className="hint">
        New jobs as they are stored, by the country each is in. &ldquo;Not known&rdquo; is remote or
        worldwide. Tick or untick any line in the table, all countries together included.
      </p>
      <div className="toggles" role="group" aria-label="How far back">
        {RANGES.map(([value, label]) => (
          <button key={value} type="button" className="toggle" aria-pressed={hours === value}
                  onClick={() => setHours(value)}>
            {label}
          </button>
        ))}
      </div>
      {failed ? <p className="error">Could not read the counts.</p> : null}
      {chart ? (
        <div className={busy ? "trend busy" : "trend"} aria-busy={busy}>
          <p className="facts" style={{ marginTop: "1rem" }}>
            LinkedIn jobs waiting for their text: <strong>{chart.waiting.toLocaleString()}</strong>. It
            jumps when a run stores new jobs and falls while their text is read. Higher at the same time
            each day means LinkedIn is read too slowly for the countries picked.
          </p>
          {trimmed ? (
            <p className="facts">Counting began {when(chart.since!, "minutes")}: nothing before it was counted.</p>
          ) : null}
          {countries.length === 0 ? (
            <p className="muted">Nothing counted in this time yet.</p>
          ) : (
            <>
              <div className="trend-head">
                <span className="label">All countries</span>
                <strong>{sum.toLocaleString()}</strong>
                <span className="muted small">
                  busiest {unit}: {total[busiest]!.toLocaleString()}, {when(starts[busiest]!, by)}
                </span>
              </div>
              {lines.length ? (
                <>
                  <ul className="trend-legend" aria-label="Lines drawn">
                    {lines.map((each) => (
                      <li key={each.key}><i className="line-key" style={{ background: each.color }} />{each.name}</li>
                    ))}
                  </ul>
                  <LineChart id="countries" label="Jobs found" endLabels height={300} by={by} step={step}
                             starts={starts} series={lines} hover={hover} onHover={setHover} />
                </>
              ) : <p className="muted small">No line is ticked.</p>}

              <table className="country-table">
                <thead>
                  <tr>
                    <th scope="col">Country</th>
                    <th scope="col" className="num">Jobs</th>
                    <th scope="col" className="num busiest">Busiest {unit}</th>
                    <th scope="col" className="num">Share</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>
                      <label className="pick">
                        <input type="checkbox" checked={together} onChange={() => setTogether(!together)} />
                        <i className="line-key" style={{ background: together ? "var(--ink)" : "transparent" }} />
                        All countries
                      </label>
                    </td>
                    <td className="num">{sum.toLocaleString()}</td>
                    <td className="num busiest">{Math.max(0, ...total).toLocaleString()}</td>
                    <td className="num">{sum ? "100%" : "—"}</td>
                  </tr>
                  {rows.map((each) => {
                    const slot = picked[keyOf(each)];
                    return (
                      <tr key={keyOf(each)}>
                        <td>
                          <label className="pick">
                            <input type="checkbox" checked={slot !== undefined}
                                   disabled={slot === undefined && full} onChange={() => toggle(keyOf(each))} />
                            <i className="line-key" style={{ background: slot === undefined ? "transparent" : hue(slot) }} />
                            {each.name}
                          </label>
                        </td>
                        <td className="num">{each.total.toLocaleString()}</td>
                        <td className="num busiest">{Math.max(...each.counts).toLocaleString()}</td>
                        <td className="num">{sum ? `${Math.round((each.total / sum) * 100)}%` : "—"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              {countries.length > ROWS_SHOWN ? (
                <button type="button" className="linklike more" onClick={() => setAll(!all)}>
                  {all ? "Show fewer countries" : `Show ${countries.length - ROWS_SHOWN} more countries`}
                </button>
              ) : null}

              <details className="trend-numbers">
                <summary>The numbers, {unit} by {unit}</summary>
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th scope="col">{unit.charAt(0).toUpperCase() + unit.slice(1)}</th>
                        <th scope="col" className="num">All</th>
                        {drawn.map((each) => <th key={each.key} scope="col" className="num">{each.name}</th>)}
                      </tr>
                    </thead>
                    <tbody>
                      {starts.map((_, index) => starts.length - 1 - index).map((index) => (
                        <tr key={starts[index]}>
                          <td>{when(starts[index]!, by)}</td>
                          <td className="num">{total[index]!.toLocaleString()}</td>
                          {drawn.map((each) => (
                            <td key={each.key} className="num">{(each.values[index] ?? 0).toLocaleString()}</td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </details>
            </>
          )}
        </div>
      ) : failed ? null : <p className="muted">Reading the counts…</p>}
    </section>
  );
}

/** One or more series against time, one axis, with a crosshair and every series' value under the pointer
 * or the arrow keys. `hover` is shared, so both charts mark the same hour. */
function LineChart({ id, label, series, starts, by, step, height, endLabels = false, hover, onHover }: {
  id: string;
  label: string;
  series: Series[];
  starts: string[];
  by: By;
  step: number;
  height: number;
  endLabels?: boolean;
  hover: Hover;
  onHover: (hover: Hover) => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(720);
  useLayoutEffect(() => {
    const element = box.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => entry && setWidth(Math.max(entry.contentRect.width, 240)));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const n = starts.length;
  const [left, right, top, bottom] = [44, endLabels ? 112 : 18, 18, 24];
  const plotWidth = Math.max(width - left - right, 1);
  const plotHeight = height - top - bottom;
  const ticks = yTicks(Math.max(1, ...series.flatMap((each) => each.values)));
  const ceiling = ticks[ticks.length - 1]!;
  const x = (index: number) => (n <= 1 ? left + plotWidth / 2 : left + (index / (n - 1)) * plotWidth);
  const y = (value: number) => top + plotHeight - (value / ceiling) * plotHeight;
  const at = hover && hover.at < n ? hover.at : null;
  // The last hour or day is still being counted, so the lines end at the one before it, and it is a faint
  // open dot: drawn on, its drop read as one.
  const last = Math.max(n - 2, 0);
  const line = (values: number[], upto: number) =>
    values.slice(0, upto).map((value, index) => `${index ? "L" : "M"}${x(index)},${y(value)}`).join(" ");

  const point = (clientX: number) => {
    const rect = box.current!.getBoundingClientRect();
    const index = n <= 1 ? 0 : Math.round(((clientX - rect.left - left) / plotWidth) * (n - 1));
    onHover({ at: Math.min(Math.max(index, 0), n - 1), chart: id });
  };
  const keys = (event: KeyboardEvent) => {
    const now = at ?? n - 1;
    if (event.key === "ArrowLeft") onHover({ at: Math.max(now - 1, 0), chart: id });
    else if (event.key === "ArrowRight") onHover({ at: Math.min(now + 1, n - 1), chart: id });
    else if (event.key === "Escape") onHover(null);
    else return;
    event.preventDefault();
  };

  // A name at the end of each line, and none where it would sit on another's.
  const named: { key: string; name: string; y: number }[] = [];
  if (endLabels) {
    for (const each of [...series].sort((a, b) => y(a.values[last] ?? 0) - y(b.values[last] ?? 0))) {
      const level = y(each.values[last] ?? 0);
      if (!named.length || level - named[named.length - 1]!.y >= 13) {
        named.push({ key: each.key, name: each.name, y: level });
      }
    }
  }
  // The busiest bucket of the line with a wash under it (all countries together), named.
  const washed = series.find((each) => each.area);
  const peak = washed ? washed.values.indexOf(Math.max(...washed.values)) : -1;
  const clamp = (value: number) => Math.min(Math.max(value, left + 20), left + plotWidth - 20);

  return (
    <div ref={box} className="trend-chart" tabIndex={0}
         aria-label={`${label}, new jobs per ${unitName(by, step)}: arrow keys read each in turn`}
         onPointerMove={(event) => point(event.clientX)} onPointerLeave={() => onHover(null)}
         onFocus={() => onHover({ at: at ?? n - 1, chart: id })} onBlur={() => onHover(null)} onKeyDown={keys}>
      <svg width={width} height={height} role="img" aria-label={`${label}: new jobs per ${unitName(by, step)}`}>
        {ticks.map((tick) => (
          <g key={tick}>
            <line x1={left} x2={left + plotWidth} y1={y(tick)} y2={y(tick)}
                  stroke={tick === 0 ? "var(--faint)" : "var(--hairline)"} />
            <text x={left - 8} y={y(tick) + 3} textAnchor="end" className="trend-axis">
              {ceiling >= 10_000 ? `${tick / 1000}K` : tick.toLocaleString()}
            </text>
          </g>
        ))}
        {xTicks(starts, by, step, plotWidth).map((index) => (
          <text key={index} x={clamp(x(index))} y={height - 6} textAnchor="middle" className="trend-axis">
            {tickLabel(starts[index]!, by)}
          </text>
        ))}
        {series.map((each) => (
          <g key={each.key}>
            {each.area && n > 2 ? (
              <path d={`${line(each.values, last + 1)} L${x(last)},${y(0)} L${x(0)},${y(0)} Z`}
                    fill={each.color} opacity={0.1} />
            ) : null}
            <path d={line(each.values, last + 1)} fill="none" stroke={each.color} strokeWidth={2}
                  strokeLinejoin="round" strokeLinecap="round" />
            <circle cx={x(last)} cy={y(each.values[last] ?? 0)} r={4} fill={each.color}
                    stroke="var(--bg)" strokeWidth={2} />
            {n > 1 ? (
              <circle cx={x(n - 1)} cy={y(each.values[n - 1] ?? 0)} r={3} fill="var(--bg)" stroke={each.color}
                      strokeWidth={1.5} opacity={0.6} />
            ) : null}
          </g>
        ))}
        {named.map((each) => (
          <text key={each.key} x={x(n - 1) + 10} y={each.y + 4} className="trend-name">{each.name}</text>
        ))}
        {washed && peak >= 0 && washed.values[peak] ? (
          <text x={clamp(x(peak))} y={y(washed.values[peak]!) - 8} textAnchor="middle" className="trend-peak">
            {washed.values[peak]!.toLocaleString()}
          </text>
        ) : null}
        {at !== null ? (
          <g pointerEvents="none">
            <line x1={x(at)} x2={x(at)} y1={top} y2={top + plotHeight} stroke="var(--dim)" />
            {series.map((each) => (
              <circle key={each.key} cx={x(at)} cy={y(each.values[at] ?? 0)} r={4} fill={each.color}
                      stroke="var(--bg)" strokeWidth={2} />
            ))}
          </g>
        ) : null}
      </svg>
      {at !== null && hover?.chart === id ? (
        <div className="trend-tip" role="status"
             style={x(at) > width - 210 ? { right: width - x(at) + 14 } : { left: x(at) + 14 }}>
          <div className="when">{when(starts[at]!, by)}{at === n - 1 ? ", so far" : ""}</div>
          {[...series].sort((a, b) => (b.values[at] ?? 0) - (a.values[at] ?? 0)).map((each) => (
            <div key={each.key} className="row">
              <i className="line-key" style={{ background: each.color }} />
              <strong>{(each.values[at] ?? 0).toLocaleString()}</strong>
              <span className="name">{each.name}</span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

/** A country's hue, by its slot. */
function hue(slot: number): string {
  return `var(--series-${slot + 1})`;
}

/** Round steps from zero to at least `peak`, four or so. */
function yTicks(peak: number): number[] {
  const rough = peak / 4;
  const power = 10 ** Math.floor(Math.log10(rough));
  const step = Math.max(1, [1, 2, 5, 10].map((times) => times * power).find((each) => each >= rough)!);
  return Array.from({ length: Math.ceil(peak / step) + 1 }, (_, index) => index * step);
}

/** Which buckets get a label along the bottom: round times, or midnights, or every few days, as many as
 * fit. */
function xTicks(starts: string[], by: By, step: number, plotWidth: number): number[] {
  const room = Math.max(2, Math.floor(plotWidth / 84));
  if (by !== "day") {
    const minute = (at: string) => new Date(at).getHours() * 60 + new Date(at).getMinutes();
    for (const every of [15, 30, 60, 120, 180, 360, 720, 1440].filter((each) => each >= step)) {
      const fit = starts.flatMap((at, index) => (minute(at) % every === 0 ? [index] : []));
      if (fit.length <= room) return fit;
    }
    const midnights = starts.flatMap((at, index) => (new Date(at).getHours() === 0 ? [index] : []));
    return midnights.filter((_, index) => index % Math.ceil(midnights.length / room) === 0);
  }
  const every = [1, 2, 7, 14, 30].find((step) => Math.ceil(starts.length / step) <= room) ?? 30;
  return starts.flatMap((_, index) => ((starts.length - 1 - index) % every === 0 ? [index] : []));
}

function tickLabel(at: string, by: By): string {
  const date = new Date(at);
  if (by === "day" || (date.getHours() === 0 && date.getMinutes() === 0)) {
    return date.toLocaleDateString([], { day: "numeric", month: "short" });
  }
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** What one bucket is called: "15 minutes", "hour" or "day". */
function unitName(by: By, step: number): string {
  return by === "minutes" ? `${step} minutes` : by;
}

/** An hour or a day, in full: "Thu 5 Nov, 20:00" or "Thu 5 Nov". */
function when(at: string, by: By): string {
  const date = new Date(at);
  const day = date.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" });
  return by === "day" ? day : `${day}, ${date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
}
