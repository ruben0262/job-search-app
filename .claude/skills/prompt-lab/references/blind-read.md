# The blind side-by-side read

A wording is never judged alone. v41 passed every target when its letters were read one by one, went live,
and the owner read their own letter beside the one v37 had written and said "the v37 read much better". Read
alone, a letter is compared with the list in your head. Read beside the letter it replaces, it is compared
with what the person would otherwise have sent, which is the only comparison that matters.

Your own side-by-side read is not enough either: you wrote the wording and know which letter is which. So
the decision between two wordings goes to a fresh reader who knows neither.

## How

1. Write both wordings for the same pairs with both writers (`look.sh`), so every pair has a letter from
   each. Include the wording that is live and, where the owner has named one they liked better, that one too.
2. `python scripts/cvlab/look.py blind <sets.md> <key.json> live=<version> v42=<version> --out <bench out>`
   writes the sets: the letters for one person and posting under A, B, C in an order of their own, with
   nothing in them that says which wording wrote which. The key stays with you.
3. Start **one** fresh agent (general-purpose) with the brief below and the path of the sets. One reader of
   25 sets of three costs about 150,000 tokens of the session's plan: one is the rule, a second only where
   the first is close and the choice matters.
4. `python scripts/cvlab/look.py unblind <answers.json> <key.json>` gives each wording's first places, mean
   place and mean flow, by writer, and a line a set with the reader's reason.
5. Read the reader's reasons, not only the counts. They name the faults your own read walked past: the
   first blind read found "I would bring that same care to…" in 38 of 46 letters of both wordings.

## Check the reader before trusting it

When the owner has told you which of two letters they prefer, put that pair in the sets. A reader that
disagrees with the owner on the pair the owner read is measuring something else: fix the brief before you
use its other answers. (2026-11-28: the reader chose the owner's v37 letter over their v41 letter, clearly,
flow 8 against 4.)

## The reader's brief

Give the reader the faults that matter for this kind of document, from the targets and the mistakes list,
and tell it what to ignore. Do not tell it what you hope to find. For cover letters:

> You are doing one careful reading job. No code changes, no web access.
>
> Read this file completely: `<path to sets.md>`
>
> It holds N sets of cover letters. Each set has two to four letters (A, B, …), all written for the same
> person and the same job by different sets of writing instructions. The order within each set is random
> and differs from set to set. You are not told which instructions wrote which, and you must not try to
> guess or to track them across sets: judge each set on its own.
>
> Read as an experienced hiring manager who has read thousands of cover letters, and as a careful editor.
> Rank the letters of each set from best to worst as cover letters that a real, capable person wrote
> themselves and that would make you want to meet them. What matters, in this order:
>
> 1. FLOW. Each sentence follows from the one before; each paragraph has one job and holds together; the
>    letter reads as one piece from opening to close. Count against it: stray facts with no link, abrupt
>    paragraph starts, runs of short unrelated sentences, three results in a row, a letter that reads like a
>    CV turned into sentences, a one-line paragraph of practical details in the middle.
> 2. NATURALNESS. Would a person really write this sentence? Count against it any sentence that reads as
>    artificial, stiff, formulaic or machine-made: one that only announces the application or points back
>    ("That is what draws me to…"), a hedge ("I would want to"), a stock bridge ("I would bring the same
>    care to…"), a list of tools or certificates, the reader told what their own job is or what their own
>    company does, a label for an employer the reader already knows ("at a financial services
>    organisation"), the posting's own phrases said back.
> 3. THE FIRST AND LAST PARAGRAPHS. Many readers skim to these two, so they weigh most. A strong opening
>    tells you what this person would do for you in this job, why they want to do it here and who they are.
>    A strong close ties their strongest point to what the job needs in a sentence, asks confidently to
>    meet, and thanks you: that shape is what this employer expects and is not counted against as stock.
>    A close that retells the letter, or trails off, is weaker.
> 4. CONFIDENCE AND WARMTH. Sure, plain and interested in the work, neither stiff nor chatty.
> 5. Whether it makes you want to meet the person.
>
> Do not reward length. Do not penalise contractions as such; judge whether the register is consistent and
> suits a letter to an employer. Be strict and use the same standard for every set. Read every letter fully.
>
> Write your answers as JSON and nothing else to `<path to answers.json>`: a list with one object a set, in
> order: `{"set": 1, "ranking": ["B", "A", "C"], "flow": {"A": 7, "B": 8, "C": 5}, "opening": {"A": 6,
> "B": 8, "C": 5}, "close": {"A": 7, "B": 7, "C": 4}, "clear": true, "why": "one sentence on what
> separates best from worst", "weakest_sentence_of_best": "copied exactly", "worst_fault_of_worst": "a few
> words"}`. "ranking" lists every letter of the set, best first; "flow", "opening" and "close" mark every
> letter from 1 to 10 (the first paragraph and the last, each read alone as a skimmer would); "clear" is
> true when the best is clearly better than the second.
>
> When the file is written, reply with only: how many sets you judged, and the three faults you saw most
> often in the letters you ranked first, one line each with one copied example.

## What it cannot do

- It reads flow, naturalness and warmth. It does not check a single fact: the trace against the person's
  record is still yours, by grep.
- It ranks within a set. A wording first in most sets is better than the others in them, not good.
- One reader has habits of its own. Where two wordings are close in its ranking, they are close.
