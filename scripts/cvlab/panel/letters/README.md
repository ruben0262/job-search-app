# Weak letters and About you texts

Everything here is made up, like the rest of the panel.

The site only writes a cover letter for someone who has uploaded a cover letter of their own (`libs/documents/needs.py`), and most people also fill in the short "About you" box (`history.txt`). What real people upload there is usually weak. The owner: "users should have an about you section and a cover letter, both probably weak, its best to test with that rather than something strong". The letters most panel people already had were mostly too good for that, so this folder gives the 25 letter-test people a weaker version of both.

A letter wording that works out "why this employer" from what the person says they want has to find it in material like this: real motives, said vaguely, among clichés.

## How the folder is laid out

Each `<person>/` folder holds only the files that stand in for the panel folder's file of the same name:

- `cover_letter.txt` for all 25: a letter the person wrote earlier for a different application.
- `history.txt` for 14 of them: a weaker About you, only where the original was clear, specific or well structured. Where the original was already short, vague, informal or in broken English, there is no file here and the panel's own is the one to use.

Everything else (`candidate.json`, `cv.txt`, other documents) comes from `scripts/cvlab/panel/<person>/`, which is left as it was, because other evaluations read those folders.

## What every letter keeps to

- **A different job.** Each is to a job in the person's trade at an employer that is not named ("your company", "your store", "Spett.le Direzione"), so it is never either of their bench postings, and none gives a reason about a bench employer.
- **Weak, in different ways.** 101 to 187 words. Clichés, little evidence, few numbers, and typos where the person's About you is typed that way. The kinds of weakness vary from person to person, so no one formula runs through them all.
- **True to the CV and the About you.** No employer, qualification, number or result the CV does not support, and nothing the About you rules out (a licence not held, a qualification not finished, work never done). Every number is one the CV gives, said loosely ("over 40 years", "more than twenty clients").
- **One or two real motives, said vaguely**, taken from the person's CV and About you.
- **Contact lines as in the CV.** No letter is dated.
- **Language of the CV**, except `payroll` (see below).

The weaker About you texts are shorter, vaguer and typed quickly, in the original's language, and keep every fact and constraint the original states, because the bench checks the letters' honesty against them.

## The 25

| Folder | Letter language | Letter words | Kind of weakness | Motive(s) it carries | About you replaced? |
|---|---|---|---|---|---|
| `warehouse` | English, second language | 148 | Broken English in lower case; duties only; "hard working and reliable, always on time" | An organised warehouse where the lorries go out on time; "maybe later more responsibility" | No: already broken English |
| `nurse` | English | 173 | Adjectives and stock claims ("caring, compassionate", "great asset to your team"), none of them shown | Women's health and the gynae patients; a smaller hospital where she can develop | Yes: clear and specific |
| `sales` | English | 163 | Her CV's personal-statement clichés ("go the extra mile", "a new challenge"); "always hit my targets" with no figure; missing apostrophes | To progress and take on more responsibility, with no chance to move up where she is | No: already informal |
| `school-leaver` | English | 116 | Very short, informal and apologetic ("I know I dont have much experience") | Busy shifts where the time goes quickly; being with people | No: already informal |
| `chef` | English, second language | 172 | Passion talk ("cooking is my passion since I was a child"); his one result mentioned without its figures | A restaurant kitchen, Italian or modern, over hotel breakfast shifts; a team that cares about food; the next step | No: short, non-native |
| `teacher` | English | 187 | An apologetic career change; "transferable skills" never named; the CIPD course mentioned in passing | Working with adults and seeing colleagues grow in confidence; learning how training works outside schools | Yes: clear, well structured |
| `driver` | English | 107 | A list of licences and a few blunt lines, hardly any sentences | Tankers, what he knows best; showing new drivers the ropes | No: already informal |
| `office-senior` | English | 166 | An old-fashioned template ("for your perusal", "I believe I possess the necessary skills and attributes") | Working closely with a director, being the person everyone comes to; a varied role | Yes: clear, well structured |
| `marketing` | English | 170 | Buzzwords ("results-driven, data-driven") and a list of tools; "Hi there" and "would love to chat!" | The numbers side; bigger budgets and more say in how the channels fit together | Yes: clear and specific |
| `care-worker` | English | 156 | Sentimental ("caring for people has always been in my heart"); nothing about her medication rounds or leading a shift | Making residents' days; ready to become a senior carer and keep learning | No: four short sentences |
| `housekeeper` | English, second language | 117 | Brief and plain, with grammar slips ("I am apply") | Staying in housekeeping and becoming a supervisor; rooms perfect for guests | No: four short sentences |
| `hr-advisor` | English | 151 | HR jargon and abstractions ("proven track record", "people are its greatest asset", "employee lifecycle") | Fair solutions before things go formal; a broader role, more of the employee lifecycle | Yes: clear, well structured |
| `nursery-practitioner` | English | 161 | Gushing and bubbly, exclamation marks and a smiley | Seeing children learn and grow; back to the babies; carrying on with her Level 3 | Yes: a clear, specific list of constraints |
| `paralegal` | English | 166 | Self-description ("highly motivated", "thrive in a fast paced environment"); her work in one line | Staying in property law for the long term; a firm that supports people qualifying | No: four short sentences |
| `payroll` | English, second language (CV in Polish) | 150 | Polish-English errors ("I am working in payroll since over six years"); CV clichés ("resistant to stress"); duties | An international company and more English at work; figures right to the last grosz | Yes: clear and specific |
| `pharmacy-technician` | English | 138 | Short and vague ("lots of experience in all areas of pharmacy", "a keen eye for detail") | The checking side and supervising trainees; Monday to Friday hours for her family | Yes: clear and specific |
| `plumber` | English | 136 | Blunt and short, missing apostrophes; says what he wants to get away from (paperwork, chasing payments) | Back to being employed and doing the job properly; helping younger lads | No: already informal |
| `site-manager` | English | 160 | Generic claims ("proven track record", "my number one priority"); flattery of the company; qualifications tacked on at the end | Larger schemes and the next step; a tidy site and a clean handover | Yes: clear, well structured |
| `vehicle-tech` | English | 109 | Casual; a list of qualifications; "a hard worker, I take pride in my work" | More diagnostic work, electrics, hybrid and EV | No: already informal |
| `kundenservice` | German | 131 | Stock German phrases ("hiermit bewerbe ich mich", "teamfähig, belastbar und zuverlässig") | New subjects after years in energy, to keep developing; she enjoys dealing with customers | Yes: clear, well written |
| `pflegehelferin` | German | 141 | Heartfelt but vague ("eine Herzensangelegenheit"); "das" for "dass" and a missing comma | Caring for people in their own homes; early shifts and part time | Yes: clear and specific |
| `elektriker` | German | 101 | Very short; stock adjectives; missing hyphens ("Wohn und Gewerbebauten") and "Zur Zeit" | Fault finding; more responsibility and training later | No: already informal |
| `transport-planner` | Dutch | 134 | Stock Dutch phrases ("stressbestendig", "een echte teamspeler", "Graag licht ik mijn motivatie toe") | The puzzle of planning when a trip goes wrong; knowing the driver's side | Yes: clear, well written |
| `hotel-reception` | Italian | 145 | Stock Italian phrases ("persona solare, dinamica", "predisposizione al lavoro di squadra") and the privacy line | An upscale hotel where she can grow; willing to move | Yes: clear, well written |
| `konyvelo` | Hungarian | 119 | Stock Hungarian phrases ("precíz, megbízható és terhelhető", "Várom megtisztelő válaszukat"); little on what she does | A place to develop for the long term, with more independence and responsibility | Yes: clear, well structured |

Words are counted with `wc -w` over the whole file, contact lines included.

## Notes

- **`payroll`** writes her letter in English although her CV is in Polish. Her About you says she wants an international company where she would use English more and already writes English emails to clients, so an English letter to an international firm is what she would send. A Polish letter would be just as true to her.
- **`kundenservice`**'s About you names health insurance (Krankenkasse) as a field she would like to try, as her original did. It is a fact of hers, so it stays, although her bench posting is a customer service desk for health insurers. Her letter does not mention it.
- The six people who already had a letter in the panel (`nurse`, `teacher`, `marketing`, `paralegal`, `pharmacy-technician`, `konyvelo`) keep it there. The new letter here is a different, weaker one, for a different job.

## The two trap people (2026-11-21)

Two software developers test the one thing a letter must never do: say more about a person than their documents
do (CLAUDE.md §3b, gate 10). The owner's own letter once said their "main languages are Go and TypeScript" when they
had worked in Python, Go, Java, JavaScript and TypeScript alike. Both apply to postings that ask for Go
(`scripts/cvlab/jobs/*backend-dev*.json`, `*polyglot-dev*.json`), and both are measured apart from the 25
(`bench.sh trap`).

| Folder | What the trap is | Letter words |
|---|---|---|
| `backend-dev` (in the panel since before) | Python only, no Go and no TypeScript, for two postings that ask for Go: a letter must not give him either | 176 |
| `polyglot-dev` (new, its CV and About you in `scripts/cvlab/panel/polyglot-dev`) | Python, Go, Java, JavaScript and TypeScript, none ranked, Go on one service: a letter must not call the posting's languages her main ones, or one Go service years of Go | 122 |
