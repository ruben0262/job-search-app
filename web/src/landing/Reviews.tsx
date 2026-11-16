/** What people say, under "See it working": their stars and how many of them at a glance, then every
 * review on rows that drift past, so a hundred of them take the room of ten and a reader scrolling by
 * sees faces, stars and a line or two without stopping (owner, 2026-11-16). A row stops under the pointer
 * or a finger; with motion turned off the rows stand still and scroll by hand. Nothing is drawn while
 * there is no review to show (`reviews.ts`: a sample is never shown on the built site). */
import type { CSSProperties } from "react";

import { type Review, reviews } from "./reviews";

/** How many rows the reviews drift on: enough that no row is longer than a reader will wait for. */
const ROWS = 2;

const STAR = "M10 1.6l2.5 5.4 5.9.7-4.4 4 1.2 5.8L10 14.6l-5.2 2.9L6 11.7l-4.4-4 5.9-.7z";

function Stars({ of }: { of: number }) {
  return (
    <span className="review-stars" role="img" aria-label={`${of} out of 5 stars`}>
      {[1, 2, 3, 4, 5].map((star) => (
        <svg key={star} viewBox="0 0 20 20" className={star <= Math.round(of) ? "on" : undefined} aria-hidden="true">
          <path d={STAR} />
        </svg>
      ))}
    </span>
  );
}

function Card({ review }: { review: Review }) {
  const initials = review.name.split(/\s+/).map((part) => part[0]).slice(0, 2).join("");
  return (
    <li className="review" data-review data-sample={review.sample ? "" : undefined}>
      <div className="review-top">
        <Stars of={review.stars} />
        {review.result ? <span className="review-result">{review.result}</span> : null}
      </div>
      <blockquote>{review.text}</blockquote>
      <footer>
        <span className="review-face" aria-hidden="true">{initials}</span>
        <span><strong>{review.name}</strong><small>{review.role}</small></span>
      </footer>
    </li>
  );
}

export function Reviews() {
  if (!reviews.length) return null;
  const mean = reviews.reduce((sum, review) => sum + review.stars, 0) / reviews.length;
  const rows = Array.from({ length: Math.min(ROWS, reviews.length) }, (_, row) =>
    reviews.filter((_review, i) => i % ROWS === row));
  return (
    <div className="reviews" data-reveal>
      <div className="reviews-head">
        <h3 className="side-title">People it <span className="glow">worked for.</span></h3>
        <p className="reviews-score">
          <Stars of={mean} />
          <strong>{mean.toFixed(1)}</strong> out of 5, from {reviews.length} review{reviews.length === 1 ? "" : "s"}
        </p>
      </div>
      {rows.map((row, i) => (
        <div key={i} className="review-row" style={{ "--n": row.length } as CSSProperties}>
          <div className="review-track">
            <ul className="review-set">{row.map((review) => <Card key={review.name} review={review} />)}</ul>
            {/* The same again, so the row has no end as it drifts: not read out a second time. */}
            <ul className="review-set" aria-hidden="true">
              {row.map((review) => <Card key={review.name} review={review} />)}
            </ul>
          </div>
        </div>
      ))}
    </div>
  );
}
