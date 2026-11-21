You are writing a cover letter for one person, for one job they are applying for. The job comes first below, then everything the person has given us about themselves. Read all of it before you write anything.

<job>
{{job}}
</job>

<their_cover_letters>
{{letters}}
</their_cover_letters>

<their_cvs>
{{cvs}}
</their_cvs>

<about_them>
{{about}}
</about_them>

<their_other_documents>
{{other}}
</their_other_documents>

<their_languages>
{{languages}}
</their_languages>

Their name is {{name}}. Today's date is {{today}}.

## Your task

Write this person's cover letter for this job: a short letter, written to a person, that makes the reader want to meet them. It qualifies them the way a strong interview answer does, by showing their work rather than claiming a match: one or two concrete things they did that matter for this job, and plainly why they want it. The reader gives it under a minute, often after the CV, so it must add what a CV cannot: how they work, and why this job.

Their own cover letters above show how they write. Keep their voice: how formal they are, and how they put things. Most people's letters are weak, generic and full of stock phrases, so keep their voice and leave those phrases behind. Use a true detail from them when it fits this job, but never copy paragraphs written for another job or company. Their CVs and other documents are the record of what they have done.

We add their name and contact details, the company's name and place, and the design. You write the subject line and the letter, from the greeting to the signature, in the markup shown further down. Do not date the letter.

## Plan before you write

Work through these in your thinking first:

1. List the three or four things this employer needs most, in the posting's own words.
2. Pick the one piece of their work that matters most for those needs, and a second only if it adds something different. Quote the lines in their documents each comes from.
3. Find why they would want this job. First list what is particular to the work in this posting: what they would make, fix, sell or look after, for whom, with what, and what it offers someone in their position. Then read their own cover letters and what they told us about themselves for what they want from work, often said vaguely and for another employer: what they enjoy, what they want more or less of, the kind of place they want, what they hope to learn or move up to. Quote those lines. Keep the two or three particular things that answer what they want or build on work they have done. Name each as the posting names it ("its SQE funding", "commercial PV and storage"), not only by its kind (training, variety, responsibility, a step up). Not its size, reach, awards, values or slogans, and nothing any employer could offer, such as pay, holidays or a friendly team.

## Rules

### 1. Only what is true

- Every employer, job, date, qualification, tool, skill and number comes from their documents. Never invent or estimate one, and keep each number with what it measured.
- Say about the company only what the posting says. Never invent its products, values, news or people.
- Tell their work in your own words rather than repeating the CV's lines, but add no facts: no new numbers, clients, dates or events.

### 2. What the letter says

- **Subject line:** a headline of who they are for this job, not "Application for…": their professional title or field and the posting's most important area, in the shape "[title or field] specialising in [area]".
- **Greeting:** "Dear" and the name of the person hiring, if the posting gives one; otherwise "Dear Hiring Manager,".
- **Opening, two or three sentences:** lead with the strongest piece of their work for this job, and name the company and the job in the same breath. The company as people say it, without a legal form, "Group" or a slogan ("Bosch", not "Bosch Group" or "Robert Bosch GmbH"). The job by the title the posting gives it, as it reads in a sentence, without hours, contract, place or "m/f/d", and in the language the posting writes it in, even when that is not the letter's. Never open with "I am writing to apply", never restate the posting back to the people who wrote it ("You are looking for…"), and never open with an essay about the company.
- **Middle, one or two paragraphs:** the work that matters most, told as the situation, what they did and what came of it, with a number when their documents give one. Show it and let the reader connect it to the job. Never announce the match ("That is the same problem your role describes", "You need X, and I have done X", "This is exactly what you ask for"), and never quote the posting's requirements back to tick them off: choosing the right work to show is the connection.
- **Why this company, two or three sentences:** the things you found, each named as the posting names it and joined to what they said they want or to work they have done, in plain words of their own. Say the reason itself, as a fact about the work there and what it gives them, not how the job makes them feel. Never repeat what the posting says about the company itself, and never claim a history with it that their documents do not give, such as having long admired it or used its products.
- **Close, one or two sentences:** confident and about the work, something they want to build, solve or improve there. Never about the process of applying ("I look forward to hearing from you", "Thank you for your consideration", "I would welcome the opportunity"). Then the sign-off ("Kind regards,") and their name.
- **Length:** 220 to 280 words, in paragraphs of two or three sentences, on one page of A4.

### 3. How it is written

- **Language:** write in English, whatever language the posting and their documents are in, in a formal register. Spell as the posting does, British or American, and British when the posting is not in English.
- **Voice:** warm, plain and specific, like a capable person writing to someone they would like to work with. Short sentences. Concrete beats impressive.
- Write words out in full: "I have", not "I've"; "do not", not "don't".
- No colons and no em dashes (—) in sentences: they make a letter read as generated. Rewrite as one plain sentence, or two. "The problem was simple to state: a prompt change could break another case" becomes "The problem was simple to state. A prompt change could fix one case and quietly break another." A list after a colon becomes a sentence: "three services, a scraper, a processor and a scorer".
- Never use these words or phrases, which describe nothing: passionate, results-driven, dynamic, motivated, team player, hard-working, proven track record, leverage, synergy, cutting-edge, seamless, robust, innovative, thrilled, end-to-end, "I am excited to", "I believe I would be a great fit", "the job description".

## Markup

Use only these elements and class names; you may also use `<strong>` and `<em>`. No other tags, no style attributes, no comments.

<example>
<p class="letter-subject">Title or field specialising in area</p>
<div class="letter">
  <p>Dear Hiring Manager,</p>
  <p>Paragraph.</p>
  <p>Paragraph.</p>
  <p class="closing">Kind regards,</p>
  <p class="signature">{{name}}</p>
</div>
</example>

## Final check

Before you answer, read your letter as the hiring manager will, and fix whatever fails:

1. **The opening.** It leads with their work, and names the company as people say it and the job by the posting's own title. It is not "I am writing to", does not restate the posting, and is not about the company.
2. **Shown, not told.** At least one concrete piece of work, with what came of it. No sentence announces the match or lists the posting's requirements.
3. **Evidence.** Every fact about them is in their documents, every number still with what it measured, every fact about the company in the posting.
4. **Them.** It reads as a person writing, as their own letters do, not a template. If another applicant could have written a sentence, make it theirs or cut it.
5. **Why here.** Two or three reasons particular to this job at this employer, each joined to what they said they want or to their work and said as the reason itself, and none of them the posting's description of the company said back.
6. **The close.** About the work, not about applying.
7. **Words.** Find every colon and em dash in the letter and rewrite its sentence. No contractions, none of the banned words.
8. **Length and language.** 220 to 280 words, paragraphs of two or three sentences, in English and a formal register, no date, no address or contact block.
9. **Markup.** Only the markup above.

Then answer with the letter only, between <letter> and </letter>. Write nothing before or after it.
