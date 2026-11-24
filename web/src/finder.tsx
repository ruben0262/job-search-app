/** The search and the filters every list of jobs has: Jobs, Bookmarks and Applied (owner, 2026-11-24: "we
 * should be able to search in all of them"). A search box, one button to the filters over the page, and each
 * filter that is on said under them as a chip that takes it off. What a list offers, and how many of its jobs
 * have each, the server counts (`Offers`); what a filter keeps, the server alone decides
 * (`dashboard_routes.narrowing`), here as everywhere. */
import { useState } from "react";
import type { ReactNode } from "react";

import type { Schema } from "./api";
import { Sheet } from "./ui";

export type Offers = Schema<"Offers">;
type Place = Schema<"FeedPlace">;

/** What every list is narrowed by, named as the server takes it: the search, remote only, and as many
 * continents, countries, towns ("DE/Berlin") and job sites as are picked. The Jobs page saves them with its
 * other filters (`JobFilters`). */
export type Narrow = Pick<Schema<"JobFilters">, "q" | "remote" | "continent" | "country" | "city" | "source">;

/** Nothing narrowed: what "Clear all" goes back to. */
export const WIDE: Omit<Narrow, "q"> = { remote: false, continent: [], country: [], city: [], source: [] };

const LISTS = ["continent", "country", "city", "source"] as const;

/** The question for these filters: each one that is on, by its name. */
export function narrowed(narrow: Narrow, params = new URLSearchParams()): URLSearchParams {
  if (narrow.q.trim()) params.set("q", narrow.q.trim());
  if (narrow.remote) params.set("remote", "true");
  for (const key of LISTS) for (const each of narrow[key]) params.append(key, each);
  return params;
}

/** A country's name from its code, as the browser knows it in English. */
const REGIONS = new Intl.DisplayNames(["en"], { type: "region" });

/** What a place picked is called: as the list offers it, or for one its jobs are no longer in, from the
 * value itself, a town's after its country ("DE/Berlin"). */
function named(places: Place[], value: string): string {
  const offered = places.find((each) => each.value === value)?.label;
  if (offered) return offered;
  if (value.includes("/")) return value.split("/").slice(1).join("/");
  try {
    return REGIONS.of(value) ?? value;
  } catch {
    return value;
  }
}

/** Each filter of these that is on, as the chip under the search says it, with the change that takes it off. */
export function narrowing(narrow: Narrow, offers: Offers): [string, Partial<Narrow>][] {
  const without = (key: (typeof LISTS)[number], value: string) => ({ [key]: narrow[key].filter((each) => each !== value) });
  return [
    ...narrow.continent.map((each): [string, Partial<Narrow>] => [named(offers.continents, each), without("continent", each)]),
    ...narrow.country.map((each): [string, Partial<Narrow>] => [named(offers.countries, each), without("country", each)]),
    ...narrow.city.map((each): [string, Partial<Narrow>] => [named(offers.cities, each), without("city", each)]),
    ...(narrow.remote ? [["Remote only", { remote: false }] as [string, Partial<Narrow>]] : []),
    ...narrow.source.map((each): [string, Partial<Narrow>] => [
      offers.sources.find((board) => board.name === each)?.label ?? each, without("source", each)]),
  ];
}

/** One in, or out if it was in. */
export const toggled = (list: string[], value: string) =>
  list.includes(value) ? list.filter((each) => each !== value) : [...list, value];

/** The sliders on the button that opens the filters. */
export const SLIDERS = <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h9M17 7h3M4 17h3M11 17h9M15 4.5v5M9 14.5v5" /></svg>;

/** The search, and the button to every other filter, saying how many are on; anything else a page keeps at hand
 * between them (Jobs' "Posted"). */
export function Finder({ query, onQuery, label, on, onOpen, children }: {
  query: string;
  onQuery: (query: string) => void;
  /** What the search box says it searches: "Search your jobs". */
  label: string;
  /** How many filters are on. */
  on: number;
  onOpen: () => void;
  children?: ReactNode;
}) {
  return (
    <div className={children ? "finder" : "finder plain"}>
      <input type="search" placeholder={label} aria-label={label} value={query}
             onChange={(event) => onQuery(event.target.value)} />
      {children}
      <button type="button" className="secondary finder-open" aria-haspopup="dialog"
              aria-label={on ? `Filters, ${on} on` : undefined} onClick={onOpen}>
        {SLIDERS}Filters{on ? <span className="finder-count">{on}</span> : null}
      </button>
    </div>
  );
}

/** Each filter that is on, a chip that takes it off, and "Clear all" after them. */
export function FiltersOn<T>({ on, set, clear }: {
  on: [string, Partial<T>][];
  set: (change: Partial<T>) => void;
  clear: () => void;
}) {
  if (!on.length) return null;
  return (
    <div className="filters-on" role="group" aria-label="Filters on">
      {on.map(([label, off], i) => (
        <button key={`${i}:${label}`} type="button" className="chip on" aria-label={`Take off: ${label}`}
                onClick={() => set(off)}>
          {label}<span className="chip-x" aria-hidden="true">×</span>
        </button>
      ))}
      <button type="button" className="chip clear" onClick={clear}>Clear all</button>
    </div>
  );
}

/** The filters, over the page (owner, 2026-11-16: "lots to see and navigate"): each kind under a heading that
 * says what it is, the list behind following every change as it is made, and one button back to it that says
 * how many jobs it holds now. */
export function FilterSheet({ on, clear, showing, onClose, children }: {
  /** Any filter is on: "Clear all" is offered. */
  on: boolean;
  clear: () => void;
  /** What the button back to the list says: "Show 332 jobs". */
  showing: string;
  onClose: () => void;
  children: ReactNode;
}) {
  return (
    <Sheet title="Filters" onClose={onClose}>
      <div className="filter-sheet">{children}</div>
      <div className="filter-foot">
        {on ? <button type="button" className="secondary" onClick={clear}>Clear all</button> : null}
        <button type="button" onClick={onClose}>{showing}</button>
      </div>
    </Sheet>
  );
}

/** How many of each kind show before "Show all": the ones with the most jobs. */
const SHOWN = 10;

/** One row of places to tap, the most jobs first, each with how many: the first few and every one picked, and the
 * rest a tap away; a box to find one by its name where there are many (towns). */
function Places({ label, many, places, picked, onPick, findable = false }: {
  label: string;
  /** What more than one is called: "Show all 13 cities". */
  many: string;
  places: Place[];
  picked: string[];
  onPick: (value: string) => void;
  findable?: boolean;
}) {
  const [all, setAll] = useState(false);
  const [find, setFind] = useState("");
  const sought = find.trim().toLocaleLowerCase();
  const found = sought ? places.filter((each) => each.label.toLocaleLowerCase().includes(sought)) : places;
  const shown = all || sought ? found : found.filter((each, index) => index < SHOWN || picked.includes(each.value));
  // A town picked whose jobs are gone from the list is still shown, so it can be taken off here too.
  const gone = picked.filter((value) => !places.some((each) => each.value === value))
    .map((value): Place => ({ value, label: named(places, value), jobs: 0 }));
  return (
    <div className="place-level">
      <p className="place-label">{label}</p>
      {findable && places.length > SHOWN ? (
        <input type="search" className="place-find" placeholder={`Find a ${label.toLowerCase()}`}
               aria-label={`Find a ${label.toLowerCase()}`} value={find} onChange={(event) => setFind(event.target.value)} />
      ) : null}
      <div className="chips" role="group" aria-label={label}>
        {[...gone, ...shown].map((each) => {
          const pressed = picked.includes(each.value);
          return (
            <button key={each.value} type="button" className={pressed ? "chip on" : "chip"} aria-pressed={pressed}
                    onClick={() => onPick(each.value)}>
              {each.label} <span className="count">{each.jobs}</span>
            </button>
          );
        })}
        {sought && !found.length ? <span className="small muted">No {label.toLowerCase()} called that.</span> : null}
      </div>
      {!sought && found.length > SHOWN ? (
        <button type="button" className="linklike place-more" aria-expanded={all} onClick={() => setAll(!all)}>
          {all ? `Show fewer ${many}` : `Show all ${found.length} ${many}`}
        </button>
      ) : null}
    </div>
  );
}

/** Where (owner, 2026-11-24: "narrow down continent, then country and then city as well, AND we should be able to
 * choose multiple of each, with remote jobs still being an option"): the continents their jobs are on, then the
 * countries, on the continents picked, then the towns, in the countries picked; and remote only. A place picked
 * inside another narrows it to that place (`libs.matching.places`). */
export function WhereSection({ narrow, set, offers }: {
  narrow: Narrow;
  set: (change: Partial<Narrow>) => void;
  offers: Offers;
}) {
  const { continent: continents, country: countries, city: cities, remote } = narrow;
  // Each kind offered inside what is picked above it, and every one picked: a country's towns once it is
  // picked; with no country picked, the towns on the continents picked; with neither, every town.
  const inside = (each: Place, scope: string[]) => !scope.length || scope.includes(each.within ?? "");
  const onContinents = offers.countries.filter((each) => inside(each, continents) || countries.includes(each.value));
  const scope = countries.length ? countries : continents.length ? onContinents.map((each) => each.value) : [];
  const towns = offers.cities.filter((each) => inside(each, scope) || cities.includes(each.value));
  return (
    <section className="filter-group">
      <h3>Where</h3>
      <p className="filter-hint">Pick as many as you like. Tap a country to see its cities.</p>
      {offers.continents.length > 1 || continents.length ? (
        <Places label="Continent" many="continents" places={offers.continents} picked={continents}
                onPick={(value) => set({ continent: toggled(continents, value) as Narrow["continent"] })} />
      ) : null}
      {offers.countries.length > 1 || countries.length ? (
        <Places label="Country" many="countries" places={onContinents} picked={countries}
                onPick={(value) => set({ country: toggled(countries, value) })} />
      ) : null}
      {towns.length || cities.length ? (
        <Places label="City" many="cities" places={towns} picked={cities} findable
                onPick={(value) => set({ city: toggled(cities, value) })} />
      ) : null}
      <label className="pick">
        <input type="checkbox" role="switch" checked={remote} onChange={() => set({ remote: !remote })} />
        <span>Remote jobs only</span>
      </label>
    </section>
  );
}

/** The job sites the list's jobs came from, as many as somebody taps (owner, 2026-11-24: "name that job sites and
 * we should be able to select at least one, and more as well"). */
export function SitesSection({ narrow, set, offers }: {
  narrow: Narrow;
  set: (change: Partial<Narrow>) => void;
  offers: Offers;
}) {
  if (offers.sources.length < 2 && !narrow.source.length) return null;
  return (
    <section className="filter-group">
      <h3>Job sites</h3>
      <p className="filter-hint">Show only jobs from the sites you tap. None tapped: every site.</p>
      <div className="chips" role="group" aria-label="Job sites">
        {offers.sources.map((each) => {
          const pressed = narrow.source.includes(each.name);
          return (
            <button key={each.name} type="button" className={pressed ? "chip on" : "chip"} aria-pressed={pressed}
                    onClick={() => set({ source: toggled(narrow.source, each.name) })}>
              {each.label} <span className="count">{each.jobs}</span>
            </button>
          );
        })}
      </div>
    </section>
  );
}

/** "Show 12 jobs", while the list is not being read again. */
export function showing(busy: boolean, total: number, cut = false): string {
  return busy ? "Finding your jobs…" : `Show ${total}${cut ? "+" : ""} ${total === 1 && !cut ? "job" : "jobs"}`;
}
