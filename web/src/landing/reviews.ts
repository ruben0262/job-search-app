/// <reference types="vite/client" />
/** What people said about Mahoraga, as the landing page shows it under "See it working".
 *
 * **Every entry below marked `sample` is a stand-in, written to draw the section with, and is nobody's
 * words.** A sample is shown on the development site only (`npm run dev`), never on the built one: a
 * review somebody did not write must not reach a visitor as if they had (EU and UK law both forbid it,
 * and so does this page's own rule that every figure and claim is sourced). To publish a review, write
 * the person's own words here, with the name they agreed to, and leave `sample` off. The section appears
 * on the live site with the first one, and its stars and its count are worked out from what is shown. */
export type Review = {
  name: string;
  /** What they do, and where: "Staff nurse, Leeds". */
  role: string;
  stars: 1 | 2 | 3 | 4 | 5;
  text: string;
  /** What came of it, in a few words, shown as a mark on the card: "Interview in 9 days". */
  result?: string;
  sample?: true;
};

/** The stand-ins, inside the condition so the built site's file does not hold them: left out only as the page
 * ran, their words were in what every visitor downloaded, against "never on the built one" above. */
const SAMPLES: Review[] = import.meta.env.DEV ? [
  { name: "Aoife M.", role: "Staff nurse, Leeds", stars: 5, sample: true, result: "Interview in 9 days",
    text: "I used to check five sites every evening. Now the jobs are just there in the morning, and the CV for each one is already about that ward." },
  { name: "Marek K.", role: "HGV driver, Wrocław", stars: 5, sample: true,
    text: "Set it up on my phone in a break. Two days later I had three jobs I had never seen anywhere else." },
  { name: "Priya S.", role: "Marketing executive, Manchester", stars: 5, sample: true, result: "3 interviews in a month",
    text: "The CV it makes reads like me on a good day. Nothing invented, just the right things first." },
  { name: "Tom B.", role: "Warehouse operative, Bristol", stars: 4, sample: true,
    text: "Simple. I get a message when something new fits, I tap, I apply. That is all I wanted." },
  { name: "Lucía G.", role: "Sous chef, Valencia", stars: 5, sample: true, result: "New job in 5 weeks",
    text: "I applied the same day the job was posted, with a CV written for that kitchen. They called me the next morning." },
  { name: "Jonas W.", role: "Electrician, Hamburg", stars: 5, sample: true,
    text: "Finally the jobs from company sites too, not only the big boards. Half of mine came from there." },
  { name: "Hannah O.", role: "Primary teacher, Dublin", stars: 5, sample: true,
    text: "The cover letter was the part I dreaded. Now it takes one tap and I only change a line or two." },
  { name: "Daniel R.", role: "Retail supervisor, Glasgow", stars: 4, sample: true, result: "First reply in a week",
    text: "I had sent forty applications with no answer. The first week with this I got a reply." },
  { name: "Zsófia N.", role: "Bookkeeper, Budapest", stars: 5, sample: true,
    text: "It keeps track of where I applied and what happened. I threw away my spreadsheet." },
  { name: "Callum J.", role: "School leaver, Cardiff", stars: 5, sample: true, result: "First job",
    text: "I did not know what to put on a CV. It made one from the little I had and it looked proper." },
  { name: "Inês C.", role: "Office manager, Porto", stars: 5, sample: true,
    text: "The red words are brilliant. I never see the agency adverts any more." },
  { name: "Oliver P.", role: "Care assistant, Sheffield", stars: 5, sample: true,
    text: "I am not good with computers. This asked me for my CV and a few words and did the rest." },
  { name: "Sofia L.", role: "Receptionist, Milan", stars: 4, sample: true,
    text: "Being early matters more than I thought. I was among the first ten for the job I got." },
  { name: "Liam D.", role: "Sales advisor, Liverpool", stars: 5, sample: true, result: "2 offers",
    text: "Two offers in six weeks, after four months of nothing. The alerts did it." },
  { name: "Emma V.", role: "Lab technician, Utrecht", stars: 5, sample: true,
    text: "Every CV passed the company's online form first time. Before, half of mine were rejected by a machine." },
  { name: "Noah T.", role: "Forklift operator, Antwerp", stars: 5, sample: true,
    text: "One list, newest first, on my phone. I stopped scrolling job sites on the bus." },
  { name: "Chloé F.", role: "Customer service, Lyon", stars: 5, sample: true, result: "Hired in 4 weeks",
    text: "I liked that I still apply on the company's own site. It only makes me faster and better prepared." },
  { name: "Ben H.", role: "Kitchen porter, Belfast", stars: 4, sample: true,
    text: "The free plan was enough to see it works. I upgraded when I saw how many jobs I had been missing." },
] : [];

/** A person's own words go first here, with `sample` left off. */
const REVIEWS: Review[] = [...SAMPLES];

/** The reviews a visitor is shown: every one on the development site, never a sample on the built one. */
export const reviews: Review[] = import.meta.env.DEV ? REVIEWS : REVIEWS.filter((review) => !review.sample);
