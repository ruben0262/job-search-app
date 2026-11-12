# Changelog

What changed, where, and **why** — the last one is the point. A diff already records what changed;
this file exists so that six months from now the reasoning behind a decision is recoverable without
archaeology.

Conventions (carried from the reference instance, `docs/saas/CODE_STRUCTURE.md` §7):

- **Non-trivial change → an entry here.** Formatting and typo fixes do not need one.
- **Major change → update the relevant doc in the same commit**, and say so in the entry.
- Newest first. Dates are the day the work landed.
- Per-folder state lives in that folder's `README.md` under **Current state**; this file is history,
  those are position. Deliberately not duplicated — two copies of the same fact drift.

---

## 2026-11-12, late night — The development site learns the API's new paths

The owner saw the plan page say "Loading…" after signing up.
- **Cause:** it was the development server. Vite read the API's paths from `openapi.json` once, as it started.
  `/pricing` was added after the running one started, so it answered the plans with the page itself.
- **Fix:** the config imports the contract. Vite restarts when a file its config imports changes: a path added
  to a test copy reached the proxy, with "openapi.json changed, restarting server".
- **Production** was never affected: there the API serves the site and its own paths are matched first.
- **Measured afterwards:** from the email link to the cards in 0.4–0.6 s, every API call under 50 ms.

## 2026-11-12, night — "Confirm password"; My search and My documents say what the words and the CV are for; Help redesigned

The owner asked for:
- **The password fields:** remove "Longer beats complicated." and say "confirm password" in place of "Type
  it again".
- **My search:** keep or reword "Green words find jobs. Red words hide them.", telling people the words are
  what jobs appear for, so they choose them well.
- **My documents:** add "used to find your words and to make your CVs and cover letters".
- **The contact page:** "simple but make it looks good and professional".

**Passwords:** "At least 9 characters.", then "Confirm password" (or "Confirm new password", in Settings and
on the reset page).

**My search:** "Green words are what we search for: a job shows up only if it has one. Red words hide jobs.
Choose them carefully."

**My documents:** "Used to find your words and to make your CVs and cover letters." is on the "Your CV" card.
The page's own line said the same ("We read them to find your words and to make your CVs and cover
letters"), so it is removed rather than said twice.

**Help and contact.**
- **The quick answers** are one rounded list, a question a row, an arrow that turns as it opens.
- **"Write to us"** is a card of its own, lit at its top edge in the purple, with a mail icon and the line "A
  person reads every message and replies by email". Its button is full width.
- **Once sent:** a green tick, where the reply goes, and the reference.
- **The email address** sits under the card for anybody who prefers their own app.

The sign-up and reset walks in the browser tests fill "Confirm password", the fields found by the start of
their label: a hint inside a label makes it part of the field's name.

## 2026-11-12, night — The menu in words alone; an application's progress on one exact track

The owner asked for:
- **"Remove the icons in the nav bar… on all screen types",** keeping the words.
- **On Applied, a progress bar** that "looks much better: right now the circles and the lines are all over
  the place".

**The menu, words alone.**
- **A laptop** shows the full names.
- **A phone** shows its short ones ("Search", "Docs"): an admin's six fit a 320 px screen at 11 px, 13 px
  by 390.
- **The landing page's picture of the app** draws the same bar.
- **Still drawn elsewhere:** the pictures stay in `places.tsx` for the Jobs page's Applied mark and the
  landing page's dock.

**Applied's progress: one track, four dots on it.**
- **Before:** each step drew its own line from the step before, placed by hand, so lines and dots did not
  meet.
- **Now:** one track runs from the first dot's centre to the last's, filled in green as far as the
  application got (`--done`).
- **Done steps** have a tick, and the step it stands at is bold with a ring of light. The steps ahead are
  hollow, in the card's own colour, so the track does not show through them.
- **An application that ended** fades the steps it never reached.
- **The line under the title** shows only the tally ("4 interviews · 2 offers"). "Heard back from one? Say
  so on it…" is removed, as asked.

Checked at 320, 390 and 1,280 px with an application at each stage: nothing past the screen's edge.

Tests: the phone's menu has no pictures and still fits at 320 px.

## 2026-11-12, night — Admin's Subscriptions tab, and each account's subscription as it is stored

The owner asked for:
- **An Admin tab, "Subscriptions":** monitor the subscription types and their stats, and what the landing
  page says about the plans, their price, description and benefits.
- **On each account's page:** its subscription "just like they are in db", designed to read well.

**Admin → Subscriptions** (`AdminSubscriptions.tsx`, `GET /admin/subscriptions`).
- **Four tiles:**
  - members, of all accounts;
  - paying, with what they bring a month at Stripe's price;
  - in a trial, and how many end this week;
  - joined in 30 days, and how many of them are members (with those cancelling).
- **"Who is on which plan":** every account once, as one bar and a line each:
  - paying, in a trial, payment failed, paused;
  - free access, admins;
  - choosing a plan, the free plan.

  Each is counted by the rule the site lets people in by (`libs.membership`), so the tab cannot disagree
  with who gets in.
- **"The membership":** Stripe's price, large, and the two settings that come with the plans, the free trial
  and how far behind the free plan is. Both moved here from Settings.
- **"Pricing on the landing page":** the two cards' editor, moved here from Settings.
- **"Every subscription":** each account with a subscription or free access, payment failed first, then
  trials by when they end. Each row shows its plan, Stripe's word as stored, what comes next and the last
  payment, and links to the account.

**An account's page: "Subscription"**, where "Membership" stood.
- **Its plan,** a line of where it stands ("In a trial until 16 November, then it renews"), and four tiles:
  trial ends, next payment, ends, last payment.
- **"As stored":** every field the membership keeps, by its name in the database, null where empty.
  `subscription_id` and `plan_chosen_at` join the account's answer for it.
- **The free-access switch,** as before.
- **Two lines that were wrong:** the card said a non-member's site was "Closed: they see the page that asks
  them to pay", which stopped being true with the free plan. It now says where they stand.

Test: the tab counts every account once by plan, members by the site's own rule, trials ending, the
cancelling and the newly joined, and lists the subscriptions with a payment failed first. Nobody but an
admin gets it, and an account's answer has its two new fields. It fails on the previous commit, which has
no such page.

## 2026-11-12, night — The plan picked after the email link on the landing page's cards; Stripe's own checkout

The owner asked for:
- **The plan choice moved:** "similar to how its on the frontend… after sign up and email confirmation and
  before the actual onboarding begins… and not during the inital signup".
- **The payment, Stripe's own:** "it doesn't look official so it needs to be stripes payment page, either
  integrated fast… or we open a new page… once its done we get a signal back so we continue to onboarding…
  and until that happens we don't allow users through".
- **The charged amount large:** "displayed properly and its not small font".

**Signing up asks for no plan.** Address, the password twice and the privacy-and-cookies box. The "Your
plan" choice on the form is gone, with its styles.

**After the email link, before setting up: "Choose your plan"** (`PlanPage`).
- **The page:** the landing page's own two cards, Free and Member, their words as Admin sets them
  (`PriceCards`, whose buttons pick here).
- **Free** goes on to setting up with no card asked (`POST /billing/free`, which marks
  `User.plan_chosen_at`).
- **Member** opens Stripe's checkout on the same page. `#/signup?plan=member`, the landing page's Member
  card, opens the page there.
- **Nothing past it until a plan is picked.** The dashboard's new stage, `plan`, comes before onboarding
  (`libs.membership.choosing`), whatever the address: Settings or a reload lands on it again. The way back
  from Stripe's checkout is to the cards, not past them.
- **Who is asked:** only a new account. Not a member already (free access, an admin), not somebody whose
  membership ended (free without being asked again), and not an account set up before the choice began.

**The payment is Stripe's own checkout, inside the page** (`ui_mode: embedded_page`, `StripeCheckout`).
- **What it shows:** the trial ("7 days free") large, the monthly price, Stripe's currency switch (Adaptive
  Pricing), the card, Link, its own button and "Powered by Stripe", as Stripe draws them for every shop.
- **What replaced it:** the site-drawn form (`ui_mode: elements`), with its colours code, is removed.
- **Its look** is the Stripe account's Branding (Dashboard → Settings → Branding: colours, logo, font), so it
  does not follow the site's theme button.
- **The signal back.** When Stripe says it is done (`onComplete`) the site reads Stripe again
  (`/billing/refresh`) and goes on to setting up. A payment that goes to a bank's page comes back by the
  address, as before (`redirect_on_completion: if_required`).
- **Fast.** Stripe's script is fetched while the cards are read, so the checkout opens straight after the
  tap.
- **One at a time.** Stripe allows one embedded checkout on a page, and React mounts twice in development,
  so each waits for the one before it to be gone.
- **The price, large, over it:** "£4.99 a month, after 7 days free", from Stripe's price. Stripe's own page
  says it smaller, and in the person's currency.

**The upgrade page** (from the free plan, later) uses the same checkout, with the same large price. Its list
of what a membership adds is now the Member card's lines as Admin words them, where it was a second copy
typed into the page.

**Tried on Stripe's test mode** (the sandbox's restricted key, a built site, a real browser):
- **Free:** the cards, then setting up.
- **Member:** the checkout drawn 2.7 s after the tap. Away and back, Settings, a reload: the plan page each
  time. Paid with the test card 4242 at 1,280 px and at 390 px, it went on to setting up as a member
  (`trialing`), on Stripe's word.

Docs: deploy/SERVER.md step 10 (Branding, the plan page) and ROADMAP Phase 10.

Tests, each failing on the previous commit:
- **A new account picks its plan first,** and Free goes on to setting up.
- **Who is asked:** a new account, and not a member, a lapsed member, free access, an admin, an account set
  up already, or one that picked Free.
- **The checkout** is `embedded_page`, finishing in the page.
- **In the browser:** sign-up asks for no plan; the plan page shows both cards and nothing gets past it by
  the address; the Member card's link opens the checkout with its price large and goes back to the cards.

The sign-up walks in the other browser tests pick Free on the new page.

## 2026-11-12, later — The landing page's pricing: Free and Member, their words set in Admin

The owner asked for:
- **A pricing section for the one subscription:** its 7-day free trial and the whole platform emphasised,
  in a great design.
- **What the cards say, controlled from Admin.**
- **Then a free version beside it:** a week behind, without CVs, cover letters or alerts. Start either way:
  free with no card asked, or the membership with its trial.

The free tier itself, the trial and the sign-up's plan choice are the membership work. This entry is the
landing page's part.

**"Start free. Upgrade to be first."**, between "See it working." and "Start in a few minutes." The dock
gains "Price".
- **The line under the title** says that Free shows every job 7 days after members see it, and that
  members get each one the moment it's found, with alerts and a CV for every job, the first 7 days free.
- **Free:**
  - "£0 for good", then "Jobs 7 days after members see them";
  - three lines ticked;
  - three crossed out (new jobs as they appear, alerts, CVs and cover letters), each read out as "Not
    included";
  - "Start free" (`#/signup`), and "No card needed".
- **Member:**
  - a "7 days free" tab on its top edge, raised in the purple's light;
  - Stripe's price, "after your 7-day free trial";
  - five lines ticked;
  - "Start your free trial" (`#/signup?plan=member`, which the sign-up's plan choice picks up), and the
    small print that a card is needed and nothing is taken before the trial ends.
- **Side by side** where the cards have room, laid out by their container, not the screen, so Admin's
  preview is the same pair.

**The words are the operator's; the price, the trial and the delay are not.**
- **What Admin edits.** Admin → Settings → "Pricing on the landing page" switches between the two cards and
  edits each:
  - the name and the line under it;
  - what is included, and what is not (crossed out), 1 to 12 lines each;
  - the button and the small print.

  A preview of both follows the typing, and Save saves both at once (`pricing_cards`, one platform setting).
- **What it can't edit.** The price is Stripe's, read by the `billing_routes.plan` the payment form uses.
  The free days are `trial_days`, and the delay is `free_delay_hours`, the setting the feed's delay reads.
- **Why.** A typed price could say £3.99 while the form charges £4.99, and a typed "a week" could stay
  after the delay changed.
- **Without Stripe,** the cards name no price rather than a wrong one.

**The API.** `GET /pricing` is public, needing no account; `PUT /admin/pricing` is for admins,
and checks each field against the room the card has, refusing what doesn't fit with what is wrong. Saved
cards that no longer read show the defaults, with a warning in the log.

**The rest of the page says what is a member's.**
- **Buttons:** every "Create account" is "Start free".
- **"How it works":** says "on your phone too, as a member" and "ready in seconds, as a member".
- **"Get started":** names the CV and alerts as a member's.
- **The phone picture** no longer says how long jobs stay. It was typed as 7 days, and the app's shelf, now
  30 days, comes from its setting.

**The default wording** is true to the site: the CV line says "with your own AI account", and the small
print says a card is needed for the trial.

`test_every_setting_has_a_control_in_the_panel` now reads every `Admin*.tsx` file, the editor being one of
its own (`AdminPricing.tsx`).

Measured with a built site: nothing past the screen's edge at 320, 390 and 1,440 px, the dock 294 px wide
at 320.

Tests, each failing without the feature:
- **The cards:** read without an account, with the trial's and the delay's settings and no price without
  Stripe; the price passed through from Stripe's.
- **Admin:** changes the words and the landing page says them; nobody else can; a card with no room is
  refused and the saved ones kept; broken saved cards are the defaults, said in the log.
- **The browser:** both cards, the crossed-out lines read as not included, the trial tab, both buttons'
  addresses; an admin's edit seen in the preview before saving and on a 320 px phone after.

## 2026-11-12, evening — The free plan, the Free or Member choice at sign-up, and the site's own payment form

The owner asked for:
- **Free first** (2026-11-12): "ideally i would like users to sign up and complete the onboarding without
  having to give their details … for free accounts limit the jobs they see to after 7 days … and no option
  to make a cv or cover either. with a sub all these limits are removed".
- **Both ways in at sign-up:** "the free membership without any credit card info being asked after, or the
  only sub which gives a free 7 day trial, asks for credit card info via stripe".
- **The payment form "built into the site properly"**, in the light and dark modes.
- **Free access in cycles:** "turn it on, make it free, and then turn it off and its activated again, and
  this can be in cycles".

**The free plan replaces the pay page.** Everybody signs up, sets up a search and uses every page.
- **The jobs, a week late.** A free account sees a job `free_delay_hours` after members do (168, set in
  Admin → Settings → *Free accounts see a job*). The wait is counted from when the job was found and read,
  not from a posting date the site does not always have, and lasts until the job leaves everybody's list.
  Members see jobs from 0 to 30 days old, free accounts from 7 to 30 (`libs.membership.delay_hours`,
  `libs.matching`). A newer job's text and its marks are refused when asked for by number (404).
- **The Jobs page says so.** The banner reads "You see jobs 7 days after members see them…", with *Become a
  member*. `Feed.delay_hours` is new, and "Posted" offers only times longer than the wait; a shorter one saved
  earlier reads as the whole list rather than an empty one.
- **No documents.** Building one answers 402 and puts a note in the bell that links to `#/upgrade`. The Make
  buttons stay for everybody (owner, 2026-11-10); pressed on the free plan, a toast says so, with *Become a
  member*.
- **Alerts and the daily email stay members'.** Settings and onboarding say so, instead of offering a channel
  that would wait in silence. The harvest is everybody's again, because a free account's list needs its
  words searched for.
- **Deleted:** `MembershipPage`, the `membership` stage, `MembershipDashboard`, the app's 402 handler
  (`onLapsed`) and `DeleteAccount`'s `quiet`. The pay page's walk over the whole API is now its opposite: no
  page answers a free account 402 (`test_a_free_account_reaches_every_page_of_the_site`).

**The plan, picked at sign-up.** Two cards, Free and Member, with the price and trial from Stripe and Admin
(`/billing/plan` now carries `free_delay_hours`). The landing page's Member card (`#/signup?plan=member`)
picks Member. The email's link, and any fresh one, carry `&plan=member` and land on `#/upgrade`, the
membership's page, which is open before onboarding as well as after it.

**The payment form is the site's.**
- **Stripe's fields in the site's page.** Checkout Elements (`ui_mode: elements`) replaces the white
  embedded box. The fields are drawn from the site's own colour tokens (`membership.appearance`) and follow
  the theme button (`changeAppearance`).
- **The price, and the site's button.** Stripe's currency picker comes first (Adaptive Pricing). The price
  is the session's, in the currency picked, and the button is the site's own. A bank's check opens in the
  page (`redirect: "if_required"`).
- **Link left out.** Link's "save my information" (`wallets.link: never`) asked for a phone number. It also
  opened as the card was finished, moving the button out from under the first click; the browser run below
  found that.

**Free access holds Stripe's billing** (`billing_routes.hold_billing`). Turned on, it sets
`pause_collection: void` and voids any invoice already owed. Turned off, collection resumes from the next
payment. The switch moves only once Stripe has (503 otherwise), and a repeat keeps the first day.

**Tried on Stripe's test mode:**
- **The site's form in a real browser: 16 checks.** Member picked from the landing link; the link on to the
  form; the session's price in HUF, with the trial; Stripe's text colour following the theme (245,246,248 →
  15,16,19); one real click; trialing at once; on to setting up; a phone screen with nothing past its edge;
  and a card needing the bank's check, approved in the page. The bank-check run passed 2 of 3 times; one
  timed out after the test bank's button, and the cause was not pinned.
- **`tests/test_billing_stripe.py`: 5 passed, 2 of them new.**
  - Free access turned on, off and on during a trial: the trial ended with nothing charged and its invoice
    void. Turned off, the next month's payment was taken, on one subscription throughout.
  - Free access over a failed payment: the owed invoice was voided, and Stripe moved the subscription from
    past_due to active.
- **The restricted key.** The sandbox's restricted key paused, voided and resumed. The live key needs
  **Invoices: write** (deploy/SERVER.md step 10).

**Gates:** lint-imports 2 kept, 0 broken; ruff clean; `python -m libs.db --prune` built the schema on an empty
database; the contract regenerated; the site built; pytest 1,417 passed, 7 skipped (the five Stripe tests,
which passed 5 of 5 run on their own against the sandbox; a Postgres copy; the reference instance).

**Decided here; the owner's to revisit:**
1. **No Link.** Link is left out of the form.
2. **What is owed is forgiven.** Free access voids it, so turning free access off leaves a paying member from
   the next payment.
3. **The buttons stay.** Free accounts keep the Make buttons, which explain themselves when pressed, rather
   than losing them.

**Raised (CLAUDE.md §5):** the upgrade page's list of what a membership adds, and the sign-up's plan lines,
are typed in `UpgradePage.tsx` and `SignIn.tsx`. The landing page's Member card says the same from Admin
(`pricing_cards`). Once the pricing work is in, read them from `/pricing`.

## 2026-11-12 — Sign-up: the password typed twice, and the privacy policy and its cookies agreed to first

The owner asked for "a confirmation password as well, not just one password entry on signup", and "a box to
confirm the privacy policy and cookis of the site before signing up so people accept it, that's the only way
they can sign up".

- **The password twice.** On sign-up, on the reset link's page and in Settings' *Change your password*
  (`ui.useNewPassword`). Two that differ are said so before anything is sent.
- **The box.** Sign-up sends the policy's version the page showed. Sent without one, it is refused with 400
  ("Tick the box…"); an older one gets 409, because the policy changed since the page opened. The account
  keeps it as `processing_consent_at` and `processing_consent_version`.
- **The AI-documents agreement too** (decided here, the owner's to revisit). It is the same policy page, so a
  new account no longer meets the separate "Read what is sent, and agree" step. *Withdraw* in Settings brings
  it back.
- **The cookies, listed** on the privacy page (`libs.legal.COOKIES`): the two sign-in cookies, and what the
  browser keeps (the theme, a mark waiting to be sent). Nothing measures or advertises.
  `DISCLOSURE_VERSION` is 2026-11-12, so everybody agrees again.

**Raised:** the policy's consent text, which this box now agrees to, still describes "instant CVs" and the
platform's own AI key, both removed earlier. It is legal wording, and the owner's to change.

**Gates:** lint-imports 2 kept, 0 broken; ruff clean; `python -m libs.db --prune` built the schema on an empty
database; the contract regenerated (the box's version, the cookies); the site built; pytest 1,382 passed, 2
skipped (a Postgres copy, the reference instance). The three new sign-up tests fail on the commit before.

## 2026-11-12 — Jobs shown and kept thirty days, for everybody

The owner asked, through the landing page's session: "we should show jobs as old as 30 days and retire them
afterwards from corpus too so archive. make sure filters also add these numbers too. instead of 15 days its 30
days now".

- **Thirty days.** The job shelf life is now 720 hours (168 before), and jobs are kept 30 days (15 before), in
  `libs.settings`. Admin offers 2 weeks, 3 weeks and 30 days, as well as whatever is set already. Its *Jobs
  kept for* hint used to say that no list shows a job older than eight days; it now says to keep it at least
  as long as the shelf life.
- **Jobs → Posted** gains *Last 2 weeks*.
- **Defaults only.** A value saved in Admin on the server stays as saved, so look at Admin → Settings after
  the deploy.
- **The tests that assumed a week** now age their postings past thirty days: the archive's default and its
  health check, the words search's window, a match's reads, and the Jobs page's shelf line.

**Measured: a Jobs page read on the laptop's copy.** 31,513 postings over 13 days, the owner's account,
median of 5 reads:

| Shelf | Any time | Last 24 hours |
|---|---|---|
| a week | 1,096 jobs, 198 ms | 122 ms |
| thirty days | 4,274 jobs, 685 ms | 99 ms |

With a full thirty days of postings, Any time will be slower again. The 300 ms target holds for the page as
it opens (the last 24 hours), not for Any time. Nothing was changed for it here.

**Raised (CLAUDE.md §5):**
- **Retention equals the shelf.** A job its board dates up to a day after it was found can be archived up to
  a day before it leaves the list (`candidate_since` allows the day). 31 days would avoid it.
- **A new word takes longer.** `terms.STRETCH` (6 h) over a thirty-day window is up to 124 stretches, at
  90 s a run, before the word is searched for in full.
- **The scan budget.** `SCAN` (2,000 a day) reaches 60,000 at thirty days.

**Gates:** lint-imports 2 kept, 0 broken; ruff clean; `python -m libs.db --prune` built the schema on an empty
database; the contract unchanged; the site built; pytest 1,379 passed, 2 skipped (a Postgres copy, the
reference instance). The archive's, the health check's and the Jobs page's changed tests fail on the
commit before.

## 2026-11-12 — Stripe's own portal, and production needs only Stripe's four values

The owner made the monthly £4.99 price the product's default (the one-time price is gone) and activated the
sandbox's customer portal in the dashboard, which made the configuration made for the site yesterday the
account's default, login link and all. They asked for Stripe's portal rather than a custom one, for production
to need nothing but Stripe's values in the env, and kept Adaptive Pricing ("unless there are arbitrage
problems").

- **`STRIPE_PORTAL_CONFIGURATION` is removed** (code, compose, `server.env.example`, SERVER.md and its test):
  the site opens the account's default portal, as saved in the dashboard, for each person already signed in.
  Its login link opens the same portal after a code by email; the site does not need it. Production's Stripe
  settings are four: the secret (restricted) key, the publishable key, the price and the webhook secret. A
  test asserts no `configuration` is sent.
- **Adaptive Pricing, checked** (Stripe's docs, 2026-11-12, and a test-mode payment): the customer pays a 2–4%
  conversion fee in their currency, nothing if they pick GBP, and the business pays 0%; the rate is guaranteed
  through settlement and a refund uses it again, so no exchange-rate exposure. A forint payment is recorded by
  Stripe as £4.99 on the subscription and the invoice, with HUF 2,264.48 beside it on the payment; the site
  keeps £4.99, and Settings says "Last payment: £4.99". Renewals are converted at each month's rate.
- **Tried again on the sandbox, with its default portal:** the seven runs of yesterday and the forint one, 81
  checks, all passed; `tests/test_billing_stripe.py` 3 passed (51 s). Stripe made 3 attempts at the never-paid
  payment this time against 9 yesterday (Smart Retries chooses), and cancelled it within two weeks both times.

## 2026-11-12 — The landing page's selling points: be first, be seen, each figure with its source

The owner asked for:
- **The selling points, said so that somebody scrolling past gets them:**
  - jobs reported as they appear, so an applicant in a crowded field gets ahead and is among the first to
    apply, with figures behind it ("especially online applications");
  - CVs and cover letters made for each job on the site, quickly, passing ATS checks;
  - the applications tracked for them;
  - filtering that makes reading jobs far easier than on any other job site.
- **Then:** "these sections emphasised as well… so they get the message", and "its mainly european not us
  or east/asia based".

**"Be first. Be seen.", the second section,** "Why" in the dock. Under the title, one line: more people
apply for every job, and hiring starts in its first week.
- **Two figures for the market the readers are in:**
  - 140 applications for each UK graduate job, a record;
  - 65% of people say finding a job has got harder.
- **Be first: "New jobs in minutes, not days".**
  - The picture is a job's first month as applications a week: the first week twice the others, as Ashby
    counted them, with "You, day 1" at its start. The bars grow as the card is reached.
  - The figures: ~60% of hirers find someone to interview in a job's first week, and ~2× the applications
    in that week.
- **Be seen: "A CV made for each job, in seconds".**
  - The picture is a CV read the way an applicant tracking system reads it, its headings ticked in turn.
  - The figure: 14M+ applications unanswered in one quarter.
- **Three smaller cards, each with a picture so its point lands unread:**
  - **"Straight from company sites":** the hiring systems whose career pages it reads (Personio, Recruitee,
    Greenhouse, Workable, Lever, SmartRecruiters), with how many companies' pages, from `/stats`;
  - **"Filters that do the reading":** green words, a red one crossed out, a country, remote, a language;
  - **"Every application tracked":** applied and interview reached, the offer next.
- **The dock on a phone** hides its wheel (back to the top), so its sections keep their room: as many as
  six, 44 px each at 320 px.

**The figures, each read at its source on 2026-11-12, and linked on the page with its scope:**

| Figure | Source | Scope |
|---|---|---|
| 140 applications per UK graduate job, a record since 1991 | Institute of Student Employers, Oct 2024 | UK graduate jobs |
| 65% say finding a job has become more challenging | LinkedIn survey, Nov 2025, published 7 Jan 2026 | 19,113 people, 13 countries incl. UK, Germany, France, Italy, Spain, Sweden |
| Nearly 60% of hirers find a candidate to interview within the first week | LinkedIn, 7 Jan 2026 | Hiring Pro users |
| ~2× the inbound applications in a role's first week as in later weeks | Ashby, Application Per Job Report | 13M applications, 2021–23 |
| 14M+ applications ignored in a single quarter of 2024 | Greenhouse, in Fortune, 2 Feb 2025 | one hiring platform |

Left out:
- **"US applicants per role doubled since 2022" (LinkedIn) and Huntr's figures (its users mostly
  American):** the readers are European.
- **"Applying within 10 minutes of a job alert: up to 4x more likely to hear back":** quoted secondhand
  from LinkedIn staff, and not found in any LinkedIn publication.
- **"Up to 8x in the first 96 hours" (TalentWorks, 2017):** not found at its source.
- **"72% of offers go to applicants of the first five days":** no source.
- **"10x easier than any other job site":** nothing measures it, and a comparison with other sites needs
  evidence before it is published. The filters card says what they do instead.

Measured with a built site: nothing past the screen's edge at 320, 390 and 1,440 px.

Test: the landing page's test names the new section and the dock's "Why", and each of its five figures
links to an `https://` source. It fails on the previous commit, which has neither.

## 2026-11-11, evening — the membership tried end to end on Stripe's test mode, and what that changed

The owner gave the sandbox's keys and product ("Mahoraga - Tier 1", £4.99), decided that **every existing
account gets free access**, and asked for cancelling before the 7 days are over, what happens after them,
coming back, and no way in without a valid subscription to be tried first.

**How it was tried.** The built site and the API on the site's own **restricted** key, the Stripe CLI
(1.55.6, checksum checked) forwarding Stripe's real webhooks, a real browser for Stripe's form and portal,
and a test clock for each person to move time. Seven runs, 76 checks, all passed in one run:

| | What happened | Checked |
|---|---|---|
| S0 | New account: the site made the customer with its own key, the form took the card, onboarding opened | 7 |
| S1 | Cancelled in the portal on day 0: open to the trial's end, then closed, nothing charged ("Your membership has ended"), back at £4.99 at once with no second trial | 22 |
| S2 | Day 8: £4.99 taken by itself, the next payment a month on and taken; cancelled in the portal, open to the month's end, then closed; charged £0, £4.99, £4.99 | 16 |
| S3 | A card that fails when charged: on day 8 closed at once, "Your last payment didn't go through", paid in the portal with another card, open again | 11 |
| S4 | The same never fixed: closed through Stripe's 9 attempts over two weeks; Stripe then cancelled it; back through the form | 12 |
| S5 | Cancelled at once at Stripe: closed at once | 5 |
| S6 | Account deleted while paying: Stripe customer deleted, subscription cancelled | 3 |

At every closed step: `/feed`, `/applied`, `/documents` and `/notifications` answered 402, the app showed
the pay page at whatever address it was opened, and the account was outside the alerts and the harvest.

**Found, and changed:**

- **The product's price was one-time**, which a membership cannot use: a monthly £4.99 price was added in
  the sandbox (`price_1UaiyGJ7E2072aP952lBVTV9`). deploy/SERVER.md step 10 now says recurring, monthly.
- **The sandbox had no portal saved**, and Stripe opens none without one. `STRIPE_PORTAL_CONFIGURATION`
  (optional) names a portal of the site's own, made for the sandbox (`bpc_1Uaj13J7E2072aP925yImu0z`:
  cancel at the period's end, cards, invoices, no switching plans): also what a live account that sells
  other things needs, so its portal never offers them. Unset, the account's default.
- **Two syncs at once could write the older read last.** Stripe sent the last failed payment and the
  cancel 0.3 s apart. `sync` now writes only over an older read (`billing_synced_at`, when its read
  began); in the final run three older reads were not written. A test fails without it.
- `billing.status_changed` was logged once per event that arrived together (three lines for one trial);
  it is now taken from what the write replaced, so once per change.
- **Stripe's SDK logged two INFO lines per call**; `stripe` joins the quieted libraries (`libs.obs`).
- An event for a customer the site does not have is INFO, not WARNING: normal after an account's deletion,
  and in an account that sells other things.
- **Every account there was before memberships gets free access, once** (`libs.membership.free_for_existing`,
  at the deploy's `python -m libs.db`). A counter says it ran (`next_ids`), so an admin's later Off stays off
  and anybody signing up afterwards starts at the trial. Tested through the schema step itself.

**For Stripe's settings** (step 10): Adaptive Pricing shows the form in the visitor's currency (HUF from
Hungary, with a switch to GBP); the sandbox retries a failed payment 9 times over two weeks, then cancels.
At a trial's end Stripe marks the subscription active while the first charge is attempted (in live mode it
can wait up to an hour), so a card that then fails keeps access for that long.

`tests/test_billing_stripe.py` re-checks the state changes against the sandbox through real subscriptions on
test clocks (3 tests, 51 s), skipped without `STRIPE_TEST_SECRET_KEY` and `STRIPE_PRICE_ID`: for every
`stripe` upgrade. The browser harness stayed in the session's scratchpad; its screenshots were read at each step.

## 2026-11-11 — a membership through Stripe: seven days free, then billed, and the site closed to anybody not paying

The owner (2026-11-11): people sign up, fill in their details on Stripe, start a 7-day free trial and are
billed after it, automatically; anybody who did not pay is restricted and can only pay, after which the site
opens again; the membership is managed from Settings on Stripe's side; the account holds when they last
paid, when the next payment is due and whether it is active; admins can give access without a
subscription, over it. One subscription. leadops' Stripe code and its fixes were the reference.

- **One rule for who the site is open to** (`libs/membership.py`): paying or in the trial (Stripe's
  `trialing`, `active`), free access from an admin, or an admin. One filter, asked by every page, by the
  alert and daily-email sweeps, and by the harvest's LinkedIn places (`active_configs`), so nobody gets a job
  by one door that another keeps shut. `current_user` now means signed in **and** a member, 402 otherwise,
  so a page added later is closed by default; the few that stay open ask `signed_in`: `/dashboard`,
  `/billing`, `/account` (password, consent, deletion), and Admin, which still answers 404. A test walks the
  whole published API as a non-member and names those. `ADMIN_EMAILS` moved into `libs`, and the notifier
  and harvester now get it, or the owner would be a member on the site and not to the alerts.
- **Stripe is the record** (`services/api/billing_routes.py`). `sync` copies a customer's whole subscription
  from Stripe, never one event's slice, so the order events arrive in, a repeat, or a missed one cannot
  leave an account wrong. It runs on Stripe's webhook (eight events, each checked against the endpoint's
  secret; 400 if not, 503 to be sent again when Stripe cannot be read back) and when the person comes back
  from Stripe (`?stripe=back` → `/billing/refresh`), so the site opens the moment they have paid rather than
  when the webhook arrives. What leadops had to fix, avoided by that: a status taken from an invoice event, an
  older event written over a newer one, the server's clock instead of Stripe's, a period end read off the
  subscription (on its item since Stripe's 2025-03-31 API: leadops got 0). The SDK is `stripe` 16
  (API `2026-10-28.endive`), capped at the major, since each pins an API version.
- **The payment form is Stripe's own, inside the page** (`ui_mode: embedded_page`; CLAUDE.md §0, never
  leave the site to finish a step). Stripe's script is fetched on that page only (`@stripe/stripe-js/pure`,
  its own 3.4 kB chunk), so no other page contacts Stripe. A card is taken even for the trial, so the first
  payment happens by itself; the trial is only for somebody who never had a subscription; opening the form
  ends any form opened before, so two tabs cannot become two memberships; somebody whose subscription is
  still running gets Stripe's portal, never a second one.
- **Who is restricted, and what they see.** A new account lands on "Start your free trial" right after
  confirming its email, before setting up a search; a failed payment closes the site at once ("Your last
  payment didn't go through" → *Pay now* → Stripe's portal); paying opens it again. Sign out and Delete my
  account stay on that page. Nothing else of the site is reachable from it, alerts included.
- **Settings → Your membership**: where it stands ("Active. Next payment on 2 December."), the last
  payment, and *Manage membership*, Stripe's portal, which comes back to Settings.
- **On the account**: `stripe_customer_id` (unique), `subscription_id`, `subscription_status`,
  `trial_ends_at`, `period_ends_at`, `cancels_at`, `last_paid_at`, `last_paid_amount`, `currency`,
  `billing_synced_at`, and `free_access_at`. A trial's 0 is not a payment.
- **Admin**: a Membership column in Accounts, and a Membership card on each account (where it stands, the
  trial, the next payment or the end, the last payment, the Stripe customer, when Stripe was last read) with
  a *Free access* switch (`PUT /admin/users/{id}/free-access`). Free access changes nothing at Stripe.
- **A customer Stripe does not have with the keys in use** (test mode's, after the live keys; one deleted at
  Stripe) is forgotten by `sync` with all that was read of it, so a membership from test mode opens
  nothing and the next payment form makes a real customer rather than failing for good. After moving the
  server from test keys to live ones, `python -m services.api.billing_routes` reads every account again
  (deploy/SERVER.md step 10); until then such an account stayed open.
- **Deleting an account deletes its Stripe customer first**, from Settings and from Admin, which cancels
  its subscription at once. If Stripe cannot be reached the account is not deleted (503): an account gone
  while still billed is a charge nobody can stop.
- **The trial's length is a platform setting** (`trial_days`, 7; Admin → Settings → *Free trial*). The price
  is Stripe's (`STRIPE_PRICE_ID`), read once a process, since a Stripe price never changes; the sign-up page
  and the trial page say it ("7 days free, then €9.99 a month").
- **Privacy**: Stripe is named as a processor, and `DISCLOSURE_VERSION` is 2026-11-11, which by that file's
  rule asks everyone to agree again before their next AI document or help-chat answer.
- **Found on the way**: `/feed` answered 500 for a person with no green words: `matching_jobs` returned a
  plain list and `their_jobs` set `.cut` on it. It returns `Matches()`; the existing test now asks the Jobs
  page's list too, and failed before.
- **Setup**: `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_PRICE_ID`, `STRIPE_WEBHOOK_SECRET` on the
  API (`:-`, by compose's own rule: missing, the API says so at start and on every payment tried; a `:?`
  would also stop the harvest). The API's log names a missing one, or keys from both test and live mode.
  `deploy/SERVER.md` step 10 is the Stripe side, test mode first; `scripts/dev.sh` reads
  `certificates/stripe.env`.
- **Measured**: the gate is one query by `_id` per signed-in request, 0.41 ms median and 0.63 ms p99 on the
  local mongod with 2,000 accounts. Against a remote Atlas it is one more round trip a request.
- **Tests**: `tests/test_billing.py`, 25, against Stripe faked at the network under the real SDK, so what is
  asserted is what is sent to Stripe and what is made of its answers; webhooks signed as Stripe signs them.
  Each guards a behaviour and was checked to fail when it broke: the gate letting everyone in (6 failed),
  alerts ignoring membership (1), an event's status trusted over Stripe's (1), an unknown customer kept (2).
  The suite's test accounts became paying members (`subscription_status="active"`): without that 196
  failed, which is what closed by default looks like. The browser walk now meets the trial page, checks
  it at every width from 320 to 1440 px and its theme switch, and plays Stripe's part by writing what
  the webhook would.
- **A walk that passed by luck**: the Admin chart's walk (2026-11-10) measured its six controls the moment
  the heading showed, before the chart's answer drew two of them, and failed one run in three. It waits
  for all six now (five runs of five).

## 2026-11-11 — every CV and cover letter written from the person's own uploads, in one call, and checked before it arrives

The owner (2026-11-10): "2 separate routes one for cv writing and one for cover letter writing … we'll
have a prompt collect all of it, and that's the prompt that we will optimize", with a final check, the
answer in whatever form is quickest to process, and the ATS check we already have. Then (2026-11-11): learn
how Claude Haiku 5.5 works and prompt for it; judge the results in the chat for now; make the checks a
document passes before it arrives as thorough as that judgement, on Haiku 5.5; keep the old documents that
hold rules and detections, "because we don't want to work twice"; every CV passes the ATS checklist.

- **Two prompts, one call each.** `libs/prompts/write_cv.md` and `write_cover.md` carry the job (title,
  company, place, the posting's language, the whole posting) and everything the person switched on in My
  documents: every CV, for a letter every cover letter too, "About you", their other files, and the
  languages their CV and their search name. The data comes first and the task last, as Anthropic's guidance
  for long inputs and for Haiku 5.5 has it (read 2026-11-11). Before writing, the model lists the five to
  seven things the posting wants most and quotes the line in their documents that shows each. Its final
  check is the review the CVs were judged by, step by step: six seconds on the profile and first job, every
  line traced to their documents, the posting's needs in its words, relevance, order and words, language,
  length, markup. The answer comes back between `<cv>` or `<letter>` tags. **New wording, approved by the
  owner on 2026-11-11 with the measurements below (CLAUDE.md §3a).**
- **The rules the owner's own reviews found are in it**, from the files that held them, which stay:
  `libs/scaffolds/tailor_cv.md`'s "most common failures", `tailor_cover.md`'s failures and mechanical scan,
  `03-writing-style.md`, `05-cv-templates.md`, `context.md`. A CV cuts hard to what this employer wants
  (about ten to twelve bullets), leaves no orphan lines on page two, says "end-to-end" only of testing,
  summarises long lists, never ends a bullet on what it shows, passes the interview backtrack test, and
  treats an AI tool as a tool. A letter opens on the work and names the company and role, shows rather
  than announces the match, closes on the work and never on applying, has no colons or contractions, and
  runs 220 to 280 words under a headline-style subject. The three CV-bench drafts removed this morning are
  back as superseded prompts in `libs/scaffolds/history/`, and `d-fallback`'s rule for when relevant
  experience runs out is in `write_cv.md`.
- **Code checks what a reviewer would send back, and says it back once** (`services/docgen/checks.py`, §0).
  A CV: a role that ended above one still held; a bullet over 35 words; a second page with a few lines on
  it; and in English the banned words, "I", "my", "me", "he" or "she", a length of experience their
  documents never state, a profile about applying or rating itself, two bullets on one verb, a bullet that
  ends on what it shows, a task bullet. A letter: under 180 or over 320 words, a paragraph over four
  sentences, a colon in a sentence, an opening without the company, and in English the banned words
  (unless the posting itself uses them), a close about applying, contractions. With the two checks from
  before: too many pages, and a number found nowhere in their documents or the posting, which now reads
  "10k+" as 10,000 (a letter was sent back for "10,000 records"). The second answer is kept unless it is
  worse; what is still wrong is logged as a count, never the CV's words. The word rules are English rules,
  so they run on English documents only, read by `libs.corpus.langfilter`: `langdetect` joins the docgen
  image.
- **One effort for an API key and a Claude sign-in.** Every writing call asks for `medium`
  (`build.EFFORT`): adaptive thinking with `output_config.effort` on a key, `--effort` on Claude Code.
  Until now a key ran at Haiku 5.5's default and a sign-in at Claude Code's own, which nobody had chosen.
  `complete_json`, which nothing called, is gone.
- **The ATS check reads the CV's own headings, in order, in whatever language they are in.** It looked
  for "experience" and "education", so every CV written in the posting's German or Hungarian, as the
  prompt asks, would have been flagged in Admin.
- **Language detection reads one text at a time.** langdetect fills one shared factory on first use, and
  threads that start together read it half-filled: eight English CVs read at once came back as None seven
  times, and in another run twice as Norwegian. The harvester describes postings on two threads (LinkedIn
  beside the boards), so a posting could have been stored in the wrong language and hidden by someone's
  language filter. No sign of it in the local corpus: its 20 postings tagged Norwegian, Danish, Afrikaans,
  Somali, Welsh or Tagalog read the same again, and its 115 with no language are all 40 characters or
  shorter. Found because the CV bench checks on several threads and counted different faults each run.
- **Chrome would not start while MongoDB's sockets held descriptors 3 and 4**, where Chrome reads and
  writes: a Python `preexec_fn` put the pipes there, and it runs after the fork's hooks, one of which is
  pymongo closing its sockets in the child, so `make_inheritable(3, 4)` met closed descriptors (EBADF).
  bash now puts the pipes there after the fork. Its test fails on the old code with the bench's own error.
- **The posting is no longer cut at 6,000 characters.** That cut the end off 17.1% of the 27,275 stored
  postings; 15,000 cuts none (the longest is 13,985).
- **The CV bench measures what production checks.** `NAME=db:EMAIL` reads a candidate from the local
  database; `--account EMAIL` writes on that person's linked AI account; `uploaded` is their own CV with no
  call; `--baseline`, `--effort`; each CV is held to `build.problems` and to the whole ATS checklist, read
  by pypdf and by pdfminer; renders take turns through the one Chrome, which crossed two documents' answers
  when a dry run rendered them at once.

Removed, each replaced by the above: the old builder (`tailor`, `tailor_cover`, `build_prompt`,
`fill_role_angles`, `_require_personalised`, `job_context` and its salary lines, `restore_style`,
`scan_hidden`, `page_feedback`); `libs/profiles`, which only it read; the API's "needs a writing profile"
refusal, which every account made on the site got; `Template.head`, the old letter prompt's `<HEAD_BLOCK>`;
`LlmClient.complete_json`; and their tests. **Kept (owner, 2026-11-11):** the setup agents' profile files
in each account, and every prompt file nothing reads now (`libs/scaffolds/`, `libs/prompts/SKILL.md`,
`03-writing-style.md`, `06-cover-letter-templates.md`), for the rules in them.

**Measured, 2026-11-11.** claude-haiku-5-5 on the owner's Claude Pro sign-in, the owner's account in the
local database, four postings in four groups (`scripts/cvlab`).

First, the first wording against the CV they uploaded, judged by `eval_cv.md` on claude-sonnet-5-5, three
samples, paired by posting: 57.0 against 37.5, **+19.5** (sd 4.29, ±2.15, 4/0/0), every dimension higher on
every posting. Every CV filled two pages and all twelve verdicts were "borderline".

Then judged in the chat by Claude Opus 5.5, at the owner's word: each CV read against the posting and their
documents and scored out of 100, the same postings, three samples. **Not blind**: the first wording's CVs
were read before the next was written, and the wordings differ visibly. The final wording is v3 plus the
rule for when relevant experience runs out, which these postings do not reach.

| measure | first wording | v2 | v3 (final) |
|---|---|---|---|
| judged, /100 | 62.8 | 73.7 | 77.6 |
| paired against the first, by posting | | +10.8 (3.7, ±1.9, 4/0/0) | **+14.8** (3.5, ±1.8, 4/0/0) |
| bullets, mean | 16.7 | 11.4 | 11.1 |
| one page | 0 of 12 | 7 of 12 | 12 of 12 |
| sent back by the checks | 12 of 12 | 10 of 12 | **0 of 12** |
| numbers not in their documents | 0 | 0 | 0 |
| ATS checklist, pypdf and pdfminer | 12 of 12 pass | 12 of 12 | 12 of 12 |
| seconds | 40.3 (Claude Code's effort) | 47.3 (`medium`) | 41.6 (`medium`) |

Effort: v3 at `low` took 30.0 s, but the checks sent back 4 of 8 (two short second pages, two repeated
verbs), each a second call of about 25 s; `medium` was kept.
Thin experience: a fictional 2026 graduate with retail, warehouse and tutoring work, two junior postings,
two samples. v3 and the final wording both gave one page and no faults, Education first in 7 of 8,
"Hard-working" and "my" gone; the final wording ran 263 words to v3's 243, with the same seven bullets.

Letters (no judge prompt yet; read in the chat, two samples): the first wording 68.5, v2 77.3, **+8.8**
(4.0, ±2.0, 4/0/0). The checks sent back all 8 of the first wording's (every one closed on "I would
welcome…") and 5 of v2's 8 (colons). The final wording, v2 with a worked colon example and the phrases that
announce a match, one letter a posting: 1 of 4 sent back (a paragraph of five sentences). 18 to 22 s a
letter on average.

What did not move, or is not shown: **a CV takes about 42 s, twice the 20 s target** (CLAUDE.md §0), while
a letter is within it; `low` is faster per call but needs second calls. The scores are one reader's and not
blind; Sonnet's 57 and this 62.8 for the same first CVs are different scales. Seen on the final wording and
not checkable by code: a side project above the main job (1 of 12), a finished internship in the present
tense (1), a tool moved to the project it was not used on (1). The plan's five-hour limit stopped the run
once, and the last calls were made after it reset: the final CV wording, one CV a posting, gave 3 of 4 on
one page with nothing to send back; the fourth, for 12Build, kept a team-lead project and spilled a few
lines onto page two, which the check sends back; 40 to 49 s. Not measured: GPT and Gemini.

Gates, 2026-11-11: import contracts 2 kept; ruff clean; schema on an empty local mongod (28 collections);
contract current (80 paths); site built; pytest 1,379 passed, 2 skipped (a Postgres source, the
reference instance).

## 2026-11-10, evening — Admin's chart counts by source; an account's page you can read at a glance; back goes to Accounts

The owner asked for:
- **Sources on the corpus chart**, every job board ticked at first.
- **The chart's dropdowns the size of the others.**
- **Points of 30 minutes, 2, 4, 8 and 12 hours.**
- **Back from an account lands on Accounts.**
- **More about each account, laid out to be read at a glance**: it was "too simple and hard to read".

**Sources, as what the chart counts.** A second dropdown, Sources, beside Countries. Every board is
ticked when the page opens. Unticking one takes its jobs out of every line and every country, and its
table still lists it with its jobs, busiest point and share. A board that found nothing in the range is
listed at 0, which is worth seeing. "All sources" ticks or unticks them all, so one board alone is two
taps. Sources are what is counted, not more lines: nineteen lines a hue each cannot be told apart, and the
chart already caps them at six.
- **The counter gained the source.** `CountryCount` is now one row a run, country and source
  (`CorpusRepository.add`). The chart reads one counter, as before, and adds it up both ways.
- **The counts kept before this are split once**, at the deploy's schema step (`python -m libs.db`, beside
  `settle`): each is split by the sources of the jobs the corpus still holds for that run and country.
  Every count keeps its total, and what the corpus no longer holds stays as "Not recorded", a source that
  can be left out too. A second run does nothing.
  - Measured on the dev copy: all 417 counts split in 0.49 s, every total kept, 5,338 of 5,338 jobs given
    their source and none left "Not recorded".
  - On the server the corpus holds 15 days and counting began on 11-04, so all of it should be split. It
    reads every posting since the first count, about seven days' worth, once.
  - Once the deploy has run it, `split_by_source` has nothing left to do and can go.
- **`/admin/countries` takes `without`**, repeated, the sources left out ("" for "Not recorded"), and
  answers `sources` beside `countries`. `by` is gone: it said what `bucket_minutes` says. The page tells
  the date-only points from the rest by the step itself.

**Every box in the row one size.** The Countries and Sources dropdowns have their names above them, as
Show, One point is and Scale do. All six boxes are now 41 px tall with 14 px type. The dropdown's text had
been 16 px beside the selects' 13 px, and taller.

**What one point is: 15 and 30 minutes, 1, 2, 4, 8 and 12 hours, a day.** The rule is unchanged: a step
is offered while it draws a range in 1,000 points or fewer. Each new step holds whole runs and divides a
day. A point of two hours or more starts at UTC's round hours, which aren't round in Budapest, so those
are labelled every few points, counted back from the last, as days were. "Busiest hour" and "The numbers,
hour by hour" say "4 hours" where that is the step.

**Back goes to Accounts.** Each Admin tab has its own address (`#/admin/accounts`), so "← All accounts"
and the browser's back button land there, and a reload stays on the tab it was on. A tab is changed in
place, not added to the history: going back leaves Admin, not the last tab.

**An account's page.**
- **The header:** name (or address), the address, and badges for where it stands (Active, Email not
  confirmed, Still setting up, Alerts paused, Admin), with when they were last here and when they joined.
- **Four numbers in tiles**, each with the line saying what it means:
  - on their list, and how many are new since their last visit;
  - sent by alerts, and when the last one was;
  - made here, as CVs and cover letters;
  - skipped.
- **Their applications** as a funnel: applied, interview, offer, hired, each with a bar of its share,
  then the rejections. It is counted by the rule the Outcomes tab uses (`applications.outcomes`, now
  for one person too).
- **What they look for** as chips:
  - green words in green and red words in red;
  - countries, how they work and languages;
  - each green word and country with the jobs on their list it accounts for. A word that finds nothing
    is faded and dashed, so a search with a dead word shows it at once.
- **Where their jobs come from:** a bar for each source on their list.
- **Documents:** CV and cover letters uploaded, the looks picked, and the AI accounts linked, with the
  one in use marked. "None in use" when there's none.
- **Alerts:** email, browser and Telegram, each on, off or where it stands.
- **What they did,** newest first, as a timeline: on the site, alert sent, document made, applied,
  finished setting up, confirmed their email, signed up.
- **Limits**, as before, in a card.
- **On a phone**, the tiles go two to a row, and each card's facts and each limit stack. Checked at 320,
  390 and 1,280 px in both themes, with nothing past the screen's edge.

**"New since their last visit" counts when a job's text was read** (`described_at`), as the Jobs page
does since 11-06. It counted when the job was found, a second definition of "new".

**The list counted is their list at "Any time",** read as far back as the Jobs page reads it
(`scan_for`). It read the first 2,000 matches, so a long list was cut short without saying so. Measured on
the dev copy (1,939 jobs on that list): the page's read is a median of 237 ms over 5 runs. The chart's
read is about 5 ms whatever the range.

Tests, each failing on the previous commit:
- **The chart:**
  - only the sources picked are counted, and every source is listed (the API);
  - the old counts are split by source, totals kept, and a second run does nothing;
  - a job is counted under its source;
  - a 4-hour point adds up its runs;
  - the steps offered at 24 hours, 30 days and everything counted.
- **The account page:** the counts for each word and country, sources, the funnel and the last times.
- **The browser:**
  - Sources opens with all 20 ticked and changes the total;
  - the six boxes in the row are one height;
  - "← All accounts" and the browser's back land on Accounts, and a reload stays there.

The country chart's browser test now finds its table among two. The tabs test reads the account page's
new header ("Last here just now") and its "What they look for".

Gates, all local:
- `lint-imports`: 2 contracts kept.
- `ruff check`: clean.
- The schema pruned onto an empty local database: 28 collections.
- The contract gained `SourceSeries`, `Tally`, the account's new fields and `without`, and lost `by`.
  Regenerated, it is the same.
- `npm run build`: 115.7 kB of gzipped script and 18.1 kB of style.
- `pytest`: 1,416 passed and 3 skipped (the Postgres copy, the reference instance, an empty parameter
  set). The browser tests ran.

## 2026-11-10, later — the broker's image starts; Haiku 5.5 writes Claude's documents; sign-ins logged

- **The sign-in broker crashed at start on the server** (`ModuleNotFoundError: httpx`). `libs.signin`
  now reaches `libs.llm`, whose providers import httpx at start, and the broker's image did not install
  it. `test_the_entrypoint_imports` could not see this: it runs where every service's packages are
  installed at once, and it did not list the broker. A new test follows each entrypoint's imports through
  `libs` and `services` as Python runs them at start, and fails when a package is not installed by that
  image's requirements, or needed by one that is. It failed on the broker alone (`httpx`, from
  `libs.llm.rest`); `httpx` is in its requirements now.
- **Claude Haiku 5.5** writes with every Claude account (owner), `CV_MODEL`, before the new prompts are
  written and tuned on it. Haiku 5.5 thinks adaptively only and answers a token budget with a 400
  (Anthropic's migration guide), so `libs.llm.client._thinking` sends a budget to Claude 4.5 and before,
  and `{"type": "adaptive"}` from 4.6 on. An Admin panel that saved its document models before keeps the
  model it saved, until it is changed there.
- **Sign-ins are logged** (owner: "add logs to debug it"): each start (where the link goes and comes back
  to), each code typed in (its length and its halves around the "#", never the code), and a refusal with
  what the tool said, in its words. A terminal shows what is typed into it, so the screen holds the code:
  the log takes it out, and its test caught that.
- **Why Claude still refused the owner's code after the fix:** the refusal they quoted ("Copy the newest
  one from the page you signed in on") was replaced at 11:22 with the fix itself, and the broker that
  answered had started at 11:01, under the old `python -m` command, which never reloads. `scripts/dev.sh`
  restarted it at 20:56 (the broker under `uvicorn --reload` now). Checked against the real `claude` 2.1.319:
  the link handed out was the whole one it printed, 8 starts of 8 (state and challenge 43 characters, back
  to `platform.claude.com/oauth/code/callback`); a made-up code reached it and was refused in its words.
- **The bin beside an upload is round.** Every button on a page is at least 2.75rem tall, for a thumb, so
  the bin, a button 2.5rem wide, was an oval beside the round download, a link. Both are 2.75rem circles.

Gates, 2026-11-10: import contracts 2 kept; ruff clean; schema on an empty local mongod; contract current
(80 paths); site built; pytest 1,412 passed, 3 skipped (the same three).

## 2026-11-10 — Claude's sign-in takes the code; the AI accounts card says what it is; uploaded files download again; the Make buttons for everyone

The owner: ChatGPT's and Google's sign-ins worked, Claude's turned down every code, "even the latest";
the Settings card did not say what it was for; a switch changed at once, so nobody could see it switch;
and a person who uploaded a CV or cover letter could not download it again. Then: each provider's own
mark on its tile and on each account linked, no "Writing with" box, the Make buttons for everyone, saying
what is missing in a toast that leaves by itself and in the bell, and a download beside every upload.

- **Claude's sign-in** (`libs.signin`). The code was sent as a bracketed paste, and `claude auth login`
  reads it as a plain line (`readline.createInterface({input})`, no terminal), so the paste markers became
  part of it. The same call in Node, given both: bracketed came back as `code "\x1b[200~abc123"`, `state
  "st456\x1b[201~"`; plain as `"abc123"`, `"st456"`. Every code reached Claude broken, and Claude answered
  "That code wasn't accepted". It is now typed in as text, as `project-planner` does, then Enter on its
  own. Claude Code also exits once it refuses a code, so a refusal ends the sign-in (the page offers a new
  link, on a 410 from the broker) instead of waiting on a dead one. The tools get `BROWSER=/bin/true`:
  the person opens the link, and nothing opens one where the tool runs. Checked against Claude Code
  2.1.319: the link's `redirect_uri` is the manual one, and a made-up code is refused and the session ended.
  A real code was not tried here; that is the owner's own sign-in.
- **The card** (`web/src/aiaccounts.tsx`): what it is for in one sentence; the linked ones, each with its
  provider's mark; then Claude, ChatGPT and Google Gemini as three tiles, each with its mark and who it is for;
  the API key form folded under "Use an API key instead". A sign-in is three numbered steps. The marks
  are inline shapes: Claude's and OpenAI's from Simple Icons, Claude's in its orange and OpenAI's in the
  text colour (OpenAI's guidelines: black or white); Gemini's sparkle and gradient from Google's own file
  on gstatic. A "Writing with …" box above the list was tried and taken out (owner): the switch says it.
- **Switching is seen.** The list put the account in use first, so every switch answered with the same
  top row "In use", as if nothing had changed. The rows now stay in the order linked, newest first
  (`libs.llm.accounts.listing`); the switch says "Turning on…" for at least 0.9 s while the server answers,
  its knob glides in 0.6 s (twice the site's other switches), and the row's bar changes after it.
- **Uploaded files are kept, to download again** (`uploaded_files`, `GET /documents/{id}/file`). Until now
  only the text read out of a file was stored, deliberately ("keeping strangers' records on disk would be
  a liability"); the owner asked for the download, so the file is kept now, in a collection of its own so
  that nothing reading documents loads it, deleted with its document and with the account. It downloads as
  uploaded, always as an attachment (`nosniff`), under one "Download" beside every CV and cover letter.
  Files uploaded before today kept only their text, so theirs is written again in the format it was
  uploaded in (owner: "download in the same format as the uploaded file"): a PDF, a Word file or text,
  with its words and lines, not the original's layout, which was never stored (`libs.documents.remake`).
  The PDF needs a writer and a font with more than Western European letters: `fpdf2` (2.8.9, capped below
  2.9, which was 12 days old) in the API's requirements, and `fonts-dejavu-core` in the API image; tested
  by reading each format back, "Łukasz Żółć" included. The privacy page already said "the files you
  uploaded never leave this server", which stays true.
- **The Make buttons are for everyone** (owner). Pressed without what a document needs, the server says
  what is missing: an AI account in use, and their own CV for a CV, their own cover letter for a cover
  letter (`libs.documents.needs`, asked by the build route and again by the worker before any text
  leaves). The page says it in a toast with the way to each ("Open Settings", "Open My documents"), and
  the server puts it in the bell (`notify.NEEDS_AI_ACCOUNT`, `NEEDS_UPLOAD`; one unread of each, brought
  up to date however often it is pressed). The bell is told at once (`notificationsChanged`), as it is
  when a document fails or is not kept: it looked once a minute, so the note showed only after a reload. Asked before consent: nothing is sent until both are settled,
  and the account is the longer step. `dashboard.ai`, which only hid the buttons, is removed with them;
  so is the Jobs page's "link your AI account" line.
- **One model a provider, chosen by us and shown** (owner, on the recommendation: prompts are tuned per
  model, Claude's first, then GPT's and Gemini's). Nobody picks a model: each provider writes with the one
  set in Admin (`settings.document_model`), and each linked account now says which ("Writes with
  claude-haiku-4-5"), read from that same setting, so the page cannot name another.
- **An upload's download and remove are pictures** (owner): an arrow into a tray and a bin, each named
  for a screen reader and titled for a pointer, the bin red when pointed at. The bin was drawn twice
  (the bell, Admin); it is drawn once now, in `ui.tsx`, beside the download arrow. A file's name keeps
  its width on a phone: the switch and the pictures go to a line of their own.
- **Toasts** (`web/src/toast.tsx`): one way for the whole site to say something briefly. A toast stays 8 s
  with the time it has left running down its foot, held while pointed at or focused, and closes on its ×.
  It is put in the browser's top layer inside whichever dialog is open: the open job is a modal dialog,
  and a notice outside it sat behind it with its links dead. My documents' "Saved" / "We found N new
  words", which had a toast and a timer of its own, says it through this one.
- `scripts/dev.sh` runs the sign-in broker under `uvicorn --reload` like the API: the broker that had been
  running was the code from before the fix, and would have been after it too.

Gates, 2026-11-10: import contracts 2 kept; ruff clean; schema on an empty local database (run twice,
`uploaded_files` with its index); contract current (80 paths); site built; pytest 1,400 passed, 3 skipped
(a Postgres source, the reference instance, an empty template set: the same three as before).

## 2026-11-09 — Your own AI accounts: sign in to Claude, ChatGPT or Gemini, or add an API key; documents only with one in use

The owner: CVs and cover letters are made only with the person's own AI account, linked in Settings: a
sign-in to their subscription (Claude as `project-planner` connects it; ChatGPT through Codex; Google
through Gemini CLI) or an API key (Claude, OpenAI, Gemini). Several may be linked, one is in use, each
switched on and off; nothing of a key or a sign-in is ever shown; the Make buttons appear as soon as one is
in use. The owner was shown, the same day, Anthropic's, Google's and OpenAI's own terms on a hosted app using
people's subscriptions, and decided to go ahead: an agreement is on the way.

- **The accounts** are rows of `user_secrets`, sealed as every credential (`libs.secrets`), several per
  kind, one `active` (a unique index refuses a second). `libs.llm.accounts` lists, links, switches and
  removes them; `SecretStore.add`, `reveal_account` and `reseal` are the AI accounts' path, and `put` /
  `reveal` by kind are left to the one Telegram bot token. `last_4` is retired (owner: not displayed), and
  so is `mark_validated`, which only a test called.
- **Signing in** (`libs.signin`, replacing `libs.claudeauth`): the broker runs the provider's own tool in a
  terminal and a folder of its own, `claude auth login --claudeai`, `codex login --device-auth` or Gemini
  CLI's Google sign-in. The person opens the link; Claude and Google show a code to paste back here, Codex
  shows one here to enter on OpenAI's page, and the page waits. Signed in is the tool writing its login
  files: those are the secret, sealed by the API; the folder is deleted. Checked against the real tools
  (Claude Code 2.1.319, Codex 0.167.0, Gemini CLI 0.67.0, pinned once in `deploy/docker/ai-tools.txt` for
  both images): each printed the link, and Codex its code, as read.
- **Writing with a sign-in** (`libs.llm.cli`): the login unpacked into a private folder for one call, the
  tool run with nothing inherited (`claude -p` with no tools, `codex exec` read-only, `gemini -p` in plan
  mode), the answer read, the folder deleted, and a login the tool renewed sealed again. Only the files a
  login is made of are ever unpacked. One docgen worker makes the calls; a second would need a lock per
  account first (a renewed login can retire another call's).
- **Documents**: `own()` is the account in use or nothing; the platform's key and allowance are now the help
  chat's alone, and the chat runs on an API key in use (a sign-in's tool offers no tool calls). A failure on
  the person's account (an expired sign-in, a plan used up) is said to them as the build's reason, not paged
  as a dead letter. The Make buttons are back, behind `dashboard.ai.account`; without one in use, the Jobs
  page says once to link one.
- **Removed**, replaced here: the `claude setup-token` connection (`libs/claudeauth`, the `/credentials`
  routes, `ClaudeConnect`, the `claude_oauth` kind and the Anthropic client's OAuth path); the deploy's
  first schema step deletes the tokens it stored (`accounts.settle`, run before the new rules: the prune
  could not change a row of a kind they refuse, and stopped the deploy in a test). Keys linked before are
  labelled and one goes in use. `TenantRepository.secret_metadata` went with its routes, `.saved-key` with
  its form, and `PLATFORM_AI_*` from the docgen service, which no longer reads them.
- **Locally**, `scripts/dev.sh` installs the three tools into its data folder and starts the broker.

Tests: the routes (`test_ai_accounts.py`), the sign-ins against stand-in tools (`test_signin.py`), the calls
(`test_cli.py`), the settle, the store's two shapes, the chat and document payers, and in the browser,
linking a key in Settings putting the Make buttons under every job and switching it off removing them.

Not changed, for the owner: the help chat's prompt (`libs/prompts/support.md`) still says the instant
version is free; `users.profile` has neither a writer nor a reader (`test_columns_have_producers`).

Gates: lint-imports 2 kept; ruff clean; schema on an empty db, and on old-style secrets twice (settled,
then nothing to do); contract current (79 paths); build; pytest 1390 passed, 3 skipped (the same three).

---

## 2026-11-08 — Making CVs and cover letters paused; the instant documents, built by code, removed

The owner: every CV and cover letter will be written by AI, from the CVs a person uploaded and switched
on (their base) and "About you" when they wrote it, on the platform's free allowance or their own key;
where AI cannot be used they are told so and nothing is made. Until that is built, nobody is offered a
document, and the instant ones are gone for good.

- **Removed:** `libs/documents/instant.py` (the CV and letter written by code from the uploads),
  `run.run_instant`, the `"instant"` mode of `POST /documents/build` (now 422), and its tests
  (`tests/test_instant_documents.py`, three in `test_docgen.py`). A build still queued as instant is
  refused with the paused sentence. The instant reader fixes proposed the same day are dropped with it.
- **Paused on the site:** no Make buttons on a job card, an open job, a job from an alert or an added
  job. The Jobs page says once: "Making CVs and cover letters is paused while we rebuild it." (one
  constant, `web/src/documents.ts`, also used by My documents' empty list and Help). The client's build
  hook went with the buttons. Documents made before are still downloadable on their jobs, in Applied and
  in My documents. The Telegram alert's second button says "See it on the site" instead of "Make my CV".
- **Kept for the rebuild:** the AI build path (`run_ai`, the queue, storage, downloads) and its API. Its
  tests now run on the AI path with a stand-in model instead of on instant documents; refusals no longer
  offer "the instant version". `BuiltDocument.mode` still allows "instant" for the documents already made.
- **Tests:** the looks' PDF tests write their sample person straight into the markup, since no code
  writes a document any more; the switched-off CV test checks what documents are made from
  (`documents_of`); the end-to-end walk checks that no Make button is offered and the page says why, and
  a kept CV is written into the database to check it is still offered after a refresh.

Not changed, for the owner: the help chat's prompt (`libs/prompts/support.md`) still says every job has
"Make my CV" and that the instant version is free. It is a prompt (CLAUDE.md §3a), so it waits for the
owner's wording. The landing page still shows a CV being made, as the product will again.

Gates: lint-imports 2 kept; ruff clean; schema on an empty db; contract current (78 paths, the build
request's mode now "ai" only); build; pytest 1380 passed, 3 skipped (the same three; the instant
builder's own tests went with it).

---

## 2026-11-08 — No document dated; dates only where there are some, at the right; links that work; bigger pictures

The owner, on the dates question left open above: dates of the sections, where the person has them, at
the right; where they are missing, "don't make a fuss, leave it out"; no CV or letter dated with the day
it was made; links on the CV, and on the letter where they fit; the right look always, never wrong; and
the pictures larger, to be seen on every screen.

- **No letter is dated.** The instant letter wrote the day it was made under the company's address; it
  writes none now, and `letter-meta` is left out when a job names neither company nor place. The six
  letter looks lose their date rules, and the picture its date block. An AI letter's model is still told
  today's date (`job_context`), which "Present" and "for four years" are counted to, and it often heads
  the letter with it: `build._undated` takes out each line that is only a date ("8 November 2026",
  "Lisbon, 8 November 2026", "November 8, 2026", "8. November 2026", "8 de novembro de 2026", "08/11/2026",
  "2026-11-08"), keeps the company's address above one, and keeps a date inside a sentence. No prompt
  changed.
- **Dates at the right in every look.** Tabula had them in a column on the left, and an entry without
  dates had its title slide into it; it now has them at the right, with its headings in a pale band to
  stay itself. A new test measures every CV look as Chrome prints it: each date at the right end of its
  line, and an undated title where the others are. It failed on the old Tabula.
- **A job written without dates is a job.** The reader anchored entries on dated lines only, so
  "Volunteer Coordinator, Lisbon Food Bank" over two bullets was read as a bullet of the job above, with
  its bullets. It is now an entry of its own, with no dates and none shown: a capitalised line with a job
  title in it, belonging to no dated entry, with a bulleted line under it. The reader's tests, the four
  golden CVs among them, pass unchanged; the new one fails on the old reader.
- **Links.** The CV's and the letter's header already carried the links typed in "Your name and links"
  (else the CV's own). The test now requires them to be followable in the PDF, in all 14 looks: a
  `/URI` annotation for the LinkedIn link and the `mailto:`.
- **Bigger pictures.** The sheet's from 0.189 to 0.25 of a page (150 to 199 pixels wide), the two in
  My documents from 0.121 to 0.18, 0.22 from 900 pixels up. At 320, 390, 768 and 1280 pixels nothing runs
  off the screen or over anything else.

Left for the owner: an AI letter still lays out its own header, address and subject, since the prompt
does not name the frame (ROADMAP item 44). That, and telling it the letter is not dated, is a prompt
change.

Gates: lint-imports 2 kept; ruff clean; schema on an empty db; contract current (78 paths); build;
pytest 1402 passed, 3 skipped (the same three).

---

## 2026-11-08 — The looks' pictures drawn at once; My documents in a new order; every look read by five ATS readers

The owner asked for:
- **The pictures in the look picker to be instant**, not 5–10 seconds.
- **My documents in this order:** Your name and links, About you, How your CVs and cover letters look, CVs
  and cover letters we made for you, Your CV, Cover letters and other files.
- **Every look checked against an ATS**, and the server's fonts tested in every look.

**The pictures.** Each was a frame with a request and a document of its own, ~40 in a sheet. Measured on a
laptop, with the server answering in 2 ms, 39 frames took 1.1–1.8 s to load; it is the documents, not the
server. Each picture is now sent with the shelf (`GET /templates`, 57.7 KB, 4.8 KB compressed, read ahead
as before) and drawn in the page, in a shadow root holding an `<html>` of its own. The look's stylesheet
applies there exactly as in a page of its own: all 14 pictures were the same pixels as before, on a page
whose own styles would have leaked in. `.picture` resets what a shadow root inherits; without that, up to
59,184 of a picture's pixels moved. The sheet's 37 pictures are drawn 0.10–0.26 s after the tap (0.39–0.47 s
on a CPU slowed fourfold), with no request. `GET /templates/{kind}/{id}/preview` is removed, and the
picture has no title any more, which in the page was the look's name a second time on its card.

**The ATS check.** Every look's sample CV, a two-page CV, and a stress CV and letter written as the AI
writes them ("C++", "C#", ".NET", "José Müller-Łukasiewicz", "€"), read by pypdf, pdfminer, MuPDF and
poppler's `pdftotext` (plain and `-layout`). Three defects, now fixed:

- **Ligatures.** Lato and Carlito draw "fi", "fl" and "ffi" as one glyph, and pypdf and pdfminer read it as
  one character: "certiﬁed", "workﬂow", "oﬃce", which a search for the word does not find. That was every
  Meridian, Kestrel, Prism, Tabula and Halcyon CV and letter.
- **Quill's headings**, in a 30 mm margin column beside their sections, were read by poppler and pdfminer
  as a column of their own: every heading after the first came apart from its section, and its 0.9pt of
  tracking on 9pt gave "E DUC AT I O N". Each heading is now on its own line at the margin, over its
  indented section, at 0.5pt.
- **Meridian's contact line** broke anywhere: a phone came out as "91234-" over "5678", read wrongly by all
  five, and by poppler as "912345678".

The first and third are fixed in one place, `templates.EVERY_LOOK`, added to the end of every look's
stylesheet: no ligatures, and each contact part kept whole unless it is wider than the line. Meridian is
the scaffold's own stylesheet and is not edited (CLAUDE.md §3a): it gets the rules this way. The model's
input is unchanged; an AI letter's `<HEAD_BLOCK>` is still read from the look's file. The contact rules each
new look carried (`flex-wrap`, `overflow-wrap`) are removed from the thirteen files: `EVERY_LOOK` is the one
copy, and a new look cannot leave it out.

After: every word whole, the name first, contact details exact, sections in order and every font embedded,
for all 14 looks with all five readers. Not every date: a job's date at the right of its title line, in
seven CV looks, Meridian among them, is read apart from its title by poppler (after the job's bullets) and
pdfminer (up to 5 of 15 gathered at a section's end). The date after the title, or under it, kept all of
them in place (38 apart before, 0 after, on Kestrel, Ledger and Meridian). That would change seven looks,
so it waits for the owner (ROADMAP Phase 8 item 48).

`tests/test_templates.py` now reads every look with pdfminer as well as pypdf, and requires every word whole
and nothing else, the name first, the headings in order, no ligature, and every short contact part on one
line. Against the looks before this change it failed 12 cases: the 11 ligature and Quill ones, and
Meridian's contact line. `pdfminer.six==20260107` is pinned in `requirements-dev.txt`.

**The server's fonts** (ROADMAP item 47). The image is Debian 13 (trixie, from `python:3.12-slim`'s own
history). Its five font packages plus DejaVu were fetched from Debian's archive. With only those, the
Dockerfile's `fc-list` checks pass, and every look's CV and letter (sample and stress, 28 PDFs) embeds the
same faces as on the laptop, with none in a fallback. Given only DejaVu, all 28 came out in DejaVu, so
Chrome does read the restricted list. The image itself is not built here: Docker is installed, but this
account is not in the `docker` group.

**My documents** is in the owner's order; an end-to-end test reads the cards' titles in it.

Also removed: `_PREVIEW_TEXT`, `_PREVIEW_WORD` and `_BAR` in `tests/test_templates.py`, left from the
rejected bar pictures with nothing reading them.

Gates: lint-imports 2 kept; ruff clean; schema on an empty db; contract current (78 paths: the preview
route gone); build; pytest 1399 passed, 3 skipped (the same three: no Postgres copy source, no reference
instance, an empty parameter set).

---

## 2026-11-07 — How a CV and a cover letter look: eight looks, picked in My documents, used by every build

The owner asked for:
- **CV and cover letter layouts that succeed, researched by job sector**, each an HTML template with a
  name of its own.
- **A picker in a modal**, with a small picture of each look. The most popular come first, then each
  sector with its looks inside; one look can be in several sectors.
- **One pick for CVs and one for cover letters.** Each account's documents are made in its picks and only
  in them.

The owner's answers when asked:
- The picker lives in **My documents**, not My search, which keeps its one job and its one Save.
- **"Most popular" is fixed from research**, not counted from picks.
- **Single column only.**
- The letter's date, company and subject are **filled when the letter is made**, never written into a
  template.

**A template is a stylesheet over one markup** (`libs/templates`). Every CV the platform makes carries
the scaffold's fourteen class names: the instant one, and the AI one tailored from a person's
`base_cv.html`. So a look is one HTML file whose `<head>` holds its style, and the writer fills the body.
No prompt file was edited (§3a).
- An instant document is written with the look's `<style>`.
- An AI CV has its base CV's style taken off, as before, and the look's put back instead of the base
  CV's.
- An AI letter gets the look's `<head>` as `<HEAD_BLOCK>`, and the look's style is put back over whatever
  the model returned.

Meridian, the default, *is* the scaffold's style, read from the scaffold. An instant document made by
somebody who never picks is byte-for-byte what it was.

**The looks:**
- **CV:** Meridian, Kestrel (compact, tech), Ledger (serif, finance and law), Prism (one colour band,
  marketing), Tabula (dates in a left column, the German tabular CV), Halcyon (larger type, care and
  teaching), Foundry (bold, one page, trades and hospitality) and Quill (academic, headings in the
  margin).
- **Letters:** Meridian, Kestrel, Ledger, Tabula (DIN 5008: company left, date right, bold subject),
  Halcyon and Prism.

The research, with its sources, is in `libs/templates/README.md`. In short:
- Ladders' eye-tracking study: simple layouts with clear headings and bold titles over bullets; multi-column
  among the weakest.
- Kickresume's 2.1M CVs of 2025: single column, restrained colour.
- ATS parsing reads columns row by row.
- The sector norms: serif for finance and law, tabular dates for Germany, registration first for
  nursing, one accent for creative work, one page for trades.

**What the ATS check caught.** Every look was rendered through the real Chrome and held to docgen's own
ATS check. Four failed on first drawing, each looking right on the page:
- **Kestrel:** 1.4pt tracking on 9pt headings came out as "E X P E R I E N C E".
- **Ledger:** synthesised small capitals came out as "A LEX M ORGAN": the candidate's own name, misread.
- **Quill:** Chrome paints floats before the text around them, so the floated margin headings reached
  the PDF's text all together, ahead of the name. Now a grid.
- **Tabula:** the date sits left of the title it follows, so the text ran "Northwind LogisticsMar 2021".
  Each title now ends in a kept space.

`tests/test_templates.py` renders every look, the CVs also at two pages, and asserts four things. Each
passes the ATS check, starts with the name, keeps each heading before its section, and is set in the face
it asked for. The four rules are in the README for the next look.

**The fonts.** The docgen image had Lato only, and Chrome falls back without a word. It now installs and
`fc-list`-checks Liberation (Ledger, Foundry), Carlito (Kestrel, Tabula) and Caladea (Quill). A test
fails on a face a look asks for that the image does not check. *Not built here:* this machine cannot run
Docker. The packages and versions are Debian's, and were checked against the local apt.

**The picker.** My documents has a card, "How your CVs and cover letters look": the two looks in use, each
with Change. Change opens a sheet with "Most popular", then the 14 sectors, each look shown by its name
only. The owner asked for the one-line descriptions to go; they went from the catalogue and the API too,
since nothing read them any more.

Each picture is the look in abstract shapes, not a document (owner: "no words or writing, just abstract
design"). `templates.picture` lays one small fixed sketch out with the look's own stylesheet: a name, a
contact strip, headings, a few entries and lines, in the class names every document carries. So the
look's colours, bands, rules, type sizes and date column show. Every part is a rounded block in its own
colour, with no character anywhere. A first version drew each word of a made-up sample as a bar; it read
as redacted writing, and went. The sample now lives only in the tests that render every look. Each picture
sits in a sandboxed frame and loads as it scrolls into view.

A tap picks the look (`PUT /templates/{kind}`); the sheet closes and says what changed. `/templates` is
read ahead after sign-in with the other tabs, so the card draws at once.

**What the first pictures showed.** In three of them the contact line ran past the right margin. Rendered
with a long email and three links, every new look's PDF lost the third link off the page (13 of 13); only
the scaffold's Meridian wrapped. Each part had been kept whole, with nothing to break between them. The
contact line now wraps between its parts, and inside a part wider than the line. A test renders every look
with four links, one ~150 characters long, and finds each one in the PDF.

**Removed:** `instant._LETTER_STYLE` (the cover looks replace it), and `build.head_block` (a letter's head
is its look's, `Template.head`). `libs/templates/README.md` described the marker renderer deleted on
2026-08-14; it now describes this.

**Measured:**
- Every look's sample is one page; the long sample is two or three.
- All 14 pass the ATS check, with the face asked for embedded.
- Eleven deliberate mutations (each defect above put back, a face left unchecked, a pick ignored by
  instant, AI CV or AI letter, a pick not saved, the letter undated, the read-ahead dropped): eleven
  caught. Then two more: the contact line kept whole, and a part unbreakable. Both caught.

Gates: lint-imports 2 kept, 0 broken; ruff clean; schema on an empty db (27 collections); contract
current (79 paths); build; pytest 1,400 passed, 3 skipped (no Postgres copy source, no reference checkout,
an empty parameter set).

---

## 2026-11-07 — The landing page, for somebody scrolling past: what they get, in big titles and moving pictures

The owner asked for:
- **Big titles, no small labels:** the pills ("The job-search radar · live", "01 · What it is", …) give
  way to big titles that say what each section is.
- **No "Honest by design" paragraph.**
- **"How it works" told from the person's side:** what they get, in order after signing up, in keywords
  somebody skimming understands. Worked out from who they are and what hurts.
- **"Get started" seen when scrolled past:** its two steps were too compact.
- **"What it is" told by design and animation.**

**Who it is for, and what it gives them.** People looking for work, in any field ("nurse" as much as
"python"), in Europe and the UK, many never having used a job tool, most of them skimming. What hurts,
and what the page now says they get:

| What hurts | What they get |
|---|---|
| Hours a day checking job sites one by one | Every job site in one list, read every few minutes |
| Results that do not fit: wrong country, language, level, or the same job twice | Only what fits: their words, countries and languages, never twice |
| Rewriting a CV for every application | A CV and a cover letter for each job, in one tap, from their own CV |
| Finding jobs late, then losing track | New jobs on their phone; apply on the job's site in minutes, then follow it to the offer |

**Each section, a big title saying what it is.**
- **What it is:** "Every job site. One list for you." A picture in three parts, jobs travelling from one
  part to the next (`Flow`): the job sites, blinking in turn; what is left out (the same job twice, a
  country you can't work in, your red words), struck through in turn; and your list, jobs landing in it,
  each saying why. Under each part, its keyword, big: "Every job site", "Only what fits", "One list for
  you". It replaces four tiles of small type.
- **How it works:** "From sign-up to applied." Four steps, in the order they come after signing up, each
  a big number, a title and two lines, and a picture of that part of the app moving:
  1. upload your CV, and the words suggested from it;
  2. get every job that fits, new ones arriving;
  3. make your CV in one tap, the CV written as the ring fills;
  4. apply and keep track, applied, interview and offer lit in turn.

  It replaces six steps of what the server does (Collect, Clean, Match, Tell, Build, Refine), which is not
  the reader's to know.
- **See it working:** the phone with the working job card, under one line, and "New jobs on your phone"
  as a title of its own beside the alert.
- **Start in a few minutes:** two big cards, each a number, when ("A few minutes, once", "Then every
  day"), what ("You set it up", "It runs itself") and three things in body-sized type. They were small
  tiles of small type that went by unseen.

**The rest.**
- **The hero** is the name, the line, two things in bold ("Every job that fits you", "A CV for each"), the
  buttons and the numbers.
- **The numbers** (owner: better ones, "25+ sources", "rapid" checks for "15 min", one more counted):
  - **Job sources:** 19, the real count of the one source list. "25+" was the count before Hiring Cafe,
    Jooble and JobSpy were removed (2026-11-06), so the page keeps the server's.
  - **Company career pages and new jobs this week:** each said as the round number it is at least
    ("40+", "70,000+"), so a big one reads at a glance and stays true. New jobs this week is
    `Reach.jobs_week`, counted as the admin's chart counts them (`CountryCount`).
  - **"24/7 · Rapid checks"** replaces "15 min · Between checks". The sections below still say every 15
    minutes.
  - **"100% Yours alone",** which said nothing, is gone.
  - **A number that counts nothing is left out.** On a phone a lone last one takes its row.
- **The foot** says the hero's line, not "The job-search radar".
- **The phone picture** says "Jobs stay here for 7 days.", as the app does.
- **Step 1's example** hides "unpaid" and "commission only". It hid "senior" beside a "Senior Backend
  Engineer" demo job.

**Removed, nothing left using them:**
- the pills;
- "Honest by design";
- the "Try it" line, which said what the line under its title says, in small type;
- the four tiles, with the live bar chart and the word toggles, and with them `Reach.found`, the
  six-hour series only that chart read;
- the styles for all of these (`.pill`, `.bento`, `.tile-k`, `.tile-demo`, `.found`, `.bars`,
  `.word-demo`, `.honest`, `.try`).

**Measured:**
- **The hero's first button against the fold,** before and after (8 sizes):

  | Size | Before | After |
  |---|---|---|
  | 320×640 | 646 px, below the fold | 510 px |
  | 390×844 | 619 px | 511 px |

  A phone held sideways still needs a scroll, as before.
- **Nothing past the screen's edge** at 11 widths from 320px to 1920px, every section revealed.
- **Reduced motion:** every picture rests in its last state, which is what a still screen shows: the
  strikes drawn, the list landed, the ring full, the CV written, every step lit. The lines that strike
  are elements, not `::after`, because the site's reduced-motion rule reaches elements only.
- **Bundle:** 109.3 kB of gzipped script and 16.5 kB of style, 0.4 kB less and 0.9 kB more than before:
  the pictures' motion.

Tests, each failing on the previous commit:
- **The titles:** the landing page's four sections have their titles. The previous commit shows the four
  old ones.
- **The numbers:** the page shows "19", "40+", "72,000+" and "24/7" for 43 company boards and 72,345 new
  jobs.
- **The week's count:** `/stats` counts the week's new jobs and not the week before.

Gates, all local:
- `lint-imports`: 2 contracts kept.
- `ruff check`: clean.
- The schema pruned onto an empty local database.
- The contract is unchanged.
- `npm run build`: as above.
- `pytest`: 1,330 passed and 3 skipped (the Postgres copy, the reference instance, an empty parameter
  set). The browser tests ran.

## 2026-11-07 — A week on the shelf, the last day first, every person's filters kept, and a list that reaches back

The owner asked for five things:
- the landing page's sections at the foot of a phone get their words too;
- the job shelf life is 7 days;
- every Jobs page opens on "Last 24 hours";
- each person's filters are saved, so they come back as they were left;
- and: "i also set the job shelf life from 24 hours to 7 days but the jobs page still didn't change".

**Why the shelf life changed nothing.** The setting was read on every request, and the line under the
title changed with it. What did not change was the list.
- **The cause:** the feed read only the 2,000 newest postings with the person's words (`matching.SCAN`),
  then filtered them in memory.
- **The cost of it, measured on the dev copy with the owner's own words** (python, rest, api, software
  engineer, developer; Europe and the UK), at a week's shelf life: 898 jobs reached the list, the newest
  day and a half, of 3,210 the week held. 55% of the 2,000 read were then dropped by red words, languages
  and places.
- **On the server,** with several times the postings a day, it is likely hours.

**The fix: the read reaches as far as the time asked.**
- **The time chosen under "Posted" goes to the database**, so the budget is spent inside it. The read
  starts a day earlier, for a board whose clock runs ahead of ours.
- **The budget is 2,000 for each day asked** (`scan_for`): "Last 24 hours" reads what the page read
  before, and "Any time" up to 14,000.

Measured on the dev copy:

| Posted | Budget | Jobs | Median |
|---|---|---|---|
| Last 24 hours | 2,000 | 64 | 334 ms |
| Last 3 days | 6,000 | 2,573 | 767 ms |
| Any time (the week) | 14,000 | 3,209 | 912 ms |

Before, "Any time" was 898 jobs in 334 ms. On the server the default costs what it did; a longer time
costs more, not measured there. The list fades while a read takes that long (`aria-busy`, after 0.3 s, so
a quick one does not flicker).

**A list cut short says so.**
- **When the budget runs out inside the time asked,** the heading says "898+ jobs" and the line under it
  says these are the newest (`Feed.cut`).
- **Spent only in the day before the time asked, it has read all of it, and is not called cut.** Found
  measuring: both shorter times were flagged while read whole.

**What the filters offer is counted inside the time asked.** "golang 5" beside a list of 2 from the last
day disagreed. The other filters still do not narrow the counts, so another can be picked.

**A week by default.** `DEFAULT_JOB_MAX_AGE_HOURS` is 168, where it was 24.
- **Nothing to bring back:** postings stay in the corpus 15 days before the archive.
- **The line under the title** says "Jobs stay here for 7 days." It said "From the last 24 hours · slower
  boards: the last 7 days", which would read against a list of the last day.
- **"Posted" offers only the times shorter than jobs stay.** "Last week" is "Any time" now.
- **Alerts:** a LinkedIn job whose text is read days after it was found can now be alerted then, up to
  the week.

**"Last 24 hours" first, and the filters kept.**
- **Where they live:** `JobFilters`, named as `/feed` takes them, is kept on the person
  (`User.job_filters`), saved by `PUT /feed/filters`, and handed to the page with the dashboard. A new
  account's is "Last 24 hours" and nothing else.
- **On the page,** one object holds them, where there were ten pieces of state. It is saved 0.4 s after a
  change and at once on leaving, by closing the tab too (`keepalive`, new in `api.ts`). This tab keeps its
  copy, and each answer is kept under the question that got it, so Jobs comes back drawn as it was left.
- **"Clear filters" clears what narrows the list,** and keeps the time and the order. The time is how the
  list is shown: "N jobs match your words", not "your search".
- **Saved filters that no longer read** (after a filter changes shape) are the defaults, with a warning
  in the log.

**The landing page's sections on a phone:** a picture over each short name ("What", "How", "Jobs",
"Start"), as the app's bar has. The light behind the one you are in grows with them.

**Tests that read ages against a day's shelf life now set it.** Five tests used a 25-hour or 3-day-old
job as "too old" and now set the day themselves:
- `test_pipeline_shape` (three tests);
- `test_user_filters`;
- `test_counters_match_workers`.

The test that the page shows what the shelf life says now starts from the week, then sets a day and two.

Tests, each failing on the previous commit:
- **The week by default:** the shelf-life test, which starts from the week.
- **The reach:** a longer time reaches a job that a day's budget does not, and says when it was cut.
- **Saved filters:** a new account opens on "Last 24 hours", what it saves comes back, and broken ones
  fall back to the defaults, said in the log.
- **The browser:** the filters stay through a reload made the moment the last one changed, "Clear
  filters" keeps the time, the page says "Jobs stay here for 7 days.", and the landing dock shows its
  words.

Two more, for faults found while measuring, each failing on the rule it replaced: a budget spent in the
day before the time asked is not called cut, and the counts stay inside the time asked.

Gates, all local:
- `lint-imports`: 2 contracts kept.
- `ruff check`: clean.
- The schema pruned onto an empty local database.
- The contract gained `JobFilters`, `ReadyDashboard.job_filters` and `Feed.cut`, and regenerated it is the
  same.
- `npm run build`: 109.7 kB of gzipped script and 15.6 kB of style.
- `pytest`: 1,330 passed and 3 skipped (the Postgres copy, the reference instance, an empty parameter
  set). The browser tests ran.

## 2026-11-07 — The phone's menu in words, with Admin; one title on My search; no purple labels; no gap above a phone's bar

The owner asked for four things:
- My search's "What we look for / Green words show you jobs. Red words hide them." becomes "The jobs you
  want / Green words find jobs. Red words hide them.";
- the small purple heading above every page's title goes;
- the phone's menu gets its words, fitting every screen;
- Admin appears there for an admin.

Then, looking at a phone, a fifth: the bar at the very top of the screen, with no space above it.

**My search had two titles.** The page's title and the first part of its form said the same thing
("What we look for", then "The jobs you want").
- The title is now "The jobs you want", with "Green words find jobs. Red words hide them." under it, and
  that part of the form drops its heading on this page.
- Onboarding's second screen is titled "What we found", so the same part keeps its heading there.
- Both read the words from one constant (`WANTED`).

**No purple labels.** The label above each title (`.kicker`: "Your jobs", "Settings", "Operator",
"Welcome back", …) is gone from every page that had one:
- the app's pages;
- the signed-out pages;
- onboarding;
- Privacy;
- the landing's picture of the Jobs page.

Four more places used the same style, and lost it too:
- **Privacy's label held its version,** which is what people agree to. It is now a plain grey line under
  the title.
- **Admin's own parts:** "Queue" and "Accounts" are headings now, "Hour by hour" a subheading, and
  "Window" a plain word.
- **The notifications panel's title** is plain bold.
- **`AuthShell`,** the shared frame of the signed-out pages, lost its `kicker` argument.

With nothing left using it, the style is deleted.

**The phone's menu: a picture over a word** (it was pictures alone from 2026-11-06).
- **Shorter names on a phone.** At 11px, "My search" is 56px wide and "My documents" 79px. A tab is 46px
  when an admin's six share a 320px phone. So the phone says "Search" and "Docs", and the laptop's row
  keeps the long names. `PLACES` carries both.
- **The words' size** is 10px on a 320px phone and grows to 12px by 390px, the size a phone's own tab
  bar uses. The widest word, "Settings", has 3px to spare on each side at 320px. A word that ever runs
  wider is cut with "…" rather than spilling into the next tab.
- **The bar is no taller** (58px). Its tabs are 2px apart instead of 4.

**Admin on a phone.** It was hidden there on purpose (`.places a[href="#/admin"] { display: none }`: "a
laptop tool; its tab would push Settings off the bar"). It now shows for an admin, as the sixth tab.

**Admin's tables on a phone.** Admin had only been swept from 1024px up. Swept on phones too, its Queue
table pushed the page sideways at 320px: the sweep's one finding.
- **The cause:** three of its tables (Queue, Accounts, and Pipeline's "Who it is running for") were as
  wide as their columns.
- **The fix:** they scroll inside their own box (`.table-scroll`), as Admin's other tables already did.
- **Checked:** all eight of Admin's tabs, at 320px and 390px, are no wider than the screen.

**The top of a phone's screen.**
- **The cause:** at 720px and below, one rule gave the app the signed-out pages' padding, 1rem, top
  included. That undid the rule that keeps the bar at the very top, so a phone's page began 16px down.
- **The fix:** the app's top padding is 0 there too.

Measured on the build, with an admin's six tabs, at 15 sizes from 320×640 to 1100×800 (phones held
sideways included):
- every word whole;
- the bar inside the screen and 58px tall up to 900px;
- the laptop's row unchanged from 901px;
- the top bar at 0 everywhere.

Tests:
- On a phone, each tab shows its word ("Jobs", "Applied", "Search", "Docs", "Settings").
- On a 320px phone, an admin's six tabs fit, every word whole, Admin visible, and the bar at the very top.
- My search's title is "The jobs you want".

Against the previous commit all three fail: the phone shows the long names, and My search's title is
"What we look for". Every tab now holds both names, so the tests that compared the menu's text read what
is shown (`innerText`).

The sweep of every page at every size (28 sizes), Admin now included at every width, found nothing
once those tables scrolled. Its sign-in was checked first: it reaches the Jobs page.

Gates, all local:
- `lint-imports`: 2 contracts kept.
- `ruff check`: clean.
- The schema pruned onto an empty local database.
- The contract is unchanged, and regenerated it is the same.
- `npm run build`: 109.3 kB of gzipped script and 15.6 kB of style, 0.1 kB and 0.03 kB less than before.
- `pytest`: 1,327 passed and 3 skipped (the Postgres copy, the reference instance, an empty parameter
  set). The browser tests ran.

## 2026-11-06 — Nobody applies here, and the site and its alerts say so: "Apply on …", "Mark as applied", Skip apart

The owner asked for the site to make one thing plain: nobody applies to a job on it or through it. It
helps with applying and saves a lot of time. "Open on …" was to say "Apply on …", "I applied" was to
become "Mark as applied", and Skip and "I applied" were to be clearer, moved or given pictures.

**A job's card.**
- **The buttons, in the order they are used:**
  1. "Apply on Greenhouse": the board's name, or "the job's site" for a job added by its link.
  2. "Mark as applied".
  3. Skip, at the far end.

  It was "Open on …", Skip, "I applied". Skip sat between the two steps of applying, and all three looked
  alike.
- **Each has a picture:**
  - "Apply on" ends with an arrow out of a box: the job opens on another site, in a tab of its own.
  - "Mark as applied" starts with Applied's tick from the tab bar, the place the job goes.
  - Skip starts with an eye shut: the job is not shown again. It keeps the others' outline and colour,
    because greyed out it read as a button that could not be pressed.
- **After "Mark as applied"** the card says "Moved to Applied: <title>", with Undo, so it says where
  the job went.
- **"Undo" stays whole.** Beside a long title it was squeezed until it broke as "Und / o".
- **"Read it all"** sits in its line of the summary. Every button has a thumb's height (44 px), and that
  included the link-style buttons inside a sentence, which pushed their line away from the one above.
  The same rule had held "Withdraw" in Settings and "Use another bot" in the alerts.

**The words.**
- **The landing page.**
  - **Hero:** "You apply on the job's own site, in minutes, not hours." It replaces "shows every match
    with the reason it matched", which the sections below say. The hero keeps the same number of lines
    as before. The buttons are where they were at all eight sizes measured, from 320×640 to 1440×900.
  - **Section 01:** the heading was "Your job search, on autopilot.", the phrase auto-apply tools use.
    It is now "You apply. It does the rest."
  - **Section 03:** you apply on the job's own site, then mark it as applied.
  - **"Honest by design":** it never applies for you.
- **The landing's card** has the same three buttons. Its job is made up, so "Apply on LinkedIn" says
  where a real job opens and who applies. It scrolls that line into view above the phone's tab bar: the
  bar's height (96 px) is now one value, `--tabs`, used for both the room kept free and where the scroll
  stops.
- **Help** answers "Can I apply to jobs here?". The other answers and the empty Applied page say "Mark as
  applied".
- **Comments and docstrings** that named the button now name the new one. `Step`'s docstring is part of
  the API contract, so `openapi.json` and `api-types.ts` change by that one line.

**Removed:** the style `button.secondary.on` ("I applied" once it was true). No button sets the class
`on` beside `secondary`; a search of `web/src` found none.

**Telegram** (owner: "open job should be changed on telegram").
- **The alert's first button** says "Apply on LinkedIn", the board's name, where it said "Open job". A job
  somebody added by its link says "Apply on the job's site": such a job is shared and alerted like any
  other.
- **The line above the buttons** names the board the same way, "Northwind · LinkedIn", not by its key,
  "linkedin".
- **The landing's picture of an alert** matches. At 320 px its "Apply on LinkedIn" broke in two, so the
  two halves stay equal, as Telegram draws them, but never narrower than their words.

**One list of board names.** The names were the Jobs page's own (`BOARDS` in `JobsPage.tsx`), and the
Telegram card needed them too.
- They are now `libs/corpus/boards.py`: `board_name` ("No Fluff Jobs") and `apply_on` ("LinkedIn", or "the
  job's site").
- The feed sends each board's `label` and each job's `apply_on`.
- The site's copy is deleted, with its rule for jobs people added.
- `link`, the source the add-a-job route writes and `apply_on` reads, is one constant (`repository.LINK`),
  beside `TYPED`.

**Not changed** (owner: leave the help chat for now): `libs/prompts/support.md`, the help chat's prompt,
still says "I applied". CLAUDE.md §3a: a prompt changes only on the owner's word. No page on the site
calls the chat, so nobody sees it today.

Tests: on the Jobs page, "Apply on Test" links to the job's own address and opens in a new tab, and "Mark
as applied" says "Moved to Applied". On the landing page, the card's "Apply on LinkedIn" says the job
opens on LinkedIn and you apply there. Run against the previous commit, both fail: neither the link nor
the button exists there.

The Telegram card says "Apply on LinkedIn" and names LinkedIn, and a job added by link says "Apply on the
job's site". The feed sends the names: for a board with a name of its own ("SmartRecruiters"), for a job
added by link, and for a job written in. Run against the previous commit, all five of those tests fail:
the card says "Open job" there, and the feed has neither field.

The sweep of every page at every size (28 sizes) found nothing. A sweep that never signs in also finds
nothing, so its sign-in was checked first: it reaches the Jobs page, with the new row on all five cards.

Gates, all local:
- `lint-imports`: 2 contracts kept.
- `ruff check`: clean.
- The schema pruned onto an empty local database.
- The contract changed by the one docstring line and the two new fields, `FeedSource.label` and
  `FeedJob.apply_on`. It is regenerated and current.
- `npm run build`: 109.4 kB of gzipped script and 15.6 kB of style. That is 0.24 kB and 0.07 kB more than
  the previous commit, measured on a build of it.
- `pytest`: 1,327 passed and 3 skipped (the Postgres copy, the reference instance, an empty parameter
  set). The browser tests ran.

## 2026-11-06 — The new-jobs count says what left too; company boards found by name; a bigger corpus chart

Three things the owner asked for on the same day.

**"100 new jobs", and the list only 15 longer after a refresh.** The count was already this person's
own: their words, their filters, read only. Replayed on the laptop's corpus, it counted exactly the jobs a
refresh added, 68 of 68, and none of them repeated a role already listed. The gap was the other end: in
the same 8 hours, 24 older jobs passed their shelf life. On a list over 2,000 matches, the newest-2,000
window pushes old jobs out the same way.
- `GET /feed?found_after=` no longer narrows the list. It counts the jobs on the whole list that arrived
  after it (`arrived`), with `total` beside it. The button says both: "100 new jobs · 85 older ones gone ·
  Show them".
- **Cost:** each poll reads the whole list, as a page load does. On the laptop that is 78 ms, against
  3 ms for the new jobs alone, once a minute while the Jobs page is open and visible.

**Company boards from the names postings carry.** The ATS tier polls `ats_tokens`. Links had added one
board in three days on the laptop: a board's own postings link to that board, and LinkedIn gives no link
to apply. Meanwhile about 5,000 new companies a day arrived, nearly all on LinkedIn.
- **Measured:** of 150 of those companies, looked for by name on seven ATS, 16 had a board. Together they
  listed 1,515 jobs, 131 of them in the harvest's three-day window. There were 290 boards in all.
- **Each run now looks companies up** (`cycle._boards_by_name`), newest first, last in the run. It stops
  at its own budget (`BOARD_LOOKUP_BUDGET_SECONDS`, 60 s) or the cycle's, whichever comes first.
- **A name is not a company.** A board is kept only when it lists a job one of that company's postings
  here is (`content_key`). That dropped a land-use firm found for "Unique" and a pharmacy for "ABF". Of 17
  boards found by name alone it kept 11, holding 126 of the 131 recent jobs.
- **Asked once.** A company is asked once whether a board is found or not (`company_lookups`), and a board
  refusing the name (a 4xx other than 429) counts as an answer. Too many requests, or no answer, means it
  is asked again next run.
- **Not Workable,** which refused for hours after a burst (2026-11-02).
- **Eight companies at once, each trying its ATS in turn.** Seven ATS at sixteen at once each made 88 of
  280 requests fail to connect, and took 101 s on a 60 s budget. The deadline is checked before every
  request, so a run goes past it by one request, not by a company's fourteen.
- **On the laptop:** three 60-second runs looked up 136 companies and added 8 boards, at about 40–60
  companies a run.
- The seed script (`deploy/scripts/seed_ats.py`) now takes its fetchers and spellings from
  `sources.ats` (`FETCHERS`, `spellings`), so a name is tried the same way in both.

**Admin → Corpus chart.**
- **Taller,** growing with its width: 300 px on a phone, about 460 on a laptop.
- **"Show" and "One point is" are picked apart.** "Show" gains "All time", from the first count.
  "One point is" offers 15 minutes, 1 hour or 1 day: whichever draw the range in at most 1,000 points
  (`libs.ops.countries.steps`), so the server says which, and a month by the run is not offered.
- **A log scale** beside the even one: 0, 1, 10, 100, 1K, so a country of tens moves as visibly as one of
  thousands.
- **The countries fold** under one button, first in the row, and open over the chart rather than pushing
  it down. A tap anywhere else or Escape shuts them.

## 2026-11-06 — Smoother motion; every page checked at every size, and a tablet's bar fixed

The owner asked for much smoother animations, transitions and motion, and for breakpoints where nothing
on the site is too big, too small, squeezed or out of place.

**Motion, measured first.** The landing page was scrolled top to bottom by script, 14px a frame, with
the CPU slowed 4× (a mid-range phone), three runs each:

| | Before | After |
|---|---|---|
| Phone: frames over 34 ms | 7.8–8.4% | 0% |
| Phone: slowest 5% of frames | 50 ms | 16.8 ms |
| Phone: style recalculation | 4.9 s | 1.9 s |
| Laptop: frames over 34 ms | 96–99% | 0.8–1.2% |
| Laptop: typical frame | 50–67 ms | 16.7 ms |
| Laptop: style recalculation | 28 s | 1.25 s |

- **The cause.** Every scroll frame and every pointer move wrote a CSS variable (`--tilt`, `--grow`,
  `--turn`) on the landing's root element. That restyled every element under it, every wheel's SVG
  included: about 54 ms a frame on the laptop.
- **The halo.** Its transform is now written on the halo itself. It eases toward where the scroll and the
  pointer put it, at the same pace at any refresh rate, so a mouse wheel's jump becomes a glide.
- **The step cards.** The shade is an element of its own; where each card sticks is read once per size,
  not every frame.
- **Transforms only.** The dock's pill slides by transform, and the chart's bars grow by transform.
- **Reveals** fade and rise without the blur, which was drawn again on every frame; the hero's few words
  keep theirs.
- **Frost** (`backdrop-filter`) stays only on what is fixed: the bar and the dock. The bar's frost fades
  in.
- **The dock** tells React only when the section changes.
- **The app:** a softer spring for what is pressed (`--spring`) and a glide for what slides (`--glide`).
  Dialogs ease in over a backdrop that fades. Tabs, focus rings and switches transition rather than snap.
- **No entrance animation on the app's pages:** each draws whole the moment it opens (owner,
  2026-11-03).

**Sizes, checked by script and then by eye.**
- **What was checked.** Every page, at 23 widths from 320 to 2560px and on five short screens (844×390,
  932×430, 667×375, 1280×720 and 1024×600):
  - pages: the landing, sign in and sign up, reset and privacy, Jobs, Add a job, Applied, My search,
    My documents, Settings, Admin (from 1024px) and onboarding;
  - faults: anything past the screen's edge, text spilling out of its box, two things on top of each
    other, and type under 12px.
  The pages were then looked at side by side.
- **A tablet's bar.** A tablet held upright (721–900px) had the laptop's bar of words, which ran under its
  own help and bell buttons: by 117px at 768px and 65px at 820px. The tab bar now serves it, centred and
  never wider than a phone's. From 901 to 1099px the bar's spacing tightens so six tabs fit.
- **The hero** was sized by width alone. A phone on its side showed the halo but not the title, and a
  720px-tall laptop had the buttons below the fold. The halo, the title, the tagline and the headings now
  follow the height too. The buttons are above the fold at 1024×760, 1280×720, 1366×768 and up.
- **The step cards** stack only where a card fits the screen; under 620px tall they are a list.
- **The four "what it is" tiles** sit two to a row on a tablet.
- **The sign-in wheel** stays inside a 320px screen, and gives way on a phone on its side.
- **Headings** balance their lines, and paragraphs avoid a last word alone.
- **Admin's table headings** are 12px.
- **The end state:** no finding at any size.

Tests: on a phone, a tablet and a laptop (320–1440px), the landing page and the Jobs page put nothing past
the screen's edge and nothing in the top bar over anything else. Run against the previous commit, the test
fails at 768px: Settings over the help button.

Gates, all local:
- `lint-imports`: 2 contracts kept.
- `ruff check`: clean.
- The schema pruned onto an empty local database.
- The contract is unchanged.
- `npm run build`: 109 kB of gzipped script and 15 kB of style.
- `pytest`: 1,320 passed and 3 skipped. The browser tests ran.

## 2026-11-06 — The site in one design: graphite, white and one purple

The owner chose concept C of three static mock-ups (not committed), in one flat "galaxy" purple
instead of its indigo. The bottom menus on a phone were to be pictures that match their pages, not
words. The app keeps its buttons, words and layout. The landing page, sign in, sign up and the
emailed-link pages are new.

**The design.** `styles.css` keeps its rules and layout; its tokens and components change.
- Three colours: graphite `#0f1013`, white `#f5f6f8` and purple `#8b3dff` (white on it is 5:1). Small
  purple text on graphite takes a lighter shade of the same purple (`--accent-text`, 7.5:1). Green and
  red keep their one meaning each: words that find jobs or hide them, and red for errors.
- Sora for titles and Inter for text, served with the site (`@fontsource-variable/*`): no page asks a
  font host.
- The theme button grows the new theme out of itself as a circle (View Transitions). It switches at
  once where the browser cannot, or where the person asked for less motion.
- Job cards are solid, not frosted: a blur under each of 25 cards makes a phone stutter. Only what
  floats over the page is frosted: the bars, dialogs and the toast.
- On a phone the tab bar floats and shows pictures only. Each place keeps its name for a screen reader.
  - Settings had a sun-like icon (the theme button's sun), and My search had a price tag. They are now
    a gear and a magnifier.
  - The places and their pictures are one list (`places.tsx`), which the landing page's phone draws too.
  - On a phone the Jobs filters are one row that swipes, so the first job is above the fold. The Jobs
    tabs are one switch.

**The landing page** is rebuilt on CSS transitions and one scroll handler. framer-motion goes from the
dependencies: the old landing was its only user. The old landing's components (the wheel nav, the
background wheel, its sections, `Reveal`, its icons) are deleted. Its words were out of date:
- **The numbers were typed into the page.** It said "25 job sources" and "~940 company boards". The code
  reads 19 sources (`HARVEST_SOURCES`, LinkedIn included), and the live boards are a count in the
  database. Both now come from a public `GET /stats`, with `PLATFORM_TICK_MIN` and the jobs found per
  run in the last six hours. That last count is `libs.ops.countries.by_country`, the admin chart's own:
  the concept's chart of found jobs was made up. Without an answer the page shows no number rather than
  an old one.
- **Telegram**: "Connect Telegram in one tap" now reads "your own bot, in three steps" (decided
  2026-11-01).
- **Order**: "Newest first" now reads "the most of your words first", the default order.
- **Ranking**: "A word in the title counts more" now reads "the more of your green words, the higher"
  (2026-11-02).
- **When a job is matched**: as soon as its text is read (2026-11-06).
- **The alert example** is laid out as `services/notifier/card.py` sends one ("Matches: …").

**Fixed on the way:**
- **The phone tab bar was drawn in the wrong place.** The top bar's frost (`backdrop-filter`) made the
  bar the box that fixed things inside it are placed against. So the phone's tab bar, which lives inside
  the top bar, was drawn at the top bar's foot. The frost is now on a layer of its own.
- **Sign in and sign up could show the wrong title.** `#/signin` and `#/signup` are one component.
  Going from one to the other by the address, Back included, kept the first page's title. The component
  is now keyed by the address.
- **Small link-buttons sat on a line of their own.** "Read it all" took the small button's padding,
  which indented it.

Tests:
- `/stats` answers a signed-out visitor with the counted numbers. It fails on the previous code with a
  404.
- The landing page shows the server's count of sources.
- Sign in and sign up follow the address.
- A phone's tab bar is pictures only, each named and each opening its page (e2e).

Gates, all local:
- `lint-imports`: 2 contracts kept.
- `ruff check`: clean.
- The schema pruned onto an empty local database.
- The contract was regenerated, and regenerating again changed nothing.
- `npm run build`: 108 kB of gzipped script and 15 kB of style.
- `pytest`: 1,319 passed and 3 skipped (Postgres copy, the reference instance, an empty parameter set).
  The browser tests ran.

Left as they are, for the owner (CLAUDE.md §5):
- `/stats` counts the source list, so Adzuna and Findwork are counted even where their key is missing.
- `index.html`'s description still says "every 15 minutes". Search engines read it and cannot read
  `/stats`.
- `favicon.svg` holds the purple's hex. A file outside the page cannot read `--accent`.

## 2026-11-06 — One list of sources; a search is one section, with no board of its own

The owner: fix the two things found after Hiring Cafe's removal, checking first that they are what they
looked like.

**The sources were listed twice.**
- `source_config.DEFAULT_ENABLED` was `HARVEST_SOURCES` less the two boards that need a key and LinkedIn
  (its own pass), in the same order: checked, the same boards. Admin's per-source controls read
  `HARVEST_SOURCES`, and the run read the other list, so the two agreed only by care.
- A board needing a key was read whenever its key was set, whether or not `HARVEST_SOURCES` still named
  it: how Jooble kept running after its block, until the key map was edited too.
- `ATS_SOURCES` was a third hand-kept list, of the company boards the registry already marks
  (`needs_ats_repo`): checked, the same nine.

Now the run reads `libs.settings.HARVEST_SOURCES` itself (`build_source_configs`): every board on it
with an adapter, the keyed ones only with their key. `ATS_SOURCES` comes from the registry. The unused
`enabled` parameter goes. The same boards are read in the same order. Tests: the boards a run reads are
the boards Admin lists; a board taken off the list stops even holding its key. The second test fails on
the previous code, which still read Himalayas and Adzuna.

**A section's board did nothing.** Checked before removing:
- `platform` (all, LinkedIn, Indeed, Google) was read by the schema's one-section-per-board rule, and by
  nothing else since the query set went (entry below).
- Matching, red words, countries, ways of working, suggestions and the help chat all read every section
  as one search.
- My search has no control for a board. It showed and saved the first section only, so the words, red
  words and countries of any other section found jobs without being shown or editable. The test below
  shows it: a LinkedIn-only section's "rust" matched a Greenhouse job in Paris.

Done:
- `platform` and the `Platform` enum are removed.
- `migrate_config` merges stored sections into the one My search shows (`_one_section`): every word, red
  word and country once, every way of working any section switched on (none switched on meant every
  one, as `filters._workplaces` read it), and the widest window. That is what matching already read, so
  nobody's jobs change; what finds them is now on the page.
- The schema keeps one section and runs `migrate_config` on what a page sends, so a tab loaded before
  this still saves.
- `CONFIG_SCHEMA_VERSION` is 3.

The laptop's one account had one section, set to "all". The server's accounts are not readable from
here; any with two sections are read as one from this deploy, and stored so when next saved.

Tests: several sections read and save as one; through My search's own routes, a section the page never
showed is on it, and saving what it shows finds the same jobs. Both fail on the previous code. The
keyword cap still counts every word, however many sections a page sent.

## 2026-11-06 — Hiring Cafe removed, and with it every way of sending a word to a board

The owner, after Hiring Cafe was found unreadable (entry below): "scrap it and remove it all".

Since 2026-11-04 no board is asked for a word: each is read whole and people's words are matched on the
stored jobs. Hiring Cafe could be asked for nothing else and answered a Cloudflare challenge to every
request; Jooble was the only other board that took a word, off since its block of 2026-08-10 and barred by
the same rule; JobSpy's Indeed and Google were registered and never enabled. The machinery that asked them
served nothing that worked:

- **Removed**:
  - the Hiring Cafe, Jooble and JobSpy adapters, and the `python-jobspy` dependency;
  - the query set and its budgets, carry-over and fold (`services/harvester/query_set.py`,
    `QUERY_TAKING_SOURCES`, `QUERY_SHAPES`, `union_keywords`, `libs.harvest`);
  - the harvester's `--dry-run` and `--max-queries`, which planned and capped queries;
  - `deploy/scripts/show_query_set.py`;
  - Hiring Cafe's `harvest_from_hits` and the language filter's exemption for it;
  - `hours_old`, which only JobSpy read;
  - `JOOBLE_API_KEY`.
- **Admin's "Search terms" section is gone**: the operator's own terms (`OperatorKeyword`, the
  `/admin/keywords` routes) only fed the query set, and "What users are searching for" listed what that
  query set spent its budget on. Each person's words are still on their account page. The deploy's prune
  drops the `operator_keywords` collection (`RETIRED_COLLECTIONS`), and the account copier no longer
  copies it.
- **Admin → Settings → Per source**: "wanted" goes, and "queries" becomes **calls**. It is now set only
  for the one source metered by the call, Adzuna (2 a run, its free tier's countries). Every other row
  shows "—". Before, Adzuna's row showed a greyed "1" while its adapter made 2 calls: the panel and the
  code disagreed. `queries_per_cycle` is `None` for every other source, and a value left stored for a
  removed source is ignored.
- `run_cycle` returns its report alone. With no query set there is nothing deferred to carry over.

The "Search terms" limit stays: it caps how many green words an account may have, now described as such.

Tests: the query-set tests go with what they tested. The two that asked who is folded into the harvest
become one test of what still depends on it: a stopped account's or an unverified signup's country keeps
no job. Also tested:
- no board is handed a word, to ask for or to keep jobs by;
- the Sources list sets calls for Adzuna alone and ignores a stored value for a removed source (it
  showed 40 for every source before);
- the deploy's prune drops the retired collection and nothing else.

## 2026-11-06 — The Jobs page says how long a job stays; company boards found from apply links

The owner: show the job shelf life on the Jobs page, following Admin's setting; look into Hiring Cafe and
remove it if nothing can be read; the proxy's two dead exits are fixed; the prompt files stay.

**How long a job stays, on the Jobs page.** Under the count: "From the last 24 hours · slower boards: the
last 7 days". Both numbers come from the rule that decides what is shown (`freshness.shelf_life`, over
`showable`'s per-source floors and Admin's job shelf life), so changing the setting changes the line and
the list together. Tested by what the page shows: a LinkedIn job 23 hours old is there and one 25 hours
old is not, a Workday job 160 hours old is there and one 170 hours old is not, and with the setting at 48
hours the LinkedIn job 47 hours old is there too.

**Company boards from apply links too** (`cycle._discover_ats_tokens`). Discovery read each posting's own
link only. A board's posting often sends applicants to another company's board, and that link was never
read: on the laptop's corpus the apply links named 29 boards not polled (7 Workday, 6 Recruitee, 4 Lever,
4 Ashby, 3 Workable, 3 SmartRecruiters, 1 Greenhouse, 1 Personio), a tenth more than the 291 polled. And
the row cap (20,000 of the last 48 hours, in no set order) filled with LinkedIn postings, ~40,000 a day,
whose own link is LinkedIn's and whose guest page gives no apply link (1 of 18,965 had one). A LinkedIn
posting is now read only when it has one. The tests that stored a LinkedIn posting under a board's own
link describe one LinkedIn never sends, and use an apply link now.

**Hiring Cafe, looked into.** Every page but `robots.txt` answers a Cloudflare challenge, the sitemaps its
`robots.txt` names for crawlers included. `robots.txt` disallows `/*?searchState=*` and `/*?page=*`, the
URLs the adapter read. No API, partner programme or feed exists that a search found, only scrapers of its
internal endpoints. Not removed yet: the query machinery goes with it, and that includes Admin's "Search
terms" section (ROADMAP Phase 8 item 23), which waits on the owner.

**Working Nomads:** 33 jobs, the newest 1.1 days old, 2 in the week. Kept; checked again on 2026-11-09.

## 2026-11-06 — A job's words found as its text is stored; the Pipeline's descriptions folded

The owner: "Words found" sits at hundreds waiting; make it faster, with an index or a new structure if that
is what it takes. And fold the Pipeline's descriptions: too long.

**Words were found by a step at the end of each 15-minute run** (`terms.catch_up` → `_index`), so a text
the 24/7 describer read just after one waited for the next: 689 waiting on the server, the oldest 12
minutes. Meanwhile the Jobs page read every such job with a regex. On the laptop the same list took 84 ms
with every job's words found, 112 ms with the newest 700 read jobs not yet indexed, and 171 ms with 2,000.

- **Found in the write that stores the text** (`CorpusRepository.set_description`, `terms_of` over the
  vocabulary). A job's words are found the moment its text is read, so the Jobs page reads the index, not
  a regex. Storing a text took 9.4 ms against 7.9 (median of 300, a 200-word vocabulary, 6,000-character
  texts), against the hundreds of milliseconds it takes to fetch the page.
- A word that joins the vocabulary while a text is being written is looked for again straight after, as
  `catch_up` looks for a new word only in postings that already have their words, and this one may not have
  had them yet when it looked.
- `catch_up` keeps the vocabulary and looks for each new word in the postings already stored, as before.
  `_index` is removed: it found words for postings with none, title-only ones included, which are never
  shown. Its fill-in of city, country and text-read time was for jobs stored before those fields existed,
  and every job since has them when it is stored. Its test goes with it.
- `words_in`, `terms_of` and `norm` move to `libs/corpus/words.py`, which needs nothing, so the match, the
  words index and the repository can all import it.
- **"Words found" is no longer a step**: the Pipeline and Latency show found → text read and words found
  → alert sent. `steps.words_queue`, its `QUEUES` entry and the `described_to_indexed` limits and stage go.
- The Pipeline's explanation folds under "How to read this", and each step's description under its name;
  the limits, the too-old count and the numbers stay in view.

Tests: a job's words are found the moment its text is stored (the previous code left `terms` empty and
`terms_at` unset); a word joining while a text is stored is still found in it (fails with the second look
removed); a posting whose text is read waits at no step (the previous code had it waiting for its words).

## 2026-11-06 — LinkedIn's list asked fresh every run, and a check of what it shows against what is stored

The owner: how do we know every LinkedIn job in the picked countries, in the window, is fetched, with no
new one missed?

**LinkedIn answers a list it was asked for lately from that answer.** Measured from the laptop, direct,
Ireland, 2026-11-06 (about 150 requests in all):

| Asked | Jobs | Youngest |
|---|---|---|
| `f_TPR=r3600` (a window every tool asks) | 21, the same for most of an hour | 54 min |
| `r3601`, `r3599` | 58 | 5 min |
| `r1800` | 46 | 27 min |
| `r1801` | 12 | 1 min |
| `r1803`, then again four minutes on | the same first page both times | |
| `r1807`, never asked | a different page | 4 min |

Response headers say `no-cache`, and a parameter the search ignores (`trk`, `refresh`, `pageNum`) changes
nothing: only the search does. The run asked `int(since the last sweep + 900)`, which with runs 900
seconds apart is 1,800 give or take a few seconds, nearly always the same number. So a run was often
answered with the copy of an earlier run's list, without the jobs listed since. Each window reaches back
only to the run before, so a job missed that way was never read.

- `linkedin.unasked`: every window is rounded up to the minute and given 1–59 seconds more, a different
  number each minute. It is never a whole number of minutes, and with runs 15 minutes apart no number
  comes back for 59 runs (14 h 45 min). It reaches back under two minutes further. `sweep_window` reads
  it, and so does the check below.
- Asked fresh, the country's one list held what narrower lists show: 54 of the 55 jobs Ireland's four
  biggest cities' lists showed for the hour, and 42 of the 43 that five word searches did. The one job
  missing was a minute old, inside the next run's reach. Splitting by way of working (`f_WT`) or by
  experience (`f_E`) changes nothing on a no-word list: LinkedIn ignores both there.
- `deploy/scripts/check_linkedin_coverage.py`: a country's hour asked three ways (its list as the run
  asks it, its cities' lists, word searches as a yardstick only, never harvested), and how many of the
  jobs each way shows are stored, by the harvest's own duplicate check. Jobs listed in the last two runs
  are counted apart, and the older ones missing are named. About a request per ten jobs.

Before this, an earlier reading of the same measurement looked like a list running 20–30 minutes behind.
It was the saved copy: every number then was asked for r3600 or r7200.

Tests: 59 runs 15 minutes apart ask 59 different windows, none a whole number of minutes, each reaching
back to the run before and one more, under two minutes further (the previous code asked r1800 for 58 of
them); the check counts each way against what is stored and names a settled miss.

## 2026-11-06 — LinkedIn's posting time, read where it is most exact

The owner: no made-up times. Read the real posting time, if LinkedIn gives one.

**LinkedIn gives no exact time anywhere** (looked at 2026-11-06): its list dates a job with no time
(`datetime="2026-11-06"`) beside "39 minutes ago", and the job's guest page and its public page (233 KB)
both say only "39 minutes ago", with no JSON-LD and no `datePosted`. That caption rounds the age down:
minutes under an hour, hours under a day, then days. So each reading is at or after the truth, and the
earliest is the nearest.

**What was kept was the later reading.** The sweep stored the list's date (midnight), and the job's page,
read by the describer, replaced it with its own "N ago". Of the laptop's 16,354 LinkedIn jobs with a time,
59.7% were read under an hour old (to the minute), 40.0% between an hour and a day (to the hour: up to 59
minutes late) and 0.2% later (to the day). 85 more had their page read and still only a date.

- The sweep reads the card's "N ago" as the job is found (`linkedin._cards`, `postings.posted_from`),
  falling back to the date only when there is no caption.
- `set_posting_metadata` keeps the earliest of the stored time, the page's and first-seen
  (`repository._set_posted_at`). Before, it took the page's every time. A stored date with no time gives
  way (`freshness.DATE_ONLY`, now the one definition of "a date, not a time", which `showable` reads too).

Tests: a swept card saying "5 minutes ago" is stored five minutes before it was found, not at midnight;
a list's "39 minutes ago" is kept over the page's "1 hour ago" read 80 minutes on, and a date gives way
to the page's reading. Both fail on the previous code.

Also: `test_the_admin_charts_the_jobs_found_in_each_country` read the page's width straight after
resizing to a phone and failed one run in two (1,104 px, the desktop chart, not yet measured again by its
`ResizeObserver`). It now waits up to 5 s for the page to fit, which still fails if it never does.

## 2026-11-06 — A job reaches the Jobs page once its text is read

The owner: a job that disappears when its full text comes in should never have been shown; show jobs with
their real text, not on their title alone.

**A candidate is a job whose whole text has been read** (`libs/tenant/filters.py`, `candidate_conditions`:
`described_at` set, which `set_description` sets at `MIN_DESCRIPTION` characters). Matched on its title,
a LinkedIn job sat on the page for the minutes or hours until the describer got to it, then left once the
text showed another language, a way of working not wanted, more applicants than the cap or a salary
under the minimum. Red words were never part of that: they read the title and company only. Alerts and
the daily email already waited for the text (`described_since`); the Jobs page, the word suggestions and
the assistant did not, and now all of them ask the one rule. The old "refused twice" condition goes: a job
given up on never has `described_at`.

**"New jobs" counts from when the text was read, not from when the job was found.** A job reaches the
list when its text is read, so a LinkedIn job found before the list was read and read after it was never
counted as new, and appeared unannounced on the next load. `found_after` now keeps the jobs read since
(`their_jobs(described_since=…)`, the alerts' own question), "Just arrived first" sorts by it, and the
card's new mark reads it (`FeedJob.described_at`, in the contract). `matching_jobs(since=…)`, by time
found, had no other caller and is removed.

Measured on the laptop's corpus (2,078 jobs young enough to show): 503 had no text yet, all stored by
the boards' last run, which reads 200 texts a run (`describe_per_cycle`), so they wait for the next run.
The one account's list went from 233 jobs to 187, and listing it took the same time: 267–276 ms
median over four runs of 15, against 277 ms before.

Tests: a job waiting, one with a teaser and one given up on are nobody's candidate, and the one read is;
a job is on the page only after its text is read; one found before a list was read and read after it is
counted as new; "Just arrived first" puts the job read last on top. All four fail on the previous code.
The Pipeline's "Text read" step says a job is on Jobs pages only from there.

## 2026-11-05 (night) — The admin's numbers, made true to how the platform now works

The owner: make the admin's numbers right, and tested for accuracy; drop jobs too old to have their text
read rather than leave them waiting anywhere; measure the database against its 98 GB disk, not 512 MB;
make sure every source is read, stored, given its text and shown; one country chart, every line tickable,
an hour to a month; Latency to follow the pipeline, with limits for each step.

**One rule for what can still be shown** (`libs/corpus/freshness.py`, `showable`). The Jobs page judged a
date-only posting time (LinkedIn's list gives a date) by when the job was found; the text queue read it as
midnight. So every LinkedIn job still without its text left the queue at midnight UTC, one listed at 23:50
included, while the page went on showing it without text. The page, the text queue (and so the describer
and every count of it), the boards' own text fetching (which had no age limit at all), the words search
and the admin all use the one rule now. Latency's backlog had a rule of its own, every job ever stored
without text, so it grew for ever beside Pipeline's count; it reads the describer's queue now.

**The text queue from an index.** Without its whole text is without `described_at`, which
`set_description` sets the moment a job has 200 characters of it. On every one of the laptop's 24,452
jobs, the same 2,482 as measuring every description's length, read from `text_queue` instead: Pipeline's
live frame, once a second, 500 ms → 64 ms.

**The database against its disk** (Admin → Health). MongoDB reports the disk it is on (`dbStats`
`fsUsedSize`, `fsTotalSize`), so the 98 GB is read, not set: the setting `database_budget_mb` (Atlas's 512
MB) is gone. The alert pages past 80% of the disk, before server_db's own at 85%.

**Pipeline and Latency, the same steps** (`libs/ops/steps.py`): found and stored → text read → words found
(new: texts come between runs now, and their words at the next run's end) → alert sent. One table of
limits, from the 15-minute run: fine under / slow to / too long past —

| Step | Fine under | Too long past |
|---|---|---|
| Listed → stored | 5 min | 15 min |
| Stored → text read | 15 min | 60 min |
| Text read → words found | 30 min | 60 min |
| Found → alert sent | 45 min | 90 min |

Pipeline colours a step by the job waiting longest, Latency by the slowest tenth; what waits is one
function both read. New: how many jobs aged out without their text in the day (dropped, not waiting); how
many accounts have Telegram or browser alerts on, beside "alerts sent"; and each source's day — found, with
text, waiting for text, newest — every source listed, a silent one as "nothing".

**The country chart**: one chart, "All countries" a line like any country's, each ticked on or off in the
table; 1, 2, 4, 8, 12 hours, 1, 2, 7, 14, 30 days. Counted per run (15 minutes) from now, as jobs arrive;
the hourly counts before stay valid.

**Every source, checked** (`deploy/scripts/check_sources.py`, one real cycle on the laptop, direct, at
22:47): ashby, greenhouse, himalayas, jobicy, justjoinit, lever, personio, profession, recruitee,
smartrecruiters, themuse and workingnomads listed and stored jobs; most of the rest of what they listed is
in no country anybody picked, not kept by design. No Fluff Jobs listed none in its 2-hour window, and 39
in 6 hours, 475 in 48: the board was quiet, not broken. breezy, workable and workday had no board due or
changed in that run. Hiring Cafe answers 403, as known. The check now says how many listed jobs were in no
picked country, and its `--linkedin` option, broken by the pass's new readers, works again.

Tests, each against behaviour with known inputs: the four places agree on a LinkedIn job dated yesterday
and found two hours ago (kept) and found 25 hours ago (dropped); each step counts exactly what went through
it; a job aged out without text counted once; a step turns slow, then too long, by its oldest job; the wait
for words begins when the text came; both tabs read the same backlogs; every source listed, a silent one as
zero; the words step timed from text to words; the disk alert and its numbers; each chart range's buckets;
a job counted in its run; runs added into hours and days at a fixed moment.

## 2026-11-05 — LinkedIn's job texts read all the time, and a refused proxy connection asked again

The owner: the jobs waiting for their text reached 13,797; fetch them all the time, not only inside the
15-minute run, and use the threads the server has.

**What the log showed (the server's `logs/harvester.log`, 18:56–20:28).** Each run read **two to four
texts**, then `describe: stopping, the proxy refused: 503 Service Unavailable`. 36 requests through the
site proxy to an address echo, one after another: it rotates over six addresses, and **the 4th and 5th
answered 503 to all twelve of theirs**. The other four worked. Two faults in our handling turned one
dead address into almost nothing read:

- **A text fetch took any proxy refusal for the proxy being unusable** (`postings.ProxyUnusable`) and
  stopped every text for the run. A 407 is the proxy refusing us at every address (its password, its
  traffic), and still stops at once. Anything else is the one address that connection left from: the
  next try leaves from another. Every try refused is the proxy itself (`postings._proxy_refused`).
- **A list page the proxy could not open was taken as an empty page.** After a full page it looked like
  the end, so the country "ended on empty pages" and was read again from hours back next run (twelve of
  29 at 19:13). On a country's first page it was worse: the country ended with no jobs and counted as
  read. The page is now asked again, and the run's line says how many connections were refused.

**LinkedIn's texts are read by a service of their own, all the time** (`services/harvester/describer.py`,
the `describer` container, always on). The 15-minute run only lists now; before, it read texts in what was
left of its 11 minutes, after the lists. The describer reads the newest waiting jobs, 120 a round, as many
at once as the proxy has addresses (`proxy_exits`). Between rounds it looks again for newer jobs and waits
while a run is listing (the `linkedin` lease), so the two never spend LinkedIn's allowance at once. After
a round that read nothing it rests 30 seconds, and it logs one line every 15 minutes: read, tried,
refusals, and how many still wait.

**Not measured on the server yet.** With four working addresses at ~40 pages a minute each, ~160 texts a
minute is the ceiling, ~9,600 an hour; on six, ~14,000. The busiest hour measured for the EU and the UK
listed ~6,000 new jobs (2026-11-04).

Tests (each fails on the previous commit): one address refusing every connection stops neither the
lists (0 of 25 jobs stored before) nor the texts (1 of 25 read before), and is counted in the line; every
try refused is the proxy itself; the run reads no text, and the describer reads every stored job's; it
waits while a run lists, goes straight on while there is text, rests when there is none, and a round
that fails is said and the next one runs.

## 2026-11-05 — LinkedIn reads a page from each proxy address at once; jobs by country, as lines in time

The owner, the evening after deploying: LinkedIn's jobs waiting for their text grew run after run (8,425,
then 10,973 two minutes later); and the chart of jobs by country "looks terrible", where it should be a
time series, a colour a country, over a chosen time.

**The waiting count.** A pass sweeps every picked country first and stores what it lists, which is the
jump, then reads job texts, newest first, for the rest of its 11 minutes. It read four pages at once
(`READERS = 4`), set when the proxy had four addresses. Each new connection leaves from the proxy's next
address and LinkedIn's limit is a rate per address (~40 pages a minute), so on six addresses four
readers leave a third of what the proxy allows unused.

- **`proxy_exits`, a platform setting (Admin → Settings → Proxy addresses, 6 by default):** how many
  addresses the proxy sends from, and how many LinkedIn pages a pass reads at once, sweeping and reading
  texts alike. The constant is gone.
- **Not measured on the server yet.** Expected: up to half as many pages again per pass, if four readers
  were what held it back; nothing, if LinkedIn's refusals were. The harvester log says which, before and
  after: `linkedin: swept … in N request(s)`, `describe: … attempted, … described, … throttled`, and
  `describe: stopping at …, every try refused`.
- Jobs older than `job_max_age_hours` (24) are not read and leave the count. A backlog left at the day's
  end never shrinks back: those jobs stay without their text, and are matched on their title alone.
- "Descriptions fetched per cycle" said it decides how fast every backlog clears. It is the other
  boards' only; its hint says so now.

**The chart** (`web/src/pages/AdminCountries.tsx`, replacing the rows of columns in `AdminPage.tsx`):

- Two line charts on one time axis: every country together, then the countries ticked in the table under
  it, a colour each (the busiest five at first, six at most). A country keeps its colour while it is
  drawn, whatever is ticked or unticked beside it. The colours are the data-viz reference palette, checked
  against this site's two backgrounds; in light mode four are under 3:1, so lines carry their names and
  the numbers are in a table.
- 2, 7, 30 or 90 days: by the hour up to a week (it was two days), by the day beyond. It starts at the
  first hour anything was counted, not with zeros before it (`CountryChart.since`).
- The hour still being counted ends each line as a faint open dot, so its drop does not read as one.
- A crosshair and every drawn series' number under the pointer, or the arrow keys; the table gives each
  country's jobs, busiest hour and share, and "The numbers" every hour's.

Tests (each fails on the previous commit): a pass reads as many LinkedIn pages at once as the setting
says, 2 or 5; the chart is hourly to a week and starts at the first count; in a browser, the busiest five
are drawn, a country keeps its colour when another is unticked, the keys read each hour's numbers, and a
phone's width needs no sideways scroll.

## 2026-11-05 — The admin page and the health alerts work on server_db

The owner, after deploying: Admin read "Could not read the platform state", every time.

**Why.** The health read (`libs.ops.health.platform_health`) asked MongoDB for `serverStatus`, for one
number: connections open to the whole server. That command reads the server, not a database, and
server_db's app logins are `readWrite` and `dbAdmin` on their own database only (its `bin/sdb add-app`,
by design). MongoDB refused it, and the read failed as a whole. Atlas allowed it, and so does the
laptop's mongod, which has no logins, so neither the tests nor the laptop could see it.

**The same read feeds the health timer** (`deploy/scripts/check_health.py`), so no alert email could be
sent from the move to server_db until this deploy.

- **The number is gone.** Nothing used it but one line of the health timer's log, and the admin page
  never showed it. The server's own connections are server_db's business (`bin/sdb status`, Uptime Kuma).
- **A test asks MongoDB what the two roles allow** (`rolesInfo`), and fails if the admin's health read
  sends any command outside them. It fails on the previous commit: `['serverStatus']`.

## 2026-11-05 — Full size, measured on the server; `harvest-everything` merged

The owner ran server_db's step 13 (`~/projects/server_db/ROADMAP.md`) on the real servers: the probe in
the harvester's image on the apps' LXC, the throwaway database on the `db` LXC (two cores, MongoDB
8.0.34, zstd). The same 1.5 million jobs over 30 days and 300 people as the laptop's measurement
(2026-11-04, night), copied from the newest 25,000 of Mahoraga's own jobs.

| | Laptop, 3.5 GB cache | Server | Step 13's line |
|---|---|---|---|
| Jobs page, median: first look / again | ~450 ms | 539 / 461 ms | 0.5 s |
| 90% of people under | ~1.1 s | 1.37 / 1.18 s | |
| The slowest person | ~2 s | 2.30 / 1.71 s | 2 s |
| A new word, searched for in every job that can be shown | 70–97 s | 90 s ("agile", 37,745 jobs), 58 s ("blockchain", 1,226) | 5 minutes |
| A person's page while their new word waits | 1.6–1.8 s | 2.8–3.5 s | |
| A run's 1,800 new jobs: stored, texts, words | 12 s, 45 s, 13 s | 3.6 s, 16.6 s, 2.3 s | stored in 30 s |
| Data, on disk (zstd), indexes | 7.1, 2.5, 0.45 GB | 6.73, 2.47, 0.73 GB | |

- **A second look is inside both lines; the first look is over both**, by 39 ms at the median and 0.3 s
  for the slowest person. The build writes 30 days in random order, so the 8 days a match reads were
  spread through the whole collection and partly on disk. In use, those 8 days are what the harvester
  wrote last. The first look is also what a page costs after anything pushes the jobs out of the cache
  (a restart, a backup reading every job). Taken as a pass, and recorded as over: the 15-day keep holds
  half of what was measured.
- **Writing is 3–6× faster than on the laptop.** A run's new jobs cost seconds.
- **"agile" ran to the 90 s budget** (`TERMS_BUDGET_SECONDS`), so its oldest jobs may wait for the next
  run, as on the laptop. "blockchain" finished in one.
- **Slower here: the page while a new word waits**, 2.8–3.5 s against 1.6–1.8 s. That search reads the
  newest jobs' text on the database's two cores, until the word has been searched for (one or two
  harvest runs). It is not one of step 13's lines, but it is over the site's "a click answers within a
  second". ROADMAP item 34, which makes what a match reads smaller, is where it goes.

So `harvest-everything` is merged into `main` (fast-forward, 15c910a…6c69c0f). Nothing in it changes
how a match reads (`libs/matching.py`, `libs/tenant/filters.py` and `libs/corpus/terms.py` are as
measured).

## 2026-11-04 (later) — Boards keep only jobs in a picked country; jobs found by country, charted; archives in xz

The owner: only countries somebody picked are read and kept, LinkedIn and the other boards alike; a
chart in Admin of the jobs found in each country through time; archives compressed to save space.

- **A board keeps only the jobs in a country somebody picked** (`cycle._ingest`, with `geo.allows`, the
  rule the jobs page applies). One whose country cannot be told, "Remote" or "Worldwide", is kept, as
  the jobs page keeps it. LinkedIn already read only the picked countries. The cycle's line and audit
  say how many were left out (`elsewhere`).
- **Every new job is counted by its hour and its country** (`country_counts`, written by
  `CorpusRepository.add`, the one place a job comes in). The counts are kept after the jobs are archived,
  so the chart reaches back further than the corpus.
- **Admin → Corpus: "Jobs found by country"** (`GET /admin/countries`). By the hour over 2 days, by
  the day over 30 or 90. All countries together, then each country on one shared scale, the busiest
  first and the rest folded. With it, the number of LinkedIn jobs still waiting for their text
  (`count_awaiting_description`, the describer's own question): if it grows run after run, the pass
  cannot keep up.
- **The archive is xz, not gzip.** Measured on 1,000 LinkedIn jobs (5.1 MB as JSON): gzip 1.45 MB in
  0.6 s, xz 0.95 MB in 5 s, once a tick. The archive is kept forever, at ~70,000 jobs a day once every
  country is read: ~14 GB a year less. Files written before still read.
- The ROADMAP has the order to put all of this live, and what more the same four proxy exits could
  give (items 27–33).

Tests (each fails on the previous commit): a board's job in a country nobody picked is not stored and
a "Remote" one is; the jobs found in each country are charted by the hour and the day, and kept after
the jobs are archived; the waiting count; the archive is xz and an old gzip one still reads; and in the
browser, the chart's countries, its waiting count and its two ranges.

## 2026-11-04 — Every source read whole on every run; LinkedIn by country, every job described

The owner: no word goes to a source and no title is filtered. Every source is read for everything new,
at most every 30 minutes, every job stored, and people's words matched on the stored jobs, title and
text. LinkedIn is read country by country and every job's description fetched. Each run must finish
inside its 15-minute tick. **Not to be deployed before the database moves to `~/projects/server_db`**
(ROADMAP Phase 8, item 22): Atlas's 512 MB would fill in a day or two.

**Measured** (live, 2026-11-04; LinkedIn through the site proxy, with the owner's go-ahead):

| | |
|---|---|
| LinkedIn jobs per 15 minutes, no word, 09:50 | EU 659, Spain 111, France 110, Germany 104, UK 87, Italy 78, Netherlands 59, Belgium 24, Sweden 19, Ireland 18, Austria 18, Poland 9, Portugal 9, Hungary 7 |
| The same at ~11:00, 25-minute windows | UK 643, Germany 358, France 332, Netherlands 260, Spain 245, Italy 179: ~1,500 per 15 minutes for the EU and the UK |
| The proxy, one job page per request | 30, 45, 60 and 90 a minute for 41 minutes: 2,040 of 2,040 answered. 150 a minute: 744 of 747. 240: ~180 a minute got through. 360: 8 of 360 by the second minute. Answered again a minute after stopping |
| A pass, EU and UK, 11:06 | 28 countries, 501 list pages, 4,064 jobs listed, 1,801 new, 1,427 described (492 tries refused and asked again), 19 MB, in its 11 minutes |
| The boards beside it | 152 s and 169 s a run (Lever 29 s, The Muse 36 s, the rest under 7 s) |
| No Fluff Jobs | Listed newest first by when each posting went up or was renewed: everything renewed in the last hour on the first 4 of 130 pages; the whole list in 36 s |
| The Muse | Answers no page past the 100th ("Value `page` is too high"), whatever it says it has: 2,000 of its 6,174 remote jobs |

**Changed:**
- **LinkedIn** (`services/harvester/linkedin.py`, rewritten). Each run sweeps every country any active
  person picked, with no word, newest first, four countries at once. A region is read as its countries:
  the EU's list stops at 1,000 jobs, and its 30 minutes held ~1,300. Each country reaches back to its
  last sweep read to the end, plus one run (`swept_places`), and the operator's 4 hours the first time.
  Every job is stored, then every job without its text is described, newest first, until the pass's
  budget is spent; what is left is first next run. A lease stops two passes reading at once.
- **The boards and LinkedIn run side by side** (`services/harvester/__main__.py`). One after the other, a
  long LinkedIn pass pushed the boards and the words past the unit's 14 minutes. Each has the same
  budget (`HARVEST_CYCLE_BUDGET_SECONDS`, 11 minutes); the words are found after both.
- **No board filters by word.** Just Join IT, Himalayas and No Fluff Jobs keep every job in their window.
  Adzuna asks each country's newest jobs with no word, two countries a run (its free tier). Findwork
  reads its newest list instead of "python", "backend" and "golang" (no key here, so unmeasured).
- **No Fluff Jobs is read newest first**, until a page has nothing renewed inside its 2-hour window, at
  most 10 pages: about 4 a run, against 33 for an hourly slice of its whole list.
- **The Muse reads the 100 pages it serves, every run**: 400 requests an hour of its 500. Its rotation
  asked pages it does not serve and read nothing in two runs of three (found by this measurement).
- **Every company board is polled.** A run polled the first `max_companies` boards of each ATS, every
  run, and never the rest; now the next ones each run (`sources.base.turn`, on the platform clock).

**Deleted:**
- LinkedIn's word searches: the pooled query set, `search_window`, jobspy's LinkedIn search and its
  connection patch, the per-user `jobspy_linkedin` source and `PER_USER` tier.
- Each account's own LinkedIn schedule: the `linkedin_interval_min` limit, `next_linkedin_run` and its
  jitter, and `users.linkedin_next_run_at` with its index. A field a model drops can be named in
  `RETIRED`: stored documents still load, and the deploy's prune removes it.
- `KEYWORD_FILTER_SOURCES`, `PREFILTER_SOURCES`, `ats.JobFilter`'s word and place filters, and
  `_keyword_hit`. The LinkedIn query and result ceilings in settings had no reader left.
- The admin panel no longer offers LinkedIn a query ceiling.

Kept: Hiring Cafe, the one board still asked for words, because it can be asked nothing else; it
answers 403 to everything (item 23, for the owner).

Tests (each fails on the previous commit, or guards a relationship): every job a country lists stored
and described whoever has a word for it; the same cost for 1 person or 5 with 125 words; a region read
as its countries, each once; only active people's countries; each country's window back to its last
complete sweep and one run, further after a refused one; two passes never at once; a stored account
with the retired field loads and the prune removes it; LinkedIn beside the boards with the words after
both; No Fluff Jobs stops at the first page with nothing new; The Muse never asked a page it does not
serve; every company board polled over a round; Adzuna asked no word. Removed with what they tested:
the pooled-search, per-user schedule and jitter tests.
## 2026-11-05 — Jobs are kept for 15 days, and moved to the archive fast enough to keep up

The owner: retire every job older than 15 days (21 at most) to the compressed archive, daily, reliably;
keep each person's applied jobs on their Applied page; fewer jobs to search through.

**What the measurement said first.** No job older than eight days is anybody's match since 2026-11-04
(`filters.candidate_since`), so a match never reads the older ones, and the keep does not change search
speed. It changes the disk (2.5 GB for a month of the EU and the UK, half for 15 days), the indexes and
the backups. What did need fixing was the move itself, measured at 400,000 jobs over 30 days with
15 days kept:

| | Before | After |
|---|---|---|
| Finding the jobs to move | read all 397,000 and sorted them, 2 s, every run | read 5,139 for 5,000, 39 ms |
| Jobs moved a run | 1,000 (3.4 s) | 5,000 (8.3 s) |
| Most a day, a run every 15 minutes | 96,000 | 480,000 |
| A keep shortened from 30 days to 15 | ~197,000 due: about two days to clear | about 10 hours |

- **Kept for 15 days by default** (`DEFAULT_CORPUS_RETENTION_DAYS`), set in Admin → Settings, which now
  offers 7, 10, 15, 21, 30, 60 and 90 days, and keeps showing a value chosen before.
- **The oldest first, by an index** (`first_seen`), **5,000 a run** (`ARCHIVE_BATCH`). Written to the
  archive file first and deleted only once it is written, as before.
- **A job somebody applied to, or wrote a cover letter for, stays whole** for their Applied page, as
  before: the job's text is there for an interview weeks later. It never comes back into anybody's jobs
  list, which reads only the last eight days.
- **An alert when it falls behind** (`archive_overdue`, Admin → Health and the health timer): jobs a
  day past their keep and still in the database. A working pass leaves none; counting them took 0.8 s
  with 166,000 of them.
- Admin → Settings' note said a job somebody hid stays; it has aged out with the rest since 2026-11-02.

Tests (each fails on the previous commit): by default a job leaves after 15 days and one somebody applied
to stays; a pass reads only the jobs it moves; the keep is the setting, 21 keeping a 16-day-old job;
jobs left a day past their keep warn, and an applied-to one does not count.

## 2026-11-04 (night) — Matching at full size: a month of jobs for the EU and the UK

The owner: measure the Jobs page and a new word at the size the platform will hold, before deploying it.
`scripts/scale_probe.py` builds it in a throwaway database (`scale_probe`, never Atlas): our 24,452
real jobs copied to 1.5 million over 30 days, weighted by country as LinkedIn listed them, and 300
people searching for 3–12 words each in one to three countries. Run on a MongoDB like server_db's VM:
8.0.34, zstd, two CPU cores. 7.1 GB of data took 2.5 GB on disk and 0.45 GB of indexes.

**What it found, and what changed:**

| | Before | After |
|---|---|---|
| The busiest person's match | read 586,000 jobs, 199 s | read ~2,000–8,000, under 2 s |
| Everybody's Jobs page (300 people) | minutes for many | median 433–455 ms, 90% under 1.1 s, slowest ~2 s (3.5 GB cache) |
| A new word, searched for in the stored jobs | 612 s ("agile"), 210 s ("blockchain"), every run, never finishing inside the run's 14 minutes | 70–97 s, in one or two runs |
| The page while a new word waits | 90 s, and ~1,500 jobs cut to 458 | 1.6–1.8 s, every job kept |
| One run's 1,800 new jobs | stored 12 s, texts 45 s, words 13 s | (unchanged) |

- **A match asks separate questions, not one with an "or"** (`libs.matching`): jobs whose words are
  indexed, through the words index; jobs not indexed yet, by their text; and a word not searched for yet,
  in the newest jobs' text, that word only. Asked as one, the database read every job with a person's
  words and sorted them in memory.
- **An age bound the index can use** (`filters.candidate_since`): no job found before the widest window
  any board has, plus a day, is a candidate. The per-board rule stays as it was, after it.
- **Indexes that keep the match's order** (`libs/db/models/corpus.py`): words, country, newest first
  (`open_terms_newest`); not-yet-indexed, newest first (`terms_at_newest`); and newest first with the id
  (`open_newest_first`), without which a word not yet searched for sorted 400,000 jobs (79 s). The
  deploy's schema step builds them: 1.6 minutes at 1.5 million.
- **A country is one `$in`**, which the words index reads country by country: a person in Czechia read
  every job with their words in the EU before.
- **A new word is searched for a stretch at a time** (`libs.corpus.terms`): only jobs that can still be
  shown, newest first, 6 hours of them at a time, keeping where it got to (`vocabulary.searched_to`), at
  least one stretch a run and at most `TERMS_BUDGET_SECONDS` (90). It joins the vocabulary at once, so new
  jobs are indexed with it; it counts as indexed (`indexed_at`) once it has reached the oldest that can be
  shown.

**Not fixed, for the owner:** the Jobs page's own target is under 300 ms. At this size the median is
~450 ms on two cores with a 3.5 GB cache (~550 ms with 1.5 GB). Every match reads whole jobs, ~4.7 KB
each and mostly description, where it needs a few fields. Keeping the descriptions in a collection of
their own would make what a match reads ~8× smaller: measured next, before anything is built (ROADMAP).

Tests (each fails on the previous commit): a match never reads jobs older than the bound; a new word is
searched for a stretch a run, its jobs found meanwhile, and done; it is not searched for in jobs too old
to show; adding a word keeps every job the other words found; the word search stops in time for the run
to end inside its unit.

## 2026-11-04 — An account's page says what they do with the site; "last online" is when they were

The owner: per-account stats on the admin page, mainly to work out what to improve; last online as a
date and a time; and the red "Setup … missing" line gone.

- **"Last online" was the last sign-in.** `last_active_at` was written only by signing in, and a
  session lasts weeks, so an account used every day read as last active a month ago. Opening the site
  (`/dashboard`) now writes it, once a minute at most. It is the server's record, not a cookie: a
  cookie lives in the person's browser, where the admin cannot read it.
- **The account page** (`/admin/users/{id}`, `AdminUserPage.tsx`) shows:
  - jobs waiting on their Jobs page, and how many came in since they were last here;
  - jobs applied to and skipped, CVs and letters made, and jobs sent by alerts;
  - when they signed up and finished setting up, and last online to the minute, with how long ago;
  - their green and red words, countries, ways of working and languages;
  - CVs (and how many in use) and cover letters uploaded;
  - how they hear of new jobs.
- **"Their jobs" is one function** (`libs.matching.their_jobs`: their matches, less what they skipped or
  applied to), read by the Jobs page and this page alike.
- **Deleted: the setup files list, its "Incomplete, 10 file(s) missing" line and the switch under it.**
  The files were the setup agents', which are gone; the switch (`profile_files_visible`) was read by
  nothing once the file tiles left the site. Its field is retired (`Document.RETIRED`: a stored account
  still loads, and the deploy's prune removes it), with `ProfileFiles.listing`, `missing_setup` and the
  `/admin/users/{id}/files` route.
- **A pressed button's label vanished under the pointer** (a country on My search, a range in Admin):
  the hover painted the label in the page's ink on an ink background. Fixed in `styles.css`.

Tests (each fails on the previous commit): the account page's numbers and lists, with skipped and
applied jobs gone from the waiting count; opening the site moves "last online"; in the browser, a
pressed button's label is readable under the pointer, and the account page shows "just now" and no
setup section.

## 2026-11-04 — A search always has three green words, a country, a way of working and a language

The owner: onboarding does not finish until each of these is set, a check always runs, and no part can
be left empty. Three green words is the least; more is recommended.

- **One rule, the schema's** (`libs/contracts/config.py`). A search is refused, with what to add, when it
  has fewer than `MIN_GREEN_WORDS` (3) different green words, no country, no way of working or no
  language. Every save goes through it: the form, and the help chat's word buttons.
- **The page says what is missing before Save does** (`WordsPage.tsx`): "To go on: add 1 more green
  word, pick a country." The number comes from the server (`choices.min_words`), so the page holds no
  copy. The last country, language or way of working switched on cannot be switched off.
- **Onboarding's search page is done only when the schema would accept the search** (`libs/onboarding`).
- **A finished account whose search predates this is sent back to that page** (`/dashboard`), because
  jobs are about to be fetched country by country and a search with no country would get none. Their
  CV and the rest stay as they were.
- The search still starts with two green words read off the CV, one fewer than it takes, so the first
  thing a person does is add one of their own.

Tests (each fails on the previous commit): the four refusals and what each says; a word twice counts
once; a stored search missing a part reads as incomplete and reopens its page for a finished account;
the page is served the minimum; and in the browser, two words keep "Looks good" greyed with the reason
shown, and the last country and language cannot be switched off.

## 2026-11-03 — Searching by place and time: LinkedIn swept, Himalayas, No Fluff Jobs and The Muse read as lists

The rest of the plan (owner): each search-type source measured, and the idea applied where the numbers
allow it. A source read as a list costs the same however many words people have; a search per word
costs more with every new word.

**Measured** (live, 2026-11-03; LinkedIn through the site proxy, ~900 requests, none refused):

| Source | With no word | Verdict |
|---|---|---|
| LinkedIn | One hour, newest first: Ireland 9 jobs (2 requests), the Netherlands 35 (5), Germany ~300 (32), the UK ~410 (44), the EU 937+ (100, at the 1,000 a list stops). A 15-minute run is a quarter of that | **Yes, for titles** |
| Himalayas | Newest first, 20 a page whatever `limit` says, ~2,000 a day: 5 pages are its last hour | **Yes** |
| No Fluff Jobs | 18,221 jobs over 131 pages, ~620 new a day spread over all of them, in no date order. The page number is read from the address, not the body | **Yes, read in slices** |
| The Muse | 6,159 remote jobs over 308 pages, in no date order, ~38 new a day | **Yes, read in slices** |
| Adzuna, Findwork | No key on this computer | Not measured |
| Hiring Cafe | 403 | No |

**What LinkedIn showed, and what it changed:**
- **Sweep vs word searches.** A sweep held 100% of the UK word searches for "software engineer",
  "nurse" and "accountant". The misses elsewhere had two causes:
  - **The window's edge:** every UK job missed was listed exactly an hour before, and a wider window held
    all 8. So a sweep reaches back to the previous pass and one run more.
  - **A refusal that looks like the end:** a Germany sweep stopped at 20 of ~300 jobs, on an empty page
    after a full one. So such a page is asked again, from the next address, before it counts as the end.
- **LinkedIn's word search is fuzzy.** "accountant" found "Sachbearbeitung Finanzen". The site shows
  only jobs with the word, so those were stored and shown to no one.
- **A sweep sees titles only, and half the matches are in descriptions.** On the 6,406 LinkedIn jobs here
  with a description, 46–54% of matches were description only, and 88% for skills. So the sweep
  finds title matches for every word, and the word searches stay for the rest (ROADMAP Phase 8, item 21).

**Changed:**
- **LinkedIn sweep** (`services/harvester/linkedin.py`). Each pass sweeps every country someone due wants:
  no word, newest first, back to the previous pass and one run more, an empty page after a full one
  asked again three times. It stores the jobs whose title has anybody's word, at the address the word
  searches use, so one job found both ways is one job. Live: Germany's last 30 minutes, 284 jobs in 29
  requests.
- **Himalayas is read as its newest list.** Pages are `source_pages_per_cycle`, 5 by default (5
  requests a run), instead of a search per word with 20 jobs each.
- **No Fluff Jobs is read as its list**, a slice a run, 33 pages by default: the whole list every hour.
  The words are matched on the skills each entry requires, as its search matched them (all 220 of a
  "python" search's jobs name python there). Live: 33 requests, 388 jobs kept for five words. Its list of
  languages (English and Polish only) is gone; each person's languages judge.
- **The Muse reads the next slice of its list each run**, 26 pages by default: every page every three
  hours, 104 requests an hour of the 500 it allows. Before, it read the same two pages every run.
- **The slice comes from the platform's own clock** (`PLATFORM_TICK_MIN`, `_rotation`). No state is
  stored and no second clock is added.
- **A filtered feed keeps whole words, as the site matches** (`_keyword_hit` uses `libs.matching`).
  Matching parts of words kept 53 of Himalayas' newest 100 for five words ("rest" in "interest"); whole
  words keep 10.
- **Settings:** Himalayas and No Fluff Jobs leave `QUERY_TAKING_SOURCES`, the per-source query and
  result settings, and `QUERY_SHAPES`. They join `KEYWORD_FILTER_SOURCES` until the database has room
  (ROADMAP item 22). `_tokens` had no reader left and is deleted.
- **A lease let go is free in the same millisecond** (`libs.db.lease`, `$lte`). Dates are kept to the
  millisecond, and a run starting in the one the last let go found it still held. A test file failed one
  run in three on this; a test with the clock pinned fails every time without the fix.

**Tests:**
- The sweep: stores title matches with no word search; the same requests for 1 word or 25; asks an empty
  page again; one job found both ways; the window; no sweep for no place; the 1,000 warning; a failing
  sweep reported.
- Himalayas and No Fluff Jobs: the same requests however many words; required skills matched.
- The Muse: a round of runs reads all 308 pages.
- Whole words only; the lease.

15 of 17 fail on the previous commit. The two that pass on both are guards: nobody is swept before they
are due, and one job found two ways is one job.

Gates: lint-imports 2 kept; ruff clean; schema ok; contract current; build ✓; **1,332 passed, 3 skipped**.

## 2026-11-03 — Feeds keep every job; Profession in one request; The Muse in every category

The first step of searching by place and time rather than by word (owner). The words are matched
here, so a feed that keeps everything costs the same however many words people have, and a word added
tomorrow finds today's jobs.

**Measured first** (each feed read with no word filter, 2026-11-03):

| Feed | New jobs a day | Kept before | Storage kept whole, 30 days |
|---|---|---|---|
| Jobicy | ~380 (100 newest reach back ~6 h) | about half | ~38 MB |
| The Muse | ~38 remote, every category | software and data only | small |
| Profession | ~110 (15 newest reach back ~3 h) | 15 per word, one request per word | ~13 MB |
| Working Nomads | 0: its newest job is 5 days old | — | — |
| Just Join IT | ~2,650 distinct | about a third | **~330 MB** |

Stored jobs measure 1.3–8.5 KB each (Just Join IT 4.2 KB), against the 512 MB the Atlas plan holds.

**Changed:**
- Jobicy, The Muse, Working Nomads and Profession keep every job (`KEYWORD_FILTER_SOURCES` holds
  Just Join IT alone).
- Jobicy no longer drops jobs outside the regions the reference instance wanted (Europe, the UK,
  Hungary): each person's countries judge where a job may be done.
- The Muse asks every job category. Its page count is now read from `source_pages_per_cycle` (2 by
  default), the key the panel writes; before, it read a key nothing wrote.
- Profession is read in one request a run, with no word, instead of one request per word.

**Not changed, with the reason:**
- **Just Join IT stays filtered** until the database has room. That is the self-hosted MongoDB (the
  owner's `server_db`).
- **The Muse's pages are in no date order.** Its 6,159 remote jobs are spread over 308 pages, new ones
  anywhere among them, and 2 pages a run finds a few of its ~38 a day. Reading a different slice each
  run, so that every page is read once a day, needs a little saved state. It is proposed, not built.

Tests: each of the four feeds keeps a job nobody has a word for; Profession is one request with three
people's words; The Muse reads the pages set, in every category. Five of these six fail on the
previous commit; the sixth (Profession keeping the job) passes on both, because the fake feed ignores
the word, and the one-request test covers it.

Gates: lint-imports 2 kept; ruff clean; schema ok; contract current; build ✓; **1,315 passed, 3 skipped**.

## 2026-11-03 — Names, links and words read from any CV or cover letter; onboarding says it is setup

**People uploaded CVs and onboarding filled in nothing** (owner). Measured on nine sample files built
the way people's tools build them (`tests/fixtures/cvs/make.py`): **3 of 9** gave the right name,
email, phone and links before this change; **9 of 9** now.
- **Word** (`extract.py`): the page header and footer, text boxes, and layout tables are now read in
  the order they appear; before, tables were read after all paragraphs. A text box Word stores twice
  is read once. The addresses behind linked words ("LinkedIn") are read too.
- **PDF**: the addresses behind linked words, from the link annotations.
- **The reader** (`read.py`) now also finds:
  - a name set over two lines, one word each ("JORDAN" / "PETERSON");
  - a name on the job title's line, cut where the rest are words the CV's own sections use;
  - a name that is also a place (Sofia Lima, Jordan Peterson), but only when the email agrees.
- **Already-uploaded files are not re-read.** The files themselves are never kept, only their text, so
  an account whose name was missed needs to upload its CV again.

**Suggested words from any upload** (owner). Every sample CV, Word or PDF, now gives its job titles as
suggestions. A cover letter alone gave none: it has no dated jobs and no skills list. Now job titles
that boards here use (three postings or more) are found in the person's own text and offered
(`_written`, up to 3), so a letter naming "the Product Designer role" offers "product designer".

**Not built: reading a CV with AI as a fallback** (the owner suggested Gemini). Three reasons:
- Rules now read all nine layouts.
- CLAUDE.md §0 keeps CV reading to code.
- A CV would go to a third party before the person has agreed to AI use, and it would mean a new prompt.

It can be added behind consent if a layout turns up that rules cannot read.

**Onboarding says it is setup** (owner):
- A "Set up your profile" bar over each step: Your CV, Your search, Done.
- A new search starts with **two** green words from the CV (`PREFILLED_GREEN`), so the list reads as
  filters to add to.
- Each word list has a coloured **+ Add** button that is never greyed out; empty, it puts the cursor
  in the box.
- Hints cut to a few words each.

Tests: each sample uploaded through the real routes fills in "What we found" and offers its titles; a
Word and a PDF cover letter each offer the titles boards use, and not one too rare to search for (8 of
these 11 fail on the previous commit); the browser test walks the setup bar, the two words and "+ Add".

Gates: lint-imports 2 kept; ruff clean; schema ok; contract current; build ✓; **1,309 passed, 3 skipped**.

## 2026-11-03 — The sign-up link reached nothing; every deploy now opens it

**Nobody could verify a sign-up** (owner): the emailed link said "This site can't be reached". It is
built from `PUBLIC_URL`, and `deploy/SERVER.md` said to set it to `https://mahoraga-app.rubenlekli.com`.
That was the old VPS's name, and it no longer resolves. The site is `https://mahoraga.rubenlekli.com`.
The guide is corrected; the server's value is changed by hand (no code reads a fixed address).

It is the second wrong `PUBLIC_URL` on this server in a day (the first was the tunnel's internal
`http://platform-api:8000`), and both failed only in a stranger's inbox. So:
- `/health` reports `public_url`, the address the site builds its links from.
- `deploy/reach.sh <address>` opens `<address>/health` from the internet. It passes only if the site
  answering reports that same address, so the old VPS still answering at an old name does not count.
- `deploy.sh` runs it with the running site's own value, and a failure fails the deploy.
- Tests: `reach.sh` against a local site (passes), a site reporting another address (fails), and
  nothing listening (fails). The sign-up link is on the address `/health` reports, and following it
  verifies the account.

Gates: lint-imports 2 kept; ruff clean; schema ok; contract regenerated; build ✓; **1,298 passed,
3 skipped**.

## 2026-11-03 — Several CVs and cover letters, each switched on or off; workplace switches; "Just arrived first"

**Every CV and cover letter a person uploads is used, and each has a switch** (owner). Someone
applying as a mechanical engineer and as a design engineer keeps a CV for each, and both inform the
suggested words and the instant CVs and letters.
- `UserDocument.in_use`, set by `PATCH /documents/{id}` from a switch on each file. A file switched
  off stays listed. A row stored before the switch has no value and counts as on (`$ne: False`), so
  no data change is needed. Uploading a file again switches it back on.
- `libs.suggestions.documents_of` is still the one place documents are read from, by suggestions,
  onboarding and the instant builder. It now reads only switched-on files and returns each text on
  its own, rather than every file of a kind joined into one.
- `libs.documents.read` reads each CV on its own, then combines them. Joined into one text, a second
  CV's name and contact line were read as lines of the first CV's last section, its
  "Certifications". This happened before with a CV plus a LinkedIn export, too.
- The instant CV shows a job both CVs list once (same organisation and months). It uses the title
  closer to the posting and picks from the bullets of both. It uses one summary, the closer one; a
  section both have (e.g. "Certifications") is shown once; a line both have appears once.
- **Unchanged:** AI-written documents still write from the person's writing profile
  (`base_cv.html`), not from the uploads. Changing what they read is a prompt change (CLAUDE.md §3a).

**"How often would you go in?" is now three switches: Remote, Hybrid, On-site** (owner).
- Switches are an "or": any switched on admits a job. None switched on narrows nothing, like
  countries and languages. "Anything" is gone; people switch on all three.
- `SearchSection.workplaces` replaces `remote_policy`. A stored answer is read as the switches that
  admit the same jobs: remote only → remote; remote or hybrid, or no answer → remote and hybrid;
  anything → all three. This happens in `migrate_config`, which every reader uses, and in the schema,
  for a page loaded before the deploy. No stored row is rewritten.
- `libs.corpus.workfilter.NAMES` is the one list of what job sites call each way of working. It now
  includes the Spanish and Polish words boards use (híbrido, remoto, teletrabajo, zdalna) and
  "on site" / "in office". Measured on the 8,896 local postings: **18 change**, all from unknown to
  the right answer (10 remote, 8 hybrid), and **no posting with an answer changes**. A bare "office"
  is left out: it is in 31 titles such as "Front Office Supervisor".
- `WORKPLACES` is defined once, on the posting model, and imported by the classifier and the config.
- The radio style in `styles.css` is removed: no radio button is left on the site.

**Jobs: "Just arrived first"** (owner): sorts by when a job arrived here (`first_seen_at`). A job a
board dated a week ago and found a minute ago is new to the person. "Newest first" is renamed
"Newest posted first". The word-count filter now reads "Any word" and "2+ words".

Tests assert behaviour, and each new one fails on the previous commit. The one exception is the test
that a search saved before the switches keeps its jobs: it guards what must not change.

Gates: lint-imports 2 kept; ruff clean; schema applied to an empty database; contract regenerated;
build ✓; **1,293 passed, 3 skipped** (copy_accounts, dedup_parity, prompt_templates, as always).

## 2026-11-03 (on the server) — Admin opens at once, the notifications over the Jobs tabs, a job view that closes

**Admin said "Loading…" for 2–3 seconds** (owner). Measured against Atlas, each admin read:
- `/admin/sources`: **2,748 ms and 87 queries**. It asked the settings collection once per number,
  four numbers for each of 21 sources, all from the same few documents. `libs.settings.snapshot` reads
  every setting in one query per request. Each getter takes it where it takes `db`, and gives the same
  answer, never older than the request. Now **168 ms, 5 queries**.
  - `all_settings` reads through it too, so one function reads them all.
  - `test_the_source_list_shows_each_setting_as_it_is_set_from_one_read_of_them` compares each number
    with its getter's own answer, and counts the reads on a client of its own. 88 reads on the old
    route; at most 10 now.
- `/admin/latency`: 679 ms. Found → delivered was read three times, once for each part that shows it.
  `latency_report` reads each zone once and builds all four parts from that. Now 476 ms.
  `latency_series` had no other caller and is gone. A test holds the report equal to its parts read
  apart.
- The page waited for all four of its first reads together (`Promise.all`), so for the slowest. Each
  part now draws as soon as its own read is back, and every admin read is in `kept.ts`: a return
  visit draws at once.
- Admin's reads are fetched ahead once an admin is signed in, the first views of Corpus and Latency
  included. An account's page is fetched while the pointer rests on its row, and no longer fades in.
- Not changed: `/admin/health` (323 ms, 10 queries) is the slowest left. It is read ahead, so only a
  cold first visit waits on it.

**The notifications opened under the Jobs tabs** (owner's screenshot). The top bar's rule was
`.app nav`, which reaches every `nav` in the app. The Jobs tab row (and Admin's) became sticky, opaque
and above the page, across the open panel. It is `.app > nav` now, the top bar alone.
`test_the_notifications_open_over_the_jobs_tabs` reads what is on top across the open panel. It fails
on the old rule.

**A job's view, closed after a CV was made in it, could not be opened again.** Found by the browser
test of skipping and refreshing, which failed the same way at the previous commit.
- × and a tap outside shut the `<dialog>` and left it to the browser's `close` event to tell the card.
  After a build, that event did not reach the card, so the sheet stayed mounted, shut. The card still
  counted it open, and tapping the job did nothing.
- `Sheet` now calls `onClose` itself, and the card takes it away (an open dialog removed from the
  page is closed). Escape still goes the browser's way.
- The test now checks that no sheet is left in the page and that the job opens again.

## 2026-11-03 (late night) — the first deploy to the new server: the sign-in broker on Python 3.12

**`oauthbroker` restarted forever on the new server.**
- Its image was `node:22-slim` with Debian's own `python3`, which is 3.11. The shared code is written
  for 3.12 (`def insert[D: Document]`), so the broker died on import: "SyntaxError: expected '('".
- Every other image was `python:3.12-slim`. The image check of the night before missed it, because it
  built every service's Python as 3.12 rather than as each Dockerfile does.
- The broker is `python:3.12-slim` now, with `node` and `npm` copied from the official Node image for
  the Claude CLI.
  - Checked without Docker by laying out that image's `/usr/local` from the official Node 22 tarball
    (checksum verified): npm installs `@anthropic-ai/claude-code@2.1.220`, and `claude --version`
    answers.
- `test_every_image_runs_the_python_the_code_is_written_for` holds every image's final stage to
  `pyproject.toml`'s target. It fails on the old Dockerfile.
- **The same gap on the host:** the health and backup timers run from the VM's `.venv`, and Debian
  12's Python is 3.11. `SERVER.md` says how to get 3.12 there (`uv`), and `install-units.sh` refuses
  an older one rather than install timers that fail every run.

**`deploy.sh` sent a crash to the wrong cure.** A service that is not running was told to "rebuild it
without cache", which builds the same thing again. That advice is for a service running other code
than this commit's. A service that is not running is now pointed at its log, shown 12 lines deep.

**`sudo`**: Debian without it, and Proxmox's container templates, are covered in `SERVER.md`. Only
installing and the timers need root. `deploy.sh` and `compose.sh` need the `docker` group.

## 2026-11-03 (night) — the tree holds what runs, settings in certificates/, the Proxmox server

**What no longer runs is in `retired/`** (owner: "so we can see what we are using").
- Found by walking the imports from every entry point (the five services, the schema tool, every
  deploy and dev script). 23 tracked modules in `libs/` and `services/` were reached by none, or by
  tests only:
  - the embedder service, with its compose file and Dockerfile;
  - `libs/contracts/embedder.py`, `libs/corpus/embedtext.py` with its five tests;
  - `libs/contracts/profile.py`, which only a docgen test still built, for a name;
  - `port_from_reference.py`, the one-time adapter port.
- Moved with `git mv`, so each file's history follows it. `retired/README.md` says what each was and
  why it stopped. Nothing there is imported, built, linted or tested.
- Not `archive/`: that folder is where retention writes old postings on the server (`ARCHIVE_DIR`).
- Nine folders in `libs/` and `services/` held nothing but bytecode of modules deleted on 2026-10-30
  (scorer, trainer, labeller, capture, setupagent, scoring, labels, feedback, agents). Untracked,
  and removed.
- Settings with no consumer, deleted:
  - `services.embedder` in the import contract;
  - lint rules for `status_routes.py`, `libs/db/session.py` and the Alembic migrations, none of which
    exist;
  - CI's embedder job.
- **Not moved: the prompts** (CLAUDE.md §3a). Five seeded files and one template may have no reader
  (ROADMAP Phase 8, item 20).

**The server's settings are `certificates/server.env`**, beside a laptop's `dev.env` (owner).
- `deploy/compose.sh` hands that file to compose, and every compose command goes through it:
  `deploy.sh`, the harvest timer, anything typed by hand. The health and backup timers read it as
  their `EnvironmentFile`.
- The template is `deploy/server.env.example`. It stays in git: a file inside an ignored
  `certificates/` cannot be tracked without loosening the rule that keeps every secret out.
- Values with `&`, `$` or spaces go in single quotes, which compose, systemd and bash all take
  literally. Checked with `docker compose config`: an Atlas address keeps its `&`, and a password's
  `$` stays a `$`.
- The units named `/home/ruben/...` and `User=ruben`, one box's layout. They carry `@REPO@`,
  `@USER@` and `@HOME@` now, which `install-units.sh` fills from the checkout and its owner. It
  refuses to install without `certificates/server.env` and `.venv`.

**Found getting it ready for a new server.**
- **Every full deploy started a harvest and a whole company-board seed.** `up -d --force-recreate`
  was given the one-shots too, and naming a service to `up` starts it. That is a quarter of an hour
  of probing every ATS, on every deploy. One-shots are only built now; their next run uses the new
  image.
- `deploy.sh` never started the tunnel, so a fresh server came up unreachable. It brings
  `cloudflared` up without recreating it unless its settings changed, and checks it is running.
- `deploy.sh` hashed with the host's `.venv`, which a fresh server does not have yet; it uses
  `python3`. It makes `logs/` and `archive/` as the deploying account, where Docker made them root's.
- The sign-in broker runs as the checkout owner's uid (`BROKER_UID`, set by `compose.sh`), so it can
  write to `logs/`. `useradd --non-unique`, because its base image already has `node` at 1000, the
  first account on a fresh server.

**`deploy/SERVER.md`: the Proxmox server, from nothing to the site answering.**
- The VM: 4 vCPU, 8 GB, 4 GB swap, 64 GB disk; a VM rather than LXC; one VM rather than one per
  service. Measured by PSS for it: the API with 4 workers, 324 MB after 240 requests; docgen with
  Chrome, 560 MB at its peak over 10 CVs.
- Docker with bounded logs, a read-only deploy key, the settings, Atlas's address list.
- The tunnel: a new one for the new server, never the old token twice, because two connectors split
  the visitors. Its service is `http://platform-api:8000`, tried on a test name, then the real name
  moved over.
- `deploy/README.md` is day-to-day running now. Moving the accounts off the old VPS is adapted to a
  new server: carry `SECRETS_KEK` across.
- `tests/test_deploy_config.py`:
  - every setting compose reads is in the template (it names `TUNNEL_TOKEN` when that is left out);
  - every compose command goes through the wrapper;
  - the units hold only the placeholders the installer fills;
  - no code reads settings from `deploy/`.

## 2026-11-03 (evening) — tabs drawn at once, one User-Agent, the deploy checked

**Switching tabs showed nothing for a moment, then the page** (owner).
- Two causes. `<main>` was keyed on the place, so each tab replayed a 0.4 s fade-in that began fully
  transparent. And Applied, My search and My documents read their data only once open, drawing
  "Your applications…" or an empty card first.
- The fade is gone from navigation. `web/src/kept.ts` keeps each page's last answer by address: a
  page draws from it at once and is brought up to date behind it, and a page that changes its data
  (Undo, delete, save) changes what is kept. One cache: it replaces the jobs page's and Settings'
  own, and sign-out forgets it.
- Once somebody is signed in, every tab's first read is asked ahead (`readAhead`), so a first visit
  is as quick as the next. The jobs list is not, because Jobs reads it itself where everybody lands.
  Measured on the dev database: each of those reads 2–9 ms, the jobs list 71 ms.
- A form keeps what somebody types over a fresh read (My search, About you, name and links).
- `test_every_tab_is_drawn_whole_the_moment_it_is_opened` slows the server to 400 ms and looks at
  each page two frames after its tab is pressed. It fails at 2.5% opacity with the fade, and on
  "Your applications…" with nothing kept.

**One User-Agent** (owner: "fold it").
- It was written out five times. `make_shared_client(headers=…)` replaced the shared headers, so
  each adapter that needed one more header wrote out a User-Agent of its own. Adzuna, passing only
  an `Accept`, went out as `python-httpx`, and Hiring Cafe's copy was used by nothing.
- An adapter's headers now add to `PAGE_HEADERS`, the one definition in `libs`. docgen's freshness
  check sends it too.
- **Profession is the exception, measured:** it answers a browser's User-Agent with a 500 page and
  serves its feed to anything else (15 items either side). Its own agent stays, named in a test.
- No Fluff Jobs answers the same 206 postings with either.

**The deploy, checked.** Docker cannot run from this machine's shell, so each image's Python was
built as its Dockerfile builds it: a clean 3.12 venv from its own `requirements.txt` and only what it
copies. Then its entrypoint was imported: api, docgen, notifier, oauthbroker and harvester import,
and seed-ats runs. The site was built with `npm ci` from a clean copy.
- **`deploy.sh` stopped every deploy at its schema step.** `compose run api python -m libs.db
  --prune` handed those words to the image's entrypoint, uvicorn ("No such option '-m'"). It runs
  `--entrypoint python` now. The step came with the MongoDB switch and had never run on the box.
- `.env.example` named neither `TUNNEL_TOKEN` (cloudflared exits without it) nor `ADMIN_EMAILS`,
  nor the Adzuna, Jooble and Findwork keys. Every variable `compose.yml` reads is in it now.
- `.dockerignore` keeps `certificates/`, `deploy/.env` and `archive/` out of the build context.
- Dockerfile and unit comments still said Postgres where they mean MongoDB.

## 2026-11-03 (later) — add a job by its link, every board's text in paragraphs

**Add a job** (owner: "if users find jobs that are outside of our search reach, users can add it").
- The second tab of Jobs ("Your jobs · Add a job", `#/add`), not a place in the bar of its own (owner,
  the same evening). One box, one button: paste a job's link, "Find this job".
- **Already here** (by its address, its address with the query cut off, its apply link, or its title,
  company and place): "We already have this job", with the card the jobs page shows, a CV a tap away.
  Nothing is read. A LinkedIn link is one job however it was copied: `/jobs/view/<n>`, the
  title-and-number form, `?currentJobId=` from a search or the feed (`job_link`).
- **Not here:** read from its page by code, never AI (`libs.corpus.postings.read_link`). JSON-LD,
  LinkedIn's job card (the guest page has no JSON-LD, so a pasted LinkedIn link used to come back
  without a title) or No Fluff Jobs' API is *complete*, and kept in the shared corpus. From there it
  is matched, alerted and archived like any other job, for everyone whose words it has. Source
  `linkedin` for a LinkedIn job, `link` for anything else; `discovered_via="link"` either way.
- **Part of it, or nothing:** a page with no JSON-LD gives a guess: its title less the site's name
  after a "|", `og:site_name` for the company, the text of `<main>` rather than its menus. A guess
  is shown, never kept. The person checks it, fills in what is missing and saves it
  (`POST /jobs/link/save`). A page that refuses us (Himalayas answers a server 403) gives an empty
  form and a link to open the page and copy from it.
- **A job written in is the person's alone** (owner, the same evening: "so they do not ruin the
  corpus"). Nobody checked it, so it is source `typed` and is never anybody else's match or alert
  (`candidate_conditions`), word suggestion or company board. It has no dedup key and an address of
  its own (`#typed-…` after the page's), so the real posting is still stored when the harvest finds
  it (`CorpusRepository.add_typed`). No user id on the row (TENANT_SAFETY §1, rule 2): the person's
  own `added_at` is the way to it, and bringing the link again finds theirs.
- **Closed** (404, 410, "no longer accepting applications"): said, and nothing is kept.
- Each job a person brings is listed for them under "Jobs you added" (`UserJobState.added_at`), whether
  or not it has their words, until they skip it, apply, or it closes. A job they had skipped is theirs
  again when they bring its link back.
- Measured live from this computer, one link from each of ten boards: **0.2–1.0 s** a read.
  - Complete: LinkedIn (through the site proxy), Lever, Ashby, Personio, Just Join IT, Jobicy, No Fluff Jobs.
  - A guess: SmartRecruiters, a Recruitee board on its own domain, The Muse, a company careers page.
  - Nothing: Himalayas.
- **Safety.** Every request and redirect must go to a public address. A link to `localhost`, the
  private ranges or `169.254.169.254` is refused before it is asked ("That link is not to a public
  web page"), or anybody could have the server read its own network back as a job's text. 10 s a
  read, 5 MB a page, 30 links an hour a person (`rate_limit`, as uploads and the contact form have).
- The API image now carries `beautifulsoup4` and `langdetect`, and `HARVEST_PROXY`
  (`deploy/compose.yml`), because the API now reads pages and stores postings. The browser headers are
  one definition in `libs.corpus.postings.PAGE_HEADERS`, used by the harvest and the API.
- `fetch_posting` had no caller since `/cv <link>` was deleted. It is replaced by `read_link`.

**Every board's text in paragraphs** (owner: "The Muse … is one huge paragraph").
- Text read before 2026-11-02 is one line, because the old reader flattened every page. That is
  530 dev postings: 417 LinkedIn, 91 No Fluff Jobs, 9 of The Muse's 10, 6 Himalayas, 4 Greenhouse,
  3 Profession. They were left to age out (up to 30 days). Re-reading them would not help:
  The Muse's page has no JSON-LD, and re-reading LinkedIn spends the proxy.
- Now `/jobs/{id}/description` lays out a one-line text as it is shown (`paragraphs`). A paragraph
  ends at the sentence that takes it past 300 characters, so a heading run into its first sentence
  ("Requisition #: … Position Summary …") stands alone, and "J. J. Keller" ends no sentence. A text
  with any line break comes back as stored. The same covers a page whose JSON-LD has no markup
  (Okta's Greenhouse pages).
- **Lever** kept `descriptionPlain`, the opening paragraph alone. What the job asks for (`lists`) and
  the closing (`additional`) were never stored, and its `&nbsp;` lines were kept as they were. Now the
  three parts, each list a list (`_lever_text`).
- **Ashby's** plain text made each list item a paragraph of its own (" - Ship it"); its HTML is read
  now, so lists are lists.
- **Jobicy** stored its 150-character excerpt ("…industry leaders&hellip;"), which counted as
  described, so it was never read in full: 75 dev postings at 376 characters on average. Its
  `jobDescription` is the whole text.
- **Not changed:** the 91 old No Fluff Jobs postings are that site's menus, not a job (read from its
  API since 2026-11-02), so laying them out does not help. They age out. Profession's feed gives only
  a job's highlight bullets (~480 characters). They show as a list, but they are not the whole job.

**Hiring Cafe.** The repository the owner linked (`umur957/hiring-cafe-job-scraper`) calls itself an
"official API". It is not one. It POSTs to `hiring.cafe/api/search-jobs`, the site's own internal
endpoint, with headers written to pass for Chrome (`sec-ch-ua`, `Sec-Fetch-*`, `Origin`). That is the
browser imitation ruled out on 2026-11-03, so it was neither copied nor tried. ROADMAP Phase 8 item 15
stands.

## 2026-11-03 — how far applications get, LinkedIn searches pooled, the company seed on the box

**What happened after "I applied"** (owner: "key feedback for us").
- Applied shows each application as four steps (Applied, Interview, Offer, Got the job) with one
  question and two answers at a time: "Heard back?" → "I got an interview" / "Not this time", and so
  on. "Not this time" ends it from wherever it stands, a step may be skipped, and Undo takes the last
  one back. The page's top line adds them up ("2 interviews · 1 offer").
- Each step is kept with its time on the person's row for the job (`UserJobState.progress`,
  `libs/tenant/applications.py`): `PUT` / `DELETE /jobs/{id}/progress`.
- **Admin › Outcomes** counts how far applications got, all together and by the CV that went with them:
  none made here, instant, AI-written, passed or flagged by the ATS check.
  - The verdict was only logged before; `BuiltDocument.ats_ok` keeps it now.
  - The CV is the newest one still kept for the job, so one deleted counts as none made here.
- **Found by the screenshots:** every row saved before today has no `progress` field, and the
  double-tap guard (`$size`) never matches a field that is not there. Every first answer was refused
  as "changed meanwhile". The guard reads a missing list as empty, with a test on a row saved the old
  way.
- **Found on the way:** `cover_built` had nothing that wrote it. Only tests set it, and the producer
  check counted the archive's query as a write. So retention kept postings people applied to, never
  ones they had written a cover letter for. docgen sets it now.

**LinkedIn searches are pooled** (owner: "scale with keywords, not users").
- Everyone due in a pass has their searches folded into the distinct (words, place, remote) queries
  they make (`build_query_set`, as the board tier always did). Each is asked once, reaching back as far
  as its furthest person's gap. The most-shared go first, up to `queries_per_cycle`, and among equals
  the order changes each pass, so a search left out once is not always the one left out.
- **Deleted with the per-person search, as no longer meaning anything:**
  - the `linkedin_concurrent_users` setting and its Admin control ("LinkedIn accounts per tick");
  - `LINKEDIN_MAX_CONCURRENT_USERS`;
  - `ProxyLease`, with the lease side of the client (`MeteredClient`, `make_shared_client`,
    `make_site_client`). The site has had one proxy for everyone since 2026-11-01.

**The company seed on the box.**
- `seed-ats` is a one-shot service in `deploy/compose.yml` (profile `tools`) with its own
  `deploy/docker/Dockerfile.seed-ats`, on the harvester's requirements.
- `deploy.sh` builds and verifies it with every service. Whenever a deploy brings a change to
  `ats_companies.txt` (the first one with it, too), it starts a run in the background. The run writes
  its report to `logs/seed_ats.log`.
- The README's table of services was still listing Postgres and five switched-off ML services; it
  lists what `compose.yml` runs now. Each service is its own container from its own Dockerfile, so one
  can be rebuilt alone (`./deploy/deploy.sh docgen`).

**Hiring Cafe** still answers 403. Probing it by imitating a browser's TLS was refused by the
assistant's safety check as getting round a bot wall, and was not tried another way. The companies it
pointed at are polled directly now (285 boards). **Working Nomads**, checked 2026-11-03: 35 jobs, the
newest 4.7 days old.

## 2026-11-02 (night, later) — LinkedIn's newest jobs, Just Join IT back, sign-in that does not wait

The owner's sixth list: the app started and its way in checked, then jobs reported accurately, quickly
and from many sites, before CV quality.

**LinkedIn found almost none of its newest jobs.**
- Every search covered the last 4 hours and read its first 30 results. LinkedIn orders a query's
  matches by relevance, not date, and ignores `sortBy=DD` on the guest search (identical pages, both
  ways, 2026-11-02).
- Measured that night, of the jobs posted in the last hour, that search caught:
  - "python developer" in the EU: 1 of 23 (200+ in the 4-hour window);
  - "software engineer": 3 of 47;
  - "data engineer": 0 of 28.
- Now a search reaches back to the person's previous search (`search_window`): at least an hour, at
  most the operator's window (4 hours) when the harvester was down. It reads up to 50
  (`source_results_per_query`). An hour holds 23–47 jobs a query, so 50 takes it whole. A query ends
  at its first short page, so this costs about the requests 30-of-4-hours did.
- `RESULTS_WANTED = 30` in `linkedin.py` was a second copy of the `linkedin: 30` setting, which
  always won. It is deleted.
- Each search request now opens a new connection, so it leaves from the proxy's next address, as the
  describe pass's reads already did. jobspy's session kept one connection, and so one address, for a
  whole query's pages: two 429s in the 16:49 pass. `new_connection_each_request` swaps the class
  jobspy's LinkedIn scraper builds its session from. A test guards that the scraper still builds it
  by that name.

**How quickly a job is found, measured (dev, jobs found after the first board sweep).** These are
medians from posted to found:

| Source | Median | 90th percentile |
|---|---|---|
| Greenhouse | 15 min | 1.3 h |
| SmartRecruiters | 13 min | 0.9 h |
| Ashby | 23 min | 0.9 h |
| LinkedIn (before the window change) | 1.0 h | 2.0 h |

Company boards are found within about one 15-minute pass of going up.

**Just Join IT is back.** `api.justjoin.it` has answered 503 to everything since 2026-11-01. The site
reads `justjoin.it/api/candidate-api/offers`: newest first, `itemsCount` a page from the cursor
`from`, with the same records. The adapter walks page by page until an offer is older than the window,
5 pages at most (`source_pages_per_cycle`). Live, it found 238 offers for the dev account's words in
1.2 s.

**Company names on board jobs.**
- Ashby and Lever name no company, so cards read "Openai", "Elevenlabs", "N8N", "Blablacar".
- `AtsToken.name` holds the name from `ats_companies.txt`. The seed script stores it, and a re-run
  updates it. `gather_ats` fills a nameless job from it, or from the board's address in words.
- SmartRecruiters says its own name ("Bosch Group"), and that is now used rather than the identifier
  ("BoschGroup").
- 111 dev boards were named; 72 Ashby and Lever jobs and 137 SmartRecruiters jobs were renamed in the
  dev database. Production has no board jobs yet.

**The owner's approved fixes from the last list.**
- **Lever:** paging would not have helped. Its first read of a board is slow whatever its size
  (Spotify: 22 s for 50 jobs, then 3 s for all 86; Palantir 40.9 s; Pigment 25.7 s), and it does not
  list newest first. So Lever alone gets a 60-second read limit (`LEVER_TIMEOUT`). The `--ats ashby
  --ats lever` re-run found 5 Lever boards that had timed out.
- **Workable:** asked 2 boards at a time (`PLATFORM_DEFAULTS`), as Recruitee is 8. This computer is
  still refused: it got 429 hours after the probe, so its block is long. The server has its own
  address. `seed_ats.py --ats workable` tries it alone.

**Sign-up, confirmation, sign-in and password reset**, through a real browser on the test server:
sign up 0.52 s, open the link 0.08 s, sign in 0.61 s, forgot 0.43 s, set a new password 1.01 s, sign
in with it 0.14 s, and the old password is refused
(`test_sign_up_confirm_sign_in_and_reset_the_password_each_step_at_once`).
- The verification, resend and reset emails were sent inside the request, and Zoho takes 0.4–0.8 s
  only to log in. So sign-up waited on the mail server, and an address with an account answered
  slower than one without: the timing told what the opaque answer hides. They are now sent once the
  answer has gone (FastAPI `BackgroundTasks`).
- `test_signup_and_forgot_answer_before_the_email_is_sent` runs a real server with a mail server that
  takes 1.5 s. It fails on the old routes.

**Smaller:** `check_sources.py` says what an empty answer from a company-board ATS means (no board
changed since the last poll), where it said "listed nothing for these words". Working Nomads' feed is
stale on its side: 35 jobs, the newest 4 days old.

## 2026-11-02 (late) — 287 company boards, the readers measured, two routes gone

The owner's fifth list of the day.

**Company boards to poll** (owner: "a collection of companies that are valid, stored and kept").
- The nine ATS sources poll `ats_tokens`, which only ever filled from links in postings. None of the
  dev corpus's 1,740 postings links to a board, so those sources answered nothing every pass
  (`empty_sources=13` of 17).
- `deploy/scripts/seed_ats.py` looks for each company in `ats_companies.txt` (582 lines) on every ATS
  whose board is one token. It uses the harvest's own fetch code, so a board counts here exactly when
  the harvest could read it. A board counts only if it lists at least one job: SmartRecruiters answers
  for any name. What answers goes into `ats_tokens`.
- The list: well-known tech, AI, fintech, European scale-ups, engineering and hardware,
  consultancies, agencies and software houses, smaller firms.
- **Found, 2026-11-02:** 287 boards — Greenhouse 134, Ashby 88, SmartRecruiters 25, Lever 21,
  Personio 9, Recruitee 8, Breezy 2. They list 30,810 jobs, and 831 of them fall in the harvest's
  72-hour window. So a first pass takes in about 830 jobs, and after that a few hundred a day. At 4.5
  KB a posting kept for 30 days, that is about 40 MB.
- **Missed, and why:**
  - Workable answered 490 "too many requests" at 16 at once, and then refused even one at a time.
  - Lever's largest boards take longer than the 15-second read limit; Spotify took 16.6 s.
  - Neither is fixed here. See ROADMAP Phase 8.
- **Run again, it adds what refusals hid.** A second run stored 285 boards in the dev database:
  Workable 3 got through, and Lever 18, because 23 timed out that time.
- **The first harvest pass with them (dev):**
  - It fetched 984 jobs, 738 of them new, in 34 s: Greenhouse's 134 boards in 2.7 s, 435 jobs.
  - Sources answering nothing fell from 13 to 7.
  - The board describe pass did its 200 (`describe_per_cycle`) in 52 s, so the first 738 take about
    four passes. After that a pass brings a few.
  - The whole pass took 100 s of its 15-minute tick. Workable's three boards were refused (429), as
    the probe said they would be.

**The readers, measured with a new address per request** (owner: "do the fixed numbers work?").
- 120 pages each run, in alternating order, through the site proxy.
- **4 readers:** 2.91 and 2.68 pages a second, 165 and 173 requests, at most 4 and 5 tries a page.
- **8 readers:** 2.87 and 2.94 pages a second, 220 and 226 requests, and a page at the sixth try in
  each run.
- None was given up in either case. More readers are not faster: they spend a third more of
  LinkedIn's allowance for the same pages. `DESCRIBE_WORKERS = 4` and `LI_RETRIES = 6` stay, now
  measured.

**Found to described** (owner: "pretty slow"). The dev harvester's last passes each found 7–31 new
LinkedIn jobs among 130–230 results. Every one was described in the same pass, about 30 s after it
was found: the median is 1.1 minutes. The 20-hour tail was the 223 jobs that waited through the
afternoon's throttling. At 2.8 pages a second, the 10-minute budget reads about 1,700 pages a pass,
50 to 200 times what one account brings in.

Gates, run locally, in this order: lint-imports 2 kept, 0 broken; ruff clean; `python -m libs.db
--prune` on an empty database clean; contract 70 paths, current; web build; pytest 1,239 passed,
3 skipped (the same three).

**Deleted, the owner's decision:** `POST /suggestions/accept` and `POST /suggestions/dismiss`. The
jobs page's suggestions that called them went earlier today. The help chat's "add a word" now holds
the logic it borrowed from the first, red-before-green refusal included
(`test_a_red_word_tapped_before_any_green_one_is_refused_and_says_why`). `GET /suggestions` stays: My
documents reads it. The contract is 70 paths.

**The page.**
- The × on a job is a circle. It was 2.25rem wide inside a page where every button is at least
  2.75rem tall, so it was an oval; it is 2.75rem both ways.
- The help, bell and theme icons sit together, 0.45rem apart, the same size. They were 2.2rem
  apart because the bar's own gap fell between each.
- On a phone the notifications panel spans the screen. Hung from the bell, it ran off the left edge.

## 2026-11-02 (night) — every LinkedIn read from another address, help beside the bell, times that move

The owner's fourth list of the day.

**A new connection, so a new address, for every LinkedIn request** (owner: "retry with a new
connection").
- The site proxy gives each new connection its next exit: the owner measured 4 addresses in 5
  connections. The client kept its connections open, so each reader held one address for the whole
  pass, and a refused page was asked again from the address that had just refused it.
- The proxied transport now keeps no connection open (`services/harvester/fetch.py`). Every page, and
  every retry, leaves from another address, and LinkedIn's allowance is spent evenly across them.
- No `-session-` username is needed. Rotation is what a public page read wants, and this gateway
  answers 407 to that form (its username has no dash, so it is not rotproxy's parsing).
- `test_every_proxied_request_is_a_new_connection_so_a_new_address`: three requests are three
  connections to a stand-in proxy, where the old client made one.

**The pass stops when LinkedIn refuses a whole round** ("stop once we get errors").
- When as many pages in a row as there are readers come back refused on every try and every address,
  the pass stops, and the rest are first in line next run. This is logged at WARNING, and nothing is
  counted against the postings.
- No new number: the count is `DESCRIBE_WORKERS`.
- `test_the_pass_stops_when_a_whole_round_of_readers_is_refused` fails on the old code, which asked
  all ten.

**Measured live, through the owner's proxy, on the dev corpus:** 221 LinkedIn pages read in 89.3 s
with 4 readers. 206 were described, 15 had closed, and none were given up. LinkedIn refused 67 tries,
and each was asked again from another address and read. The pass before today's change did 79 pages
in 81.5 s and gave up 16. Different hours on the same proxy, so this is one run, not a controlled
comparison. It cost 4.6 MB.

**Help is the "?" beside the bell** (owner). It opens over whatever page is open, in the same sheet as
a whole job (`Sheet` in `ui.tsx`; `JobView` now uses it, and `.job-view` is `.sheet`). Settings no
longer has it. The answer about Skip says "then “Skip it”", not "Undo".

**Times move on a page left open** (owner: "do the times increase?").
- Each time is a moment the server already sent, and "3h ago" was worked out once, when the page drew.
  A refresh was right; a page left open was not.
- One clock (`useNow` in `time.ts`) now serves every "… ago": the jobs, Applied, My documents and the
  notifications. It wakes on the minute, and when the tab is shown again, and only while a time is on
  screen. A label re-renders only when its minute changes, and nothing is asked of the server.
- It reads to the minute, so a label can change up to a minute late.
- The browser test moves the page's clock two hours on and reads "Found 2h ago" without a refresh.
  The old page still said "Found just now".

**Smaller:**
- Pages are a little wider: 72rem, from 64.
- Applied drops "Every job you marked “I applied”, newest first."

Gates, run locally, in this order: lint-imports 2 kept, 0 broken; ruff clean; `python -m libs.db
--prune` on an empty database clean; contract 72 paths, current; web build; pytest 1,236 passed,
3 skipped (the same three). The three new tests each fail against the behaviour they replace.

## 2026-11-02 (evening) — countries only, a Skip that asks, documents you can search

The owner's third list of the day.

**My search picks countries only.** The cities list added earlier today is gone. Every country is a
button, pressed when picked: the EU, the UK and Ireland show, with any already picked, and "Show 33
more countries" opens the rest. Cities stay where they help, on the jobs page's city filter.

- **A city already in someone's search reads as its country** (`CITY_COUNTRY`, through the existing
  `_migrate_locations`). Before today a city always admitted its whole country, so nobody's results
  change. One thing does: LinkedIn and the other boards are now searched for "Germany" rather than
  "Berlin" for that person.
- A CV saying "Budapest" offers Hungary.
- A page loaded before the deploy that still sends "london" on Save is saved as the United Kingdom,
  not refused (a `before` validator on `SearchSection.locations`, the same map).
- Adzuna's country list reads searches through `migrate_config` too. It read the stored slugs raw, so
  someone who picked London would have stopped being asked about Britain. The existing
  `test_adzuna_asks_the_countries_the_users_named` caught it.
- `wanted_cities`, `countries_of`, `UserFilters.cities` and the city half of the place filter are
  deleted. `libs/tenant/filters.py` is back to the last commit.

**A job that names only its town is still in a country** (owner: "so the filters are not mistaken").
A posting whose country could not be read passed every country filter, so a job in Kielce reached
someone who asked for Ireland only.

- `posting_country` reads the name tables as before, then the code beside the town ("Toledo, OH"), then
  `libs/corpus/places.tsv.gz`: 47,177 names from GeoNames (CC BY 4.0), built by
  `scripts/build_places.py`. These are cities of 15,000 people or more, the local names of Europe's
  larger cities ("Napoli", "Cracow"), and every country.
- A name two places share goes to the European one ("Valencia" is in Spain). LinkedIn's "Greater …
  Metropolitan Area" is unwrapped, and a capitalised region ("APAC") is never read as a town.
- It is read for postings only: in a CV or a search word, "Nice" and "Split" are words. It loads in
  0.09 s the first time a posting needs it and takes about 4 MB.
- Postings stored without a country are read again on the index pass (`terms._index`).
- On the dev corpus, 69 of 1,594 postings had no country. Now 51 do: "Remote", "Europe", blank,
  "APAC, EMEA", and one "Virgin Islands, British". The 18 read are all right: Białystok, Kielce,
  Cracow, Cork and five Italian and Spanish "Greater … Area"s, plus Peru, Uruguay, Hong Kong and others.

**Skip asks first, and a skipped job ages out** (owner).

- "Skip this job? … leaves your list and is not shown to you again", then "Skip it" or "Keep it". A
  skip that was asked about leaves at once, with no Undo. "I applied" keeps its six-second Undo.
- Retention no longer keeps a skipped posting forever: `ACTED_ON` is applied or cover-built. A new
  `MARKED`, which adds the skip, decides which of a person's rows survive. So a posting kept for
  somebody else's application stays skipped for the person who skipped it
  (`test_a_skip_lasts_while_the_posting_is_kept_for_somebody_else`). The backup keeps the postings
  `MARKED` points at.
- Archive files stay forever: the owner's decision, so no change.

**My documents: the CVs and cover letters made, folded and searchable** (owner).

- The list opens from "Show your N documents".
- A search finds a job or company, and "Made over a week / a month / 3 months ago" picks out old ones.
- Each line says when it was made.
- "Delete these N" deletes exactly what is shown, after asking. `DELETE /saved` now takes the `ids`
  and requires at least one, so a call that names none deletes nothing (it used to delete all). "Clear
  all" is that button with nothing filtered.

**A magnifier on every search box**: one rule for `input[type="search"]`. The jobs page, My documents
and Admin's two filters are search boxes now.

**The proxy, measured** (owner: "see what the limit is"). The site proxy already rotates: 40 fresh
connections left from 4 addresses, 10 each, in turn. It takes no `-session-` usernames (407), so it
is not the `rotproxy` in `~/projects/proxy`, whose `config.json` is still its example. It rotates per
connection, and the client keeps its connections open, so each of the four readers holds one address.

- **Short bursts of 50 pages, one try each:** from 4 to 24 at once, 54–100% answered, with no trend by
  count. That is 6–10 pages a second at 4 and 19–40 at 24.
- **A new connection for every page** (so a new address every time) was not better. It answered 75% on
  average against 79% for reused connections, and cost 0.1 s more per page.
- **Sustained, the limit showed:** after about 1,100 requests in 12 minutes, 240 pages at 8 at once got
  111 read and 129 given up, with 880 of 991 tries refused. LinkedIn's limit is a budget per address
  over minutes, not a count at once. Retrying a refused page six times on the same address spends
  that budget faster.
- **The block lifted within 25 minutes:** 10 of 12 read, one at a time.
- `DESCRIBE_WORKERS` stays 4: see ROADMAP Phase 8 for what is proposed.

Gates, run locally, in this order: lint-imports 2 kept, 0 broken; ruff clean; `python -m libs.db
--prune` on an empty database clean; contract 72 paths, current; web build; pytest 1,234 passed,
3 skipped (the same three). Each new test fails against the rule it replaces, put back for the
check: no world towns, cities not retired, skips kept forever. The exception is
`test_a_skip_lasts_while_the_posting_is_kept_for_somebody_else`, which guards the split instead. It
passes on the old code and fails if the skip is only dropped from `ACTED_ON`.

## 2026-11-02 (later) — words found by index, alerts for whole jobs, countries apart from cities

The owner's second list of the day.

**Finding a person's jobs is an index lookup** (`libs/corpus/terms.py`). Each posting stores which of
everyone's green words it has (`terms`, `title_terms`), with a multikey index, and a `vocabulary`
collection says which words have been looked for. The harvester's `catch_up`, after each run and under
its lock, is the one writer. It looks for a new word only in postings already indexed, and lets the
database pick those out so only matching text crosses the network. A new or re-described posting gets
every word. Nothing waits on it: a posting not yet indexed, and a word not yet in the vocabulary, are
read with the regex as before. Measured on 5,000 postings of real text and ten words: **101–132 ms
against 439–482 ms**, with the same 1,220 jobs and the same words either way. The new-jobs check is
17 ms. On the dev corpus, the first pass indexed 1,422 postings in about a second. Each word is looked
for on its own, so "engineer" is found inside "backend engineer", which one regex of every word passed
over.

**Alerts wait for the whole job** (owner: "that is a full job"). A posting gets `described_at` the first
time it has a description of 200 characters or more. Alerts and the daily email send only described
jobs, and the alert watermark is when a job was described, not found. Otherwise a LinkedIn posting found
early and described late fell behind the watermark and was never sent. It is inclusive, less what was
already sent, so two jobs described in the same millisecond are both sent.
`test_a_job_is_alerted_once_it_has_its_text_not_when_it_is_found` fails on the previous code.

**Countries apart from cities** (owner). Superseded the same evening, before it shipped: My search now
picks countries only (see the entry above). What stays from it: `libs.corpus.geo.COUNTRIES` and
`CITIES` as the one place list, the config's `Location` built from it, each posting's `city`, and the
jobs page's country and city filters.

**No Fluff Jobs from its API.** Its pages are built by a script, so describing one stored its menus
("JavaScript is required for the page…"). Its `api/posting/{slug}` gives the description, the must-have
and nice-to-have skills, the daily tasks and the pay, which is put first so the salary is read
(100–135 PLN an hour on the probe). Seen live on the dev harvester: `nofluff_ok`.

**The site.**

- One drawn arrow on every select, set in from the right, replacing the browser's own at the edge.
- My search asks before anyone leaves with changes not saved: "Save and go", "Leave without saving"
  or "Stay here". A link is stopped before it goes, the back button is put back while it asks, and
  closing the tab gets the browser's own question (`web/src/leave.ts`).
- Uploaded CVs and files are solid cards: an icon, when each was uploaded, how much was read, and
  "In use". They sit apart from the dashed upload box, which has its own heading.
- Applied opens a job whole, in the same pop-up as Jobs (`web/src/jobview.tsx`).
- Every question over the page is the browser's dialog (`Prompt`). The hand-placed boxes sat under
  the top bar: the page's fade-in leaves a transform that fixes them to the page. This covers the
  delete-account box, Claude's sign-in step and Admin's delete box too, and `.modal` is gone.

**Prompt change, approved by the owner on 2026-11-02** (CLAUDE.md §3a): `libs/prompts/support.md` says
*"Skip" takes a job off the list for good. "I applied" does too, and keeps it in Applied.* where it named
a "Hide" button that is gone. The wording is the one proposed in ROADMAP Phase 8, word for word. It is
not A/B-measured: it changes what the chat says the page does, to what the page now does, and the old
wording described a button nobody can find.

Gates, run locally, in this order: lint-imports 2 kept, 0 broken; ruff clean; `python -m libs.db
--prune` on an empty database clean; contract 72 paths, current; web build; pytest 1,217 passed,
3 skipped (the same three). Run against the previous commit, every new test fails there, apart from
`test_terms.py`, which cannot import the module it tests.

## 2026-11-02 — every LinkedIn job described and matched on its whole text; a jobs page that keeps your place

The owner's list of 2026-11-02, in three parts.

**Fetching: every posting gets its text, quickly, with its layout.** LinkedIn's search results carry no
text, so each posting is opened to read it, and until then it matches on its title only. Locally, 444
of 804 LinkedIn postings had none.

- **Once per LinkedIn pass, after every due search**, every LinkedIn posting still without text is
  described, newest first, four pages at a time, until the pass's 10-minute budget runs out. It was
  once per account, 60 at most, one page at a time — and four accounts could take four budgets.
  Measured through the owner's proxy: one page at a time 1.10–1.44 s each; four at a time 0.24–0.47 s,
  all 50 read; eight 0.17–0.63 s, and in one run 3 of 24 given up. A real pass over the dev corpus:
  79 postings in 81.5 s, 63 described, 16 throttled and left for the next pass.
- **A throttled read is not a failed read.** Six 429s used to count against the posting, and two such
  passes abandoned a live job for good. Now it is asked again next pass.
- **Deleted, the owner's decision:** `--describe-linkedin`, with `describe_linkedin`,
  `refresh_metadata`, `awaiting_metadata` and `PROXIED_LIMIT` — no timer ever ran it, and the pass now
  leaves it nothing. And the per-account limit `describe_per_run` ("Job descriptions fetched per run"):
  describing is per pass, not per account, and with no bandwidth limit (2026-11-01) there is nothing
  left for a count to protect; the budget bounds it.
- **Text keeps its paragraphs and list items.** Every page was flattened to one line, which is how
  every card read. `strip_html` lays it out, and every board adapter now uses it in place of a regex
  of its own (Recruitee and Personio were storing raw HTML).
- **12,000 characters kept, not 4,000**, which cut 389 of 1,209 local postings mid-sentence; the whole
  LinkedIn text of 24 of those was a median 5,831 and at most 10,260. Applied once, where every
  description is stored (`CorpusRepository.set_description`); the adapters' own 2,000 and 4,000 cuts
  are gone.

**Matching: the whole description, and more of your words first.**

- Every word of the description is read (it was the first 2,500 characters), and the words found
  are worked out in the database, so no description crosses the network unless a caller asks
  (`text=True`: the Telegram card reads its salary line off it).
- **Most of their words first**, a title breaking a tie, then newest (owner: "the more ANDs the
  better"). The 75/25 points are gone, and with them `score` on each job.
- Measured paired, three rounds, the same 5,000 postings of real text and ten words: **431–451 ms
  finding 459 jobs, against 491–500 ms finding 400.** The new-jobs check is 20 ms. Not moved: both are
  over the 300 ms budget at 2,000 candidates, because the regex is the cost (ROADMAP Phase 8, waiting
  item 2).

**The jobs page.**

- **No word suggestions** on it. They stay on My search. `/suggestions/accept` and `/dismiss` stay too:
  the help chat's changes run the same code.
- **Skip and I applied take a job away for good**, each with an Undo for six seconds. "Hide" is now
  "Skip"; "Not applied yet" went with the `applied` filter and `applied_at` on each job. A mark is kept
  in the browser until the server has it (`web/src/marks.ts`), so a lost connection, a refresh or a
  phone switched off loses none, and none is shown again.
- **A tap on a job opens it whole**, over the page: its text laid out (paragraphs, lists, short
  headings), and every button the card has. The × or a tap outside closes it. A CV made there is the
  card's too: both read one build.
- **Filters on "why"**: "Only jobs with" picks words, and a job must have every one picked; "At least
  2 (3, 4) of your words". Each word says how many jobs have it.
- **The company in bold and the country named**, as a pill, before anything else on the line.
- **New jobs are counted, not pushed in.** Every minute — the bell's interval, not a second clock — the
  page asks how many have been found since it loaded; "3 new jobs · Show them" shows them, and they
  are marked New. Nothing moves under somebody reading.
- **Telegram is folded** in Settings, its state said on the fold; it opens by itself while it waits
  for Start, or when it has stopped working.

**Not changed:** `libs/prompts/support.md` still calls Skip "Hide". A wording is proposed in ROADMAP
Phase 8 and waits for the owner (CLAUDE.md §3a). Postings stored before today keep their one-line text
until they age out.

Gates, run locally, in this order: lint-imports 2 kept, 0 broken; ruff clean; `python -m libs.db
--prune` on an empty database clean; contract 72 paths, current; web build; pytest 1,198 passed,
3 skipped (the Postgres copy source, the reference checkout, one empty guard). Each new test was also
run against the previous commit: all 18 failed there.

## 2026-11-01 — the owner's review: a clearer onboarding, new tabs, kept documents, own-bot Telegram, one proxy

Six steps from the owner's walk through the site, each its own commit with its tests and its reasons:

1. **Look** (5f1b626): every on/off choice is a sliding switch; "How often would you go in" is round
   buttons (they were ovals); the theme button is a circle on every page, the landing page included,
   which gained light colours; step 2 is three titled parts with clearer links and suggestions.
2. **Tabs** (d1afe8a): Jobs, Applied, My search, My documents, Chat, Help, Admin (admins), Settings
   last. Applied lists every application. Help is answers plus a contact form, kept and emailed to
   `support_email` with the person as Reply-To; the admin panel's Messages tab lists them.
3. **Kept documents** (2d2b32e): made CVs and cover letters are kept within `document_storage_mb`
   (5 MB by default), shown again after a refresh, in Applied and in My documents, deletable one by one
   or all at once; a full storage says so and stops keeping new ones.
4. **Filters** (a0e63a1): posted lately, one board, not applied yet, no CV yet, newest first. "Has a
   salary" was left out: reading it took 554 ms per load against the 300 ms budget.
5. **Alerts** (899a1da): Telegram is each person's own bot — a token and a press of Start, the chat
   found by itself — and the shared platform bot is gone; a daily email of new jobs at
   `email_digest_hour`.
6. **Proxy** (e3f1a11): one site proxy, `HARVEST_PROXY`, for everyone's LinkedIn searches, in place of
   a proxy of each person's own; LinkedIn jobs reach everyone; the privacy page names the site's proxy
   provider, so AI writing asks for agreement once more.

**Prompt change, approved by the owner on 2026-11-01** (CLAUDE.md §3a): `libs/prompts/support.md` names
the "My search page" where it said "My words page", so the help chat sends people to a tab that
exists. One phrase, the tab's new name, nothing else. Not A/B-measured: it changes no instruction,
only a name the page itself now shows, and there is no earlier wording to compare it against that a
person could still find.

Not done here: the proxy is not yet tested live; it waits for the owner to put the link in
`HARVEST_PROXY`.

Gates, run locally, in this order: lint-imports 2 kept, 0 broken; ruff clean; `python -m libs.db
--prune` on an empty database clean; contract 71 paths, current; web build; pytest 1,197 passed,
3 skipped (the Postgres copy source, the reference checkout, one empty guard). The full copy test,
run separately against a Postgres at the tag's schema: 2 passed.

## 2026-10-30 — the help chat opens, retention and the database budget are settings, and the leftovers go

**The help chat is on.** The owner approved `libs/prompts/support.md` on 2026-10-30 (CLAUDE.md §3a), so
`support_chat_enabled` now defaults to 1. It is new wording with nothing before it to measure against;
the two defects it shipped closed with are fixed first:

- **A reply cut off by a restart waited up to 8 minutes** to be written again, and the person could not
  ask anything meanwhile. The lease was twice the longest model call (480 s) because nothing renewed it
  while a model was silent. Now a live writer renews it every third of `LEASE`, and `LEASE` is 15 s.
  `test_a_live_writer_keeps_its_reply_through_a_model_call_longer_than_the_lease` fails on the old code
  (480 s), and fails again with the renewal switched off (the reply is written twice).
- **Reply threads had no ceiling**: each question got a thread and a provider connection of its own.
  Each API process now writes at most `WRITERS` (8) at once, and the rest wait, `pending`.
  `test_replies_beyond_the_writer_ceiling_wait_their_turn` fails on the old code.

**Retention and the database budget are platform settings**, with controls in the admin panel:
`corpus_retention_days` (30, unchanged) and `database_budget_mb` (512, Atlas's free tier). The archive
pass and the size alert read them. The database stat now reads "X of Y MB". Each has a test that fails
without the setting being read.

**Deleted, with no caller on the site** (the owner's decision, 2026-10-30): `GET /status`, which ran the
whole alert match on every load; `GET /onboarding/state` and `GET /onboarding/config`, which the
dashboard and `/onboarding/review` already answer, so their tests now read those; and
`POST /credentials/{kind}/reveal`. The contract went from 67 paths to 64.

**`ProxyCredential.daily_cap_mb` is gone.** It was a second answer beside the `proxy_mb_per_day` limit,
read only for an account that no longer existed; such an account now gets no proxy budget. The copy
from Postgres no longer reads the column.

Dead code removed: `libs.db.transaction()`, which nothing called (and the replica set the test docs
asked for because of it), `numpy` and `joblib` in `requirements-dev.txt`, and `MODEL_DIR` in
`deploy/.env.example`. All four were left over from the switched-off model. The README now says how to run the platform on your own
computer, and no longer says "Phase 1 of 4".

Gates, run locally, in this order: lint-imports 2 kept, 0 broken; ruff clean; `python -m libs.db --prune`
on an empty database clean; contract 64 paths, regenerated; web build; pytest 1,183 passed, 3 skipped
(the Postgres copy source, the reference checkout, one empty guard). The full copy test, run separately
against a Postgres at the tag's schema: 2 passed.

## 2026-10-30 — MongoDB instead of Postgres (Phase 9)

**The owner's decision:** users, their logins and everything about them live in MongoDB Atlas.
Postgres, SQLAlchemy, Alembic and 58 migrations are gone. Before they went, the switched-off model
was saved under the git tag `model-on-postgres` (CLAUDE.md §3) and removed from the tree.

**What the data layer is now.** It lives in `libs/db/README.md`:

- a dataclass per collection, holding its indexes and its allowed values;
- `ensure_schema()` in place of migrations;
- integer ids from a counters collection, so the site's job links and Telegram buttons stay numbers;
- `delete_account()` in place of `ON DELETE CASCADE`.

**Matching runs in the database.** The jobs page used to pull up to 2,000 postings into Python. It
now sends the newest 2,000 candidates through a server-side regex on the green words, so only matches
cross the network. Measured against Atlas from the owner's machine (26 ms a round trip, 5,000
postings, realistic text):

- 70 matches: 337 ms median.
- 196 matches: 451 ms median.
- The query itself took about 140 ms. The rest was four round trips and the Python filters.

From the server, those trips cost whatever the distance to the Atlas region costs, which is why the
cluster's region is on the owner's list.

**Behaviour kept where rollback used to provide it:**

- A refused new password leaves the reset link usable (`token_owner`).
- A failed send is not counted against the mail cap.

**Defects found in the port and fixed, each with a test that fails without the fix:**

- **Schema startup race.** Two services starting together on an empty database crashed one of them:
  the server answers a concurrent create with `NamespaceExists`.
- **Backups could not be written at all.** The JSON settings had no way to write a UUID. The id
  counters are now kept too, so a restore never reissues an id that an old alert link names.
- **The help chat.** A writer that outlived its lease could keep writing over the worker that took
  over. Now only the lease holder writes (`lease_holder`). A stream whose writer died starts it again.
- **The per-account lane view** showed the alphabetically last error, not the newest.
- **Metadata counting.** The describe pass counted no metadata it wrote. Retention's state sweep had
  lost its batch bound.
- **Account deletion.** It needed `SECRETS_KEK` from both the person's page and the operator's. It
  was also a second way of doing what `delete_account` does.

**Index pruning and lease timeouts:**

- `ensure_schema` drops an index nothing declares only with `--prune`. Only the deploy passes it, from
  an image of the commit being deployed, so an older service cannot undo a newer one.
- Claude's calls get the same timeout as Gemini's and OpenAI's (`TIMEOUT_SEC`), so one call cannot
  outlive the docgen claim it runs under.

**Removed with no consumer left:**

- the four reference-instance import scripts and the embedder probe;
- `plan_id`, the Telegram per-bot fields and the `telegram_bot` credential kind with its validator;
- the credentials page's `requirements` block, which nothing read, and `libs/profiles/harddata.py`;
- the latency page's scoring stages and filter table, and the corpus-projection alert;
- `too_old_to_send`, `unwanted_for_training`, `touch_last_seen` and `fill_missing_languages`.

**Moving the data.** The switch-over copies **accounts only** (owner): `deploy/scripts/copy_accounts.py`,
with the checklist in `deploy/README.md`. It reads only the final Postgres schema. It refuses a target
that already holds accounts. The company boards, operator keywords and settings travel with the
accounts, because without them postings would not be "collected again within hours". It was tested
against a Postgres built from the tag, with rows it must leave out.

**Backups** keep every collection except the ones that rebuild themselves, plus the postings someone
acted on. They are written as canonical extended JSON and verified by reading every document back.
`restore.py` replaces the Postgres restore drill.

**Tests run on a local `mongod` 8.0.34**, the version Atlas runs, and never against Atlas: conftest
refuses an Atlas URI.

Gates, locally, in this order:

- lint-imports: 2 contracts kept, 0 broken.
- ruff: clean.
- `python -m libs.db --prune` on an empty database: clean twice.
- Contract: 67 paths, regenerated.
- Web build: passes.
- pytest: 1,191 passed, 3 skipped. The skips are the Postgres copy source, the reference checkout and
  an empty guard list.

---

## 2026-10-29 — a site that says when it cannot reach its API, and the review's fixes (Phase 8)

**"It just says Loading."** The site opens on "Loading…" and asks `/dashboard`. Only a 401 was handled;
anything else (no API behind the dev server, whose proxy answers 500; a server error; no connection) was
rethrown into nothing, so the page said "Loading…" forever and never said why. Reported by the owner,
reproduced with the dev server and no API. It now says "Mahoraga can't be reached right now", with the
status, and a Try again button. `test_a_server_that_does_not_answer_is_said_out_loud_and_tried_again`
fails on the old build and passes on this one. It needs only Chrome and the build, so the database
requirement moved from the whole of `tests/test_e2e_site.py` onto the walk that uses one, and the
stale-build guard became a fixture both tests share.

**The dev server proxied a hand-written list of API paths**, which missed `/support` (in development the
help chat got the site's HTML back and also said "Loading…" forever) and still named `/setup`, deleted.
`web/vite.config.ts` now reads the paths from `web/openapi.json`, which the contract gate keeps current.
Checked by hand: `/support` went from `200 text/html` to `401 application/json`. No test, because there is
no second list left to drift.

From the code review of this branch:

- **Gemini tool calls without an id** were numbered per answer, so every round's first call was
  `call_0`, a later round's name overwrote an earlier one's, and an answer went back under the wrong
  tool. Each call now gets its own random id.
- **Claude answering nothing at its ceiling** was saved as a finished, blank help-chat reply with no
  log line. Like the Gemini and OpenAI paths, it now logs the cut-off and is refused.
- **Alerts scanned 2,000 candidates** (the jobs page's scan) while the watermark moves past everything
  it does not reach, so the older matches in a bigger backlog were never sent, silently. Alerts now
  scan 10,000 (`NOTIFY_SCAN`), and a backlog past that is logged at WARNING. The jobs page still lists
  them.
- **Migration `3c7d1e9a5b20`.** Accounts connected through their own bot before 2026-10-28 kept
  `telegram_status = 'connected'`. The platform bot has never been started in their chats, so their
  first alert would have marked them broken and told them they had blocked a bot they never saw. They
  now see the one-tap Connect. The test runs the migration's own statement.
- **"Sent today" was counted twice**, by the notifier and by the status page, each with its own
  midnight arithmetic. There is now one `libs.notify.sent_today`, built on `limits.since_their_midnight`,
  the boundary every other daily ceiling counts from (CLAUDE.md §5).

Each fix above has a test that fails without it, except the last, which changes no behaviour and is
covered by both consumers' existing cap tests. Two findings stay open, both in the help chat, which
ships closed: a stream whose writer died is never ended, and reply threads have no ceiling. Both must be
fixed before `support_chat_enabled` is turned on.

Gates, run locally, in this order: lint-imports 3 kept, 0 broken; ruff clean; `alembic upgrade head` from
empty and `alembic check` clean, one head; contract 67 paths, no drift; web build; pytest 1,491 passed,
5 skipped (reference instance absent; three switched-off-model tests, §3; one empty parametrised guard).
The contract step runs before the build: it rewrites `api-types.ts`, and the stale-build guard reads the
new mtime as a newer source.

---

## 2026-10-29 — a help chat that changes nothing without a tap (Phase 8, workstream 7, the chat)

The setup chat's agents are switched off; its page and API were the last live code reaching them.
The help chat replaces both, with its backend ported from the owner's project-planner portal — the
ideas, not the code (that is TypeScript on the Claude Agent SDK; this is Python on plain provider APIs):

- **Streamed, not polled.** The reply is written into its row as it arrives and read back as
  Server-Sent Events (snapshot, delta, step, done), so any API worker can serve the page, and a person
  who leaves and comes back finds the answer there. First words in 149 ms median with a scripted model.
- **Restart-proof without a journal file.** A reply still pending or streaming when the API starts is
  written again; a Postgres advisory lock held by the writer means four workers starting at once write
  it once, and a worker that dies lets go.
- **One runner interface**, `complete_tools`, for Anthropic, Gemini and OpenAI, the tool-call
  translation in one module; tests run on a scripted model — free, instant, offline.
- **A guard on every tool call.** Lookups run (their words, why a job is on their list, AI requests
  left). A change — add or remove a word, stop suggesting one — is offered as a button under the reply
  and runs only when the person taps it, through the same code the site uses. The model has no path to
  an account: `libs.assistant` cannot import that code at all.
- **Allowance and consent.** One reply is one request, counted only when the platform pays; without
  consent, the lookups that read their documents are not offered.

**The support prompt is new and not approved** (`libs/prompts/support.md`, CLAUDE.md §3a): the platform
setting `support_chat_enabled` keeps the chat closed until the owner approves it. Deleted with no
consumer left: `services/api/setup_routes.py` and its tests, the `conversations` limit, and the last
two lines of the switched-off contract's `ignore_imports` — **which is now empty**, Phase 8's first
condition for done. No live code queues work for a lane nothing drains any more.

Gates on the merged tree (web build first): lint-imports 3 kept, 0 broken, 0 ignored; ruff clean; one
alembic head (ee2add9ee95f); contract 67 paths, no drift; web build passes; pytest 1,486 passed,
5 skipped; no file lost its CRLF.

---

## 2026-10-29 — the landing page describes the product that exists (Phase 8, integration)

The first page a person reads still sold the platform before Phase 8: a model that learns your taste,
a Telegram bot to set up with seven slash commands (`/top`, `/stats`, `/outreach`, `/interview`,
`/rate`…, none of which exist now), PDFs delivered into a chat, your own Claude key, a cold start of
"a day or two", and "fifteen minutes of setup". Each section is rewritten in plain words for what a
person gets now — words they choose, jobs that say why they are there, a CV in one tap, alerts if they
want them — keeping the layout. The source count is the registry's (25, not "~30"); the meta
description that link previews show is rewritten too. The CSS, the icon and the command list only the
old copy used are deleted. Checked by screenshot at 390 and 1280 px, neither scrolling sideways.

Gates: ruff clean; web build passes; pytest 1,497 passed, 5 skipped, the walk run on this build.

---

## 2026-10-29 — the site for someone who has never used a job tool (Phase 8, workstream 6)

A new account used to go through seven onboarding pages and an hour of setup chat; the home page
counted jobs it did not show; and a banner told people who had simply not chosen Telegram that
nothing was being delivered. Now there are **three screens** — your CV; what we found (words, places,
languages, how often you go in) with one "Looks good"; and done, with optional alerts — and **Jobs is
the home page**. Each card says why it is there, makes a CV or cover letter in one tap with visible
progress ending in a download, and carries "I applied" and "Hide" with an Undo. My words, My
documents and Settings replace "Your details"; on a phone the five places sit in a bottom tab bar,
and no screen scrolls sideways at 390 px. An alert's link (`#/feed?job=`) puts that job first, or says
plainly that it is no longer in the list and still offers its documents. Every refusal (no consent, no
AI available, today's limit, paused) is a plain sentence; a failed build never shows its operator text.

Every response the site reads is a generated type now — a hand-written one is how the place pickers
went blank for two months. `/dashboard` stopped running a second full match that nothing displayed:
150 → 13 ms median over 2,000 rows. `web/src` without generated types went from 10,693 to 8,612 lines
(`styles.css` 2,986 → 2,232); the setup-chat page, "Your details", the overview and the jobs table
are deleted with their only consumers.

`tests/test_e2e_site.py` walks a new person in headless Chromium through the real built site, the
real API and the real docgen worker: sign up → the emailed link → three screens → "2 jobs match your
words", each saying why → Make my CV → a real PDF. Each of seven deliberate breaks fails it.

Gates on the merged tree (build first, then the suite): lint-imports 3 kept, 0 broken (2 ignored);
ruff clean; one alembic head; contract 71 paths, no drift; web build passes; pytest 1,497 passed,
5 skipped — the walk ran. New dev dependencies: `playwright` (capped), `uvicorn`.

---

## 2026-10-29 — one definition of where a CV's stylesheet is, and a constant that governed nothing (Phase 8, integration)

The scaffold's `<style>` block was found by two hand-written copies of the same search — strip the
comments, take the first block — in `libs.documents.instant` and in docgen's `strip_comments` +
`strip_style`, each written after the same bug (the scaffold's comment says "Keep the <style> block
intact", and a search starting there carried `[YOUR NAME]` into documents). `libs.templates.split_style`
is the one now; docgen's two helpers are deleted. Breaking it fails eight tests across both callers.

`MAX_TAILOR_CALLS = 2` claimed "one retry, and only for length", but the retry loop hard-codes its one
retry and nothing read the constant except a test asserting it equalled 2 — a test of a number, which
would pass whatever the loop did (§2). Both are deleted; `test_a_three_page_cv_is_revised_once` tests
the behaviour itself.

Gates: lint-imports 3 kept, 0 broken (2 ignored); ruff clean; pytest 1,498 passed, 5 skipped.

---

## 2026-10-29 — "I applied" and "Hide" exist again, and making a document is not applying (Phase 8, integration)

Nothing on the site could record either. `applied_at` was written only by a document build, and
`dismissed_at` only by the Telegram taps the alerts workstream removed — so the jobs page offered no
way to hide a job, and "applications recorded" would have counted every free instant preview as an
application. Building a CV set `applied_at` because a build was the model's strongest positive label;
the model is switched off and the instant CV is free, so that reason is gone.

`PUT /jobs/{id}/applied` and `PUT /jobs/{id}/hidden` record the mark, `DELETE` takes it back — one typed
handler. docgen no longer touches `applied_at`; its `_record` is deleted, and so is the docgen test
that pinned the old behaviour. Retention still reads `applied_at`, so a job the person marks as applied
is still kept out of the archive. Four tests, each shown failing against the code before.

Gates: lint-imports 3 kept, 0 broken (2 ignored); ruff clean; pytest 1,498 passed, 5 skipped; contract
71 paths; web build passes.

---

## 2026-10-29 — CVs and cover letters on the site: instant for everyone, AI on the key that pays (Phase 8, workstream 5)

A document is downloaded from the site now, not sent to Telegram. **The instant one is free for
everyone**: `libs.documents.instant` over the person's own uploads, one render, no model, no key.
**An AI-written one** runs the existing tailoring prompts, unchanged (§3a), on the person's own Claude,
Gemini or OpenAI key or Claude subscription, or on the platform's one paid key within
`ai_platform_requests_per_day` (20, never unlimited). Who pays is one function, `libs.llm.resolve`,
asked when a build is requested and recorded on its queue row; what the platform pays for is counted
in one table, `ai_requests`, which the support chat will feed too. The default models for own keys —
`gemini-3.8-flash` and `gpt-6-luna` — are platform settings, checked against each provider's model
page on 2026-10-29; the prompts were tuned on Claude Haiku and are unmeasured on them.

The build is the docgen queue row, so nothing can disagree about its state; its PDF waits in the
UNLOGGED `built_documents` for `document_keep_hours` (1) and the nightly dump leaves those rows out, so
decision D9 holds for backups too. One daily cap counts every document at its one admission point
(`cv_builds_per_day`, now 50 of either kind). Consent had been checked nowhere since `/process` went;
it moved to `libs.legal`, is asked at the request and again where text leaves the server, and the
disclosure — now naming Google and OpenAI — is version 2026-10-28, so everyone is asked once more.

Found on the way: the style block was lifted with a search that began inside the scaffold's HTML
comment, so every AI CV for an account that kept the comment came back carrying `[YOUR NAME]` and was
refused; and `.gitignore`'s `models/` silently ignored every new file under `libs/db/models/`.

Faster, measured with a fake model (n=12, alternating, same load): a render 0.96 s → 0.20 s on one
Chrome kept per worker over its DevTools pipe (PDFs checked byte-identical to `--print-to-pdf`), the
interactive poll 1 s → 0.25 s, ask-to-done 1.70 s → 0.40 s. The full test suite went from ~115 s to
35 s with it. 38 deliberate mutations, 38 caught.

Merged onto the alerts work with three conflicts: the OpenAPI contract and its types regenerated (70
paths); `test_feedback`'s admission-versus-worker test moved to `run_ai` and now gives the job a
description and asserts the refusal is about setup, so it cannot pass for the wrong reason; and the
two migration heads joined by a no-op revision, `ee2add9ee95f`.

Gates on the merged tree: alembic from empty to the single head `ee2add9ee95f`; lint-imports 3 kept,
0 broken (2 ignored); ruff clean; pytest 1,495 passed, 5 skipped; contract no drift; web build passes.
New env (optional): `PLATFORM_AI_PROVIDER`, `PLATFORM_AI_KEY`.

---

## 2026-10-28 — alerts start when you turn them on (Phase 8, integration)

The alerts workstream left one question open: somebody switching alerts on weeks after joining was
sent every match still inside its freshness window at once — up to seven days' worth, one message
each. Alerts announce what is new; the jobs page already shows the rest. `libs.notify.sent_up_to` now
starts from the later of the newest job already sent and the moment their current channels were
turned on — the `used_at` of the code that linked their Telegram chat, and their oldest browser's
subscription. Derived, not stored: no new column, and turning alerts off and on again starts fresh
rather than replaying the gap. Accounts linked before this change have no code and keep the old
rule. Two tests, both failing against the old rule; the shared fixture now states that its browsers
were subscribed when the person joined, which the old rule never needed to know.

Gates: lint-imports 3 kept, 0 broken (2 ignored); ruff clean; pytest 1,451 passed, 5 skipped.

---

## 2026-10-28 — what three merges left without a caller (Phase 8, integration)

§3 for everything that is not the switched-off model: each of these lost its last live caller in the
feed or alerts merge, and each was checked for callers in live AND switched-off code before going.

- `libs/corpus/ondemand.py` — 70 lines, no caller, no test; only the deleted `/cv <link>` command used it.
- `libs/corpus/ranking.py` — `opportunity_score`, `priority_key` and their buckets served only the
  deleted notifier ranking. Its one live function, `applicant_glut`, moved to its only caller,
  `libs.tenant.filters`, and the module is gone.
- `TenantRepository.retire_stale_matches` and `filters.pending_match_conditions` — the scored-match
  lifecycle, which nothing runs since delivery moved to `libs.notify.owed`.

The producer guard (`test_columns_have_producers`) then failed, rightly: `user_job_state.retired_at`
had lost its only writer. The column is the switched-off model's state, so its data and partial
indexes stay, and it is recorded as such in the guard rather than dropped (§3's exception). The 22
tests that exercised only the deleted code went with it; the others moved their import.

Gates: lint-imports 3 kept, 0 broken (2 ignored); ruff clean; pytest 1,449 passed, 5 skipped.

---

## 2026-10-28 — alerts: one Telegram bot, browser notifications, a sign-up with no dead ends (Phase 8, workstream 4)

Getting a job alert used to mean creating your own bot with BotFather, pasting its token, and finding
a chat id with @getidsbot and a channel id starting with -100. And since the model was switched off,
the notifier only sent rows the scorer had written, which nothing writes any more. **Now one tap
connects, and delivery asks the same question as the jobs page.**

**Telegram, one platform bot.** "Connect Telegram" is a `t.me/<bot>?start=<code>` link; the code is a
single-use `telegram_connect` token, and pressing Start ties the chat to the account. The webhook
checks Telegram's secret header with `compare_digest` and replies inside its own HTTP answer, so the
API never holds the bot token. Job cards carry two plain links — Open job, Make my CV — and say which
of the person's words matched; there are no callback buttons left to go dead. The per-user bot's
chat, channel, connect and webhook routes are deleted; their columns stay (no destructive migration).
Accounts connected the old way must press Start in the platform bot once (deploy/README.md).

**Browser notifications.** Web Push with one VAPID key pair: `push_subscriptions` (cascading with the
account), typed `/push` endpoints, a service worker, a manifest so an iPhone can add the site to its
home screen, and `web/src/push.ts` for the button.

**Delivery on `libs.matching`.** `libs.notify.owed` is a person's matches first seen after the newest
job already sent to them — a WHERE clause over `user_job_state`, no new column. They go out oldest
first, capped by `notifications_per_day`, so a run stopped by its budget resumes exactly where it
stopped. A 403 from Telegram marks it broken and rings the bell while browsers still get the job; a
browser answering 404 or 410 is deleted. The status page's one stall signal uses the same `owed()`: a
match waiting over an hour.

**Sign-up.** `/auth/resend` sits behind a "Send me a new link" button — the old advice, "sign up again",
sent nothing. Opening the verification link signs the person in, as login does (suspended accounts are
refused).

Deleted with their only callers: channel routing, `libs/notify/{buttons,telegram}.py`, the scored-row
delivery, `cv_builds_per_day` and `libs.queue.jobs_today` (their reader was the Telegram build button;
the documents workstream re-adds the cap at its own admission point), and `TenantRepository.
record_feedback`, which removed another switched-off import. 30 deliberate breaks, all caught.

Gates on the merged tree: one alembic head (`f49b1ca9b869`); lint-imports 3 kept, 0 broken (2 ignored,
both the chat workstream's); ruff clean; pytest 1,471 passed, 5 skipped; contract 67 paths, no drift;
web build passes. New: `pywebpush`; env `TELEGRAM_BOT_{TOKEN,USERNAME}`, `TELEGRAM_WEBHOOK_SECRET`,
`VAPID_{PUBLIC,PRIVATE}_KEY`, `VAPID_SUBJECT`; run `deploy/scripts/set_telegram_webhook.py` once.

---

## 2026-10-28 — LinkedIn back behind its own proxy, the jobs page three times faster (Phase 8, integration)

Three faults the merge of workstreams 2 and 3 surfaced, fixed in `libs.matching` and `libs/obs`.

**Everyone saw LinkedIn jobs.** Decision D3 is that LinkedIn is fetched through a person's own proxy
and only they see what it fetched. `TenantRepository.linkedin_visible` answers that, and since the
model was unwired nothing asked it: the jobs page and the alerts showed every account the rows other
people's proxies paid for. `matching_jobs` now applies it; a test shows the row hidden without a
healthy proxy and shown with one, and fails with the gate removed.

**The jobs page missed §0's 300 ms.** Measured over 2,000 fresh rows with the vectors every
pre-Phase-8 row carries: 471 ms median at a realistic ~12% match rate, 578 ms at ~85%. Three changes,
none of which alters a result: the switched-off model's ~3 KB vector per row is deferred rather than
loaded; green words are scored before the filters run, because most candidates carry none and the
workplace classifier was the costliest step in the loop; and that classifier is skipped when the
person accepts any workplace. After: **151 ms** (p90 200) and **260 ms** (p90 324).

**Every model call would have logged a line at INFO.** The Anthropic SDK moved to `httpx2` at 1.0 —
2.3.0 is what a fresh image installs — and `NOISY_LIBRARIES` raised `httpx` but not `httpx2`, so
each request to a provider would have narrated itself (§8). The logging test now covers both, and
failed for `httpx2` before the fix.

**The instant CV put an older job above the current one.** Jobs were ordered by relevance to the
posting, so a recruiter (and an ATS) met the person's 2019 role before their current one. Relevance
now decides which jobs make the cut when there are more than five; the ones kept stay newest first,
and inside each job its most relevant bullets still lead. Two tests, each failing against one half of
the old rule. `libs.matching._norm` is public as `norm`, since `libs.suggestions` compares words the
same way.

Gates: lint-imports 3 kept, 0 broken (5 ignored); ruff clean; pytest 1,534 passed, 3 skipped.

---

## 2026-10-28 — the jobs page on rules, uploads read by rules, onboarding in three screens (ROADMAP Phase 8, workstreams 2–3)

Two branches merged into `simplify`, each built by its own agent and checked again after merging.

### The jobs page and the front page ask one function (`services/api/dashboard_routes.py`)

Both now call `libs.matching`, so the number on the front page is the length of the list on the jobs
page. Every job says which green words put it there. The front page shows two numbers a person reads
— jobs matching now, applications recorded — instead of model versions, label counts and a
stale-model reason. `/process` and the Model and Process pages are deleted (UI plumbing for the
switched-off capture, recoverable from git); the model code itself stays.

`min_salary` was a setting nothing read; it is now a filter: a job goes only when it states a salary,
in a period that converts, whose top is below the minimum — unknown pay is kept. Red words cap at 25
like the form (the 11th was refused); `max_notifications_per_day`, which nothing read, is gone.

### Unwiring the model, and two things that would have broken the day it stopped

The fence's `ignore_imports` went from 15 to 5. Operator health lost its silent-feed page: it counted
the scorer's rows, and on the day the scorer stopped it would have paged for every account. The notify
sweep's "waiting" was scored rows that nothing writes any more, so it would never have queued a
delivery again; it now queues an account when a job was stored after its last notify run. The
harvester stops embedding and no longer needs `EMBEDDER_URL`. capture, setupagent, labeller, scorer
and trainer sit in a compose `ml` profile nothing starts. import-linter silently skips a forbidden
module inside a source package (an "overlapping pair"), so the fence names the harvester's children;
re-adding the embedding import now breaks the contract. Integration also added `libs.matching` itself
to the fence's live modules, which the foundation commit had missed.

### One reader of what a person uploads (`libs/documents/read.py`)

Name, contact details, links, places, working languages, the jobs they held with their bullets,
skills and years — read from the text python-docx and pypdf actually return: Word list items without
glyphs, sidebar-first two-column PDFs, wrapped bullets, LinkedIn exports. Tested on an engineer, a
nurse, a sales rep, an accountant and a teacher. A misread surfaces on the review screen, where the
person corrects it.

### Green and red words, each with a reason (`libs/suggestions.py`, `users.suggestions`)

Green: the titles they held, then the skills that real job titles in the corpus name. Red: the
seniority their years are furthest from, pay that is not pay ("unpaid", "commission only"), and words
their green words drag in from jobs they never did ("sales" for an engineer whose "engineer" finds
"Sales Engineer"). Never a word already in their search, one they dismissed, or one inside a green
word. Recomputed on every document or search change; 228 ms median over 2,000 matching jobs.

### Onboarding is three screens, and the pickers were empty for two months

Your CV → what we found → done; finishing needs a CV, a name and one green word, and Telegram, an AI
key and a proxy are optional settings. The review arrives filled in from the CV, and anything the
person saved wins over the CV. The place and language pickers had rendered empty since 2026-08-11:
ConfigStep read a `schema` key the API had stopped sending, through a hand-written type, so nothing
failed. The choices are now served typed and labelled and read through the generated type, so a
dropped list is a `tsc` error. `/onboarding/config/preview` — a second, disagreeing definition of
"which jobs match" that nothing called — is deleted.

### The free CV and cover letter, by code (`libs/documents/instant.py`)

Their own lines, ordered by relevance to the posting, in the scaffold CV's own style (read at runtime;
the scaffold is never edited). They pass docgen's ATS check, and every sentence about the person
traces to their documents.

Every behaviour above was checked by breaking the code on purpose and watching its test fail (36
mutations in the profile branch, all caught). Gates on the merged tree: lint-imports 3 kept, 0 broken
(5 ignored); ruff clean; one migration head (`60ea03bb2a00`); pytest 1,531 passed, 3 skipped (no
reference checkout; an empty parameter list; the score-lane test, switched off per §3); contract 66
paths, regenerated; web build passes.

Known and carried forward: `GET /feed` measured 442 ms median over 2,000 rows against §0's 300 ms;
people without a proxy still see LinkedIn rows other users' proxies fetched (`libs.matching` applies
no LinkedIn gate); `has_consented` has no caller until the AI workstream sends text to a provider.

---

## 2026-10-28 — simple enough for anyone: the foundation (ROADMAP Phase 8)

The owner's direction: the platform is for someone who finds technology hard, so jobs are chosen by
rules a person can see, not by a model they cannot. This commit is the base every later branch of
Phase 8 builds on.

### CLAUDE.md §0, and the one exception to §3

§0 "Anyone can use it" is the new first rule: one screen one job, rules before AI, fast or visible,
never leave the site to finish a step, fewer lines wins. §3 gains its only exception, by the owner's
decision: the learned model (training, scoring, labelling, capture, setup agents, embedding) is
**switched off, not deleted**, because it worked and may return.

### The fence that keeps switched-off code switched off (`pyproject.toml`)

Kept code is only safe while nothing live reaches it. The `switched-off model` import contract is
the one list of what is off; `lint-imports` fails the build if live code imports it. It starts with
the 15 live imports that still do, as `ignore_imports`, and Phase 8 is not done until that list is
empty — so the burn-down is visible in the contract rather than claimed in a message.

### `libs.matching` — the one definition of which jobs a person sees

Green words pick a job (title 75, text 25), red words veto it (title or company), and the existing
`libs.tenant.filters` supply language, country and freshness. The jobs page and the alerts will both
ask this one function, so they cannot disagree. Six tests, each shown to fail against a broken
matcher: red words ignored, `since` ignored, the remote policy ignored, the points swapped.

### Every platform user only ever saw on-site jobs in Hungary (`libs/corpus/workfilter.py`)

Found by the mutation test above: switching the remote-only check off changed nothing, because
something else was already dropping the on-site job. `UserFilters` read `onsite_locations` from a
config that has no such field, so it always fell back to the environment and then to
`"hungary,budapest"` — the reference instance owner's own setting, whose module docstring still
read "Ruben wants FULLY REMOTE work … unless the job is in Hungary". An explicit on-site job in
Dublin, Paris or Berlin was dropped for every account; one in Szeged was kept.

Replaced by the question onboarding actually asks — remote only, remote or hybrid, anything — applied
in `UserFilters.unwanted`: remote only keeps remote, remote or hybrid drops explicit on-site, anything
keeps all, and unknown is always kept. Where a job is stays the country filter's question, answered
from the structured `country` column rather than a substring of the location text. `is_allowed`,
`onsite_locations` and the default are deleted (§3); `classify` stays. The three tests that pinned
the Hungary rule are rewritten to the new one.

Gates: lint-imports 3 kept, 0 broken (15 ignored, to burn down); ruff clean; pytest 1,525 passed,
2 skipped (no reference checkout; a deliberately empty parameter list), with Chrome present; no
migration, route or web change.

---

## 2026-09-12 — a document read to 41%, a guess that replaced an answer, and a bench for the CVs

Five changes, all from one investigation: why the account this platform onboarded itself gets worse
CVs than the one migrated into it.

### 60,000 characters was not the ceiling anyone thought it was (`libs/documents`, `libs/agents`)

Two ceilings existed. `MAX_TEXT_CHARS` stored 200,000 characters of an upload, and
`MAX_DOCUMENT_CHARS` in `libs/agents/tools.py` then re-truncated every `read_document` at **60,000**
— so a file kept in full was cut before the setup agent ever saw it. Measured on the live platform:
one account's 145,493-character context file was read to **41%**, and the two headings in the unread
remainder were `KEY TECHNICAL ACHIEVEMENTS` and `SKILLS DEMONSTRATED` — the material a base CV is
built from. Nothing errored.

The second cap is deleted. One number now, applied once where the text is stored, raised to
**300,000**: whatever is kept is what is read.

The truncation notice was going to the wrong reader, too. It was appended into the agent's tool
result, where the only audience was a model that could do nothing about it, while the person who
could shorten the file went on believing all of it had been read. The upload response now carries a
`note` and both upload surfaces show it — kept, not refused, with the choice left to the user.

### A rubric change was replacing ground truth with a guess (`libs/labels`, `services/labeller`)

`stale_job_ids` selected stale labels by generation and **did not filter on source**, so a rubric
edit made the user's own answers stale and `write_labels` upserted an LLM guess over them. On the
live box: of the 101 jobs one account had applied to, **one already carried an `llm` label at weight
1.0** instead of the applied label, and a 98-application backfill sat one rubric edit from the same
fate.

`bump_generation()` is now the one place that decides what a new generation means: a rubric change
invalidates an *estimate*, never a *fact*, so feedback labels are carried forward instead of
relabelled. They stay in the training set and cost nothing to keep. Two guards behind it —
`stale_job_ids` skips feedback rows so the account never pays to re-buy an answer, and the upsert
carries `WHERE source != 'feedback'` so no future caller can overwrite one.

### `label_pool` reaches 1,500 and 2,000 (`libs/limits`)

Cold start stops for good once an account hits its target and nothing tops it up as the corpus
grows, so this is not "per run" in practice — it is the entire automatic training set the account
will ever have. The first account labelled by the platform rather than migrated stopped at **338**;
the migrated one's 2,547 labels were imported, not produced here. 1,000 was the old ceiling.

### The Model page says how to teach it (`web/src/pages/ModelPage.tsx`)

Once the automatic run finishes, `/rate` and building a CV are the only things that add training
data, and nothing said so. `/rate` is not starved either — one account had **5,548** eligible jobs
waiting and had answered five, because it is one card per typed command.

### One base CV instead of two (`libs/templates`, `services/docgen`)

`libs/templates/base_cv.html` was the marker-filled layout `cv.py` rendered from `ProfileSchema`.
That path is gone: a CV is built from the person's own `base_cv.html`, seeded from
`libs/scaffolds/base_cv.html`. Measured before deleting — both live accounts' base CVs carry the
scaffold's fourteen class names, its `@page` rule and its typography, and neither carries this
file's, which had seven of those classes and no `.entry`, `.profile` or `.competencies` at all. Its
only caller was `build.template_base()`, whose only caller was a test. Deleted with the loader, and
`tests/test_cv_template.py` moved to the scaffold users actually get.

### A bench for CV prompts (`scripts/cvlab/`)

`libs/prompts/eval_cv.md` has existed with no runner since `libs/scoring/cveval.py` was deleted.
`scripts/cvlab/` is that runner, and the measurement it produced is in ROADMAP 6bi: the third-person
and fallback drafts move every mechanical measure they target and move **no** judged quality
measure (overall −1.06 ± 1.03 over 72 cells). Per §4 that does not justify shipping them, and no
prompt has been changed.

Three defects in the bench itself were found and fixed before its output could be trusted: the seven
judge dimensions arrived nested and were read top-level, so only `overall_100` was ever compared;
the comparison column said "better / worse" when half the measures improve by going *down*; and the
baseline was decided by glob order, which flipped the sign of every difference.

### Also

Six tests that errored or skipped wrongly without `DATABASE_URL` are fixed — `test_feedback.py`
never set `SECRETS_KEK` and passed only when an alphabetically later file had already leaked it into
the process, and four more used a DB fixture without their file's own skip guard. The suite is now
clean on a machine with no database.

---

## 2026-08-13 — three copies of four two-letter codes, and one of them had drifted

Raised as a §5 finding and then fixed on the owner's instruction. It was worse than first reported.

`callback_data` is capped at 64 bytes, so a Telegram tap carries an action code and a job id and
nothing else — which means the service that *emits* a button and the service that *parses* the tap
must agree on four strings, and `services` may not import `services`. There were **three copies of
that agreement plus a fourth literal**:

- `services/notifier/buttons.py` — the keyboard actually sent, plus an `ACTION_CODES` map whose
  **only consumer was the test asserting it matched the parser**. Exactly the pattern CLAUDE.md §5
  names: two constants and a test asserting they are equal, which passes while both are wrong.
- `services/api/telegram_routes.py` — **a second `keyboard()`, still returning the pre-2026-07-29
  buttons** (✅ Applied / 🚫 Skip / 📄 Build CV). Sent by nothing, exported in `__all__`, and asserted
  by `test_the_keyboard_carries_three_buttons_within_telegrams_limit` — a test that checked the dead
  copy carried three buttons while the live feed sent two. This is the drift, and it is the one the
  equality test could never have caught, because it was not part of the equality.
- `CALLBACK_PREFIX`, the parse table, written out as a fourth literal.

Now one definition in `libs/notify/buttons.py`: `CALLBACK_CODES`, its inversion `ACTION_FOR_CODE`,
and both keyboards **built from the codes** rather than repeating them. A button cannot carry a prefix
the parser does not know — the drift is not tested for, it is unrepresentable. `services/notifier/
buttons.py` is deleted, the API's stale `keyboard()` and its local `rating_keyboard` are deleted, and
both services import the one module (§3, §6: a value needed on both sides of a boundary goes in
`libs`).

This also closes the last loose end in the tap archaeology: Andras' two `ap:` taps came from cards
delivered *before* 2026-07-29. **Telegram keeps an inline keyboard on a message forever**, so
changing `keyboard()` never rewrote the buttons already in somebody's history, and old ✅ Applied
buttons stayed tappable indefinitely.

Tests: `test_the_keyboard_carries_three_buttons_within_telegrams_limit` asserted the removed copy and
is gone. Three replace it — every callback fits 64 bytes at a ten-digit job id, every button either
keyboard emits parses to a known action, and **every code the parser accepts is emitted by a button
somebody can tap**. The last is the one that would have found `ACTION_CODES`.

**One flake, reported rather than smoothed over:** `test_admin.py::test_the_user_list_carries_no_
credential_material` failed once under one random order, then passed in isolation, on a full re-run
(1,488), and across four repeats of its own module. It touches the admin user list and credentials,
not the button surface. Recorded as a suspected pre-existing order dependency to look at, not as a
clean bill of health.

Gates: lint-imports 2 kept 0 broken; ruff clean; alembic from empty to `c7a1f2e94d80`; pytest
**1,488 passed, 13 skipped**; openapi + api-types current; `npm run build` ok.

---

## 2026-08-13 — a placeholder that could reach an employer, and the document nobody checked was the right one

Closing out the Telegram and CV-generation work by asking what the suite would *not* have caught.
Two gaps, one of them a real defect.

**A marker in the model's reply was delivered.** `build._require_personalised` refuses a writing
prompt still carrying `<<NAME>>` or `<<CONTACT>>`, so the input is checked. Nothing checked the
output. A document that came back with a marker in it compiled, was delivered, and went to an
employer — no exception, no failed job, just a worse CV than the person believed they sent. The check
now sits in `_render`, the one chokepoint every document passes on its way to Chrome, beside the
hidden-content scan and failing closed the same way. Verified by stashing the guard: the test fails
with `DID NOT RAISE NotBuildable` and nothing is delivered once it is back.

**Nothing asserted the worker built the document that was asked for.** The queue payload is the only
thing carrying `kind`, and the one test driving that path asserted only that `handle` returned at
all. A `kind` dropped between the tap and the build would have delivered a CV to somebody who tapped
Build Cover — right person, wrong document, no error anywhere, suite green. Now asserted for `cover`,
for `cv`, and for a payload with no `kind`, because production's oldest `docgen` row is
`{"job_id": 1396}` and the default is part of the contract.

This second one is a **relationship guard**, not a defect test: it passes against the code that
prompted it, like `test_schedules.py` and `test_columns_have_producers.py`. Recorded as such rather
than dressed up as a fix.

**What was checked and found already covered**, so that the next person does not repeat it: there are
**no websockets in this platform** — the notifications migration says so in its own docstring
("minus websockets and preferences; the dashboard polls"), and the only stream is the admin
pipeline's SSE endpoint, which has coverage. CV generation had 44 tests before today's two, including
every refusal path, delivery to the personal chat, hidden-content stripping, the page-limit revision,
cover-versus-CV checking, and scratch-file cleanup. The Telegram button and webhook paths have 46,
now including the reply.

Gates: lint-imports 2 kept 0 broken; ruff clean; alembic from empty to `c7a1f2e94d80`; pytest
**1,487 passed, 13 skipped**; openapi + api-types already current; `npm run build` ok.

---

## 2026-08-13 — the button that answered nobody, and the gate that named a file

Two defects between them made "tap Build and nothing happens" the normal experience, and neither
raised anything.

**The Telegram webhook never replied to Telegram.** Every handler composed a sentence for the person
who tapped and returned it as the HTTP response body. Telegram discards that body unless it names a
Bot API method, and `answerCallbackQuery` had never appeared in this repository — checked with
`git log -S`, it has been absent since the feature was written. So a tap spun and cleared in silence,
`/rate` answered with a keyboard nobody received, and every refusal — over quota, not ready, not the
owner — was computed and thrown away. A build takes a minute or two to arrive, so silence was
indistinguishable from a broken button, and a refused build had no evidence at all.

`libs/notify/telegram.py` sends it: `answerCallbackQuery` for a tap, `sendMessage` for a typed
command. **Committed before the reply, deliberately** — "Building your CV" is a promise about a
queued row, and saying it before the row is durable is how somebody is told about work that then
rolls back. Both are best-effort and log at `WARNING`: the tap is already recorded, and no
acknowledgement is worth losing a build over. Verified live — the reply reached Telegram and was
refused only because the drill's synthetic `callback_query.id` was not a real one
(`"query ID is invalid"`), and the build completed anyway.

**A build was gated on a confirmed capture schema in order to name a PDF.** `build_blocker` and
`docgen._load` both required `user.profile`. Once the schema came out of generation the only line
still reading it was `_filename`, for `profile.identity.name` — which `users.full_name` already
holds, as the name the user typed at signup. Guided setup writes the ten profile files and never
writes `users.profile`, so the gate refused **every account that onboarded through the chat**: now
the normal path. Measured on the drill account, which finished every setup step, had ten filled
files and a CV that built by hand, and was told "Confirm your details first".

`user.profile` keeps its real consumers — `spec_for` in the trainer and the scorer, which build the
three profile scoring features worth −0.005 average precision (6bg). Only docgen stopped asking.
Deleted with the gate: the `profile` parameters `_cover` and `_tailor_until_it_fits` still declared
and never read, and the `ProfileSchema` import.

**What the archaeology actually found, recorded because the conclusion was the opposite of the
premise.** Andras appeared unable to build, and none of the first four hypotheses held: his
`build_blocker` was already `None`, his webhook was registered with `callback_query` allowed and no
errors, his ownership check passed under a synthetic tap, and his `applied=2` turned out to be two
thumbs-up taps from `/rate`, which shares the `ap:` wire prefix with the old Applied button. He had
never tapped Build, because tapping it had never visibly done anything.

Verified live, 2026-08-13:

- A real `bc:` tap for his account queued, built and delivered his first document ever —
  `state=done`, 1 attempt, 2 pages, 1 call, `ats_ok=True`, no error. Before today the `docgen` lane
  held eight rows and all eight belonged to the owner.
- A fresh chat-only account (ten filled files, `user.profile` NULL) returned `build_blocker: None`
  and its tap queued a `docgen` job. Deleted immediately afterwards: no credential, so leaving the
  row would have burned retries.

Tests: five new, each verified to fail against the code that made it necessary.
`test_a_build_is_refused_when_the_profile_was_never_confirmed` asserted the behaviour being removed
and is gone (§3); `test_the_admission_check_and_the_worker_ask_the_same_question` keeps the
relationship it was written for and asks it about the condition that still exists.

Gates: lint-imports 2 kept 0 broken; ruff clean; alembic from empty to `c7a1f2e94d80`; pytest
**1,485 passed, 13 skipped**; openapi + api-types regenerated; `npm run build` ok.

---

## 2026-08-12 — the writing prompts nothing filled, and the gate that could not see them

**PROMPT CHANGE, approved explicitly by the owner (CLAUDE.md §3a).** `libs/agents/setup-director.md`
gains one table row and has nine numbers corrected. No tone, no instructions, no gate logic changed.
Recorded here in full because §3a requires it:

- new table row `| 3 | **Your document style** | tailor_cv.md, tailor_cover.md |`, role angles
  renumbered to 4 and the rubric to 5
- "the foundation the other three read" -> "the others read"
- five occurrences of "eight" files -> "ten"; "none of the four is optional" -> "the five";
  "Everything the four steps do" -> "the five steps"; "do not start a fourth step" and "rather than a
  fourth step" -> "a sixth step"; "skipping step 4 means no rubric" -> "step 5"

**Why. `setup-prompts` owns `tailor_cv.md` and `tailor_cover.md` and nothing ever ran it.** Yesterday
it was added to `DIRECTOR_STEPS`, which put its instructions into the director's system prompt — and
that was not enough on its own, because the director's own text said there were four steps, listed
four in a table, and gated on eight files. Five instruction blocks against prose describing four.

**And the gate could not have caught it either way.** `setup_files()` is `SEED_FILES` minus
`SHIPS_USABLE`, and both writing prompts were in `SHIPS_USABLE` — so `missing_setup()` never checked
them and `list_profile_files` reported them `filled=True` unconditionally. The director could verify
all day and be told they were fine while both still said `<<NAME>>`.

Both now leave `SHIPS_USABLE`, and `<<NAME>>`, `<<CONTACT>>`, `<<LANGUAGES>>`, `<<WORK_ELIGIBILITY>>`
and `<<HOME_LOCATION>>` join `PLACEHOLDER_MARKERS`.

**The comment that argued against this is replaced, because its premise was measurably false.** It
read: these are filled at build time from one authoritative source, so baking values in would put
contact details in two places where they go stale. Measured on the live platform 2026-08-12, that one
source was already two and they already disagreed — one account's typed name lacked the accent its
profile carried, its three typed links were one link in the profile, and the other account's profile
held an email and phone it had never typed.

**Consequence, stated because it is user-visible**: the second account's setup now reads as unfinished
and `may_train` goes false for it, because its two writing prompts genuinely still carry placeholders.
That is accurate rather than a regression, and it is what prompts the step to be run. Training is not
blocked — the trainer's gate short-circuits on `user.profile`, which both accounts have.

Three guards, all failing against the previous state:

- `test_the_writing_prompts_count_as_setup_files` and
  `test_a_writing_prompt_still_holding_a_substitution_marker_is_unfilled`
- `test_the_file_count_the_director_states_is_the_real_one` — the prose said "eight" in five places
  while the code decided the number. Proven by setting it back to eight.
- `test_the_draft_accounts_for_every_file_the_trainer_gates_on` already existed and passed only
  because those two files were exempt. Removing the exemption is what gave it teeth.

**Two things I got wrong on the way, both worth recording.** The one-line `DIRECTOR_STEPS` change was
reported as the fix and was half of it. And the proposal listed six prompt edits where there were
nine — the count "eight" appeared in five places, not one.

## 2026-08-11 (night) — the health check, 18.8 seconds to 1.0 at fifty accounts

ROADMAP **6be**, and the drill was run before anything was touched: a scratch database at **50
accounts, 53,000 corpus rows, 2.65M `user_job_state` rows**, matching production's ratio *and* its
distribution — 93% of live state rows satisfy `notified_at IS NULL AND retired_at IS NULL`, so the
fixture is not a flattering one.

| | before | after |
|---|---|---|
| `user_health`, 50 accounts | **18.83 s** | **1.00 s** |
| queries | 351 | 156 |
| queries per extra account | 5.0 | 3.0 |

Against `health.service`'s `TimeoutStartSec=60`. **The monitor was on course to be the first thing
the platform's own growth broke** — the failure `OPERATIONS_PLAN.md` §1 is about, and one this
project has now hit twice in a day from different directions.

**Correcting my own estimate**: the earlier note said "order thirty seconds" at fifty accounts. The
measured baseline is 18.8. The cliff was real, the number was not, which is why rule 4 says to
measure rather than to argue.

Three changes, in the order they were measured:

1. **`PlatformWindows`.** `job_max_age_hours` and the source floors are platform settings, identical
   for every account, and `filters_for` read both *per account* — 2N queries with one answer. Read
   once and passed down. Not a cache: nothing is stored and nothing expires, so there is no second
   copy to drift (§1). Applied to all three looping callers.
2. **Last delivery and proxy status** were a query each per account; both are now one grouped query
   for the whole platform.
3. **`ix_ujs_owed`, which is the whole of the 9.5 → 1.0 second step.** `ix_user_job_state_pending`
   cannot answer "has this account anything owed": 95,203 of 101,903 live rows satisfy its predicate,
   so it selects 93% of the table and the planner ignores it, correctly. `score` is the selective
   column — 30,442 of those rows are `score_lane.skip()` tombstones with no score — so
   `(user_id, score) WHERE … AND score IS NOT NULL` turns the common answer, *nothing owed*, into a
   seek that reads no corpus rows at all.

**One behaviour change, said plainly.** `pending_matches` stops counting at
`PENDING_MATCH_CEILING = 200`. Its only two consumers are a `> 0` test and the number in the
`silent_feed` message; nothing in the panel reads it, and nobody acts differently on 400 than 4,000.
The reason to bound it is that the cost was otherwise **proportional to how badly delivery was
broken** — an outage leaves thousands owed per account, and the monitor that should report it becomes
the thing that times out. The alert renders `200+` rather than `200`: a number that is really a floor
and does not say so is what an operator plans against.

`_proxy_status` went with the change that replaced it (§3).

**The guard is a relationship, not a stopwatch.** The new test counts queries at two account counts
and fails if the per-account cost grows — timing would be flaky and would not say what broke. It
fails at 5.0 queries per account against the previous code and passes at 3.0.

## 2026-08-11 (evening) — matches destroyed two minutes after we found them

**Reported as a delivery failure. It was the retirement sweep, and the code carried a comment
asserting the opposite.** Found from the live notifier log after the alert proved unactionable.

`retire_stale_matches` read a flat `job_max_age_hours` — the operator has it at **6** — against
`coalesce(posted_at, first_seen_at)`. The pre-score filter admits a posting against its **own source
floor**: 48 hours for Adzuna, 168 for hiringcafe, 24 by default, because a six-hour window switches
most boards off entirely rather than raising the bar (6bf). So the filter let those postings in on
purpose and the sweep destroyed them.

Measured on production over 48 hours — all 21 retirements the panel was reporting:

| source | retired | avg minutes from first seen → retired |
|---|---|---|
| adzuna | 13 | **2** |
| hiringcafe | 8 | 8,855 |

**Two minutes.** Found, scored, and written off before the notifier could ever send them — while the
log line blamed the notify lane: *"the notify lane did not keep up"*. It had; it delivered 1–7 items
regularly through the whole window. Every source except LinkedIn has a floor above six hours, so this
reached **every account**.

Whether a given match survived was a **race**. Delivery applies no age filter at all, so if the score
lane's own notify job ran first the posting went out — one was 27 hours old and fine — and if the
sweep ran first the same row was destroyed.

**The comment is why it survived a year of reading.** It said: *"The same clock the pre-score filter
reads, so 'too old to score' and 'too old to send' cannot mean two different things."* It read a
different clock, with a different window, and no handling of a date-only `posted_at`. §5's defect with
a reassurance attached is worse than the defect alone.

**The fix removes the second number rather than correcting it.** `too_old_to_send()` is defined as
`~_fresh_enough()` — the negation of the admission rule — and `retire_stale_matches` now takes the
account's `filters` instead of an hours count, so passing a bare window is no longer expressible.
Two tests; the first fails against the previous code with the message naming the defect.

**Three more, found looking for the first:**

- **`pending_matches` never adopted the shared conditions.** `pending_match_conditions` was written on
  2026-08-08 after an account showed "99 matches waiting" with all 99 retired; the notifier was
  switched to it and `libs/ops/health.py` was not. So the page that pages an operator kept counting
  work the notifier had correctly abandoned — the reported "2 matching job(s) waiting" on a healthy
  account. One caller fixed, one forgotten, is how the same defect gets found twice.
- **The alert's remedy named a log line that does not exist.** It said to read the notifier's
  `dropped {...}` line; nothing in the notifier logs `dropped`. The first step of the runbook was a
  dead end, which is most of why the alert went unactioned. Replaced with the two lines that do exist.
- **Two alert texts still described the 90-day retention trim**, replaced that morning by the 30-day
  archive. Same-day drift, in the commit that renamed the thing.

Also here: `dispatch_due_notifications` now selects whole `User` rows rather than ids, because
`filters_for` needs the account's own limits — one fewer per-account fetch in a loop that has too
many (6be).

## 2026-08-11 (late) — the four sinks, closed

ROADMAP **6bd** and **6c**. Three mechanisms existed to drain what grows, all three were implemented
and tested, and **none of them had a caller**. Two of the three turned out to be the wrong mechanism
rather than a missing call, which is why this is a replacement and not a wiring job.

**Retention archives a posting whole; it no longer nulls it in place.** `trim_cold` set
`description`, `embedding` and the embedding metadata to NULL and kept the row. The owner's
objection — losing data we already have is illogical — was right, and the cost was worse than it
looked: `services/trainer/train.py` rebuilds every training set by joining `user_labels` back to the
corpus and reading the embedding, `WHERE embedding IS NOT NULL`. **A trimmed row therefore left every
future retrain silently** — no error, just a smaller training set and a worse model. Nothing had ever
been trimmed on any box, which is the only reason this never happened.

`libs/corpus/archive.py` replaces it: rows past `CORPUS_RETENTION_DAYS` (**30**, set by the owner;
the constant it replaces said 90) are written to an append-only gzipped JSONL file and then deleted.
**Write, then delete** — if the write raises, nothing is removed and the next pass retries, so a full
disk cannot cost a posting. Columns come from the mapper rather than a hand-written list, so a new
column cannot be dropped from the archive unnoticed. The one exclusion is `embedding`: the vectors
are the bulk of the 386 MB and are reproducible from the description, which is kept.

**`pinned` is gone, and it is the more interesting removal.** It was a denormalised copy of "somebody
labelled or acted on this", and **two of the three places that write a label never set it** —
`libs/feedback/actions.py` (a Telegram tap, weight 5×) and `services/labeller/label.py` (the bulk
labeller, most of every training set). So the majority of every user's training data sat on rows the
retention code considered disposable. The archive asks `user_labels` and `user_job_state` directly
with `NOT EXISTS`. A derived answer cannot drift from what it describes; a copy always can (§5).
`trimmed_at` went with it — nothing would have written it again, and a column with no producer fails
`tests/test_columns_have_producers.py`, which is what forced the cleanup to be complete.

Runs on the harvest tick, after the follow-up enqueue, bounded at 1,000 rows a pass. **No new
timer**: the corpus is what grows, that cycle already touches it, and a second clock is a second
outage (§6). A failure there is caught and logged at ERROR rather than losing the cycle's intake.

**`prune_model_artifacts` was two defects, not one.** It had no caller, *and* it deletes the `models`
rows while only returning the paths — so even called, it frees no disk. Now invoked at the end of
`train_user`, the only thing that creates artifacts and so the only thing that can know one is
surplus, with the caller doing the unlinking. Four versions and 34 MB had accumulated for one account
in a fortnight, growing per retrain forever. The new test asserts **two files on disk after three
retrains**; it fails with three against the previous code.

The 24 MB belonging to a **deleted** account cannot be reached by that function at all — `user_id` is
`ON DELETE CASCADE`, so the rows went and the files stayed, leaving nothing to prune from. That is a
one-off shape, so it gets `deploy/scripts/prune_orphaned_artifacts.py`, dry-run by default.

**`mark_dormant` was deleted rather than wired**, on the owner's instruction, and the latent bug it
was hiding is the argument for it: it set `status='dormant', active=False`, and the login path
restored only `status`. A returning user would have read as active while `active_configs()` — which
requires both — silently left them out of every harvest. A permanently silent account, unreachable
today only because nothing ever called the sweep. Gone with it: `reactivate()` (a duplicate of the
inline login flip), `touch_activity()` (a third writer of a column two live paths already write),
`DORMANT_AFTER_DAYS`, and the `dormant` status itself, with a migration for the CHECK constraint.

What replaces it is a number and a person: `last_active_at` is now in the `/admin/users` payload and
rendered as "45 days" in the accounts list, beside the delete control that already existed. Nothing
acts on it. Four tests that used `status="dormant"` as shorthand for "not eligible" now say
`suspended`, which is what actually gates them.

**`user_job_state` is drained by the same pass, under one rule**: a row survives while its posting
does, or because it records something that account's own user did — applied, built a cover letter,
dismissed, or labelled. Everything else (`score`, `scored_at`, `notified_at`, `retired_at`) is
working-out the platform can reproduce, so nothing is archived here; there is nothing to lose.

The half that archiving a posting does **not** cover is the one that made this a sink. A posting one
account labelled stays in the corpus permanently and correctly — and every *other* account's score row
for it stayed too, forever, with no reason to. That is growth in accounts × kept postings rather than
in the retention window, and it is how the table reached 88,041 rows for two accounts in a fortnight.
`forget_stale_state` removes exactly those. The invariant is asserted as a relationship: after a pass,
no row exists that is both past the window and records nothing.

The 30,442 `score IS NULL` rows are unaffected as tombstones inside the window — they are
`score_lane.skip()`'s record that the filter already judged a posting, and a test pins that they do
**not** keep a posting alive.

**One index, with the number that justifies it.** Retention asks "does *any* account have a label on
this posting", a lookup by `job_id` alone — and `job_id` is the *second* column of `user_labels`'
primary key, so the PK cannot serve it. Measured on a scratch database loaded to 50,000 postings,
100,000 state rows and 2,000 labels: **21.2 ms → 10.6 ms**, with the plan turning a sequential scan of
every label into an index-only scan. `ix_user_labels_job` in the same migration.

**Accepted risk, confirmed by the owner:** an archived posting that gets re-discovered re-enters as
new and is embedded again. At 30 days no source's freshness window reaches back that far, so it is
unlikely rather than prevented.

## 2026-08-11

**A harvest cycle now says when it is close to its budget, while the margin still exists.** The
cycle already had a deadline (`HARVEST_CYCLE_BUDGET_SECONDS`, 11 minutes), but it was only handed to
`embed_all_pending` — nothing compared the *cycle's* own duration against it. Past the budget
`harvester.service`'s `TimeoutStartSec` kills the run with the stages behind the overrun skipped
rather than retried, and nothing raises, so the failure is a corpus that quietly stops growing.

**Why now, and the numbers it was set from** (ROADMAP 6az). The ATS backfill took the polled board
list from 337 to 641 in one step and the cycle went from 30.7 seconds to a peak of 8.4 minutes —
76% of the budget, 2.6 minutes of margin, and the only evidence any of it happened was that the
corpus grew. The cycle after it fell to 5.6 minutes (51%). `HARVEST_CYCLE_WARN_FRACTION = 0.75` is
the value that reports the one cycle worth knowing about and stays silent for both routine ones; a
threshold that fires on the ordinary case is a line an operator learns to skip past, which is the
same as no line.

**Not a second clock** (CLAUDE.md §6). The fraction multiplies the budget that is already there
rather than naming a duration of its own, and both live in `libs.settings` beside each other, so
raising the budget raises the warning with it. It is a module constant and not a platform setting on
purpose: the budget it is a share of is not operator-facing either, and making the alarm tunable
while the thing it watches is not would be the two-sources-of-truth shape §5 is about. If the budget
is ever exposed, this goes with it.

Two tests, and the first fails against the code that made it necessary: a cycle at the measured 8.4
minutes warns and carries its own duration, and a 30.7-second cycle does not.

**`out/` is ignored.** `3dbe1b5` committed a rendered CV by accident. Generated documents are one
real person's contact details and whole career, and regenerable from the profile that produced
them — the same two reasons `scripts/coverlab/` is ignored.

## 2026-08-09 (late)

**The cover prompt no longer receives a text extract of the CV.** Approved explicitly by the owner,
A/B'd before adoption, previous version kept at
`libs/scaffolds/history/tailor_cover.2026-08-09.pre-cv-block-removal.md` (CLAUDE.md §3a). The two
accounts' own `tailor_cover.md` were edited in place by the same rule, matching each file's own
wording rather than being overwritten with the scaffold.

**Why it was redundant.** The system prompt for a cover call is already `libs.profiles.grounding` —
`context.md`, `01-candidate-profile.md`, `02-behavioral-profile.md` and `05-cv-templates.md`,
verbatim. For one account that is 43,000 characters carrying every job, project, grade and skill
they have. The `<CV_HTML>` block repeated a *subset* of that as 1,500 characters of tag-stripped
`base_cv.html`, cut mid-sentence before a single experience entry, under a heading announcing
itself as the candidate's CV. Enlarging it to the whole CV was considered and rejected: that is
~6,000 tokens a call to say what the system prompt already says.

**The number, and it is not the obvious one.** Variant `k` is variant H with those two lines
removed and nothing else, over the same 8 postings, judged by the same Sonnet rubric:

| measure | with the block | without | t (paired, n=8) |
|---|---|---|---|
| judge /100 | 40.0 | 43.5 | +1.04 — **not significant** |
| judgement findings | 30 | 18 | **-2.39** |
| `CV_LIFT` | 6, in 5 of 8 letters | 1, in 1 | **-2.38** |
| `TELLS_THE_MATCH` | 4, in 3 letters | 0 | -1.87 |
| mechanical findings | 13 | 11 | -0.61 |

The judged score is a wash and is reported as one — 4 postings better, 4 worse, the +3.5 mean
carried by two outliers. What holds is the deterministic count: the block was labelled "facts only
(do not copy wholesale)" and the model copied out of it wholesale in five letters of eight. Remove
the extract, and the lifting goes with it. That is the justification, not the judged score.

`services.docgen.build.CV_TEXT_MAX` and `html_to_text` are deleted with it — the truncation
constant was the defect, and the function had no other consumer in the product (§3).

## 2026-08-09

**The cover-letter prompt changed, for the first time under measurement.** `libs/scaffolds/
tailor_cover.md` is now variant H from the cover-letter bench. Approved explicitly by the owner,
A/B'd before adoption, and the version it replaced is kept at
`libs/scaffolds/history/tailor_cover.2026-08-09.pre-H.md` (CLAUDE.md §3a).

**The number:** +8.4 judged points over the previous prompt across 16 paired letters, t=4.19,
14 of 16 improved. Anecdote 3.0 → 7.0, human voice 3.9 → 5.6, warmth 3.0 → 4.7. Zero invented
figures in either arm. Judged by a hiring-manager rubric scoring two gates in order — does it pass
a keyword screen, then does a person open the CV — with the candidate's `not_list` supplied so an
honest gap is not marked down as a weak letter.

**What H actually changes** is placement, not content. The rules it moves already existed in the
prompt: the filler ban, the weak-close ban, the instruction not to reuse CV phrasing. H states each
one *inside the paragraph it governs* rather than in a preamble, and deletes the free-floating copy.
Nothing was added.

**Why that works, and where it stops.** Four variants tested the idea and two refuted the easy
version of it:

- Moving a **prohibition** into its slot reliably produces compliance. `WEAK_CLOSE` went to zero
  across sixteen letters, `BANNED_PHRASE` 15 → 3, and in a separate arm `LONG_SENTENCES` 11 → 5.
- **Compliance is not quality.** Variant I moved a 25-word sentence cap into every paragraph, hit
  its target exactly, and produced worse letters — obeying it cost content, and warmth and close
  strength fell with it.
- **It does not generalise to positive instructions.** Variant J moved the ATS keyword rules in
  ("mirror the posting's terminology"). The targeted dimension did not move, and `CV_LIFT` — text
  copied verbatim from the CV — went from 4 to 17 across two runs. Telling the model to use their
  words pulls it toward copying.
- An earlier arm added six literal search-and-fix passes and was the **worst** of four: it nailed
  the two rules it named and broke rules it had not touched, including one the old prompt never
  broke. Attention is finite.

So the rule worth carrying to other prompts is narrow and worth stating precisely: **state a
prohibition where the decision is made, not in a preamble — and do not assume the technique
transfers to instructions that ask for something rather than forbid it.**

**Reach.** `docgen` reads each account's *own* `tailor_cover.md` and falls back to the scaffold only
when there is none, so this reaches **new accounts**. Existing accounts keep their own copy until
`setup-prompts` rewrites it.

**The bench** lives in `scripts/coverlab/` and is deliberately not committed: candidate prompts
sitting beside the real one is the two-copies failure CLAUDE.md §1 names, its fixtures are one real
person, and its output is regenerable. `libs/scoring/coverchecks.py` is the tracked half — the
measurement, so the numbers above are reproducible from something in the repository.

---

## 2026-08-08

**The credential blocks read field → what is attached → how to get it, and the buttons say which way
they go.** Front end, plus the one route the delete button needed. Onboarding and Your details share
these components, so both pages moved together.

**The Save button was invisible until you typed.** `.advance` is `opacity: 0` until a form marks
itself ready, and `CredentialForm` used it — so the bot token, the Claude key and the proxy URL each
had a submit button that could not be seen from an empty field. There is nothing to fade in *to*
here: the field is the whole form. Now an ordinary `secondary` button, present and disabled, matching
**Connect my chat** beside it, and labelled **Connect my bot** for Telegram.

**Order, per block:** the field, then what is already attached, then the how-to fold. It was the
reverse, which put a fold of instructions in front of somebody who already had the value in their
clipboard.

**The chat and channel IDs are rows, not a sentence.** They were a green line of prose, copyable only
by selecting the text. Both are now the same `.stored` row the secrets use. An id is not a secret, so
copying one costs no password. Deleting one does cost something, so it is confirmed the way a
credential is.

- **New: `DELETE /credentials/telegram/chat`.** The channel already had one; the chat did not, so the
  delete button had nothing to call. It clears the id *and* drops `telegram_status` back to
  `unconfigured` — a row still claiming "connected" with no chat is the silent failure, so the test
  asserts both together rather than the id alone. No fallback, unlike the channel: removing the chat
  stops commands and CV delivery until another is connected, which is what the confirm text says.

**Red removes, green adds, the tick is a yes.** The delete control was `−` in `--muted`, the same
weight as the copy button beside it: two grey glyphs, one of them destructive. Removal is now a cross
in `--danger`, adding a row a plus in `--score`, and the copy-confirmation tick turns `--score`.
Every credential and the links list, not only Telegram.

**Removed: "Read it for me".** The button that re-read the chat id from `getUpdates` after pressing
Start. Saving a bot token already captures the chat id on the same path (`put_credential`) and the
box beside it takes the id directly, so it was a third way to set one value. `POST
/credentials/telegram/connect` is left in place and now has no caller.

---

## 2026-08-07 (late)

**A CV is rendered once, a dead posting stops costing anything, and the Build button answers
faster.** No prompt, model or threshold was touched.

**One Chrome launch per document.** Measured on the live box: a Chrome launch is **17.5 s cold and
~6 s warm**, and a build did **two or three** of them — a probe render to count pages, and then a
final render of *the same bytes*. Now every check that can be answered from the markup is answered
first, the document is rendered once, and the file that gets delivered is the one that was measured.
A CV that fits is one launch; only a CV that came back too long is two. **~6 s off every build.**

**The revision rate is now counted.** `docgen.built` carries `revised`, and `BuildResult.revised`
with it. Each revision is a second frontier call and a second render, so how often it fires is the
number that says whether the fix belongs in the prompt — which is the owner's call, not this
commit's.

**A posting that has closed stops costing us.** The describe fetch was always going to happen, so it
now reads whether the posting is still live off the same response — `404`/`410`, or the page saying
so — and marks the row closed. Deliberately not `403` or `429`: a board refusing us is not a posting
ending, and treating it as one would hide live jobs. This works for every source now; before today
only the LinkedIn metadata pass detected it.

And a closed row is no longer re-fetched or re-embedded: `awaiting_description` and
`awaiting_embedding` both had no `closed_at` condition, so a posting we knew was dead still cost a
proxy fetch (the user's own metered bandwidth) and ~0.37 s of the shared embedder. `docgen`'s
pre-build refresh reads the column before it spends a fetch, too.

**The Build button answers faster.** Two things sat between the tap and "Building your CV":
`ProfileFiles.is_ready()` loaded **every profile file — ~180,000 characters** — to compute one
boolean, inside the webhook Telegram was waiting on; and the docgen worker then idled up to **5
seconds** before claiming the job. `is_ready` now reads the one file the answer depends on
(`context.md`, which is exactly what `grounding` refuses on) and the interactive lanes — `docgen`
and `setup` — poll at 1 s via `INTERACTIVE_IDLE_SLEEP_SEC`. **~5 s off the tap.**

Deliberately *not* the `filled` column, though it would have been faster still: it is derived, it
has drifted in production before (`setup_routes` repairs it on page load), and a stale flag would
refuse a build for somebody whose profile is finished.

**CLAUDE.md §3a: prompts are sacred.** Written down at the owner's instruction — no prompt change
without explicit confirmation, covering `libs/prompts/`, `libs/agents/*.md`, `libs/scaffolds/` and
every per-user file (`base_cv.html`, `tailor_cv.md`, `tailor_cover.md`, `label.md`, …). Those are
tuned per person for the quality of their CV, and the label template's hash is every user's
`profile_version`.

Gates: `lint-imports` 2 kept; `ruff check .` clean; `alembic upgrade head` from empty; **1,371
passed, 22 skipped**; contract unchanged; `npm run build` clean. The render-count, closed-posting
and check-ordering tests were each verified to fail against the code that made them necessary.

## 2026-08-07 (night)

**Removing the age check from delivery released a 19-day-old backlog, and this is the guard that
replaces it.** Delivery still filters nothing. The queue is *maintained* instead.

What happened: the delivery query's freshness condition went on the owner's instruction — a posting
is judged once, before scoring, and everything after executes. Correct, and it left one gap: nothing
between scoring and sending looks at age, so a queue that stops draining keeps ageing. There was
already an 832-row queue behind the old per-run cap, **every row more than three days old and the
worst 37 days**, and on deploy it began going out. 92 messages at a mean age of **19.4 days** before
the lane was paused.

The two mechanisms the owner asked about were both correct, and the numbers say so: in the hour
after the deploy the score lane admitted **542 rows, all under 24 hours**, and the harvester's 5,047
rows over 24 hours had a single posting older than three days. Neither was implicated.

So: `user_job_state.retired_at`, written by the notifier's sweep once a minute. A match that ages
past `job_max_age_hours` while waiting is one the lane failed to send in time — sending it late is
worse than not sending it — so it is written off, counted, and logged at WARNING with the account
and the window. Delivery skips retired rows and is otherwise untouched.

`retired_at` rather than `notified_at`, deliberately: a row we failed to send is not a row we
delivered, and writing one as the other would corrupt every delivery count on the platform. The
pipeline panel's `expired` now reads this column instead of re-deriving the condition — it counts
what somebody actually gave up on, which is the honest version of the number the roadmap has been
calling expired since it first counted 576 of them.

## 2026-08-07 (evening)

**The language filter has never run, and now it can** — plus the two remaining bounds from the
pipeline audit.

**`corpus.language`, read once at describe time.** Detection used to run inside
`UserFilters.unwanted`, on the description, **once per candidate per account per scoring pass** — the
same posting re-detected for every user and again on every retrain. It is a property of the posting,
so it is stored beside `country` and `workplace`, indexed, and backfilled in the migration. The
filter compares a two-letter code instead of running `langdetect` over a 20,000-character
description. `fill_missing_languages` runs once a cycle for rows described before the column
existed — no network, bounded, and it disappears when the backlog does.

**And nothing could set the languages.** `user_config.languages` was in the schema and in the
onboarding *type*, with **no control anywhere in the app** — so the set was empty for every account,
`is_allowed` returned True on an empty set, and a Hungarian mechanical engineer received German
postings he could not apply to. There is now a picker on the config step, built from the schema's own
`Language` enum, so a new code cannot exist on one side only.

**Labels are no longer bought for postings the account could never read.** `select_pool` applies
`UserFilters.unwanted_for_training` — the *content* half of the pre-score filter: language, and the
terms the user asked never to see. Not the circumstantial half: how many have applied, whether it is
on site, or how old it is say nothing about whether a posting is a good training example, and
filtering on them would thin the set for no gain. Every label is bought on the user's own Claude
credential, so a posting they can never receive is spend.

**The embed step stops on a clock.** It was bounded by `max_batches=50 × 60 rows` — a row count, so
at ~0.9 s a row it can legitimately run forty-five minutes against a unit systemd kills at fourteen.
And a killed cycle does not just lose embeddings: `describe`, the language pass and
`_enqueue_scoring` all sit *after* it and never run, silently. `HARVEST_CYCLE_BUDGET_SECONDS` (11
min) is now derived once in `libs.settings`, passed into `embed_all_pending` as a deadline, and
`test_settings_are_wired.py` fails the build if it ever exceeds the unit's `TimeoutStartSec`.

**`tests/test_settings_are_wired.py`** is new, and is the guard that would have caught the window
defect: every key in `settings.DEFAULTS` must be read by something, and must have a control in the
operator panel. Both checks pass today only because `job_max_age_hours` got both.

Gates: `lint-imports` 2 kept; `ruff check .` clean; `alembic upgrade head` from empty; **1,361
passed, 22 skipped**; contract regenerated; `tsc --noEmit` and `npm run build` clean.

## 2026-08-07 (later)

**Delivery stopped judging, and the age window became the operator's** — the owner's rule, applied:
the pre-score filter is the only place a posting is judged against an account, and everything after
it executes.

**`services/notifier/freshness.py` is gone**, and with it the pre-send liveness refresh. It fetched
up to twelve LinkedIn pages on the user's proxy *inside the notify claim*, costing 13–24 s of every
~80 s run and reserving 30 s of the claim whether it used them or not. The harvester already does
this work: `CorpusRepository.awaiting_metadata` + `describe.refresh_metadata`, on the proxied pass,
where a page fetch is the unit of work. So delivery now opens no HTTP client at all, and its send
budget is the whole claim rather than what the refresh left: **54 s → 72 s, and 14 items per run →
18** at the default four-second gap.

**Delivery filters nothing.** `candidate_conditions` is out of the delivery query and
`eligibility.is_eligible` is deleted. What a user may receive was decided once, before scoring; the
notifier ranks what is above the cutoff and sends it. `eligibility.py` is now one function, `rank`.

**The age window is a platform setting** — `job_max_age_hours`, default 24, with a control in the
admin panel beside the delivery cadence. It was `DEFAULT_MAX_AGE_HOURS = 24` in
`libs/tenant/filters.py`, reached through `section["posted_within_hours"]` — a key the 2026-08-01
migration **stripped out of every stored config**. So the list was always empty, the fallback always
fired, and every account's scoring and delivery window was a constant no user and no operator could
change, sitting next to a freshness control in the panel that governs only what is fetched.
`UserFilters.from_config` now requires the window from its caller and defaults nothing.

**One construction site for the filter**: `libs.tenant.filters.filters_for(session, user)` resolves
the operator's applicant cap and window together, for the three callers that ask (score lane, health
page, pipeline panel). They each built their own before, and the window was the thing they were all
getting wrong.

**`libs.ops.health.deliverable_window` deleted** — no consumer, and it answered from
`posted_within_hours`, which no longer exists in any stored config.

Gates: `lint-imports` 2 kept; `ruff check .` clean; `alembic upgrade head` from empty; **1,355
passed, 22 skipped**; `openapi.json` unchanged; `tsc --noEmit` and `npm run build` clean.

## 2026-08-07

**The delivery continuation stopped waiting for the dispatcher, and four thresholds got one owner
each** — the first batch out of the pipeline audit.

**A run that fills its limit now queues the rest itself.** `_deliver_again` set `users.next_run_at`
and nothing else, so the continuation depended on the dispatcher's periodic pass — which runs once a
minute. A run is ~80 seconds of work; up to 60 more were spent idle with a queue waiting, on every
continuation. It now releases its own `due` slot on the mapped row (the same move
`services/scorer/__main__.py` already makes) and enqueues directly, keeping `next_run_at` as the
backstop for the case where the enqueue collapses against somebody else's job. Measured effect on
the drain rate: 14 items per ~140 s becomes 14 per ~80 s.

**`libs.labels.POOL_SIZE` is gone.** It was 300 beside the `label_pool` catalogue entry's 300 — the
cold-start target an operator sets in the admin panel, restated as a constant nothing would update.
`pool_target()` reads the catalogue, `target_for(user)` resolves the account's own ceiling, and the
processing page now reports that ceiling rather than the platform default.

**Two thresholds that both read as "enough labels to train" were named for what they each decide.**
`services.labeller.coldstart.MIN_LABELS_TO_TRAIN` → `LABELS_BEFORE_FIRST_FIT`, **and raised from 100
to 200**: it is the point where a cold start has bought enough to be worth its first fit.
`services.trainer.train.MIN_LABELS` → `MIN_LABELS_TO_FIT`, still 25: the floor below which a fit is
refused outright. They are different questions, they cannot import each other, and reading either
name told you nothing about which one you were looking at.

`ready_to_train` also clamps to the account's own `label_pool`. An operator lowering that below 200
would otherwise leave the account permanently one label short of a model it is not allowed to buy
its way to — no error, no page saying so.

**Two dead constants deleted.** `services.notifier.eligibility.DEFAULT_APPLICANT_CAP` had no reader
(the applicant cap moved to the pre-score filter; the test that looks like a user imports the `libs`
one under that alias). `services.notifier.deliver.MAX_ITEMS_PER_DIGEST` was a "10 per digest" rule
that has not been in force since `items_per_run` went unlimited — `deliver` always passes a computed
limit — so `pending_feed` now requires one.

Three new tests, each failing against the code that made it necessary: the continuation is *queued*
rather than merely due; an account allowed fewer labels than the constant can still train; and the
label-pool default is read from the catalogue rather than restated.

Gates: `lint-imports` 2 kept; `ruff check .` clean; `alembic upgrade head` from empty; **1,358
passed, 22 skipped**; contract regenerated with no change to `openapi.json`; `npm run build` clean.

## 2026-08-06 (late night)

**One line per comment, across the whole tree** — the owner's instruction. Every module, class and
function docstring is now a single sentence, every comment block is one line, and the reasoning that
used to sit above a constant now lives where it is already recorded: this file, the roadmap, and
`git blame`.

The scale of it: **20,100 lines removed, 6,900 added, across 313 files** — `libs/` and `services/`
first, then `tests/`, `deploy/scripts/`, `scripts/`, `web/src/` and `web/scripts/`. Comment and
docstring lines were 39% of `libs/` before this.

Nothing about behaviour changed, and two checks say so rather than one:

- Every non-docstring string literal in every changed Python file is byte-identical before and
  after — prompts, log messages, SQL, error text. The one file that differs is `ats.py`, and that is
  the dead code below.
- `web/dist/assets/index-CYtWZgGD.js` — **the same content hash as the build before the pass.** The
  bundle is byte-identical, which is what a comments-only change to a TypeScript app should produce.

**Three things deleted for having no consumer** (`services/harvester/sources/ats.py`, 46 lines):

- `harvest_from_urls` — recorded ATS tokens found in apply-URLs from any source. Nothing has called
  it since the port. The place it would plug into now is `services/harvester/describe.py`, which
  resolves `application_url` on every described posting; if that is wanted, this is the function to
  restore rather than rewrite.
- `extract_tokens`, `_URL_PATTERNS`, `_NOT_A_TOKEN` — the pattern table `harvest_from_urls` read,
  and its only caller. Dead with it.
- `FETCHERS` — an ats-name → fetcher map with no reader. The `gather_*` wrappers pass
  `fetch_company=` explicitly, so this was a second copy of that routing table that nothing
  consulted and nothing would have failed on if it had drifted.

**One dead test double deleted** — `DatingClient` in `tests/test_ranking.py`, 18 lines of stub
client that nothing constructed. The section it belonged to now tests dating through
`postings.parse_posted_at` directly.

**Four tombstone comments deleted** — "`fetch_easy_apply` was removed on 2026-08-05", "`profile_context`
was here", "Location is not scored here any more", "Removed 2026-08-05, and the removal is the fix".
A note about something that is gone is the same maintenance burden as the thing itself; the removals
are in this file and in the history.

**Not touched: the ported source adapters** (`services/harvester/sources/{http_apis,hiringcafe,jobspy_source}.py`).
They are lifted from the reference instance and deliberately kept diffable against it — the reason
`pyproject.toml` exempts that glob from E501. They were slimmed and then reverted; `ats.py`,
`base.py` and `__init__.py` in the same folder are this platform's own code and were kept slim.

**Not touched: `libs/db/migrations/versions/`.** A migration is a record of what was applied, which
is why `pyproject.toml` already excludes it from ruff.

Gates, locally, all six: `lint-imports` 2 contracts kept; `ruff check .` clean; `alembic upgrade
head` from empty on a throwaway database; **1,355 passed, 22 skipped** with `DATABASE_URL` set;
the OpenAPI dump and `api-types.ts` regenerated — 72 endpoint descriptions shortened, **no type
changed** (every line of the 482 removed from `api-types.ts` is a comment); `tsc --noEmit` and
`npm run build` clean.

## 2026-08-06 (night)

**One filter, once, before scoring** — the owner's instruction, and it turned out to be the cause of
three separate symptoms.

The applicant ceiling, workplace mode, language and the freshness window all ran **at send time**, in
`services.notifier.eligibility`, while the pre-score filter knew only about countries, freshness and
excluded words. So a posting an account could never receive was still embedded against their model,
scored, counted as *waiting* by the health page and by the radar — and then dropped in the last
second before sending, by a filter neither of those pages knew about.

That is one question with two answers, which is the defect this project has shipped more than any
other, and it produced:

- **A permanent health alert.** *"1 matching job waiting and last delivery was 2h ago"* on the second
  account, every cycle. The notifier's own log said it: `dropped {'workplace': 1}` then `notified
  with 0 item(s)`. The count and the send disagreed, and the count was the one an operator read.
- **A delivery queue that would not drain.** 395 rows above cutoff, of which `dropped
  {'applicant_glut': 51, 'excluded_keyword': 4, 'workplace': 2}` every run — the queue was mostly
  postings that were never going to be sent, so the radar's "waiting" was counting work that did not
  exist.
- **Wasted model calls**, on every posting the account had already ruled out.

All four checks now live in `libs.tenant.filters.UserFilters.unwanted` and run in the score lane, so
a row that fails them is skipped before the model sees it and never counted as waiting. Delivery has
one check left — `closed`, learned from the posting's own page seconds before sending, which is the
only thing no earlier filter could have known.

`Eligibility`, `is_eligible`'s preference half and `too_old` are deleted in the same commit, and the
tests that covered them moved to the layer the behaviour moved to rather than being dropped — the age
window's tests are now against a real database, because it is a SQL condition now.

**`scripts/rejudge_scored_rows.py`** re-applies the filter to rows scored under the old arrangement.
Without it those keep their score forever: the score lane never revisits a row already carrying the
current model version, so the alert would have outlived the fix.

**And a bug the suite caught in this change**: `UserFilters.from_config` read `applicant_cap=None` as
"not supplied" and substituted the catalogue default — turning an operator's deliberate *Unlimited*
back into 200. `tests/test_limits_are_wired.py` exists for exactly that pattern and found it before
it shipped.

---

## 2026-08-06 (evening)

**Nothing stands between a scored match and Telegram any more**, on the owner's instruction — and
the measurement agreed with them.

**Corrected after deploying — the cause was the wait, not the ceiling.** `items_per_run` defaulted
to 10, which is 960 a day, and that default is wrong for the reason below. But both live accounts had
it explicitly set to `null`, so `resolve` was already returning Unlimited and the ceiling was never
what held them back. The evidence is the throughput shape: raising the default changed nothing, and
re-arming the run took delivery from ~18 every fifteen minutes to ~18 every fifty-five seconds —
**about 24×, measured at 950 an hour against 40 before.** The default is unlimited now anyway,
because 960 a day is below what an account matches and a new signup should not inherit that.

**What actually cost the 576:** A run stops at its claim so a second worker cannot
start the same digest — that stays, it is the fix for the flood-then-silence incident. What was wrong
was what happened next: the remainder waited a full fifteen-minute cadence, so a backlog drained at
one run's worth per cycle while its tail aged past the freshness window. A run that could not finish
now makes the account due immediately and the dispatcher already inside the worker picks it back up.
No second mechanism and no new clock.

`UNLIMITED_ITEMS = 200` is gone. It was a number with no relationship to anything — the shape of a
fallback that swallows a real answer (CLAUDE.md §5). What a run can fetch is now derived from what it
can physically send: its budget divided by Telegram's gap.

**The freshness complaint turned out not to be a filter fault, and the measurement is the answer.**
LinkedIn rows are fetched at **0.6 hours old on average** under the source's own 1-hour window, and
25 of 5,626 lack a `posted_at` at all. Jobs arriving at three hours old had spent **191–207 minutes
in the delivery queue** — the posting was fresh when we found it and stale when we sent it. Same
cause as everything above.

**And one bug in yesterday's radar: the queue has no state called `running`.** It is `claimed`, so
the live view's one moving number counted zero forever — a page built to show whether work is moving,
whose "moving" could not move. Found by reading it against the table the same afternoon.

Measured while checking whether any of this is slow, since it was asked: the per-user filter is
**76–280 ms of SQL** over 30,441 corpus rows and **1.7–3.6 ms of Python** for 500 rows, the score
lane averages **1.39 s** a batch (worst 12.1 s), and notify averages **24.4 s** (worst 82.3 s)
against a 120-second claim. Nothing here needs optimising; the numbers are recorded so that claim can
be checked rather than believed.

---

## 2026-08-06 (later)

**The radar's first day found three of its own numbers lying**, which is roughly the point of
building it — read against the database rather than trusted, per CLAUDE.md §4.

- **The delivery queue's age described a posting the count had excluded.** The count carried the
  account's filters and the age did not, so the page read *142 waiting, oldest 138 hours*: two
  individually correct numbers that together described nothing. They now share a condition list.
- **The scoring queue's age was never computed at all** — initialised to `None` and never assigned,
  so whatever piled up behind the score lane the row stayed green. The one colour that cannot fire
  is the one nobody notices is missing.
- **"Gathered" and "Stored" were one `INSERT` drawn as two stages.** `fetched_at` and
  `first_seen_at` are set by the same statement, so nothing is ever queued between them and the two
  counts could only ever be equal — except across 2026-08-04, before which `fetched_at` did not
  exist, where the page showed *fewer gathered than stored*. Now one row (CLAUDE.md §6).
- **A stage with no queue said `0` rather than a dash**, breaking the page's own rule that zero is a
  claim and a dash is an admission.

**Delivery is now judged on lost work rather than on queue age**, and the measurement changed the
answer. `items_per_run` caps sending at ten a cycle *on purpose*, so a queue behind it is the design
working and an age-based colour would be red every day of the platform's life. What is genuinely
wrong is a posting the model matched, that nobody was ever shown, that is now too old to send —
**measured live at 576 in 48 hours for one account**, against 931 delivered. The cap is below that
account's own match rate, so the tail ages out; the page now says so and names the setting.

**The pipeline streams.** `GET /admin/pipeline/stream`, server-sent events at one second, and the
page shows which counts moved. Polling was right first — it proved the numbers before anything was
built on them, which is how the three above were found — but the platform works in bursts, and
fifteen minutes of quiet followed by a thirty-second drain is exactly the interval where "moving"
and "stuck" look identical unless you can watch it. SSE rather than a websocket: one-way text that
reconnects by itself and needs no library on either side. The queue's own lanes are beside it, which
is the only place a dead-lettered job is visible at all — from the outside, work abandoned and work
never enqueued look like the same empty queue.

**The contract gate failed for a reason that had nothing to do with the contract.** `dump_openapi.py`
let Python pick the line ending, so a dump run on Windows committed CRLF and CI regenerated the
identical document with LF — a 4,642-line diff in which no line had changed. Same defect as
`alembic>=1.13` (ROADMAP 6al): **a gate whose result depends on which machine ran it is not a
prediction of CI, it is a coincidence.** The writer now pins `newline="
"` and a test fails the
build if a CRLF contract is ever committed again.

---

## 2026-08-06

**Where the jobs are, and what the filter removed** — the two numbers nobody was reporting.

The pipeline radar (`libs/ops/pipeline.py`, `GET /admin/pipeline`, an Admin tab) is one row per
stage with how many are waiting in it and how long the oldest has waited. Every outage this platform
has had was a stage quietly stopping while everything around it looked fine — six hours of ingestion
behind an invisible filter, LinkedIn dead for five hours behind a timer that could not fire, 229 rows
stuck awaiting an embed while both health alerts pointed at delivery. **The number existed every
time; reading it meant knowing which query to run.**

Every count is `SELECT count(*)` against the condition the *next* stage actually reads, so a stage
cannot report empty while work sits in it. Where a number is genuinely unavailable it is null and the
page shows a dash — zero is a claim, a dash is an admission. Verdicts are about backlog *age*, not
size, and derived from the tick meant to clear the stage: a thousand rows moving is healthy and forty
stuck for a day is not, and colouring by size would turn a busy platform permanently red.

**The embedder could not have succeeded.** Measured against the live backend: 16 texts in 17.1s, 32
in 26.8s, 64 in 52.1s, 128 in 107.8s — flat at ~840 ms a text. `EMBED_BATCH_SIZE` was 200, needing
~168 seconds against a 120-second client timeout, and the backlog is what produces a full batch, so
the timeout preserved the condition that caused it. Now derived from the timeout with the measured
rate named beside it, and pinned by a test in `test_schedules.py`.

**What the pre-score filter removed** (`filter_effect`, on the latency page) is the roadmap's stage-3
row, and deliberately not a duration: the filter costs microseconds a row, so timing it would report
noise. Per account, because one user whose filter rejects everything is invisible in a platform
total — and a feed silenced by a tight filter looks exactly like a feed with nothing new, which is a
failure this project has now shipped twice. Judged at one end only: everything removed is red,
nothing removed is cold start and is not.

**Stage 2's planned latency row was not built, and the roadmap now says why.** Country extraction
turned out to run inside the same `INSERT` that sets `first_seen_at`, so the span is zero by
construction. A row for it would be a permanent green measuring nothing, which is worse than no row.

---

## 2026-08-05 (later)

**The CV and cover-letter buttons did nothing, and the cause was general** (ROADMAP 6af). Reported
on the second account, with the instruction that made the fix right: investigate the *setup*, not
the account. It was general — the first account worked only because its database row had been
filled in by hand.

### A column with no producer (`services/api/credential_routes.py`)

`users.telegram_webhook_secret` was declared on the model, created by a migration, and read by the
webhook route to identify whose bot a tap came from. **Nothing in the tree ever wrote it.** Probed
live: both bots had a webhook registered with Telegram, one had the matching secret on its row, and
every tap on the other matched no user and 404'd.

Connecting a bot now does both halves — the secret on the row, and `setWebhook` on that user's own
bot pointing at `PUBLIC_URL/telegram/webhook/<secret>`, with the same value as Telegram's
`secret_token` header so a forged POST to a guessed path fails. Idempotent, so pressing Connect
repairs any account that predates it. Best-effort: a Telegram outage must not strand somebody
mid-onboarding, and the secret is stored either way.

**The 404 was silent, which is why this survived weeks.** A tap matching no account now logs at
WARNING — that line is the only symptom the platform ever gets.

### Four definitions of "jobs per day", and the home page showed the wrong one

The user config said 20, `status_routes` mirrored a constant of 100, `deliver` held a third at 100
in a parameter **nothing read**, the plan quota held a fourth, and the notifier enforced none of
them — it reads `libs.limits`, where the default is unlimited. So the dashboard told an account *"At
most 20 messages a day"* on a day it received **908**, and a test was keeping two of the fictions
equal to each other.

One reader now, and `None` stays `None` rather than becoming a number that understates what the
platform sends. The dashboard renders "No daily limit on messages"; the status page claims a spent
allowance only when a cap exists. Three constants and an unused parameter deleted.

### A dead letter could be read but never retired (`services/api/admin_routes.py`)

`DELETE /admin/queue/dead?lane=<lane>`, with an `×` on the banner. **The alert goes because its
cause is gone, not because a banner was hidden** — deliberately not a general dismiss, which would
let a real outage be silenced in one click. Each row's lane, owner and final error are logged at
WARNING before deletion: the row goes, the evidence does not. No retry — that decision belongs to
whoever fixed the cause.

### The per-source table's columns did not line up (`web/src/styles.css`)

Every row was its own grid with `auto` columns, so each sized its columns from its own contents:
`168h` set a wider column than `2h` and nothing aligned down the list. `auto` cannot align across
siblings — one shared grid with fixed control columns, rows as `display: contents`.

### Also recorded

The usage meter is **G9 cost visibility** in `GAPS_REVIEW.md`, still open and never built: nothing
meters spend, `libs/llm` does not read `usage` from a response, and no ledger exists. The `Quotas`
mechanism enforces `cv_builds_per_day` at the Telegram admission point; the other seven limits have
no display. Recorded rather than guessed at.

## 2026-08-05

**Both alerts on the admin health panel were real, and neither meant what it said** (ROADMAP 6ad).
The owner's observation is the one worth keeping: having them on the panel at all is what made this
findable in minutes, instead of by an account going quiet for a week.

### A Telegram outage permanently lost two digests (`services/notifier/__main__.py`)

Two dead-lettered notify jobs, both `RuntimeError: Bad Gateway`, five attempts each, sixteen minutes
apart. `send` retried only 429 — every other `>= 400` raised on the first response, so the queue
spent all five attempts inside five and a half minutes and dead-lettered while Telegram was still
unwell.

**Nothing here was broken.** The retries worked, the attempt counter worked, the dead-letter worked.
Together they sampled an outage at a resolution far finer than the outage, which is a different kind
of mistake and one that no component could have detected alone.

5xx now retries inside `send` with a backoff, before it costs the job an attempt. 4xx other than 429
still fails immediately: a malformed payload or a revoked bot does not improve by asking again. The
two conditions get **two budgets, counted separately**, so a burst of flood waits cannot consume the
allowance an outage needs. The failure message names which budget ran out — the old one said
"Telegram kept rate limiting this bot" during an upstream failure, sending an operator to look at
bot limits for a problem that was never theirs.

`tests/test_telegram_send.py`, five tests. Deliberately in their own file with no database
dependency: written first inside `test_notifications.py`, they were silently skipped by that
module's `DATABASE_URL` guard, which is the same trap CLAUDE.md §6 now names.

### An alert that could never be cleared, about a notifier doing its job (`libs/ops/health.py`)

"71 matching job(s) waiting and last delivery was 5h ago." Measured before changing anything: all 71
rows were **older than the 24 hours that account had asked for**, so `pending_feed` excluded every
one in SQL and correctly sent nothing. The lane was healthy — 657 jobs done, newest minutes old —
and the account's own log line read `dropped {'applicant_glut': 1, 'workplace': 1, 'too_old': 11}`
over a deliverable pool of exactly 13, matching the database to the row.

`user_health` counted `score >= 60` and nothing else, which was wrong three ways at once:

- **60 is not a cutoff anybody delivers at.** `cutoff_for` reads the operating point each user's own
  training run tuned; the two live accounts are at **29 and 42**. The constant counted a pool
  neither uses and missed jobs genuinely waiting below 60.
- **A closed posting is not waiting** — there is nothing to apply for.
- **A posting past the user's window is not waiting** — it can never be sent, so counting it
  restates "the corpus contains old jobs", which is always true and never actionable.

`pending_matches` now draws the same pool `pending_feed` does: per-user tuned cutoff, open, inside
that user's own freshness window. The account reads 0. Still a deliberate upper bound — eligibility
and the dating pass run over descriptions, not in SQL — because over-counting is a false alarm
somebody investigates and under-counting is silence.

**The general defect is the one this project keeps shipping: an alert that defines its trigger
differently from the code it watches is not an alert, it is a second implementation.** Written where
the number is computed, not only here.

### The logs that should have shown this were nine parts noise (`libs/obs/logs.py`)

Roughly one line in ten of the notifier's output was the notifier's. The rest was `httpx` announcing
every posting-refresh request that had **succeeded**, while the lines that mattered scrolled away.
Third-party loggers are raised to WARNING (`NOISY_LIBRARIES`) so they still report trouble and stop
narrating success — and *cleared* again under `LOG_LEVEL=DEBUG`, not merely skipped: a logger's
level is process-global and outlives the call that set it, so skipping left the trace missing at
exactly the moment somebody had asked for it. Found by a test, not by reasoning.

### Two working rules added (`CLAUDE.md`)

**§6, green before it is pushed.** The four gates CI runs — `lint-imports`, `ruff`, `alembic upgrade
head` from empty, `pytest` with `DATABASE_URL` — run locally first, in that order. A red CI run
after a push is a fix under time pressure that could have been a fix at leisure. Includes the skip
trap: ~290 tests skip silently without a database and the suite still prints green, so the skip
count is part of the result.

**§7, logs are the only view into a running system.** Every swallowed exception gets a line naming
what failed and what was done instead; levels are defined so severity means something; and the
INFO-flooding rules, because a log nobody can read hides whichever failure happened during the
noise.

### The latency panel's two red rows, checked and mostly not defects (ROADMAP 6ae)

**`Posted → fetched`, worst 54h43m: entirely HiringCafe** (p90 30.5h over 105 rows, against
LinkedIn's 1.0h over 2,333). The wiring is correct — it is asked for `posted_within_days = 1` — and
it returns 54-hour-old rows anyway, because its filter granularity is days and its day boundary is
loose. Nothing to tighten from here, which is why that row is `reported, not judged`. It is wasted
*intake* rather than wrong delivery: those rows are caught at delivery as `too_old`, having already
been fetched, described and embedded. A pre-score filter (6u/6x) is where that stops costing.

**`Described → embedded` p90 2h18m: already over.** Backlog zero, current p90 0.8 minutes; the
24-hour window is showing an evening catch-up burst that drained by 02:00. No code change — the
finding is that a 24-hour p90 stays red long after recovery and the row does not say so. Deferred.

## 2026-08-04

**Stage 1 — "the queries are right" — closed.** Eight defects found and fixed, and **not one of them
raised an error**: the query set was wired to disabled sources; the union readers read the wrong
config level, so every board on the free tier fetched its unfiltered recent feed while the platform
behaved as though nobody had signed up; eight sources were fed a query shape only two read; seven
ignored keywords entirely; the fold key was being typed into search boxes; two sources ignored the
operator's freshness window; **six of ten operator limits governed nothing**; and LinkedIn searched
3 of a user's 23 keywords.

Then, in the same day, a live outage with three independent causes — the harvester was never in
`deploy.sh` so `fetched_at` was never populated; the pre-send refresh had no wall-clock budget and
starved the notify lane's two-minute claim; and `UNLIMITED_ITEMS = 10_000` built a candidate pool
that could not be ranked inside that claim. Delivery was dead for both accounts while every job
reported `done`.

Also that day: `Unlimited` silently meant "the default" everywhere it was offered, because
`resolve(...) or DEFAULT` cannot express `None` — found from a screenshot of the admin panel where
the operator's change had visibly not taken. Location left the score and `europe` became one thing
rather than two; seven sources that cannot answer a keyword were removed; the country vocabulary
(`libs/corpus/geo.py`) and the corpus's own clock (`clock_timestamp()`, `fetched_at`,
`embedded_at`) both landed; and LinkedIn's card signals were measured against live pages rather than
reasoned about (ROADMAP 6ac).

Full detail per defect is in `docs/saas/ROADMAP.md` 6q–6ac, each with the measurement that justified
it. Recorded here late — the day's work went in under twenty-six commits and no changelog entry,
which is the gap this file exists to prevent.

## 2026-08-03

**The whole workflow walked on a fresh account** — signup, onboarding, the setup chat with its
prompts and commands, training, and delivery — which is ROADMAP 6h, and it was the right call. The
platform passed every automated test throughout and could not get a second person from signup to a
delivered job. Fourteen defects, and **not one of them was an exception**: every single failure was
a component doing something reasonable with a value nobody had written, or reading a different
column from the one that had been.

Three families, each of which is a lesson rather than a bug:

1. **Two definitions of the same thing, disagreeing.** "Profile ready", "is admin", "is a run in
   progress", "is this account due" — each existed twice, and in every case one copy was the one
   enforced and the other was the one the user could see. The visible half is what a person acts on,
   so the account looked finished and refused to work.
2. **Written here, read there.** The setup conversation replaced the capture flow and writes profile
   *files*; four consumers still read columns only the old flow ever populated. Nothing errored —
   the readers found NULL and did the sensible thing with it.
3. **Nothing arms the first value.** Delivery slots, and before them the rubric column: a field that
   only ever gets *advanced* by the thing that consumes it, with no code anywhere setting it the
   first time. The founder's account hides every one of these, because a migration script filled its
   columns before the rebuild.

### Every account created on the platform was permanently silent (`libs/queue/queue.py`)

Found by the alert, which is the first time one of these has been caught by the platform rather than
by a person noticing. `andraslekli@gmail.com`: **73 matching jobs waiting, last delivery never.**

Not the notifier and not Telegram. The bot was `connected`, the model trained, and all 15,094
postings scored. **No `notify` job had ever been enqueued for that account, and none ever would
be.** `dispatch_due_notifications` selected on `next_run_at IS NOT NULL AND next_run_at <= now()`,
and nothing on this platform ever sets that column — the dispatcher only advances one that already
has a value, and the LinkedIn loop owns a different one entirely. Signup, onboarding, and connecting
the bot all leave it NULL.

The first account works only because it was migrated from the reference instance with a value
already in it. That is what hid this: the platform looked like it delivered, because the one account
old enough to test with had been given its first slot by a migration script rather than by the
product.

NULL now means **due**, which is how `services/harvester/linkedin.py` has read the same shape since
it was written. Fail-open is the only safe direction here: a missing schedule that produces a
delivery is a delivery somebody can see, while a missing schedule that produces nothing is an
account nobody hears from and no worker is failing over. Arming the column at signup instead would
have left the same bug behind every future path that forgot to — which is how this one arrived.

Every other gate is unchanged, and a test pins that, or "due when unscheduled" quietly becomes "due
regardless of dormant, inactive, or a revoked bot".

Deployed, and the next dispatch pass delivered 9 items to that account at 15:29 UTC. The alert
cleared on the following health run.

### Where the time goes: the latency tab, one row per zone (`libs/ops/latency.py`, `web/src/pages/AdminPage.tsx`)

ROADMAP 6p. The end-to-end number is the one worth publishing and the one least useful for fixing
anything: "a posting took 36 minutes" does not say which of six handoffs ate them, and a stuck
describe worker, an embedder behind a flood, a score lane with no free worker and a delivery cadence
are four unrelated problems wearing the same number. 6n gave that number a verdict; this gives it an
address.

A posting arrives as a link and leaves as a card, so the rows are the boundaries in between: posted
→ found, found → described, described → embedded, embedded → scored, scored → delivered, and the
whole journey. Collapsed, a row says *which* zone. Expanded, it says *why that zone* — waiting
versus running from the queue's own timestamps, the backlog queued at that door, the hour-by-hour
shape, and the threshold's reasoning in words. The detail is fetched on expansion: only one row is
ever open, and running six drill-downs on a page load is six times the work to answer a question
nobody asked.

**`corpus.embedded_at` is new, and deliberately not backfilled** (migration `c4d8f1a2e7b6`). The
middle of the pipeline had no clock — a row recorded when its text arrived and then jumped to having
a vector — so described → embedded and embedded → scored were unmeasurable and both hid inside one
found → scored number that could not say which was slow. Filling the column from `first_seen_at`
would have invented a measurement, so historical rows stay NULL and those two rows read "not
measured yet" until the corpus turns over.

Two measurement rules that cost something and are worth it. **Shared work is measured on `corpus`
alone**: a description is fetched once for everybody, so counting it through the per-user join would
multiply every posting by the number of accounts holding it — a real percentile over a population
that never existed, which is worse than a missing number because it looks right. And **posted →
found is reported without a verdict**: sources round what they publish ("2 days ago"), so a
threshold there judges their precision rather than our lag. `unjudged` is kept distinct from
`unknown` for the same reason — a deliberate silence must not read as missing data.

### The training run refused, then crashed, on a profile that was complete (`libs/scoring/features.py`, `services/trainer`, `services/scorer`)

**The sixth place the same assumption was made, and the previous five were each fixed alone.**

`ProfileSchema.model_validate(user.profile)` — the JSON the capture flow wrote and nothing sets any
more. An account built by the setup conversation has prose files and no JSON, so this raised: it
killed the training run five times, and once training was fixed it killed *every score job for the
same account* instead. Both dead-lettered with a pydantic error that named a schema rather than the
thing that was actually missing.

`spec_for` is now one helper for all of it. An absent profile yields an empty spec, the keyword
features contribute nothing, and the embedding carries the whole signal — a real model, and strictly
better than refusing to score a profile that is complete in the form the platform now uses.

`services/docgen/run.py` makes the same call and will fail the same way the first time this account
builds a CV. **Deliberately not fixed here**: a document genuinely needs the person's details, so it
has to read the profile files rather than fall back to an empty schema. That is ROADMAP 6q.

### A job's finish time was when its transaction opened (`libs/queue/queue.py`)

`finished_at` was set from the statement's own clock rather than the wall clock at completion, so
every duration measured on the queue was short by however long the transaction had already been
open. Harmless until something reads it — which the latency drill-down now does, per lane, as the
number that separates "no free worker" from "the work got slower".

### Stop did not stop, and the page could not tell a run from a gap (`libs/labels`, `services/api/process_routes.py`, `web`)

Four defects in one surface, found by watching a real training run rather than by testing one.

**Stop cancelled queued rows, which could never have ended it.** The labeller re-enqueues itself
while work remains, so the deleted row came straight back and the run carried on spending the user's
own Claude credential while the page reported it stopped. A flag the worker reads before it re-arms
is the only thing that can end a self-perpetuating loop from outside it: `users.labelling_paused`,
set by Stop, checked in the one place that keeps labelling alive, cleared when the user starts again
— a pause, not a way to brick your own account.

**A run between batches read as no run at all.** Labelling goes in batches, and between a worker
finishing one and the dispatcher enqueuing the next the account has nothing queued. "Running" read
only `ready` and `claimed`, so the page flipped back to its start button mid-run and told a user
nothing was happening while ten jobs had just been judged. A recent finish now counts too, for as
long as the phase has work left. The page had the mirror of the same bug — it polled only while
running, so one idle moment ended the polling for good, and a page opened fresh mid-run never
started at all if its first answer landed in a gap.

**Nothing said the run survives the browser.** Pressing the button writes queue rows and the worker
pools drain them, so closing the laptop changes nothing — but the page said "Starting…" once and
then looked identical whether the run was halfway through or had failed an hour ago. It now derives
the phase, a count against the target, and a bar from the queue rather than from a progress column,
for the reason every other state here is derived: a counter that can disagree with the queue
eventually will.

**And the label budget an operator set was read by nothing.** `label_pool` was in the limits
catalogue and rendered in the admin panel as "Jobs bought per labelling run" — a control that looked
like a setting and behaved as decoration. It matters more than most, because labelling runs on the
user's own credential: how many jobs to buy opinions on is a decision about their money.

### A cold start stalled at ten labels because the corpus belongs to whoever harvests most (`services/labeller/pool.py`)

The candidate window was the newest 5,000 rows **on the platform**, filtered against the user's
config only afterwards. The corpus is shared, so recency belongs to whoever searches most: an
account looking for mechanical engineering roles had 159 matching rows sitting in the corpus and saw
none of them, because thousands of fresher software postings stood in front of them. Ten labels, a
warning in a log nobody reads, and a page reporting "10 of 300" as though it were progressing.

The user's own terms now narrow the query before the budget is spent. A share of the newest rows is
kept alongside regardless of terms, because the rules score reads location and remoteness too and a
pool drawn only from title matches would never surface a job worded in a way the user did not think
of.

**Not the harvester's query budget**, which was the first suspicion and turns out to be already fair
— `select_within_budget` is round-robin with carry-over, so a niche search gets its first query
before anyone gets their second. An earlier remark blaming the 24-hour freshness window was
speculation and was wrong: `score_job` has no age term and the pool looks back 90 days.

### Setup writes one place and three consumers read another (`services/api/setup_routes.py`, `process_routes.py`, `libs/profiles`)

The family that cost the most, all three found on one account in one afternoon.

**The rubric never reached the labeller.** `label.md` is written as a profile file; cold start,
relabelling and label scoping all read `users.rubric_md`, which only the superseded capture flow
ever populated. So an account with a complete 13,809-character rubric had a labeller answering
`confirm_profile` forever — no pool, no labels, no model, and nothing anywhere saying why. Writing
`label.md` now mirrors it onto the user row and re-derives `profile_version`, which is
`sha256(rubric_md)[:8]` and scopes which labels a training run may use, so it has to move with the
text. `profile_generation` advances only when the rubric actually changed, or an identical rewrite
would send somebody back through relabelling they did not need.

**A finished profile was offered the flow that would overwrite it.** An account that had just
completed setup was told to "process your documents to build your profile" — the capture flow the
conversation replaced, and running it would have overwritten a finished profile with a
schema-derived one, the exact regression `REBUILD_PLAN.md` exists to prevent. Two definitions of
"profile ready" that disagreed: `may_train` and the setup page read the files, this read
`processed_at`, a column nothing sets any more.

Found while checking a live account was ready to train, **not by a test** — which is the argument
for checking before pressing the button rather than after.

### The director handed the user back to itself (`libs/agents`, `services/api/setup_routes.py`)

A live run ended by telling the user to open two more chats — for role angles and the scoring rubric
— which that same conversation was carrying the instructions to do.

Two causes, and the second is the real one. The director's body said it was not handing off; the
environment block appended *after* it said the opposite, with the exact sentence to use. **Last word
wins, and the last word was wrong.** The handoff paragraph is now a slot in that block: the four
standalone steps still get it, the director gets the opposite instruction.

But no wording would have held, **because after the first turn it was not the director**. Only a
message opening with a slash command names an agent; every ordinary reply omits it and the field
defaulted to `setup`. So a chat started as the director ran the director exactly once and handled
every subsequent answer with a prompt that knows nothing about the four-step map and is told,
correctly for itself, to send the user elsewhere for anything it cannot do. That is also why the
handoff always arrived at a step boundary. **A conversation is one command from end to end** — the
assumption the whole design rests on, and the one thing that was not enforced. The agent is now
resolved from the conversation, per conversation, so redoing one step in a second chat cannot
retarget the first.

### The setup page withheld its own good news (`web/src/pages/SetupPage.tsx`)

Progress was read on mount and when a message was sent. The turn that writes the last file is by
definition one nobody sends a message after, so "setup complete, your model can be trained" waited
for a reload — the single moment this page has good news and it was the moment it stayed quiet. It
now re-reads the moment a turn stops running.

Chat titles were generated and only generated, so three chats called "Your profile" told nobody
which was which. A pencil beside the delete cross renames in the tab itself; auto-naming only ever
fills a chat still holding the default, so a renamed one keeps its name.

### The operator can now see the corpus, and seed it (`services/api/admin_routes.py`, `web`)

ROADMAP 6n, first two pieces. A new user's training data is whatever the shared corpus already holds
for their field, and the corpus only holds what previous users searched for — which is how the
second account found 240 matching postings against 14,359 rows. Until now the only way to look was
SQL, so nobody did.

A Corpus tab searches every posting with source and date facets, and operator keywords inject terms
into the query set to fill the corpus ahead of the people who will need it. **Stored separately from
user keywords and visibly distinguished**: an operator may delete their own and must never be able
to delete somebody's real search term, which is theirs and is what their feed depends on. Both lists
are searchable and paged — twenty-odd terms in an unordered grid reads as a wall.

Also fixed on the way: **a trained account was permanently "training"**, because the state was read
from the presence of a model rather than from the queue.

## 2026-08-02

### Onboarding could not be completed at all (`web/src/pages/onboarding/`, `services/api/onboarding_routes.py`)

Three separate walls on the signup path, each of which stopped a real person.

**The chat id could only be discovered, never supplied.** It was captured by reading whoever last
messaged the bot, which needs the update to still be queued at Telegram and nothing else to have
consumed it — neither of which the platform controls. When it came back empty there was nothing else
to try: the page stayed incomplete and its only instruction was to press Start again, which the user
had already done. The id is not optional — delivery returns early without it even when a channel is
configured — so there is now a field for it, proven by *messaging* the chat rather than by looking
it up, because a bot can read a chat it may not write to and writing is the only thing delivery
needs. Auto-capture stays as "Read it for me"; what it can no longer be is the only way through. The
bot-token hint promising "you never have to find it" is gone, being a promise the platform cannot
keep.

**Moving forward was a side effect of saving.** Nothing on the page moved you on, so a step already
finished — revisited from the rail, or completed a moment earlier — had nothing to press, and a step
that saves twice could disappear part-way through. Telegram is the clearest case: it takes a token,
then wants the chat id, and the page had already gone. Continue is now always present and disabled
until the step is satisfied. That is a control you can see before you can use it, which the previous
design deliberately avoided; it is the lesser problem, since a greyed button explaining itself beats
an absent one somebody hunts for.

**The locations cap was five, and the radios were invisible.** The picker offers about twenty
choices, so any honest answer to "tick everything that applies" went past the cap — and the only
thing that said so was a validation failure after the tick, naming a count rather than a choice.
Twelve now, counted as the form fills. `accent-color: var(--ink)` painted the selected dot near-white
on a near-white control; checkboxes survived it because a tick reads as a shape at low contrast and a
dot does not.

### One admin rule, in three copies, disagreeing (`services/api/dashboard_routes.py`)

Granting operator access from the panel set `is_admin` and the API began accepting that user's
`/admin/*` calls straight away — while their dashboard carried on hiding the menu that leads there,
because it computed its hint from `ADMIN_EMAILS` alone. **Access with no way to reach it**, which to
the person granted it is a toggle that does nothing. The onboarding branch omitted the check
entirely, so an operator part-way through their own signup saw no panel at all — exactly when they
want one, since a fresh account being set up is usually the thing they are watching.

Both branches now call the predicate `require_admin` enforces. The two sources stay ORed, so a flag
can grant access but cannot revoke the bootstrap operator.

### The CI check that would have caught two crash-loops existed, and the new service was not in it

The OAuth broker crash-looped twice in production on transitive dependencies of `libs.obs` —
cryptography, then sqlalchemy — each found by watching a container restart. Every other service has
a job proving it starts from its own requirements alone; the broker was added without one.

Also green again: the committed API contract was missing three credential routes, and a test ended
with `assert stale in stale`, which is true of every string and tested nothing.

### `/setup-boards` was answered by improvisation (`services/api/setup_routes.py`, `libs/agents`)

Found in the live run. A typed slash command was never a command: it reached the model as ordinary
text in whatever conversation was open. `/setup-boards` does not exist on this platform at all --
`SETUP_AGENTS` is context, setup, role-angles, scorer, plus the director and prompts steps -- so the
agent did the reasonable thing with an instruction it could not carry out and invented a
destination, announcing it would write the search configuration into two profile files instead.

Nothing errored. Nothing was refused. The user was told a plausible sentence about work that could
not happen, and the "configuration" went somewhere nothing reads as configuration: what actually
drives the harvest is `user_config`, written from the Settings page, and no agent tool touches it.

Now refused before the model sees it, in three cases: the conversation's own command passes (that is
how a chat starts), a real command names its own chat, and an absent one says so plainly. The prompt
side is told which commands exist here and that search settings are not its to write -- both needed,
since the ported bodies still name `/setup-boards` and friends and a user reading one will type it.


### Agent prompts: two guardrails on what the user is told (`libs/agents/__init__.py`)

**Prompt change, made on the owner's explicit instruction** — the standing rule is that prompts are
not edited without one. Both rules were added to `ENVIRONMENT`, the block appended to every command,
rather than to any individual prompt: the ported bodies stay byte-identical to the reference
instance, and a command added later cannot be written without inheriting these.

**Internals are never named to the user.** A real setup transcript ended with a summary table of
`context.md`, `base_cv.html` and `08-role-angles.md`, and told the user to run
`powershell -File cv/compile.ps1` and check whether Lato rendered instead of Arial. None of that is
theirs: the filenames are the platform's names for its own storage, and the script does not exist on
their machine. It reads as a demand to manage machinery they cannot see and are not responsible for.
The prompt now requires plain terms — "your CV is written and ready" — and points at the file list
on their own page, which is already labelled in words, when something must be pointed at.

**A command that cannot run here is a new chat, said in those words.** One conversation runs one
command and cannot hand off inside itself. The transcript said "start it as a new command", which a
user reasonably read as "type it here" — and typing it there does nothing at all. The required shape
is now literal: *"Open a new chat and run /setup-boards there."*

`apply.md` and `setup.md` still contain the `cv/compile.ps1` lines they were ported with. They are
left alone deliberately — divergence from the reference instance is the thing the porting rule
exists to prevent — and `ENVIRONMENT` already overrides them, both by saying the model cannot run
commands and now by forbidding it to pass one on.

Tests assert both rules are present for every startable command, so dropping them fails the build.

---

## 2026-07-31

### The password floor drops to nine

12 was turning invited beta users away at the door, which costs more than the marginal entropy
buys (owner's decision). `MIN_PASSWORD_LENGTH = 9` — signup, reset and change all derive from it,
and the three frontend hints were the only places the number was written out by hand.

The `_OBVIOUS` list was written against a 12-character floor, so every entry in it was already
longer than the minimum and the nine-character common passwords could never have been reached.
Nine of them added.

`AUTH_REUSE.md` §4 and `ROADMAP.md` 1a said 12; both now say what the number is and why it moved,
rather than being left to contradict the code. A test pins the literal 9 and the 8/9 boundary —
every other password test reads the constant, so a future change to it would otherwise pass
silently.

### The embedding rebuild, and four things that stopped it finishing

Backfilled 2026-07-31: these nine commits shipped without changelog entries. The reasoning is in
the commit bodies and is summarised here because that is where it is looked for.

**Why there is a rebuild at all** (`0bfc773`). The platform embedded `description` alone; the
reference's default is `title_desc` — title first, then description, capped at 4,000 characters.
Two consequences, and the second is the one that mattered: the title is the single strongest
statement of what a job is, **and it has to lead, because the encoder truncates**. `bge-base`
reads ~512 tokens; Arbeitnow descriptions average 6,662 characters, so a title appended at the end
would be cut off every time. Those sources scored 16–22 against LinkedIn's 43. The mode is folded
into `EmbedderVersion.version`, so every existing vector went visibly stale rather than
confidently wrong — the honest price being a re-embed and a retrain. Also fixed: locations reached
the boards as slugs, so LinkedIn was sent `european_union` and one query died with
`Invalid country string`.

**Then the rebuild would not run.** Four failures, each found only by watching the live box:

- `0a80ffc` — eight consecutive cycles rebuilt zero vectors. Each holds the shared advisory lock
  for its whole run, so every cycle after the first was locked out by the one before and the loop
  looked like it was working. `--embed-only` takes no lock and does nothing but embed.
- `d039f52` — that pass ran the whole 6,601-row rebuild inside one transaction, committing
  nothing for an hour. A crash at minute fifty-nine discarded fifty-nine minutes of paid
  embedding, and while it ran the database still read zero embedded, so a working run and a wedged
  one were indistinguishable. Now a session per 200-row batch.
- `3265519` — the rebuild then died at sixty minutes on a Postgres deadlock against the scheduled
  cycle's own writes to `corpus`, and rolled back everything. Ordering cannot fix contention
  between two different write patterns, so a failed batch no longer ends the run: the rows stay
  pending for the next pass, capping the loss at 200 rows. Five consecutive failures still stops
  it, so a broken embedder does not spin.
- `bd305ad` — the first batch of the rebuilt run died on a raw `httpcore.ReadTimeout`. `_post`
  wrapped HTTP status errors but let transport errors through untouched, so every caller — all of
  which handle `EmbedderError` — was killed by a timeout anyway. Wrapped, with a test. The timeout
  itself was contention with the scheduled cycle on a single-threaded encoder, so `--embed-only`
  now uses a 900s timeout and a batch of 64.

**Position at the time of writing: 3,136 of 6,601 rows re-embedded, 3,465 pending, one version.**
Both users' active models were trained on the old vectors and cannot be retrained until this
finishes.

### The ingestion gap closed: LinkedIn runs, and the source list was wrong

`23f461d`, `0610a40`. The measured cause of "my feed is quiet", and it was neither the scorer nor
the filters — both were suspected first and both were fine. LinkedIn produced 337 of the founder
account's 348 jobs at or above the cutoff; the sources running in its place produced **nine in two
days**.

**The per-user LinkedIn pass did not exist.** Every part it needs was already built — adapter,
lease, governor with its concurrency ceiling and interval floor and kill switch, both scheduling
columns on `users` — and the loop between them was missing, which is why every LinkedIn row in the
corpus carried the same `first_seen_at`: one import, and nothing had fetched since. It is separate
from the shared cycle because a different person pays: the cadence, the bandwidth and the ban risk
are the user's, so one account's quarantine cannot stop everyone's feed.

**And the enabled source list was the wrong seven** — all remote-work aggregators, which the
reference instance does not use at all. They were a deliberate first notch and the ramp then
stopped, because nothing measured that it had. Now the reference's own set: ATS boards where
employers post directly, VC boards, and the European boards where this platform's users are.

`jobspy` was never added to the harvester requirements despite three sources depending on it, so
those sources could not have run once — and nothing surfaced it, because they were also not
enabled. Two halves of one omission, each hiding the other.

**Confirmed working since:** 24 hours of intake now shows linkedin 212, hiringcafe 167,
nofluffjobs 87, profession 53 alongside the aggregators — sources that produced nothing before.

### The scorer stopped asking a regression for a classifier's number

`eebeaee`, `b3e65a6`. The live reference model settles it: `model_type=histgbm`, `task=classify`,
`decision_cutoff=44`, against a `score_threshold` of 60. The platform used a fixed
`Ridge(alpha=1.0)` regression and took **60** — which is the *label* threshold deciding what counts
as fit while training — as its delivery cutoff. A regression fitted on mostly-low labels pulls
toward the mean, and the platform then demanded 60 of it while the reference asks 44 of a
calibrated classifier. That is why the feed went quiet even where jobs existed.

Now four candidate heads, class-balanced with calibrated probabilities, selected on
cross-validated average precision — not ROC-AUC, which is optimistic at a quarter positives, and
not the held-out split, which would be selection-on-test. The winner is refit on everything before
deploying.

Separately, `b3e65a6`: fifty candidates, fifty dropped for age, zero sent, while twenty-five fresh
matches sat one page further down. The pool is ordered by score and the age filter ran only after
selection, so a backlog of month-old high scorers took every slot. The filter runs in the query
now; undated rows still come through, because they are what the pre-send dating exists to resolve.

### The setup flow can now be finished, and `setup-all` is retired

`setup-context` and `setup-director` are live. With them, **the four steps produce exactly the eight
files the trainer gates on** — a property a test asserts rather than a claim: every step is checked
for owning at least one required file, and the union is compared against `setup_files()`, so a stray
step or a missing file fails the build instead of stranding an account.

`context.md` has an owner for the first time. Until today nothing wrote it while
`setup-role-angles` required it, which made setup unfinishable and — after the training gate landed
— training unreachable for every new user. `UNOWNED_FILES` is now empty and a test keeps it that
way: a required file with nobody responsible for it is an account that cannot finish.

**`setup-all.md` is deleted.** It was unreachable, superseded, and its gates verified a filesystem
this platform does not have; leaving a broken director in the tree is how one gets wired up by
accident. What it was measured against is recorded here rather than in a parallel file:

| | `setup-all` (retired) | live |
|---|---|---|
| References to things this platform lacks | 11 | 0 |
| Required files never mentioned | 6 of 8 | 0 |

The order is now stated as a constraint, not a preference — `setup-context` first, because
`setup-role-angles` says outright that `context.md` must already exist, and running it earlier
produces files grounded on nothing.

### Applications are training data now, and a draft director that can actually run here

**98 real applications became labels.** They had been sitting as `applied_at` timestamps since the
import while the trainer ran on 3,498 Haiku guesses — the strongest signal the platform will ever
get about what this person wants, unused. Only 7 of the 98 had ever been labelled at all, and the
LLM had *rejected* 4 of those: jobs he cared enough about to apply to, scored below the keep
threshold. Written at weight 5 against the LLM's weight 1, so 490 weighted true positives now
outrank the guesswork. A script rather than a migration, because it is a judgement someone should be
able to run for one account and inspect.

### The setup path could not complete, and nothing said so

Walking it before testing with a real user, which is the whole reason to walk it. **Three of the
eight required profile files belonged to steps nothing could start.** The chat sent no command name,
so every turn ran `setup` — which writes five. `context.md`, `08-role-angles.md` and `label.md`
belong to `setup-role-angles` and `setup-scorer`; the API accepted both and the UI never offered
them. Combined with the training gate added the same day, **a new account could not finish setup and
could therefore never train** — and the two failures would have looked unrelated.

Fixed: a leading `/command` selects the step, the empty chat opens with a **Start** button rather
than a blank box, and `GET /setup/progress` answers what is missing and which command produces it.
**The success criterion lives in code**, and it is the same check `train_user` refuses on, so the
page and the trainer cannot disagree about whether an account is ready.

### A draft director, measured rather than asserted

`setup-all.md` is a verbatim port of the reference instance's local director. Its gates verify
`pipeline/config.yaml`, `scorer/models/scorer.joblib` and `.env` — **none of which exist here.** An
agent told to "verify mechanically" against paths that are not there either invents a pass or never
stops, which is exactly the runaway the owner asked to design out.

`setup-director.md` is the platform rewrite, shipped **as a draft**: it is not in `SETUP_AGENTS`, so
nothing can start it until it has been reviewed. Two properties are checkable without spending a
credential, and the test asserts both — including that the *current* file fails them, so if somebody
fixes it in place the test says the draft is redundant:

| | `setup-all` | `setup-director` |
|---|---|---|
| References to things this platform does not have | **11** | **0** |
| Required files never mentioned | **6 of 8** | **0** |

It also carries what the old one had no notion of: an explicit **stop** condition (a finished profile
is finished — no fourth pass, because every extra pass spends the user's own credential and looks
identical to progress), and a pass that asks for the experience a CV leaves out — internships, side
projects, volunteering — which goes into `context.md` rather than onto a CV.

Writing it caught one defect in itself: the draft's header originally explained what the old one got
wrong, naming those dead paths *inside the prompt the model reads*. That rationale lives here and in
the test instead.

### The chat reads like a conversation

The assistant now answers inside a container on the left, the user's bubble stays on the right, and
what a turn *did* is a rail of dots — one step per file — rather than a single line running several
writes together with separators. Before this a long reply and the page around it were hard to tell
apart, and a turn that wrote three files had nowhere to show it.

### A model may no longer be built from a half-written profile

**The failure this prevents is silent, which is why it needed a gate rather than care.** A model
fitted while setup is unfinished is fitted against a rubric answering a different question from the
one the finished profile would ask — and nothing downstream reveals it. The labels look like labels,
the metrics look reasonable, and the feed is quietly wrong for as long as the artifact lives.

It was already live: one account holds four of its eight setup files and is missing `base_cv.html`,
`label.md`, `04-job-evaluation.md` and `08-role-angles.md` — and has 945 labels and an active model
trained from them.

`train_user` now refuses unless every file the setup agent owns is present and filled. That set is
the seeded scaffolds minus the ones that ship usable, so the task prompts and the CV framework —
which are already good and only want tuning — gate nothing. The check lives in `train_user` rather
than the queue handler, so every caller is behind it: the worker, a script, and the manual retrain
button that does not exist yet. Six existing tests were training without a profile and now say so.

**And the flag that describes all this had drifted.** `filled` is derived data living in a column,
so it can disagree with the content it describes, and on a live account it did: four files of real,
marker-free text — 13k to 32k characters each — every one flagged empty. `grounding()` and
`missing_setup()` read the content and are immune, but `listing()` reports the column — so the
*setup agent* was told its own finished files were still scaffolds, and might set about writing them
again. `repair_filled_flags()` recomputes from content, which is the authority; both live accounts
have been repaired.

### The status page slept through a six-hour outage, so it learned to watch delivery

Every component was green while nothing was being delivered: collection running, scoring running,
bot and channel connected, and the notifier completing its jobs having sent nothing for six hours.
**That is the precise failure a status page exists to prevent**, and this one could not see it,
because it checked whether each part was alive and never whether anything came out the end.

There is now a `delivery` component, and what it reports is a *conjunction*: matches waiting **and**
nothing sent. Either alone is meaningless — an account with nothing above its cutoff is quiet for
good reasons, and a backlog straight after a harvest is normal — so only together do they mean
stalled. Warn at 3 hours, down at 6, wider than the harvest thresholds because delivery is bounded
by the user's own notify interval and daily cap. The test pins the exact shape of the outage.

### Notifications can be dismissed, and Telegram's channel is no longer optional

Per-item dismiss, mark-all-read and delete-all, the last two as an eye and a bin — two text buttons
side by side were wider than the panel they sat in. Deletion is a real `DELETE`, not a hidden flag:
these are transient operational messages, and once acted on there is no later question a kept row
answers. The row markup had to split, because the dismiss control cannot be a button inside a button.

**The Telegram channel is now required, in onboarding as well as in settings.** It was optional, and
that was wrong: delivery is the product, and the private bot chat is for commands and finished
documents — a feed nobody scrolls is not a feed. An account could previously finish onboarding with
a working pipeline and nowhere to put its output, which surfaces months later as "it stopped sending
me jobs", the least diagnosable shape a problem takes.

The chat ID and channel ID are now displayed. **They are addresses, not secrets** — useless without
the bot token, which is never returned — and withholding them only stopped a user confirming that
the thing they configured is the thing we send to, which is the one question that page answers.

### Verified rather than assumed

Everything above ran against a real Postgres, not the 472 tests that skip without one: **1,034
passed, 11 skipped.** That is also the answer to whether the auth flows work — signup, login,
change password and email verification are covered across `test_api_auth.py`, `test_auth.py` and
`test_gdpr.py`, including the ones that matter most (an unverified account cannot log in, a
verification token is not a reset token, a reset ends every session, a changed password requires the
current one).

Delivery pause was traced end to end rather than eyeballed: the toggle writes `user.active`, and
`dispatch_due_notifications` filters on it — so pausing stops jobs entering the lane at all, instead
of enqueuing work that is dropped later.

**One bug this caught in my own change:** requiring the channel broke `test_onboarding.py`'s fixture,
which connected a bot without a route. It passed locally because those tests are DB-gated and skip
without Postgres — it would have failed in CI. Fixed, and pinned with a test.

### Four bugs between a re-embed and a trained model, each hiding the next

The corpus had to be rebuilt because the platform embedded `description` alone where the reference
embeds `title + description`, so every stored vector was in the wrong space. Getting 6,601 rows
through that rebuild took four fixes, and their shape is the lesson: **each one only became visible
once the one in front of it was gone.**

**1. One transaction for the whole rebuild.** `--embed-only` called `embed_all_pending` inside a
single `session_scope`, and that function drains up to fifty batches before returning. Twenty-five
successful `/embed/bulk` calls had already happened and `corpus.embedding IS NOT NULL` still counted
zero, because nothing commits until the end. Now one batch per transaction: the run is
interruptible, its progress is readable from the database, and a stop costs at most one batch —
which is what "we can start and stop this process" actually requires.

**2. A deadlock destroyed the first hour.** The shared harvest cycle writes the same table — ingest,
description refresh, cold trim — and Postgres resolved the lock cycle by killing the re-embed. Sixty
minutes of paid embedding rolled back. `awaiting_embedding` already orders by id, so embed-against-
embed was never the problem and ordering one side cannot fix it. Instead a failed batch no longer
ends the run: the rows stay pending and are picked up again.

**3. A raw timeout killed the next attempt on its first batch.** `EmbedderClient._post` wrapped HTTP
status errors and short responses in `EmbedderError` but let transport errors through untouched, so a
read timeout escaped as a raw `httpx` exception past every caller — all of which handle
`EmbedderError`. Now wrapped, with a test.

**4. And that timeout was contention, not a broken gateway.** The cycle embeds too, and two clients
queued on a single-threaded encoder push a 200-text call past the 120-second client default. A bulk
embed is not a web request: the rebuild now uses a 900-second timeout and batches of 64, so each call
finishes in ~33 seconds even under load, with a shorter row-lock window per commit. **Worth noting
this was fixed rather than worked around** — `sudo systemctl stop harvester.timer` was unavailable,
which forced the better outcome: a rebuild that survives alongside a live pipeline rather than one
that requires the pipeline to be stopped.

Result: 6,601/6,601, every row carrying `…|norm=1|text=title_desc`.

### A model is stale when its coordinates move, not only when labels arrive

Both retrains then went dead with *"not enough new labels since the active artifact"* — while the
active artifacts were already unusable and the scorer was failing closed on the version mismatch.

`should_retrain` compared label counts only, which assumes the one thing that can go stale is the
evidence. It can also be **the coordinates**. Re-embedding moves every vector, and a model fitted in
the old space cannot read the new one: its weights are indexed to axes that no longer mean what they
meant. None of that arrives as a new label. The guard now reads `embedding_version` off the corpus
rows the fit would actually train on — the vectors are the ground truth about which space they are
in — and retrains when it differs from the active artifact's recorded `embedder_version`.

First result, on Andras's 945 labels: **histgbm selected**, cross-validated average precision 0.622
against svc 0.610, logreg 0.468 and linsvc 0.467; test AP 0.545, AUC 0.79.

Then the owner's, on 2,554 labels: **svc selected**, CV-AP 0.663 (histgbm 0.660 a close second,
linsvc 0.532, logreg 0.518), test AP 0.665, AUC 0.832 — and an operating point of **cutoff 38 at
65% recall**, precision 0.616. Near the reference instance's 44, and nothing like the 60 that was
emptying the feed. All 6,978 rows rescored against it; 1,251 clear the cutoff.

### An unreachable recall target must not silently become the label threshold

A flaw in the bake-off added earlier the same day. When no threshold in 20–90 held the target recall
out of fold, the sweep fell back to `FIT_THRESHOLD` — **the label threshold, reproducing the exact
conflation that had just been fixed**. It hit Andras's model: cutoff 60 at 37% recall, with nothing
to say the target had been missed. An unreachable target now takes the best-F1 point, and `metrics`
records `recall_target_met`, which is the difference between a feed that is thin by design and one
that is thin for reasons nobody can name.

### Fifty candidates, fifty dropped, nothing sent

With the model fixed and 26 genuinely fresh matches waiting, the notifier selected 50 candidates and
dropped every one — `{'too_old': 28, 'closed': 22}` — twice in a row.

The age filter in SQL has to let undated rows through: LinkedIn reports no posted time to the
listing API, and `date_candidates` exists to resolve them. But the pool was ordered by score alone,
and undated rows are the bulk of the corpus and indistinguishable from fresh until something fetches
them. They took every slot, only a handful could be dated per run, and the rest resolved to old —
while the jobs we *could* prove were recent scored lower and never entered the window.

Every dated row in the pool has already passed the freshness window in SQL, so preferring them costs
nothing. Undated rows still fill the remaining slots and are still dated: ranked behind certainty,
not excluded by it.

**Verified end to end**, which is the point of all of the above: *Backend Engineer (Python/AWS)* at
Digital Waffle, score 90, posted 12:47 UTC and delivered to Telegram at 13:47 — the first delivery
since 09:11, and the first ever through the corrected vector space.

### The schema CV renderer, deleted, and five functions nothing called

A scan of every module-level function against every mention of its name in the tree, plus an import
graph over the 233 modules and 56 test files. Two categories came out of it, and only one of them
was safe to act on — the distinction is the useful part of this entry.

**Genuinely dead: the CV renderer.** `libs/templates/cv.py` filled the base layout from a
`ProfileSchema`, and `services/docgen/build.py::base_document()` was its only caller — which had
been labelled *Deprecated* in its own docstring since the base CV became a per-user file. Nothing in
the platform reached either. A real build reads the user's `base_cv.html` out of `user_profile_files`
— the document the setup agent wrote, with their actual experience in it, where the renderer could
only emit what a schema had fields for. `run.py` already raises `NotBuildable` when that file is
missing, so the renderer was not even a fallback.

607 lines gone (renderer plus its tests). **What was kept is the point:** the layout itself is still
live — docgen hands it to the tailoring prompt as the BASE TEMPLATE — so the five tests that cover
*the file on disk* moved to `tests/test_cv_template.py`, including the `@page` margin check that
caught a real bug (body padding gives page 1 a top margin and page 2 none, which is what a recruiter
sees past the fold). The seven `test_docgen.py` tests that called the renderer never wanted a
rendered CV; they wanted *a* base document, and now load the template directly.

**Five functions nothing named, removed** with the imports they orphaned:
`features.feature_matrix`, `build.prompt_text` (its docstring said "exposed so a test can assert
this" — no test ever did), `deliver.render_digest`, `eligibility.worth_applying_now`,
`proxy_usage.next_local_midnight`. `render_digest` is the interesting one: it documented itself as
superseded, because a Telegram keyboard attaches to *a message* and so a digest structurally cannot
carry per-job buttons. That reasoning is worth keeping — which is what this entry is for, and why
the code did not need to stay to hold it.

**Deliberately not deleted, and why.** `libs/corpus/postings.py::fetch_apply_url` is unused but sits
in a file `pyproject.toml` pins as kept diffable against the reference instance; deleting parts of a
ported file destroys the one property that makes it reviewable. And the whole **document-capture
flow** — `services/capture`, the `process` lane, `/process`, `ProcessPage.tsx`, `libs/scoring/facts`
and the `users.facts_md` it writes — is *superseded but wired end-to-end and passing its tests*.
That is not dead code, it is the previous product surface still reachable from the dashboard, and
retiring it is the `REBUILD_PLAN.md` phase-5 cutover rather than a cleanup. Noted here so the next
scan does not have to re-derive it. One genuine orphan found and left alone: `users.profile_context`
is declared on the model and never read or written by anything, but dropping a column is a migration
and the corpus is mid-re-embed.

567 tests pass, ruff clean.

## 2026-07-30

### A chat turn that survives the browser, and the cap that was never enforced

**The turn ran inside the HTTP request, and that was the bug.** It takes minutes on a frontier
model, so switching tabs, opening another chat, refreshing or signing out aborted it — the user's
question was already committed and the reply never arrived, with nothing on screen to say why.
Persistence was never the problem; the request's lifetime was.

So a turn is a queue job now (`setup` lane, `services/setupagent`). The API writes the question and
a **pending** assistant row and returns `202` immediately; the worker streams text into that row as
the model produces it and marks it done. The page polls while anything is unfinished, so what it
renders is the same row whether somebody watched it arrive or came back the next morning — with a
caret while text is landing, and a failed turn written down rather than swallowed, because a chat
that silently stops answering is indistinguishable from one still thinking.

Proven in a browser, not asserted: send → thinking state → streaming text → **navigate to Jobs
mid-turn** → come back → the reply is there and complete → full page reload → still there.

**Two bugs found on the way.** `CLAIM_TIMEOUT_MIN` had no `setup` entry, so the new worker
crash-looped on its first sweep rather than its first job — a test now pins that the lane list and
the timeout map agree by construction. And a stored config section written before `locations`
existed has the key absent, which the old `<select multiple>` tolerated and `.includes()` does
not: the whole details page went blank. Defaults are spread under the stored section now.

### Telegram: your own cap was being ignored

Nothing was arriving, and it was not the proxy. Two separate causes, both now understood:

- **`max_notifications_per_day` was read by nothing.** It is in the config schema and on the
  dashboard ("At most 20 messages a day"), and `deliver()` used its own hardcoded 100 regardless.
  The account that asked for 20 received **160 in sixteen hours**, which is what exhausted the
  window it is now stuck behind. `deliver` reads the user's config (migrated, so v1 rows count
  too), with tests for both shapes.
- **The other account's candidates are all too old** — 44 unsent jobs at or above the cutoff, none
  posted within its 6-hour window. That is the JobSpy/LinkedIn ingestion gap, not a filter fault.

The cap counts from the user's local midnight, so the account that overshot resumes on its own.

### Everything else asked for

**Deleting a credential asks first**, naming what stops: job delivery, the profile chat, LinkedIn
collection. A one-click destructive control next to a copy button is a mis-click waiting to happen.

**Freshness lives in Settings alone.** It was on both the config form and the delivery card, and
nobody could say which was in force — they wrote the same field, so both worked, which is worse
than one being broken. Settings keeps it, beside the interval it is so often confused with.

**The config form was rebuilt for people who do not read forms.** Two newline-separated textareas
and a `<select multiple>` became chips you add one at a time and labelled checkboxes grouped into
"work from anywhere", "regions and countries" and "cities" — with the distinction between "remote
from the EU" and "in the EU" written out, because it is the one everybody gets wrong. Onboarding
and Your details share the component, so both changed at once.

### Three chats per account, each with its own memory

Persistence was never the gap — the transcript has always been a table, so a closed tab, a
sign-out and a week away all resumed the same way. What was missing was **more than one of them**.

`setup_conversations` is the new unit of context. Messages belong to one, `history()` filters on
it, and the model is only ever shown the chat being spoken in. A new chat starts empty; the old
one keeps everything. What crosses between them is the **profile**, not the talking — the files
are the durable product and every chat reads and writes the same ones, which is what makes
starting fresh cheap rather than a loss.

**Three, fixed, not a setting** (owner's call). Every turn replays its whole transcript on the
user's own credential, so unbounded chats is unbounded spend on re-reading them. Deleting one is
how you make room, and the refusal at the cap says so rather than just saying no. Deleting a chat
never touches the profile, and deleting the last one is allowed — the next visit opens a fresh
one, which is where a new account starts anyway.

Titles come from the first thing the user says, trimmed. No naming call: a title is not worth a
request that can fail, and the first sentence is what somebody would have typed anyway.

Existing transcripts migrate into one conversation per user rather than being discarded — whatever
was said during setup is the reason the profile looks the way it does.

**The browser run earned its keep again.** A fresh chat was titled "New chat" and the button that
creates one said "+ New chat" — two identical labels side by side in the switcher, and the first
automated click hit the wrong one. Untitled chats are "Untitled chat" now. Eight new tests cover
persistence across sign-out, context isolation asserted at the enforcement point, the cap, delete,
and that another account's conversation id is useless.

### The evening batch: twelve owner asks, hardest first

**In-app notifications** — `user_notifications`, `libs/notify` with event templates and
collapse-onto-unread (producers run on timers; forty copies of "Telegram is broken" is how a bell
gets ignored), a list/mark-read API, and a polling bell in the nav. Producers wired where the
facts are learned: bot revoked, model trained, processing drafted and failed, password changed.

**A status readout on the Overview** — `GET /status` derives each component's health from the
data (bot, channel, harvester freshness, scorer, docgen), each a colour and a sentence, because a
red dot with no words is a support request.

**The chat became a chat and the Model page exists.** User in a bubble, assistant as prose with
markdown-lite rendering, autogrowing Enter-to-send composer, an empty state that suggests what to
say. Training moved to its own nav page — the chat edits what the platform knows; the Model page
owns label → train → rank, with the one paid action as a button and the free retrain described as
what it is.

**Jobs can stream to a channel.** `PUT /credentials/telegram/channel` proves the ID by posting to
it (a bot must be an admin to post — the test send is the whole check, and its failure strings are
the user's fix), then routes every stream there; the bot chat keeps commands and documents.
@getids walk-through folds sit beside every fiddly Telegram step. No message ID is stored — the
channel ID alone is what sending needs.

**Credentials show what is stored** — masked last-4, replace, delete, and copy behind the account
password. `POST /credentials/{kind}/reveal` is the one exception to write-only, and the shape of
the exception is the point: plaintext is what "usable" means, so the security is the gate — the
password (a stolen cookie is not enough), one response, an audit line either way, a rate limit.

**Documents are uploads only.** The history is one document by construction now (a new upload
replaces it, behind an are-you-sure), it no longer appears in the Documents list, and
`scripts/move_md_documents.py` moves the migrated profile `.md` files out of both live accounts'
Documents into the profile store where the chat maintains them. Agent coverage audit: `setup.md`
writes every per-user file; `03-writing-style` and `06-cover-letter-templates` are platform-shared
by design (`libs/profiles` EXCLUDED) and are not supposed to be per-user.

**Delivery has an on/off switch** (`PATCH /account {active}`) with the honest sentence beside it:
messages stop, collection and scoring keep running, the bot stays on.

**The landing page stopped being an open-source pitch.** GitHub links and self-hosted framing out;
same length, same sections, same wheel — Get Started is now "set it up once / the radar takes
over" ending in Create your account.

**Small but asked for:** the jobs table shows when each job was fetched ("30 Jul 20:04 · 3d old")
sortable on that column; the split-second "Loading…" flash on Jobs, Profile and Settings is gone
(module caches render the last answer while the fresh one loads, cleared on sign-out so the next
account cannot flash this one's data); and the dev proxy learned `/notifications` and `/status` —
the same class of gap `/setup` fell into, this time caught by the browser pass before it shipped.

### Deployed, and the two things deploying found

`api` and `notifier` rebuilt and restarted on the box, running everything above.

**The api container was never given `PUBLIC_URL`.** It was in `.env.example` and in nothing that
reads it, so `_base_url` fell back to `http://localhost:8000` and every verification and reset
mail production ever sent pointed the recipient at their own machine. Set on the box, added to
`compose.yml` as `:?`-required — a silently wrong link is worse than a container that will not
start — and asserted in `tests/test_mail_config.py`, beside the sender-selection test written
after the same shape of outage. Verified live: the API now builds
`https://mahoraga-app.rubenlekli.com/#/verify?token=…` and resolves `SmtpSender`, not the console.

**The notifier crash-looped on `ModuleNotFoundError: pydantic`.** The v1-config fix imported
`libs.contracts.config` into a service that never declared it — the indirect-dependency gap the
`service-deps` CI job exists for, reproduced locally by installing the notifier's requirements
alone into a clean venv and importing its entrypoint. Declared, rebuilt, both containers stable.

### The e2e browser run, and the three bugs the API tests could not see

"I can't log in and reset doesn't work" — reproduced by driving the real dev stack (vite, uvicorn,
Chromium) through signup → emailed link → verify → login, then forgot → reset → old password
refused → new one works. That run now passes end to end. What it caught, none of which any API
test could have:

- **`/auth/verify` took the token as a query parameter; the page sends a JSON body.** Every human
  verification failed 422 while the tests stayed green — they used the query form. The endpoint
  takes a body now, matching what the SPA sends.
- **StrictMode consumed the verification token twice.** The second POST finds it used, and the
  page flips from "confirmed" to "did not work" *after succeeding* — both requests are valid
  requests, and only a page that makes both misbehaves. A ref guards the effect.
- **A stale signed-in state survived the reset.** Hash navigation never re-checks the session, so
  an app that was open before the reset link kept rendering its shell after every session was
  revoked. The reset page tells the app to re-ask.

And the reason the flow was unfinishable locally at all: **`ConsoleSender` kept the link in memory
and logged only the subject** — and could not have logged the body, because the auth routes send
inside `redacting(token)`, which scrubs the token from every log record. It prints to stdout now,
which the redaction filter does not touch, with a test pinning that the link is visible.

### Onboarding wears the design, and everything collected becomes editable

**The wizard's progress is numbered circles now** — done is filled with a check, current is
ringed, the future is a dim number, labels underneath, joined by a rule. Finished circles are
clickable, which is what saving per page always bought and the thin bars never said. The mechanics
did not move: which page you are on is still the server's answer, and nothing here can skip.

**"Your details" is a page** (`web/src/pages/DataPage.tsx`): every onboarding form, rendered by
the same components the wizard uses, in closed folds — name and links, documents, history, search
config, and the three credentials. Not copies: a field added to onboarding appears here without
anyone remembering to mirror it, and a correction saves exactly the way getting it right the first
time would have.

**Settings grew the two delivery knobs**, worded for the confusion between them: how often we
message you (`PATCH /account`, new, bounds mirroring the DB constraint) and how fresh a job has to
be (the config's `posted_within_hours`, down to one hour now in both forms). The interval is when
we check; the freshness is what is still worth sending when we do.

### The live box check, and what it found

Prompted by "make sure my account gets last-hour jobs". Ran against the box read-only, then fixed
what it turned up:

- **The delivery age filter works** — since the 11:15 UTC image rebuild, every cycle logs
  `dropped {'too_old': N}` and sends nothing stale. The 160 stale sends earlier that morning
  predate the rebuild, and they burned the owner's daily cap, which is why his feed then went
  quiet: the cap frees itself as the 24-hour window rolls.
- **A v1 config's own answers were being ignored.** `Eligibility.from_config` reads sections; a
  v1 row keeps `posted_within_hours` and `exclude_keywords` at the top level — so the account that
  asked for 72 hours and typed exclusions was getting the 24-hour default and none of them.
  `from_config` migrates first now, inside the method so no future caller can repeat it.
- **Fresh high scorers are scarce because LinkedIn ingestion does not exist** — the handover's
  known largest gap, confirmed in numbers: 154 fresh jobs in six hours, all scored, none ≥ 60,
  because the enabled no-auth REST sources top out at 57 for this profile and every historical
  ≥ 60 came from the one-time LinkedIn import. The fix is the JobSpy tier, not a filter.
- The Telegram card's age element (`· <1h`) was already the reference's, ported line for line —
  verified rather than changed.

### The auth audit: two findings, both the missing half of something shipped

Prompted by the owner asking whether everything ported from the FlowSolutions reference
(`AUTH_REUSE.md`) actually works. The §3 decision table checks out line by line — argon2id at the
production parameters, tokens hashed at rest, 15 min/30 day TTLs, cookie flags and the scoped
refresh path, the 12-character minimum, no v1 path. What the audit found was not a wrong decision
but two flows whose libs half existed and whose route half did not:

**The emailed verification link pointed at a POST-only API path.** `{PUBLIC_URL}/auth/verify?...`
— a mail client GETs, and the answer is a 405 JSON error. Both accounts were migrated rather than
signed up, so no human had ever clicked a real link. Both emails now point at the SPA's hash
routes (`/#/verify`, `/#/reset`), and a test pins that.

**Password reset did not exist.** `reset_password` tokens, their one-hour TTL, and the mail
template all shipped in Phase 3 with nothing calling them — a forgotten password was a locked
account, on a platform whose accounts hold paid credentials. `POST /auth/forgot` (opaque always,
same no-oracle rule as login; a fresh link revokes the previous one) and `POST /auth/reset`
(strength-checked, revokes every session, does not log in — proving control of an inbox is not a
login, and a verification token is not a reset token) close it, with `#/forgot` and `#/reset`
pages behind the sign-in form's "Forgot your password?".

**Password change from settings**, same commit family: `PUT /account/password` with the
reference's semantics — current password required, new must differ, every session dies including
the one asking, cookies cleared in the response rather than left to expire.

**And CI scans for leaked secrets.** Gitleaks over the full history on every push, because a key
committed and then removed is still in every clone. The first full-history scan found exactly one
thing: a test's literal `"argon2-placeholder"`, now pinned by fingerprint in `.gitleaksignore`
so the suppression cannot widen. The local-only credential files were confirmed gitignored.

### The app looks like the landing page, and the jobs page becomes a table

The two top items of the handover's work order, plus the setup-chat fixes beneath them.

**The design system is the landing page's** (`mahoraga-jobs-web/src/index.css`), carried into
`web/src/styles.css` wholesale: `#060606`, hairline borders at falling ink opacity, mono uppercase
kickers, pill buttons that invert on hover, the faint dot-grid backdrop, 0.25 s transitions and a
single fade-up on route change. **Dark is the default now, not the system preference** — the
landing is dark-only, and the app opening in white read as a different product. The toggle stays
and offers light, an equivalent inverse built from the same variables, so the palette remains one
set of rules. Every existing class name survived the rewrite; no page needed restyling by hand.

**`GET /feed` grew the parameters a real table needs**: `offset` and `total` for pagination, `q`
over titles, companies and URLs, `source`/`remote`/`applied`/`max_age_days` filters, and
`sort`+`direction` over score, posted date, title and company. The age filter keeps undated rows —
the same fail-open rule delivery uses, because a missing `posted_at` is unknown, not old. The
salary column is extracted server-side per page by `libs/corpus/salary.extract` (regex, free) so
the description still never crosses the wire, and `applied_at` travels with the row. The stage-0
feed takes the same filters in Python, after its scoring loop, so the two stages cannot behave
differently. Seven tests, including one asserting the page and its `total` are computed under the
same filters — a pager that says "1 of 3" against a differently-filtered count lies.

**The jobs page renders that**: sortable column headers, filter chips, a debounced search box with
a response sequence guard (a late answer to an old query must not overwrite the current one), and
prev/next paging. The filters live server-side deliberately — filtering the loaded page in the
client silently lies whenever the answer is on a page that was never fetched.

**The setup chat's file list works now.** The entries linked `#/setup`, a route that does not
exist, so clicking one navigated to the overview instead of opening the file — the "clicking does
nothing" from the handover. They are buttons now, in a card grid with a monochrome icon per file
kind and short human names ("How you work", not `02-behavioral-profile.md`). The pending line has
typing dots beside the honest sentence about minutes — the dots say working, the words say how
long, and neither alone is enough.

972 passed against a real Postgres; `openapi.json` and `api-types.ts` regenerated.

### The instruments, and the nine bugs they found

Most of today was building things that measure, then believing them when they disagreed with me.
The measurements are described last because the bugs they surfaced matter more.

**A subscription OAuth token needs the Claude Code identity block, or it 429s.** The entitlement is
checked against the *first system block*; anything else is refused with `rate_limit_error` — an
error that reads as "out of quota", is politely retried by every client, and never clears because
nothing was consumed. Measured live: identical call, 429 with a plain system prompt, 200 with the
block in front. Haiku passes either way, which is why it hid — labelling and CV builds both run on
Haiku and worked, while the Opus setup agent would have failed on **every OAuth account**, telling
users they were out of quota.

**Transport retries went 8 to 2.** They were raised to 8 that morning while chasing the above; with
the cause fixed, 8 is a spend multiplier. A 429 is refused before generation and costs nothing to
retry; a 529 mid-stream has already produced tokens, and retrying re-runs the reasoning on the
user's money. The reference allows one, transient only, because "retrying just burns another call".
The logic-level retry (`MAX_TAILOR_CALLS`) already matched at one; both are asserted now.

**CV builds were failing silently on the token ceiling.** `stop_reason=max_tokens` with zero text
blocks: the model finished reasoning and never started the document. The thinking budget is spent
*inside* `max_tokens`, and a CV is around 6,000 tokens of HTML, so 16,000 left no margin. Raised to
32,000, which costs nothing until used. Found within an hour of adding the diagnostic that
distinguishes truncation from refusal — the log previously could not.

**The recency window was read from where nobody stores it.** `posted_within_hours` lives on each
`SearchSection`; `build_source_configs` read it from the top level of the config. Every generator
yielded zero, `max()` fell through to its default, and every harvest ran at 72 hours regardless of
what anyone asked for. The test added alongside the `hours_old` fix did not catch it, because it
asserted only that two keys agreed — they did, both at the default.

**The age filter never reached LinkedIn, Indeed or Google.** Those pass `hours_old` to the scraper
and ignore `max_age_hours`, which was the only key being set. The reference sets both and its
comment says why. Currently moot in production: `DEFAULT_ENABLED` is seven no-auth REST sources and
the JobSpy tier is not enabled at all — every LinkedIn row in the corpus arrived in one import on
2026-07-27.

**Chrome printed its own header onto every CV with margins.** `--print-to-pdf-no-header` is the old
flag name and current Chromium ignores it; an unrecognised flag is not an error. The symptom is a
date, a title, a `file:///` path and a page number on a document sent to an employer. It hid because
`@page { margin: 0 }` left no margin box to draw into — which was itself the bug giving page two no
top margin. Two defects cancelling out. Both fixed: margins onto `@page`, where they repeat, and
both flag spellings passed.

**A saved proxy URL never created a `proxy_credentials` row**, so `acquire_lease` had nothing to
grant and the LinkedIn tier skipped the account silently, forever.

**Excluded keywords stopped applying once a model was trained.** They were read only by the
cold-start keyword scorer, so a term the user typed specifically in order not to see it came back
the day their first training finished. Now a hard gate at delivery, on title and company only —
matching the description turns "no recruitment agencies" into a filter that eats most of a feed.

**A delivered job was shown as borderline.** The card called anything under 80 a yellow dot while
delivery sent everything at 60 and above, under a comment claiming the two matched. One number now,
passed from the send decision into the card.

**The score schema was rejected by the API on every call.** `additionalProperties: false` is
required on every nested object and was on none of them. Invisible because the account's labels were
migrated rather than produced here, so a permanently broken call looked exactly like an empty queue.

### What was built to find them

`libs/scoring/cveval.py` — the reference's objective checks, ported. `eval_cv.md` opens by saying
keyword coverage and bullet metrics are already computed and must not be re-graded, and the judge
was being handed a character count. A model told a measurement exists and then not shown it does not
abstain; it estimates. Giving it the real numbers moved scores about 11 points. The module is
careful about what it claims: **it is not an ATS**. It measures what holds whether screening is
boolean search or a learned ranker — is the text extractable, are the posting's own terms present —
and none of it is a target, because optimising for exact-term coverage *is* keyword stuffing.

`deploy/scripts/calibrate_judge.py` — three probes whose ordering is not in doubt: tailored over
untailored, the right job over a distant one, real over a mechanically degraded copy. All three pass
by 23 to 51 points, so an A/B run on this judge measures something real. Its first version failed
the wrong-job probe, and the *experiment* was wrong rather than the judge: both jobs were drawn from
the same band above the delivery threshold, which for this account meant three variants of one role.

`deploy/scripts/ab_prompts.py` — holds everything constant except the prompt bytes, and reports the
weakest dimensions plus the judge's own fixes ranked by repetition, because a mean says a prompt is
losing and not what to change about it.

`libs/obs/timing.py` — per-stage durations with the slowest named explicitly, and end-to-end
freshness at delivery from two clocks: age from posted, which is what the user experiences, and from
first seen, which is what the platform controls. No stage duration predicts the second, because most
of the latency is spent *between* stages.

### Feed correctness

**The age filter could only drop what it could date.** Of 216 jobs pending delivery, 68 carried a
`posted_at` — LinkedIn reports none to the listing API. The front of the queue is now dated
immediately before selection, on the user's own proxy, capped at about one digest, which is the
order the reference uses. First run after the fix: 33 dropped as too old and 17 as closed, where all
50 would previously have been sent.

Also ported or added: `libs/corpus/ranking.py` (opportunity ordering — youth and scarcity, with fit
as a tiebreaker only), `libs/corpus/langfilter.py`, the per-user age window applied at delivery, and
closed-posting detection stored on `corpus.closed_at`.

### Prompts

**Role angles are per user now.** The shipped `tailor_cv.md` still argued in one person's
vocabulary — two role lenses, and adjacent-skill examples naming FastAPI and Polars. Lifted into
`08-role-angles.md`, written by `/setup-role-angles`, injected at build time where `<<ROLE_ANGLES>>`
sits. Measured indistinguishable from the inline version (+1.7 across six jobs), so the split costs
nothing and a new user gets a mechanism that provably works.

**One prompt hypothesis was tested and falsified.** A variant capping length at one page while
cutting the competencies block scored 3.8 *below* current, and its own feedback said why: "far too
thin — one visible role, no education section". Cutting the keyword wall is right; capping total
length at the same time took the space away instead of moving it. The competencies finding is
confirmed on the *winning* variant, whose most-repeated fix is to cut it.

### Two of the instruments were themselves wrong

The judge did not know what day it is, and twice called a current role "a credibility killer — a
future start date". A CV is mostly dates, so a judge without a today marks down the most recent work
on every candidate, in the dimension the holistic score weights most heavily.

And the ranking tests expired overnight: fixtures pinned to a fixed date while `select` had no
notion of now, so every check inside it read the machine clock independently. `select` takes `now`
now — a caller that cannot say what time it is cannot be tested about time at all.

---

## 2026-07-28

### Delivery is on, and two things the first real build found

The end-to-end path works: a Build CV tap enqueued, `docgen` claimed it, one Haiku call, Chrome, and
a one-page PDF arrived in the right chat 105 seconds later. `applied_at` set, the corpus row pinned,
the scratch directory gone. Both timers and the notifier are running.

**The notify dispatcher had no schedule.** `services/notifier` claims from the `notify` lane and
nothing put anything in it — `--tick` existed and was wired to nothing. Starting the worker would
have delivered exactly nothing, while looking healthy: container up, no errors, no messages.
`deploy/notify-dispatch.{service,timer}` now runs a pass every five minutes.

**A profile edit re-versioned the user, and `rubric_relevant_digest` was written to prevent that and
never called.** `save_profile` bumped `profile_generation` unconditionally, so adding a contact
detail would have invalidated every label — 3,310 of them on the account that found this — and
triggered a paid relabel to answer a question that had not changed. The digest is now compared
before the bump, and the facts block is rewritten either way, because a new link belongs on the next
CV rather than waiting for a change that happens to re-version.

The digest also hashed the whole `identity` object while the rubric substitutes `identity.name` and
reads nothing else about a person. So the field it was most likely to be asked about — a link, a
phone number — was the one it treated as rubric-relevant. The paths are dotted and resolved now, and
a typo raises rather than silently dropping a field from the hash.

**Found by the same build: an empty `links` map produces a CV nobody can reply to.** The ATS check
said so — "no email address in the extracted text" — which is exactly the warning it was written
for, on the first real document it saw.

### The overview follows the onboarding pass's rules

Three bordered panels of equal weight, which meant the thing to do next looked exactly like the
delivery settings — and the whole argument for this page is that the order is **not** guessable:
processing invalidates every label, relabelling costs real money, training is free. A page that
presents one next action and then frames it identically to a settings readout has given the
information back.

Now the state is the headline, the server's explanation is the line under it, the one action is the
only control on the page, and everything else is quiet text at the bottom. The three numbers stay —
they are the answer to "is this working" — as figures rather than a table, because a table implies
rows worth comparing and these are three unrelated facts.

The split between what the server says and what the page says is the same one the wizard uses: the
server owns the explanation (`next.message`), the client owns the four words above it. Cards remain
where a page really does hold several unrelated things: the feed, the privacy disclosure, settings.

### docgen: a tap becomes a tailored PDF (Phase 5b)

The `docgen` lane has existed since Phase 1 with **no worker** — the same shape of gap the `score`
lane turned out to be. "Build CV" taps enqueued correctly and waited. `services/docgen` is the
worker: one Claude call on the user's own credential, Chrome for the PDF, a Telegram upload, and a
scratch directory that does not outlive the job.

**Python does the mechanics and the model makes one call**, which is the reference instance's
measured design: its agentic build re-read the profile, the skills and the template every turn, and
that loop rather than the writing was most of the cost.

Four things are enforced after generation rather than asked for, because each is a rule the prompt
already states and the model still breaks:

- **The CSS is re-injected from the template.** Stripped before sending, restored byte-for-byte
  after — so "the model only changes text" is true rather than requested, and the layout is always
  the platform's whatever came back.
- **Em-dashes come out of prose and bullets.** The prompt's most-broken rule; they are the model's
  default habit and its self-check is aspirational.
- **Hidden content is stripped, and then the result is scanned.** If anything invisible survives,
  **the CV is not delivered at all.** Invisible keyword stuffing looks perfect and gets the person
  filtered out — or blacklisted — by the system it was aimed at, and they would never learn why.
  Failing closed is the recoverable outcome.
- **The page count is measured from a real PDF**, not estimated from the markup, and a CV over two
  pages gets exactly one revision. The instruction says *cut*, never *shrink*: a model told to
  shorten will otherwise reduce the font, which produces an unreadable CV — and the CSS is restored
  afterwards anyway, so the attempt is silently undone and the length unchanged.

**Deliver, then record.** A crash between them costs a duplicate delivery; the reverse order costs a
user who thinks they applied and did not. `applied_at` is set by the first build and never moved,
and the corpus row is pinned so retention cannot remove the description out from under it — the
durable half of an ephemeral document (D9).

**The quota is checked at the tap, not in the worker** (`TENANT_SAFETY.md` §3). A limit checked
halfway through a build has already spent the call it was protecting, and the popup names the limit:
a button that silently does nothing is how somebody concludes the product is broken. `jobs_today`
counts enqueued jobs rather than successful builds — a failed build still spent the money.

Two supporting changes: `libs/llm` grew `complete_text`, because a tailored CV **is** an HTML
document and asking for one inside a JSON string field would have the model escape a few thousand
characters of markup by hand. And the template's fonts are now resolved by name through fontconfig
instead of by relative `@font-face` url() — `Dockerfile.docgen` installs `fonts-lato` and fails the
build if `fc-list` cannot see it afterwards, because the failure it replaces was Chrome silently
falling back to Arial.

**Built, not deployed.** The image has never been built on the box. 730 passed.

### The two tailoring prompts stop being about one person

**SACRED-prompt change, approved 2026-07-28**, and the approval was for exactly this: remove the
reference instance's specifics, keep every rule. Both prompts already read `<<NAME>>` and
`<<LANGUAGES>>`; what remained named no person and was still about one — "cut the Quantitative
Research — Global Liquidity entry", "keep the quant tooling (Bloomberg, ByBit API)", "rename Crypto
Screener", a metrics list of one CV's numbers, and a Hungarian formal-address rule in the cover
letter complete with its grammar.

Dead instructions for most users and an active hazard for the rest: told to keep the quant tooling,
a model writing for somebody who has never touched a trading terminal will still find something to
put there. Each became the general rule it was a special case of — cut pure-research entries for an
engineering role, keep the specialist tooling **the profile states** for a specialist one, use the
formal register of whichever language the letter is written in.

The guard test grew a second half. It asserted no *name* survived, which the cover letter passed for
a day while carrying "HUNGARIAN" in capitals — the check was case-sensitive — so it now scans for
the specifics too, word-bounded, and case-insensitively for the language rule.

Also fixed: the subject-verb agreement the original find-and-replace left behind ("the concrete work
they describes", "the single most relevant thing they has built"). A prompt written in broken
English is a prompt whose style rules the model has less reason to respect.

### The CV template is rendered from the profile (Phase 5b)

The prompts were generalised earlier today and **the template was not**, which left the worst of the
two half-states available: a docgen worker built on top of it would have produced a correct-looking
CV carrying the reference instance's employment history under a real user's name. The identity was
already out of the header; this is the body.

`libs/templates/cv.py` fills eight markers from `ProfileSchema` — the same deterministic-render job
as `rubric.py` and `facts.py`, and for a sharper reason: the rubric decides what a user is shown and
the facts block grounds a generation, while this ends up as a PDF attached to a real application.

**What it produces is the input to tailoring, not the output.** `tailor_cv.md` receives the rendered
CV as its BASE TEMPLATE and cuts to the 4–6 entries that earn their place for one job. So the
renderer's job is completeness and truth; it selects nothing.

Three decisions inside it:

- **A section with no data is omitted whole**, not left as an empty heading. The prompt is told to
  keep the template's sections, so an empty "Projects" title survives tailoring into the PDF.
- **`Certifications` became a schema field.** It was cut on the first pass for want of one, and
  that was wrong in the direction that matters: the section disappearing silently removes a
  credential the candidate actually holds. `ProfileSchema` version 2 adds `certifications`
  (name, issuer, year, all free text), capture fills it with no prompt change because the model
  is handed the schema itself, and the facts block lists them — which is what makes "never
  invent a certification" enforceable rather than a hope. A certification is the most checkable
  claim on a CV: an employer can verify it with the issuer in a minute.
- **`projects[]` got its own section.** The user answered `experience` and `projects` separately;
  a project presented as employment is the kind of overstatement that ends an interview.

Everything from the profile is HTML-escaped: capture reads uploaded documents, which are untrusted
text, and the output goes to a headless Chrome. `links` values are only turned into anchors when
they look like a URL or an email — a phone number in that map was otherwise a header that looked
right and a dead link in the PDF.

Still open, and now written down where the next change will hit it: the `@font-face` paths point at
the reference instance's tree. Nothing renders a PDF yet, so the fix belongs in the change that
builds the docgen image. Chrome would fall back to Arial silently. 694 passed.

### The feedback loop (Phase 5a)

`user_labels.source = 'feedback'` at `weight = 5` has been in the schema since Phase 2 with nothing
writing it. **This is the half that makes the product improve** — everything upstream ranks jobs by
what a model thought against a rubric, and this records what the person actually did, which is a
decision rather than an opinion.

**A webhook rather than polling**, now that the API is publicly reachable through the tunnel: one
request per tap instead of a per-user `getUpdates` loop with its own worker and offset bookkeeping.

**Three things stand between a stranger and somebody else's training data**, and none is redundant:

1. a **per-user path secret** — every user runs their own bot, so the URL is what says whose tap it
   is; a shared path could only answer that from the request body, which is attacker-supplied;
2. Telegram's **`secret_token` header**, compared in constant time — a path leaks into proxy logs,
   browser history and screenshots, and the header is what makes a leaked path insufficient;
3. **the tapper must be the account owner.** A feed channel has other members. Without this, anyone
   who joined to browse jobs could teach somebody else's model — not a privacy leak but a slow,
   invisible corruption of the one thing the product is built on.

**Applied is 90, not 100.** People apply to things they are unsure about; saturating the scale would
teach the model that every applied job is identical and throw away the ordering that makes a feed
useful. A second tap does not move `applied_at` — that column is the date somebody actually applied.

**The digest became one message per job, reversing an earlier decision.** Batching avoided a burst
of notifications, which was a good argument that lost to a better one: a Telegram keyboard attaches
to *a message*, so a digest structurally cannot carry per-job buttons. Acting on a match is worth
more than saving nine notifications, and the burst is still bounded by the per-delivery and daily
caps. The reference instance's watcher sends per-job cards for the same reason.

The notifier renders the keyboard and the API parses the taps, and they may not import each other —
`callback_data` is the entire contract between them, so a test reads both sides and asserts they
agree. Migration `b8e2d05f713c`. 657 passed.

### Two deployment bugs the containers found, and the drill did not

Both were invisible until something actually ran in a container.

**Artifact paths do not survive leaving the host that wrote them.** `models.path` records an
absolute path from the process that trained the model — a script on the host — while the reader is
a container with the artifacts mounted somewhere else. The scorer crash-looped on `FileNotFoundError`
for a path that was perfectly correct on the machine that produced it. The filename is now the
identity and `MODEL_DIR` is the location; a stored path that does exist is still used unchanged, so
host-side scripts keep working.

**The restore drill did not catch this**, and it is worth saying why: it checks that a `models` row
is servable — the version columns agree — not that the file behind it resolves. On a restore to a
new host it would have reported a healthy platform that could not score. Worth fixing in the drill.

**`:?` in compose blocks every command, not just the service that needs it.** A required
`TUNNEL_TOKEN` made `up scorer` fail with a message about cloudflared, twice. The rule now written
down: `:?` only where absence is *silently* dangerous — a worker that starts without `SECRETS_KEK`
claims jobs and dead-letters every one — and `:-` where the process fails loudly on its own.

### A CI job that installs each service alone and imports it

The same bug shipped three times in one day: `httpx` missing from `api`, then `cryptography` and
`httpx` missing from `scorer` and `trainer`. All are reached *indirectly* — `libs/obs` imports
`libs/secrets`, which needs cryptography; `services/scorer/serve.py` imports
`libs/contracts/embedder`, which imports httpx — so a development environment with everything
installed cannot see the gap, and the failure lands as a container that will not start.

The new `service-deps` matrix installs each service's requirements **alone** and imports its
entrypoint. It caught the second missing dependency within a minute of being written, on a service
I had already "fixed".

`harvester` was relying on cryptography arriving transitively through another package. It works
today and would break on any dependency bump that dropped it — declared explicitly now.

### The `score` lane — closing the gap between harvest and delivery (Phase 4c)

The harvest cycle stored and embedded; the notify worker delivered what was already scored. **Nothing
turned a corpus row into a score.** Both live accounts were 398 rows behind and growing at the
harvest rate, and the monitoring could not have caught it: once the scored backlog is delivered,
`pending_matches` falls to zero and the silent-feed alert goes quiet, because from outside a feed
with nothing to send looks exactly like a feed with nothing new.

**A lane rather than a call from the notifier**, because `services.notifier` may not import
`services.scorer` — and here that contract is load-bearing rather than ceremonial: the scorer's
fail-closed version checks belong in the one deployable that owns them. A lane also inherits
round-robin fairness, so one account's backlog cannot starve another's.

**A missing model is not a failure.** The harvest cycle enqueues for every active account and a new
signup has no artifact until its first train; dead-lettering those would fill the queue with red for
the one state every account passes through. The worker logs it at info and the next cycle asks again.

The rest is the same shape as the other lanes: one queue job is one batch, the continuation clears
its own dedup key before re-enqueueing, and `scored_model_version` makes a re-run cheap while a
retrain correctly invalidates everything at once.

**Deploy artifacts for the remaining four services** — `api`, `notifier`, `scorer`, `trainer` —
behind a `full` compose profile, so a bare `up -d` still starts only Postgres and the two credential
workers. Starting delivery messages real people, and that should be a decision rather than something
that happens because somebody typed `up`. The API publishes **no port**: it is reached through the
Cloudflare Tunnel (D7), so the VPS needs no open inbound port at all.

Migration `a7e3f9c14b62`. 638 passed against a real Postgres.

### Backups, and a restore drill that passed (Phase 4b)

Postgres is the single point of failure by design, and under D4 it is self-managed — no provider is
taking backups off anybody's hands. Nightly `pg_dump`, verified as it is written, 30-day retention.

**The KEK is deliberately not in the backup, and `backup.service` cannot read it.** Two silent ways
to get this catastrophically wrong: the key beside the dump makes the backup a complete credential
breach in one file; the key nowhere makes a restore a database of undecryptable secrets and every
user re-enters every credential. The surest way to keep them apart is for the process that writes
one to be unable to read the other.

A partial dump is deleted rather than left behind — a file that looks like a backup is worse than
no backup, because somebody will find it and believe it.

**The drill passed against a real 20 MB backup of the live platform.** It checks the three things
that have to be true afterwards, not just that the file loads: row counts match the live database
(2 users, 4,287 corpus rows, 3,474 labels), a stored `telegram_bot` credential **decrypts with the
separately-held KEK**, and 2 of 2 models come back servable. The second is the entire KEK-separation
design under test, and the only way to learn it works is to try it.

**It found a bug on its first real run**, which is the argument for having a drill rather than a
procedure. `subprocess` wants a real file descriptor for `stdin`, and `GzipFile.fileno()` returns
the descriptor of the *compressed* file — so psql was fed raw gzip and failed with
`invalid byte sequence for encoding "UTF8": 0x8b`, which reads like a corrupt dump rather than a
mistake in the restore path. A backup nobody has restored is a hope.

### Monitoring and alerts (Phase 4a)

**Every failure on this platform is silent** (`OPERATIONS_PLAN.md` §1). A stalled queue, a harvester
finding nothing, a retention trim that quietly stopped — none of them raise, and each is otherwise
discovered weeks later by a user asking why their feed went quiet. `libs/ops/health.py` makes them
numbers, `libs/ops/alerts.py` decides which need a person.

**The design is entirely about not crying wolf.** An alert that fires for a condition nobody acts on
trains its reader to ignore the next one, and the next one is the outage. So:

- the silent-feed alert requires **matches waiting** as well as no delivery. Without that qualifier
  it fires constantly for anyone with a narrow config, who is not broken — they simply have nothing
  this week;
- a stall is measured against **that lane's** normal wait, not a fixed number: a `notify` job
  waiting an hour is broken, a `label` job waiting an hour is a queue doing its job;
- one dead-lettered job pages, because the queue retries five times before giving up — one dead job
  means five failures nobody saw;
- there are two severities, not three. A third becomes the one everybody filters out.

Every alert names an action. "Look into it" is not one.

**The exit code is the interface** for `check_health.py`: 0 nothing, 1 warnings, 2 a page. It emails
too, but a monitoring system whose own failure is silent is exactly what this section exists to
prevent — so `health.service` sets `SuccessExitStatus=0 1`, which makes a page a unit failure that
`systemctl --failed` shows even with mail down.

**`GET /admin/health` and `/admin/users`**, read-only. `OPERATIONS_PLAN.md` §5 describes an
action-capable console; shipping the reading half now makes failures visible before the first
invited user, and shipping half of the acting half would be worse than none. Admin is
`ADMIN_EMAILS`, read per request — granting it is a deploy, which is the friction it should have,
whereas a boolean column is one UPDATE away from privilege escalation audited in the database it
grants access to. An empty variable means **nobody**, not everybody. Non-admins get 404 rather than
403: a 403 confirms the endpoint exists and that somebody is worth phishing for access to it. The
user list carries no credential material at all, not even `last_4`.

### Monochrome, with a theme toggle

Black, white and grey. Two colours survive — a red for errors, a green for a score — and both mean
one thing each, which is why they read instantly; a red among six other hues is decoration.

**Light and dark on every page**, including signed-out ones. Three states rather than two: `light`,
`dark`, and unset, with unset the default so a dark-system viewer gets dark without being asked.
Pressing the toggle leaves unset permanently — a three-way control is a puzzle in a corner nobody
wanted to solve.

**The theme attribute is written before first paint** by a snippet in `index.html`. A React effect
runs after the first frame, so a dark-mode viewer would have seen one white flash on every load —
the detail that makes the difference between a toggle that feels built in and one that feels bolted
on. Transitions are colour-only at a single 140 ms duration, and `prefers-reduced-motion` removes
them.

Andras's four candidate-profile files imported alongside his documents: nine documents, 204,388
characters.

### Both reference owners' documents imported

`import_documents.py` — everything the upload form does, for a folder: same extractor, same content
hash, same replace-on-re-upload, and the file itself never stored. Andras's five (a master CV plus
four long-form context files, 158,975 characters) and Ruben's four candidate-profile files (28,949).

**What was deliberately left out, and why.** Ruben's `documents/` folder is empty — his profile
content lives in the assistant's skill files — so the import took the four that describe *him* and
skipped `04-job-evaluation.md` (that is the rubric, already migrated as `rubric_md`), the CV and
cover-letter templates (templates, not documents about a person), and the `.html` base CVs. Feeding
template markup to the processing step would produce a profile built partly from HTML.

### Per-category Telegram channels

A single feed of everything is a feed people stop reading. The reference instance splits it by the
distinction its owner actually makes when scanning — jobs at home, jobs on LinkedIn, everything
else — and that split is now a platform feature: `users.telegram_routes`, JSONB, every route
optional (`f1d84c2e9b07`).

**The ordering is the part that is easy to get subtly wrong, so it is ported verbatim and tested.**
Hungarian jobs win first, *including a LinkedIn job in Budapest* — somebody who wants local roles
wants them wherever they were posted, and routing that one to the LinkedIn channel hides it in the
stream they skim least. LinkedIn wins second, as the highest-volume source worth isolating.

**A missing route is a fallback, never a drop.** Anything unrouted goes to the personal chat, so an
account with no routing table behaves exactly as it did before this existed. Routing is resolved
per job rather than per digest: deciding once for a batch would put a Budapest job in the LinkedIn
stream whenever it happened to be batched with one.

One limitation asserted rather than hidden: the Hungarian match is a substring on the location
string, so "New Budapest Restaurant Group, Chicago" routes to the HU channel. Parsing a free-text
location properly costs real Budapest jobs whenever the parse disagrees; the bug costs one misfiled
job in a channel the user reads anyway.

`set_telegram_routes.py` asks Telegram whether the bot can actually see each channel before storing
it — a channel the bot is not an admin of accepts the configuration and then rejects every
delivery, which the user experiences as an empty feed.

### `set_credential.py` — storing a credential before the API is deployed

`/credentials` is the real path and this does not replace it. It exists because the `api` container
is not on the box yet, and the two migrated accounts cannot supply a Telegram token without it —
which meant nothing could be delivered to either.

**The value is prompted for, never taken as an argument.** A credential on a command line is in
shell history, in `ps` output for every user on the box, and in any tracing that happens to be on.
`--stdin` covers scripted use without any of the three.

Validated against the provider by default, as the endpoint does, and the Telegram chat id is
captured automatically — the step that confuses people more than the rest of setup put together.

### The web layer (Phase 3 step 12) — Phase 3 complete

React + TypeScript on Vite, ready for Cloudflare Pages behind the Tunnel (D7). Seven onboarding
pages, dashboard, feed, profile confirmation, settings, and the public privacy page.

**`openapi.json` and `src/api-types.ts` are committed, and CI regenerates and diffs them.** That is
the mechanism `web/README.md` specified in Phase 0 and it earns its keep immediately: changing a
response shape without regenerating fails the build with the command to fix it, instead of
surfacing as a runtime `undefined` in front of a user weeks later with nothing pointing at the
commit that caused it.

**Two runtime dependencies, 69 kB gzipped.** No router — this is a wizard plus four screens, and
`react-router` would be more configuration than routing. No component library — what one would have
given us is a hundred lines in `src/ui.tsx`. A hash router rather than history, so Pages serves
`index.html` for every URL with no rewrite rule to forget.

**Nothing in it re-derives a server rule.** Which onboarding page you are on comes from
`GET /onboarding/state`; what the account needs next comes from `GET /dashboard`. Those rules live
once, in `libs/onboarding` and `services/api`. A copy here would be the one that goes stale, and the
first symptom would be somebody stuck on a page they had already finished.

**One refresh at a time** (`src/api.ts`). A dashboard load fires several requests; if each refreshed
on its own 401 the second would replay a token the first had rotated — and the server treats a
replayed refresh token as theft and revokes the entire session family. Every caller waits on one
shared promise.

The web CI job turned out to be a **second check for free**: it installs only
`services/api/requirements.txt` and then imports the app to dump the schema, so a dependency the
service imports but does not declare fails there. It caught `httpx` on its first run — declared by
the harvester and notifier, used by `libs/credentials`, and missing from the API's list, which
would have shown up as the `api` container failing to start on the box.

Known gap, written down rather than glossed: FastAPI types most handlers as a bare `dict`, so the
generated types cover paths and request bodies but not response *contents*. The response shapes the
components read are narrow hand-written views at the bottom of `src/api.ts`. Annotating every
handler with a response model closes it and is the right fix if that drift ever bites.

### Deploy artifacts for the two credential-spending workers

`capture` (the `process` lane) and `labeller` (the `label` lane) now have Dockerfiles and compose
services. Separate images despite identical dependency lists: they are separate lanes with separate
restart cycles, and one worker's dependency bump should not rebuild the other
(`CODE_STRUCTURE.md` §5).

`SECRETS_KEK` is `:?`-required for both. Neither can do anything without decrypting the user's
credential, so starting without it would produce a worker that claims jobs, fails every one, and
dead-letters a user's queue — a failure that reads as a provider outage.

Also corrected `services/capture/requirements.txt`, which still described a Managed Agents client
three commits after that client was deleted.

**Not applied on the box.** SSH from the build machine is refused, so the three pending migrations
and the `up -d` for both workers are written down in `deploy/README.md` with the reason each is safe
to apply late, rather than left as something to remember.

### The GDPR obligations, as behaviour (Phase 3 step 11) — Phase 3 backend complete

All three are things the product does now rather than things a document claims.

**`GET /legal/privacy` names each processor** — Anthropic, Telegram, the proxy provider, with what
each receives, what for, and whose credential pays. Public, because signup links it before anyone
has an account. Served from `libs/legal` so the policy and the consent screen cannot drift; a test
asserts they are literally the same object, because the drift would be invisible and a consent
recorded against a disclosure that no longer describes reality is worse than no consent screen.

**Consent at *process data*, not at upload.** The obligation was written when capture uploaded files
to Anthropic's Files API and it named upload as the moment. Files no longer leave this server, so
nothing crosses a processor boundary until that button is pressed — asking at upload would ask about
something that had not happened and be stale by the time it mattered. `/process` returns 403 without
it, checked in the request rather than in the worker where the refusal would be invisible for hours.

It is **version-compared**, not merely present: `users.processing_consent_version` records which
disclosure was agreed to, so naming a new processor makes every stored agreement stale by
construction. That is the point, not an inconvenience to route around.

**Deletion, proved rather than asserted.** `DELETE /account` requires the password — irreversible,
and a stolen session cookie must not be enough. The test enumerates every table in the metadata
carrying a `user_id` column and asserts none survives, so adding an eleventh table and forgetting
its cascade fails there rather than leaving a stranger's rows behind for a year. The DEK is
crypto-shredded in the same request: a backup restore that resurrected a deleted user's Claude key
is the worst outcome a delete request could have.

One bug caught by the tests: `verify_password(stored, password)` was called with its arguments
reversed, which would have rejected every correct password at the deletion gate.

Migration `e5c9b71a4d38`. **Phase 3 steps 1–11 are done**; step 12, the web layer, is what remains.

### The dashboard and a stage-aware feed (Phase 3 step 10, backend)

`GET /dashboard` in one call, because the panels are not independent: "you have 3,310 labels" and
"your model is stale" contradict each other if they are read a few seconds apart while a worker is
running. It reports what the account needs *next* rather than offering four actions and hoping they
are pressed in order — the order is not guessable, and a user who gets it wrong pays for it.

`GET /feed` says which stage ranked it. Before a servable model exists it is keyword matching, and
presenting that as "your model" would make the product look broken when the real model arrives and
the ordering changes completely.

**The design correction the tests found.** The dashboard first branched on live onboarding
completeness, so breaking a Telegram token threw a fully onboarded account back to the signup
wizard — hiding the only screen that explained what broke. It now branches on the stored
`onboarding_completed_at`, which is exactly the distinction that column exists for, and regressions
surface as banners.

A stale model is **named**: "your profile changed after this model was trained, so your jobs need
relabelling and a retrain". A greyed-out button is not something a person can act on.

### Cold start: a feed in 30 seconds, a model in 30 minutes (Phase 3 step 9)

**Stage 0 — `libs/scoring/rules.py`.** A score from the config alone: no model, no LLM, no
embedding. It cannot tell a good backend role from a bad one, because nothing has told it what good
means yet. Its whole job is to be non-empty and not obviously wrong in the seconds after somebody
saves a config, and that is worth building because the most common thing a person does with an
empty page is close it.

**Stage 1 is not built, and that is the decision.** A bootstrap model trained on other users'
labels is a data-use question needing consent; one trained only on the operator's labels is a
separate artifact to build and version. Stage 0 covers the gap it existed to cover, for free and
with no consent surface. Recorded rather than omitted.

**Pool selection** (`services/labeller/pool.py`) bands the rules-ranked corpus 40/40/20 and draws by
**striding through each band, not taking its head**. The head of the high band is 120 near-identical
jobs — often the same employer and phrasing — which is 120 labels' worth of a user's money for one
label's worth of information. The pool is re-selected per batch rather than stored once: it costs a
ranking pass and buys a balanced batch *every time*, so an interrupted cold start leaves a usable
training set rather than 200 likely-positives.

**The `label` lane has one job kind** and `coldstart.next_batch` decides what it means: stale labels
are carried forward before new ones are bought, because jobs the user already cared about are
cheaper per unit of signal. Two job kinds would make every caller re-derive that ordering, and the
first one to get it wrong spends somebody's money re-buying labels they had. When the labels are in,
the worker queues the train itself — 300 fresh labels and no model because nobody pressed a third
button is not a state to leave a person in. A partial run still trains at 100 labels
(`COLD_START_PLAN.md` §7).

### The relabeller, and one shared Anthropic client (Phase 3 step 8)

`services/labeller` scores jobs against a user's rubric with Haiku, in the `label` lane, on that
user's own credential. It is what makes editing a profile survivable: a profile change bumps
`profile_generation`, every label written under the old rubric answers a different question, and
without this the account has **zero** usable labels and cannot train at all.

Three decisions, each of which had a wrong-looking obvious alternative:

- **One queue job is one batch of 50.** The worker commits the handler's effect and the job's
  completion together and forbids a handler from committing its own transaction, so a queue job's
  size *is* the unit of work a crash destroys. At 200 an interrupted worker throws away four Haiku
  calls the user paid for. The re-enqueue chain also lets round-robin interleave two users'
  relabels rather than running one to completion.
- **The continuation clears its own dedup key first.** `enqueue` collapses against any job in
  `ready` *or* `claimed` with the same key — and the running job holds exactly the key its
  continuation needs. Left alone the continuation is silently dropped and a four-thousand-job
  relabel stops after fifty, which looks identical to success.
- **Scores are matched back by URL, never by position.** A model that reorders its output would
  otherwise shift every label onto the wrong job: a training set that looks fine and teaches the
  opposite of what was intended.

Confirming a profile now queues the first batch rather than leaving it to a second button — an
account that cannot train is not a state to leave somebody in because they missed a click.

**`libs/llm`** — the Anthropic adapter moved out of `services/capture`, because two services now
call Claude on the user's behalf and services may not import each other. A copy in each is how the
OAuth header ends up correct in one and missing in the other.

**`libs/queue` now exports its API.** It exported nothing, so `from libs.queue import enqueue` was
an ImportError that took down every API route — see below.

### A local Postgres, and the three things it immediately caught

Docker Desktop plus a throwaway `postgres:16` is now the pre-push check: `alembic upgrade head` and
all **553** tests, rather than the 300 that run without a database. The first run found three
things the CI-only loop had been hiding:

- **The suite exhausts Postgres's connection slots.** 69 `create_engine(...)` calls across 22 test
  files, one per function-scoped fixture, and `QueuePool` returns a connection to the pool rather
  than to the server on close — so a few hundred tests leave a few hundred idle connections and the
  run dies at "sorry, too many clients already". It surfaces as unrelated tests failing at *setup*,
  which reads like flakiness. `tests/conftest.py` defaults engines to `NullPool`, at import time
  rather than in a fixture: a fixture runs after collection, by which point a module-level
  `from sqlalchemy import create_engine` has already bound the original.
- **A test asserted on `record.message % record.args`** — already-formatted, so any log line with no
  args raised `TypeError` instead of asserting.
- **`now()` is transaction-start time**, so a test that set `processed_at` and then uploaded a
  document in the same transaction stamped both identically and could never observe the change it
  was testing. Production never hits this — the worker and the upload are separate transactions —
  so the fix is in the test, with the reason written down.

### Two CI failures, and the checks that would have caught them

Both were pushed without local verification, because the machine has no Postgres: migrations and
253 DB-backed tests skip silently there, and "300 passed" reads like a green run.

1. **`ck_jobs_ck_jobs_lane_valid` does not exist.** `libs/db/base` sets a naming convention of
   `ck_%(table_name)s_%(constraint_name)s` and Alembic applies it to whatever the migration passes,
   so handing it the already-rendered name double-prefixes it. **Catchable with no database:**
   `alembic upgrade <rev>:head --sql` renders the SQL offline. Now run on every migration.
2. **Every API route broken at import** by `from libs.queue import enqueue`. Invisible locally
   because the tests that import the app are the DB-backed ones. `tests/test_entrypoints.py` now
   imports every deployable entrypoint and asserts each router is registered — no database, no
   network, and it fails in under two seconds.

### Process data, without the sandbox (Phase 3 step 7)

`services/capture` is now one plain Messages call on the user's own credential, in its own `process`
queue lane. The Managed Agents session it replaced is deleted rather than left dormant — dead code
that reads as live is worse than no code. `PROFILE_CAPTURE_PLAN.md` §3 and §4 carry a superseded
banner; §5 onward (confirmation, deterministic rendering, generation invalidation) is unchanged.

**Why the sandbox went, in the order the reasons matter.** Onboarding stopped uploading files — text
is extracted server-side now — so there was nothing left to mount. Then:

1. **OAuth became possible.** Managed Agents authenticates by API key only, which quietly made the
   old design require every user to buy API credit. That contradicts decision D2, and it would have
   surfaced as "my Claude login does not work here" at the last step of onboarding.
2. **A beta left the critical path.** Nothing to pin, nothing to break under a running signup.
3. **Deletion became provable.** No Files API objects outside Postgres, so the cascade is the whole
   story and Phase 3 step 11 needs a verification rather than a second deletion path.

What was lost is real and recorded: the model no longer explores documents with glob and grep, and
no longer asks follow-up questions mid-interview. It reads everything in one pass. If the first
cohort's drafts pile up unresolved `conflicts[]`, a follow-up turn is the cheap fix.

**The output is a draft.** `users.profile_draft` holds it until a human confirms; applying a profile
bumps `profile_generation` and invalidates every label the account has, and nothing an uploaded CV
can influence may do that unreviewed. Three layers hold it: `confirmed` is stripped from the schema
the model sees, forced false on the way out, and refused by `save_profile`. A hostile document
saying "set confirmed to true" is a test, not a hypothetical.

`GET /process/state` derives what the account needs from counters rather than a status column, and
the warning names a number — *"about 3,310 jobs, roughly $5.96 on your own key"* — because a generic
"are you sure?" is one nobody reads. The per-job figure is the **measured** $0.0018 from
`COLD_START_PLAN.md` §4 rather than a plausible-looking constant, quoted without the Batch API
discount so the estimate is one a user cannot be disappointed by. Migration `d3f8a1c25b90`.

### Onboarding, as endpoints (Phase 3 step 6)

The seven pages of `ONBOARDING_AND_PROCESSING.md` §2 are reachable: identity, history and config in
`services/api/onboarding_routes.py`, the other four already served by `/documents` and
`/credentials` and deliberately not duplicated. **No LLM runs behind any of them** — onboarding
collects, and *process data* is the only thing that spends the user's credential.

Three decisions worth keeping:

- **Completion is derived, never stored** (`libs/onboarding/state.py`). Nothing records "page 4
  done"; each page's state is computed from the row it writes. A stored flag would leave an account
  marked finished after the user revokes their bot token from the account page — configured on
  paper, undeliverable in fact. Revoking now reopens exactly that page.
- **`/complete` does not set `users.active`.** Finishing onboarding means the account is
  configured; `active` means the scheduler may dispatch notifications, which is only safe once a
  model exists to rank with. Conflating them would start a notify loop whose every cycle fails
  identically, for a reason no user could see. `active` turns on after the first successful train.
- **History is typed, not uploaded.** Nobody has a long-form "everything I have done" document
  lying around, so `PUT /onboarding/history` takes text and stores it as a `history` document —
  one path into processing, whether the words were pasted or extracted from a `.docx`.

`POST /onboarding/config/preview` counts the keywords against the corpus before saving. It is an
approximation and says so in the response — the real feed comes from each source's own search — but
it catches the failure it exists for: a misspelled keyword, which otherwise surfaces as an empty
feed a day later with no explanation.

New columns `users.full_name`, `users.links`, `users.onboarding_completed_at` (`c1a6be40d3f2`). The
name is not derived from the email: it is what appears at the top of a generated CV, and
`r.lekli@…` is not a name.

### The facts block, and a daily cap on outbound mail

**The facts block** (`libs/scoring/facts.py`, `PROFILE_CAPTURE_PLAN.md` §6.2) — the `CLAUDE.md`
equivalent for a service with no CLI to auto-load one. The single-tenant instance gets ~434 lines of
ambient grounding on every generation call; a hosted service gets none, and the 2026-07-15 A/B
measured what follows: stripped of profile context the model invented an employer and fabricated
metrics. On a hosted product that lands on a real application under a real person's name.

It is **distinct from the rubric**, and the distinction is the point:

| | Rubric | Facts block |
|---|---|---|
| Answers | *is this job a fit* | *what is true about this person* |
| Read by | the labeller, the scorer | anything that writes on their behalf |
| Needs | gates, priorities, anchors | evidence, phrasing, exclusions |
| Changing it | invalidates labels and models | invalidates nothing |

Both render from the same confirmed profile in the same write, so they cannot disagree or lag. That
last row is why `refresh_facts` exists and deliberately does **not** bump `profile_generation`:
changing what may be written about someone cannot make a past label wrong, and bumping would force a
relabel and a retrain for nothing (§7).

**A platform-wide mail cap**, rolling 24 hours, default 400 against Gmail's ~500. The provider's own
limit fails silently — messages stop arriving, nothing raises at send time, and the first symptom is
a user saying they never got a link. Attempts are counted rather than successes, because an outage
would otherwise burn the allowance twice and the provider counts attempts too. **The table stores no
recipient address**: a cap needs a count, not a mailing list, and per-address protection already
lives in the signup rate limiter — so it never becomes one more place a user's email lives, or one
more thing an account deletion has to reach into.

**A gap this surfaced, worth naming.** Both live accounts render a facts block, but with no
Experience or Projects sections — their profiles were transcribed from each instance's *rubric*,
which describes strengths in aggregate and names no employer, date, or metric. That is sufficient
for scoring, which is why both feeds work. It is **not** sufficient for generation: a CV builder
would have nothing concrete to draw on. Filling it is exactly the job §1 says a form cannot do and
capture must — read the person's actual CV. Recorded rather than papered over.

Migration `f7c2a4e8d165` adds `users.facts_md` and `mail_sends`. 472 tests.

## 2026-07-27 (night, Phase 3 step 1b)

### The `api` service — signup, verification, login, refresh, logout

FastAPI, one transaction per request, cookie policy copied verbatim from `AUTH_REUSE.md` §3.

- **The refresh cookie is scoped to `/auth/refresh`**, not `/`. The detail most implementations
  miss: scoped that way the long-lived credential is absent from every ordinary request, so an XSS
  on any other route cannot reach it. `HttpOnly` and `Secure` on both cookies unconditionally.
- **No account-existence oracle.** Signup and login answer identically whether or not the address
  has an account, and login verifies a throwaway argon2 hash when the user does not exist so the
  *timing* does not reveal it either.
- **Verification does not log the user in.** A link travels through email and can be forwarded;
  proving control of an address should not hand out a session.
- **A failed verification email does not fail the signup.** The account exists and another link can
  be requested. Making registration depend on Gmail being reachable would be worse.

**Mail is Gmail SMTP with an app password.** One sending account needs no per-user consent and the
OAuth refresh dance buys nothing at this scale. The tradeoff is recorded rather than buried: Gmail
caps a normal account near 500 recipients a day — ample for an invited cohort, to be revisited
before self-serve, and the failure mode is silent rejection rather than an error at send time.

**One real bug, caught by CI:** raising `HTTPException` discards the injected `Response`, so a
failed refresh returned 401 with **no `Set-Cookie` at all** — a client holding a replayed or expired
token would have kept presenting it forever, since nothing ever told it to stop. The failure path
returns a `JSONResponse` with the cookies deleted on it instead.

Two test-fixture lessons worth keeping: `example.test` is a reserved special-use domain and
`email_validator` correctly rejects it (the rest of the tree writes `User` rows directly and never
passes an address through validation), and cookie attributes are case-insensitive — asserting on
capitalisation tests the client's formatting, not the policy.

469 tests.

## 2026-07-27 (night, Phase 2 closes)

### Two accounts, two models, two feeds — on one stack

The remaining third of Phase 2's exit criteria. Both single-tenant instances' labelled history is
now in the shared corpus, and both accounts have their own confirmed profile, rubric, model, and
feed.

```
lekliruben@gmail.com          andraslekli@gmail.com
  23 hard gates                 15 hard gates
  2,529 labels @ gen 1          945 labels @ gen 1
  model v1, servable            model v1, servable
  55 core / 24 off-stack        52 core / 26 off-stack
  3,892 scored                  3,892 scored
  345 high, 804 review          64 high, 207 review

corpus: 3,896 rows, 3,892 embedded
overlap of the two top-100 feeds: 0
```

**Zero overlap is the result worth keeping.** Same corpus, same embeddings, same feature columns —
and the two models rank them disjointly, because the token lists behind `core_stack_count` and
`offstack_count` come from each person's own profile. That is §6.3's whole argument, demonstrated
on real data rather than asserted.

**Retrained rather than imported.** The existing `.joblib` artifacts were fit against each
instance's *hardcoded* token lists; the platform derives them from the profile, so the weights would
have been attached to features that no longer mean the same thing — precisely what the scorer's
fail-closed check refuses. Same labels, same embedder, same feature definitions at train and serve
time. Comparing the platform against the old instances is now an outcome comparison, which is what
D6 wanted.

**Only labels at each reference's newest rubric were imported** — 3,310 of Ruben's 4,283, 947 of
947 for Andras. The rest were written under older rubrics and answer a different question. Ruben's
3,310 then collapsed to 2,529 distinct corpus rows: 781 labelled URLs deduplicated onto postings
already present, which is the four-layer dedup doing exactly what the shared corpus exists for.

**Three bugs found by doing this, all silent, all fixed:**

1. **A hyphenated skill never matched its spaced spelling.** A profile saying `full-stack` matched
   nothing in a posting titled "Full Stack Developer" — the feature simply read 0. Found by diffing
   platform-derived tokens against the reference's hardcoded list on eight real postings; only 4 of
   8 agreed before the fix.
2. **Splitting a compound produced fragments that fire on unrelated text.** `Spring Boot` yielded
   the token `boot`, which matches *bootstrap* and *bootcamp* — one NOT-list entry becoming a gate
   on jobs it has nothing to do with.
3. **`remote_policy: "any"` dropped the location gate entirely.** Andras accepts on-site, hybrid and
   remote, but only in four countries. Workplace *mode* and acceptable *region* are independent, and
   conflating them would have shown him roles requiring relocation anywhere on earth.

Two operational lessons, both paid for: the embed backfill commits per batch now (a whole pass is
hours of work a dropped session throws away), and it sizes its own client timeout (the 120 s default
is for the harvester's small batches, not a 200-text backfill).

New scripts: `set_profile`, `migrate_reference_history`, `embed_backlog`, `train_user`,
`score_user`, `show_accounts`, `set_password`.

**What is left is credentials, not code.** Neither account has a login password or a Telegram bot
token — both are values only their owner can supply, via `set_password.py` (prompts, no echo) and
the credential step. Everything they gate is built and proven.

## 2026-07-27 (night, Phase 3 opens)

### Phase 3 step 1a — auth primitives

`libs/auth`. `AUTH_REUSE.md` option B: port the **design**, not the Go code. FlowSolutions'
`internal/auth` is ~3,900 lines against MongoDB, and lifting it would reintroduce a second language
and a second datastore against D1. The value is the fifteen decisions in §3 — each easy to get
subtly wrong, each already settled in production — so those are copied verbatim while the primitives
come from maintained libraries.

- **argon2id** at the production parameters (`time=1, memory=64 MiB, threads=4, keylen=64,
  salt=32`).
- **Tokens stored as SHA-256 hashes only.** The plaintext exists in the email and nowhere else, so a
  dump does not let anyone verify an address or reset a password. Reissuing revokes the previous
  link, and a verification token cannot be redeemed as a reset — that would turn "can receive email"
  into "can take the account" without the user asking for a reset.
- **Refresh sessions hashed at rest and rotated on every use.** A replayed *rotated* token revokes
  the whole family: either the client is buggy or the token was stolen, and in the second case both
  holders should end up with nothing rather than one silently keeping access.

**Two of `AUTH_REUSE.md` §4 honoured:** the 8-character password minimum is raised to 12 (this
platform holds live Claude keys and proxy credentials), and the v1 hashing path is not ported at
all. The breached-password check is left as a documented seam — adding a network call to signup
without a timeout and a fail-open decision would make registration depend on a third party being up.

**One bug CI caught:** `rotate_session` treated a *revoked* token as a replay, so logging out on a
laptop and then having the browser retry with the stale cookie revoked every other device. Closing a
tab should not log you out of your phone. Only a rotated token is a replay.

Migration `e5f1c0a7b284` adds `user_sessions` and `auth_tokens`. 451 tests.

## 2026-07-27 (night, later)

### Two accounts on one stack — the D6 comparison is live

`andraslekli@gmail.com` migrated from `andras-job-search` the same way the first account was: config
copied verbatim, nothing in the reference touched. 15 search terms, 5 locations.

Both accounts now drive one shared harvest:

```
active accounts: 2
  7533bb9c…  23 terms  [european_union, united_kingdom, budapest, europe]
  d620f6f3…  15 terms  [united_kingdom, ireland, european_union, budapest, europe]

folded query set: 334 distinct queries, 0 wanted by more than one account
budget: 60 planned per cycle, 274 deferred
  each account: exactly 30 of the 60 planned
```

**The 30/30 split is round-robin fairness working in production**, not a coincidence — a FIFO queue
would have given the first account all 60 and the second nothing until its terms aged in.

**Zero shared queries, and that is the honest result.** A software engineer and a mechanical
engineer have no search terms in common, so the query fold saves nothing here. The saving that does
apply is the corpus: one posting is stored, deduplicated, and embedded once regardless of how many
accounts it reaches. 311 rows, 305 embedded, harvested on the 15-minute timer.

`Location.IRELAND` added — not the same as `DUBLIN`, for the reason `UNITED_KINGDOM` is not
`LONDON`: a country-level search returns roles the city one never sees.

Migrations `c41a9f77d3b2` and `d8b3e5a91c47` applied to the live database, which was still on
`b072fd4c1edb`.

**What Phase 2's exit criteria still need.** "Two accounts, each with their own model, feed, and
schedule" is two-thirds done: both accounts exist, both harvest, both are budgeted fairly. Neither
has a *model*, because a model needs labels, labels need a rubric, and a rubric needs a confirmed
profile — and a profile needs the user's own Claude credential to capture. Credential entry and
profile confirmation are Phase 3's first steps, so the criterion closes there rather than here.

Deliberately **not** done: fabricating profiles by parsing each reference instance's rubric prose
into structured fields. That is precisely the thing `PROFILE_CAPTURE_PLAN.md` §1 says cannot be done
reliably — a wrong NOT-list entry becomes a hard gate that silently suppresses good jobs — and doing
it to tick a box would put two real people on rubrics nobody confirmed.

## 2026-07-27 (night)

### Phase 2 steps 4-6 — queue, trainer, notifier, and the workers that run them

`libs/queue`, `services/trainer`, `services/notifier`. The last of Phase 2's components, plus the
wiring that makes them run rather than merely exist.

**Queue** (T5/T6/T7). `SKIP LOCKED` claim, attempts incremented at claim, per-lane visibility
timeouts swept back to `ready`, bounded attempts to a dead letter, and immediate termination for
conditions that will never succeed. Fairness is round-robin by last completion on `train` and
`label` only — `notify` and `docgen` are naturally spread, and paying for the subquery where it
changes nothing is just latency. The notify dispatcher advances `next_run_at` at **enqueue**, which
is the fix for the most likely silent failure on the platform: a feed that stops forever because one
delivery errored.

**Trainer.** Training set scoped by `profile_generation`; a mixture of embedder versions refused
outright; unembedded rows dropped rather than zero-filled. The artifact is written temp-then-renamed,
and the `models` row, the `is_active` flip, and the `model_version` bump are one transaction.

**Notifier.** `notified_at` marked in the transaction the caller commits with the job — that is what
makes at-least-once delivery safe. A revoked bot is terminal, not a retry. Over the daily cap items
are deferred, not dropped. The token is decrypted inside `SecretStore.use` and scrubbed from
anything logged in that window.

**Three bugs CI caught that local green could not**, all of them the kind that stays silent:

1. **`claim()` returned the identity-mapped copy of the row.** A long-lived worker session would
   read `attempts` and `state` as they were *before* its own claim, so a job that always kills its
   worker would retry forever. Fixed with `populate_existing`.
2. **A handler failure rolled back the claim along with the handler's work.** `attempts` went back to
   its pre-claim value, `fail()` never reached `MAX_ATTEMPTS`, and dead-lettering existed only on
   paper. The increment is now captured before the handler runs and re-applied when the failure is
   recorded.
3. **The corpus CHECK requires `text_sha256` alongside a vector** — a vector whose source text is
   unrecorded cannot be verified as current. Fourteen trainer tests failed on the constraint rather
   than on anything they were testing, which is the schema doing its job.

**Workers.** `run_worker` claims, runs, and commits once, so the handler's effect and the job's
completion land together or neither does. Handlers are given the session and must not commit. SIGTERM
stops at the next job boundary. `services/trainer` sets the BLAS thread caps before numpy is imported
— queue concurrency of 1 does not stop one fit saturating every core.

432 tests.

**Phase 2's exit criteria are not met, and will not be until Phase 3.** Every component is built and
tested, but "two accounts running side by side, each with their own model, feed, and schedule" needs
two real accounts — and an account needs credential entry and profile confirmation, both of which are
web-layer work. What exists now is the machinery underneath that, complete and wired.

## 2026-07-27 (evening, later)

### Phase 2 step 3 — the multi-tenant scorer

`services/scorer`: an LRU of per-user models that reloads on `users.model_version` instead of on a
container restart, plus the serve path.

- **Hot-swap is a version comparison on every score**, not a timer. A timer means a window where a
  just-retrained model is not yet serving and nothing says so. The check is one indexed read —
  deliberately cheaper than the reload it prevents.
- **Nothing is embedded at score time.** The corpus row already carries its vector, so N users over
  one posting is N predictions rather than N embeddings. That is the economic argument for the
  shared corpus, and it only holds if this path never falls back to embedding.
- **Four fail-closed refusals**, each of which is otherwise a silent wrong answer rather than a
  crash: no artifact row for the version the user is on (interrupted trainer — serving the previous
  version would answer with a model the dashboard calls superseded), a `profile_generation`
  mismatch (the feature columns still line up; the weights were fit against other semantics), a
  `FEATURE_SCHEMA_VERSION` mismatch, and a corpus vector from a different embedder. The embedder
  check is on the **version string before the dimension** — two models can share a width and mean
  entirely different things.
- The cached-then-invalidated case is the subtle one: a model that was current when cached, with the
  profile edited afterwards. `model_version` never moves, so only the generation check catches it.

24 tests, all against a live Postgres with a stub estimator — what is worth testing is which model
is served to whom and when it is refused, not whether scikit-learn multiplies correctly.

## 2026-07-27 (evening)

### Phase 2 step 2 — rubric render and the per-user feature extractor

`libs/prompts/label_template.md` (SACRED), `libs/scoring/rubric.py`, `libs/scoring/features.py`.
Both derived from the same confirmed profile, both landed together because they invalidate together.

- **The template is platform-owned; the profile fills two blocks.** Decision procedure, score
  anchors, calibration examples, and output contract stay identical for every user — otherwise a
  template change could not be A/B measured across the platform. Calibration examples are phrased
  *relative to the candidate* ("a role in their FIRST target area, 2+ core overlaps → 93"), which is
  the option `PROFILE_CAPTURE_PLAN.md` §6.1 preferred because it keeps rendering deterministic.
- **`profile_version = sha256(rubric_md)[:8]`**, and `rubric_relevant_digest` hashes only the fields
  the rubric actually reads — so editing a phone number does not relabel a user's whole history (§7).
- **`save_profile` renders and stores the rubric itself.** A user with a profile and no rubric, or a
  rubric whose generation does not match, is a scoring corruption nothing reports. One write path.
- **The feature extractor is the third personal artifact** (§6.3). The reference's `_CORE_TOKENS`
  and off-stack list are one candidate's stack; here they are built from `users.profile`.
  `FEATURE_NAMES` order stays global and append-only — if the column order varied per user, no model
  artifact would be portable.

**Three real bugs the tests caught, all silent:**

1. `.NET` extracted as `net`, which then failed to match ".NET" at all — the leading dot sits inside
   the boundary lookbehind. A whole category of stack invisible to the model.
2. `\bc#\b` can never match: `\b` after `#` requires a following word character. Lookarounds over
   the actual token alphabet instead.
3. A term appearing in both the strengths and the NOT-list. The NOT-list now wins — a stale strength
   entry must not quietly cancel a gate the user reviewed.

Also fixed: `remote_policy: "any"` was rendering "mandatory relocation is a dealbreaker", inventing
a gate the user never asked for.

Region locks are per user too: the reference hardcoded "US-only is a gate" because its one user
could not work there. A user with US work rights no longer gets the flag.

32 tests. Migration `d8b3e5a91c47` adds `users.rubric_md` (separate revision — `c41a9f77d3b2` has
already been applied, and a migration is a record of what ran, not a file to revise).

**Gap:** `"1-3 years"` reads as 1, not 3 — the regex takes the lower bound. Carried from the
reference deliberately and now documented in a test, because changing it would shift the meaning of
a column every existing model was fit against.

## 2026-07-27 (later still)

### Phase 2 step 1 — profile capture, with manual entry in the same commit

`services/capture` + `libs/contracts/profile.py`. The Managed Agents interview that turns a signup
into a tenant, and the fallback for when it does not.

- **One narrow protocol** (`CaptureClient`, eight methods) with the SDK confined to
  `AnthropicCaptureClient`. Managed Agents is beta and sits on the onboarding critical path; a
  contract change should break one adapter, not onboarding. Tests drive the protocol with a fake.
- **Containment is configuration** (`PROFILE_CAPTURE_PLAN.md` §3): deny-by-default toolset (`bash`,
  `edit`, `web_search`, `web_fetch` off), no egress at all, read-only document mounts, and `write`
  approval-gated so the backend rejects any path outside the outputs directory. An uploaded CV is
  untrusted text; none of this is left to the prompt.
- **`users.profile` refuses an unconfirmed profile.** The confirmation click is the injection
  boundary, so it is enforced at the write rather than by convention. Saving bumps
  `profile_generation`, which is what makes everything built against an older one stale by
  definition (§6.4).

**Two subtleties that would have silently broken stream-first ordering:**

1. `stream_events` is **not** a generator in the adapter. A generator body does not run until the
   first `next()`, so `stream_events(...)` then `send_event(kickoff)` would have opened the SSE
   connection *after* the kickoff — undoing the ordering the caller carefully arranged. It opens
   eagerly and iterates lazily.
2. The session is created **without** `initial_events`, deviating from the plan's snippet. A session
   created with them starts running immediately, which has the same effect.

34 tests, no network. Migration `c41a9f77d3b2` adds `users.profile`, `profile_schema_version`,
`capture_agent_id`.

**Gaps:** the confirmation *form* is Phase 3 (no web layer yet) — `save_profile` enforces the
boundary the form will satisfy. Capture cost is still unmeasured, and it is the first money a user
spends, on their own key.

## 2026-07-27 (later)

### Phase 2 step 0 — `libs/secrets`: envelope encryption, and T4 in the same commit

Phase 2's first commit, and **not** the order `ROADMAP.md` lists. The roadmap puts the secrets
module at step 6, after profile capture — but `PROFILE_CAPTURE_PLAN.md` §4.1 says capture runs on
the user's own Claude credential, and `TENANT_SAFETY.md` §5's onboarding order is
`credentials → capture`. Building capture first would mean holding a stranger's live API key with
no encryption behind it, which is exactly what §2 exists to prevent. Secrets first. It also unblocks
the proxy credential, which the LinkedIn loop already needs and currently has nowhere real to live.

**The design, three files:**

- `keyring.py` — AES-256-GCM. KEK from `SECRETS_KEK`, per-user DEK wrapped by it, secret sealed by
  the DEK. Two decisions worth naming:
  - **Owner and kind are bound in as associated data.** Without it, an attacker with database
    *write* access (not key access) can copy user A's ciphertext onto user B's row, or relabel a
    proxy password as a Claude key, and both decrypt silently. With it, both fail closed.
  - **A wrapped DEK carries the id of the KEK that wrapped it.** Two bytes now; unrecoverable
    later. Rotation becomes re-wrapping one small column per user — no secret is re-encrypted —
    and a retired key keeps reading old rows until `users_needing_rewrap()` comes back empty.
- `store.py` — the database layer. `user_id` required on every method, which here is load-bearing
  twice: it is also the AAD. `put()` overwrites rather than versions, because versioning keeps a
  revoked credential decryptable. `purge()` deletes the rows **and** clears the DEK — crypto-
  shredding, so "destroyed on account deletion" is true across the backups too, not just live.
- `redaction.py` — two layers, because neither is enough alone. A `Secret` type whose `str`,
  `repr`, and f-string all render `<secret:claude_api …ab12>`, so the accidental-interpolation path
  is closed *at the type level*; plus a logging filter as the backstop for values that reach a log
  through code we do not own.

**Two things that would have been silently wrong:**

1. **A redaction filter on the root logger protects almost nothing.** A logger's filters run only
   for records logged *on that logger* — a record from `logging.getLogger("services.notifier")`
   propagates straight to the root's *handlers* without consulting the root's filters. So `install()`
   attaches to the logger **and to each of its handlers**, and a test logs from a child logger
   specifically to prove it.
2. **Tracebacks are formatted after filters run.** Scrubbing `record.msg` alone leaves the
   credential in the traceback — which is precisely where a failed proxied fetch puts it, since the
   connection error quotes the URL it dialled, userinfo and all. The filter renders and redacts the
   traceback itself into `record.exc_text`, which `logging.Formatter` then uses instead of
   re-rendering.

**T4, same commit** (`tests/test_secrets.py`, 42 tests). The headline assertion is made the way a
thief would experience it: dump every column of every row belonging to a user and search the lot for
the plaintext — not "the ciphertext column differs", *nothing*. Plus: a different KEK cannot read
what this one wrote; no rendering of a `Secret` shows the value; a known secret cannot appear in a
log line, a child logger's line, an object argument, or a traceback; and the API read path exposes
`last_4` and timestamps with no field that could ever carry the value.

Test values are shaped like the real thing (`sk-ant-…`, a `\d+:…` bot token, a proxy URL with
userinfo) because the pattern half of the filter recognises credential shapes — a `hunter2`
placeholder would pass tests the real value fails.

**Gaps recorded, not closed:**

- **Save-time validation is a seam, not an implementation.** `TENANT_SAFETY.md` §2 wants a real
  minimal API call before a credential is accepted. `put(..., validated=False)` and
  `mark_validated()` exist; the callers that make the call arrive with capture (Claude) and the
  notifier (Telegram). This layer deliberately does no network I/O, so saving a credential cannot
  be blocked by a third party being down.
- **Nothing calls `install()` yet.** The harvester's entrypoint gains it when the LinkedIn loop
  starts decrypting proxy credentials; until then no service holds a plaintext.
- **`SECRETS_KEK` is not yet set on the platform box.** Documented in `deploy/.env.example`,
  including why the first rotation is a config change rather than a migration.

317 tests. `SECRETS_KEK` and `cryptography>=44.0` added to the deploy env and dev requirements.

## 2026-07-27

### Phase 1 exit — the harvester runs against live sources

The cycle deployed and run on the platform box. **193 links, 185 new, 179 embedded, 0 source
failures**, and a second cycle immediately after found **193 links, 193 duplicates, 0 new** — dedup
holds across runs.

Corpus state after: 193 rows, 187 with text, **187 embedded**, 5 sources contributing. Vectors are
exactly 3,072 bytes (768 x float32) carrying `BAAI/bge-base-en-v1.5|dim=768|norm=1` — the 36-char
version string that would not fit the original `String(32)`.

**Three bugs, all found by the first contact with real data.** Every one was silent.

1. **Three sources made no HTTP request at all.** `remotive`, `himalayas` and `arbeitnow` iterate
   `cfg["queries"]` and return `[]` when it is absent. No request, no log, and the cycle reported
   success — because an adapter returning `[]` is the registry's contract. The same property that
   stops one dead source sinking a run also hides a misconfigured one.
2. **The recency window was 24h, not 72h.** The adapters default internally to 24 while the config
   schema's own default is 72, so the first cycle fetched a much narrower slice than intended — 8
   links where there should have been ~190.
3. **`content_key` was `String(64)`.** It is a readable composite (`company|title|location`), not a
   hash, so its length is a function of a job title somebody else wrote. A German posting produced
   85 characters and every ingest died. Third instance of the same mistake this session, after
   `embedding_version` and `models.embedder_model` — bounding a string whose length is not ours to
   bound. Every remaining `String(n)` column was audited; the rest are hashes, enums, or
   RFC-bounded.

**A fourth bug hid the third.** The advisory-lock release ran in a `finally` on an already-aborted
transaction, raised `InFailedSqlTransaction`, and propagated *instead of* the `DataError` that
caused it — so the log showed a lock problem and hid a schema one. Cleanup must never mask the
failure that caused it.

**Two sources return zero, and that is correct.** `weworkremotely`'s feed has 25 items whose newest
is 26 days old; `remotive` returns 36 jobs, all outside the window. The 72-hour filter is doing its
job. Recorded so nobody "fixes" it into admitting stale postings.

275 tests passing. **Phase 1 is functionally complete.**

### The harvest cycle - one-shot, locked, timer-driven

`services/harvester/cycle.py` and `__main__.py`. The last Phase 1 piece: the loop that runs the
components in order - demand -> budget -> sources -> dedup -> upsert -> embed.

**A one-shot command rather than a daemon with a sleep**, which was the open question. Four reasons,
in order of weight:

1. **Restart safety comes free.** A crashed one-shot is a run that did not happen and the next tick
   starts clean. A long-lived loop that dies stops harvesting *silently* until somebody notices -
   the failure class `OPERATIONS_PLAN.md` §4 exists to prevent.
2. **It composes with Phase 2 instead of being replaced.** `run_cycle` becomes the body of a queue
   worker; an internal timer would be thrown away.
3. **It is testable.** `TEST_STRATEGY.md` §2.1 forbids `sleep` in tests, which rules out testing a
   loop containing one.
4. **Each run is its own log line and its own exit code.**

The cost is overlap: a timer can fire while the previous run is still going. Handled with a Postgres
advisory lock **inside the cycle** rather than in systemd, so it behaves identically when Phase 2
moves this into the queue. `pg_try_advisory_lock`, not the blocking form - a tick that arrives late
should skip, not queue behind its predecessor and turn one slow cycle into a backlog all doing the
same work while each spends the full fan-out budget.

**A skipped tick exits 0.** With a 15-minute timer and an occasionally slower cycle, treating
overlap as failure would page somebody about the system working correctly.

`Persistent=false` on the timer, deliberately: catching up missed runs after downtime would fire
several cycles back to back against sources that are already grey-area. A missed harvest is simply a
harvest that did not happen.

**Universal cadence, and that is not a simplification.** There is no such thing as harvesting *for*
one user when the corpus is shared - one fetch serves everyone, so a per-user harvest interval would
mean N cycles fetching identical bytes, which is the design this architecture rejected. What users
choose is how often they are *told* (`notify_interval_min`) and how often their own paid LinkedIn
tier runs (`linkedin_interval_min`). Universal where the cost is shared, per-user where it is
personal.

Fails closed on a missing `EMBEDDER_URL` (exit 2) rather than harvesting rows nothing can embed -
they would sit unembedded and unscoreable while the cycle reported success.

265 tests passing.

### G11 and G12 closed, and a hole found in the drift check

**G11 (account lifecycle) was brought forward out of Phase 3**, because its stated cost is a Phase 1
one: "dormant users consume harvest budget and run their own LinkedIn tier against their own proxy
indefinitely". The harvest fold being built next is exactly what has to exclude them.

**Implemented against the product rather than against the sentence.** G11 defines dormant as "no
login in N days". Delivery is Telegram — a user who reads their feed every morning and applies to
things may never open the web app again after onboarding, so login-only would pause the feeds of the
most engaged users and tell nobody. `last_active_at` is bumped by login **or** any Telegram action.
30 days, reversible instantly, and `suspended` is deliberately not reversed by activity: dormancy is
a cost control, suspension is a decision.

**G12 (artifact growth)** — `prune_model_artifacts` keeps current plus one previous. It returns the
paths rather than deleting files: the repository owns rows, not the filesystem, and splitting it
means a rolled-back transaction cannot leave rows and files disagreeing about which versions exist.

**The find that matters: Alembic autogenerate does not detect `CHECK` constraint changes, and
neither does the CI drift check, because it is built on autogenerate.** Adding `dormant` to
`USER_STATUSES` produced a migration that added the column, left the constraint listing the old four
values, and passed the drift check — while every write of the new status failed with an integrity
error. Recorded in `libs/db/README.md` alongside the other silent failure mode (a model not imported
by `models/__init__.py`), because neither is discoverable from a green build.

A second, smaller trap in the same fix: `op.drop_constraint` takes the **logical** name
(`status_valid`), not the rendered one — the naming convention expands it, so passing the rendered
name produces `ck_users_ck_users_status_valid`.

**G9 (cost visibility) stays open** and is genuinely Phase 3: it meters Claude spend, and nothing
calls Claude yet. The one part worth taking early — integer units rather than floats for anything
billed — is already the precedent (`proxy_credentials.mb_used_today` is `Numeric`, not `Float`).

255 tests passing.

### Step 5 - embed once, for everyone, ever (T8)

`libs/contracts/embedder.py` and `services/harvester/embedding.py`. A vector is a deterministic
function of the text, so embedding the same posting for fifty users is fifty times the CPU for
byte-identical output. Putting it once on the shared corpus row is the whole scaling argument.

**A regression here is invisible until the CPU bill** - nothing errors, feeds populate, scores look
reasonable. So T8 asserts the backend call count through a counting stub rather than trusting the
structure.

**A real schema bug, and it would have failed on the very first production embed.**
`corpus.embedding_version` was `String(32)`. The live embedder's actual version string is
`BAAI/bge-base-en-v1.5|dim=768|norm=1` - **36 characters**. Every embed would have failed with a
string-truncation error. It surfaced only because the test used the real format rather than a short
placeholder, which is an argument for fixtures that look like production. `models.embedder_version`
had the same bound and the same bug. Both are `Text` now: the version is a composite of everything
defining the vector space, and its length is a function of the model name, which is not ours to
bound. Migration `6f4ca35c9cb4`.

Three properties the tests pin, each silent if lost:

- **Compatibility is asserted before anything is written.** A vector from the wrong model is not a
  worse vector, it is a meaningless one, and nothing downstream catches it - the dimensions match
  and the numbers look like numbers.
- **The digest is re-checked at write time.** A cold-start batch takes minutes while `refresh.py`
  mutates descriptions; a vector whose `text_sha256` no longer matches is refused and the row is
  picked up next pass.
- **The bulk lane is used, not the fast lane.** They are separate worker pools, not a priority flag,
  because a score request otherwise waits behind an in-flight bulk call - measured at 0.08s idle
  versus 8-15s with a shared pool.

Also refused rather than guessed: a short vector list (fewer vectors than texts means the mapping is
unknown, and guessing attaches one job's vector to another job's text), and a gateway 503.

247 tests passing. **Phase 1 is complete.**

### Step 4c - the source adapters, ported as a scripted transformation

~3,500 lines lifted from `pipeline/sources/`, plus `libs/corpus/filters/timefilter.py`. Done by
`services/harvester/sources/port_from_reference.py`, which is committed alongside them: the
transformation is the interesting part of the port, and re-deriving it later from a diff would be
far harder than reading it. Retyping the adapters by hand would have introduced transcription errors
into code whose correctness depends on details no test here can check - a JSON path, a date format,
a pagination parameter.

**4c was not a pure lift, and the reason matters.** Every adapter built its own `httpx.Client` - 17
call sites. Harmless single-tenant, because none of those boards need a proxy. Here it would have
broken the invariant `fetch.py` exists to hold **and** restored `trust_env=True` on all 17, so an
ambient `HTTP_PROXY` could route the whole shared harvest through one user's credential. There is
now a test asserting no module under `services/harvester` constructs a client directly.

**Two real bugs found during the port:**

1. **The transformation silently dropped RemoteOK's custom User-Agent.** The reference sets one with
   the comment "RemoteOK blocks the generic Mozilla UA; a slightly more specific one works" - and
   my regex matched `headers=ua` and discarded it. The source would have returned nothing, with the
   explanation sitting two lines above. Caught by ruff flagging the now-unused variable, which is
   luck rather than design, so the rule is narrow now: only `headers=_UA`, the platform default the
   factory already supplies, may be dropped.
2. **`gather_linkedin` took its proxy from a config dict.** Correct with one tenant, wrong with
   many: a credential arriving as a dictionary key has no verified owner. It now requires a
   `ProxyLease`, keyword-only and mandatory. jobspy opens its own connections and cannot be routed
   through `make_client`, so this is the one place a proxy URL reaches a third-party library - which
   is exactly why the type carries its owner.

**One deliberate behaviour change: XML parsing now uses `defusedxml`.** Several adapters parse
RSS/XML from third-party boards with stdlib ElementTree, which the stdlib's own security docs flag
as vulnerable to entity-expansion bombs. Single-tenant on a laptop that is a shrug; here the
harvester shares a box with Postgres, and a hostile or merely broken feed exhausting memory takes
the platform down for everyone.

The registry now records **which tier a source belongs to**, so the shared loop cannot be handed the
proxied one - the same guarantee `make_shared_client()` gives at the HTTP layer, stated twice
because it is what D3 rests on. LinkedIn is the only per-user source, and a test asserts it.

Adapter style findings are ignored per-file with reasons, as for the embedder: restyling lifted code
would destroy the diffability that justifies a scripted port. **F841 stays on** - it is what caught
the User-Agent regression.

233 tests passing.

### Step 4b - the fetch layer, where D3 stops being a policy and becomes structure

`services/harvester/fetch.py` and `proxy_usage.py`. Decision D3 says a user's proxy credential is
used strictly for their own fetches, never pooled. That is a claim about *every* outbound request the
harvester makes, so it can only hold if there is exactly one way to make one - `make_client()` is
that way.

Three departures from the reference, all forced by multi-tenancy:

- **The proxy comes from a lease, never the environment.** The reference reads `PROXY_URL` from env,
  which is single-tenant by construction. There is no env fallback here on purpose.
- **A client carries the id of the user it was built for**, so "was this credential used for someone
  else's fetch" is an assertion rather than a code review.
- **`PROXY_HOSTS=*` is gone.** Under per-user billing, proxy-everything would spend one user's
  metered bandwidth on shared ATS fetches that work fine direct.

`make_shared_client()` takes no lease parameter at all, so the shared loop cannot spend anyone's
bandwidth even by mistake. Structural, not conventional.

**A real bug the tests caught: `trust_env`.** The shared client set no mounts, so httpx defaulted to
`trust_env=True` and would have honoured an ambient `HTTP_PROXY` - silently routing every user's
shared traffic through one endpoint. That is the pooling D3 forbids, arriving through an environment
variable nobody set deliberately, and it would have looked like it was working: jobs arrive, feeds
populate, and the only symptom is one person's proxy bill. Now `trust_env=False` everywhere, with a
test that sets the env vars and asserts they cannot leak in.

**A second bug: the daily window.** `roll_daily_window` treated a null `usage_reset_at` as "the day
has turned" and zeroed the counter, so the first lease of any credential handed back a spent cap.
Null is initialisation, not a day boundary. Fixed, and the timezone case is now tested in both
directions - a user east of UTC must reset on *their* midnight and must not reset on UTC's.

### T13 and T1 closed

**T13 is done, both halves.** The visibility half was already proven; the cross-use half - the more
important one - now is: owner identity on the client, LinkedIn-only routing, redirects out of
LinkedIn going direct, ambient proxy rejected, metering on every response including failures (the
proxy bills on the wire, not on success), and quarantine on *consecutive* failures rather than
cumulative, which would eventually quarantine every healthy user.

**T1 is complete for every read path that exists.** Added config, secrets metadata and queue rows to
the isolation coverage. Documents remain uncovered because `docgen` does not exist yet - that is
named in the status table rather than left as an unexplained amber.

Secrets have a metadata-only read path by design: `last_4` and timestamps, never ciphertext. There is
deliberately no repository method that returns a secret value; the worker that needs a plaintext
decrypts it in memory for one call and that path does not go through the repository.

213 tests passing.

### Audit against the specs, and T9

Checked what is built against `TEST_STRATEGY.md` and `GAPS_REVIEW.md` rather than against a feeling
that things are on track. Status is now recorded next to each spec — no fourth place tracking state.

**The audit found a real miss: T9 had no tests.** `libs/contracts/config.py` shipped in the previous
commit and the config schema was never tested, despite T9 scoring 7/10 as "primary onboarding
failure mode". `TEST_STRATEGY.md` §4's ordering rule exists precisely to stop that; it was followed
for T2 and T1 and not here. Now 34 tests, including the seam where a *validated* config actually
drives the harvester — testing the schema and the query-set builder separately would have left the
join between them unproven, and that join is where a renamed field silently produces zero queries.

**Writing that seam test found a second problem: the per-user keyword cap was defined twice** — a
literal in the schema and a constant in `services/harvester`. Two definitions of one rule is how
limits drift: raise one and the other silently accepts terms it will never serve. The first instinct
was to write a test asserting the two numbers agree; the right fix was to remove the duplication.
It moved to `libs/contracts`, which is the **only** package both may import from, since `libs` must
never import a service. At Phase 3 it becomes a row in `plans.limits` and the seam is unchanged.

Honest status carried into the docs:

- **T1 is partial, not done.** Scores, labels, applied, and cascade completeness are covered;
  config, secrets, queue, and documents are not, because those read paths do not exist yet. It is
  the release blocker and must be finished before launch.
- **T13 is half done.** The visibility half is proven — no healthy credential, no LinkedIn rows,
  per-user on one shared row. The "credential never cross-used" half needs the fetch layer, and is
  the more important half.
- **G5, G7, G14 closed. G15 and G16 are policy-and-schema built, execution deferred** to the queue
  (Phase 2) and step 5 respectively.

Anti-flake compliance re-checked against §2: no `sleep`, no real network, every database test in a
rolled-back transaction, seeded or injectable RNG wherever anything samples, no wall-clock
assertions, real Postgres 17 rather than SQLite.

Infrastructure: **4 GB swap is live on the VPS** (`swappiness=10`, persistent in fstab), which
closes the risk raised when TEI's CPU ceiling was raised on a box where memory contention was an
OOM-kill rather than a slowdown.

173 tests passing.

### Step 4a - the harvester's governor, before the harvester

Phase 1 step 4, split: the deterministic half now, the source adapters as 4b. Same fault line that
made step 3 tractable - build the part whose correctness is provable without a network first, and in
this case build the thing that *limits* scraping before the thing that scrapes.

- **`libs/contracts/config.py`** - the bounded user config. Invalid states are unrepresentable
  rather than validated away: controlled vocabularies, 1-5 keywords, bounded salary and recency.
  Locations are a picklist and that is not only a UI choice - harvest dedup folds on a normalised
  location, so free text would multiply the query set with spellings of the same place.
- **`services/harvester/query_set.py`** - QueryKey normalisation, folding, fan-out budget,
  round-robin selection.
- **`services/harvester/governor.py`** - the LinkedIn tier's global controls.
- **`proxy_credentials`** table, and the visibility gate in `libs/tenant`.

**A bug the tests caught in my own implementation.** `HARVESTER_PLAN.md` section 2 specifies the age
bucket as *"rounded up to the coarsest bucket any requesting user asked for"*, so a 24h user and a
72h user share one 72h fetch. I had bucketed each user's value independently and folded on it, which
*fragments* demand instead of merging it - two fetches whose results are strict subsets of one
another, exactly the duplicate work the budget exists to prevent. Age is now resolved per group
after folding.

Three more failures in that run were bugs in the tests, not the code: I had given every user
identical keywords, so folding correctly collapsed them and the scenarios I meant to build never
existed.

**Round-robin is the load-bearing choice in selection.** Ordering by demand would serve popular
terms repeatedly and starve anyone with an unusual search - precisely the user for whom this product
is most valuable. Deferred queries are served first next cycle so an unlucky query does not stay
unlucky.

**The visibility gate makes free-riding impossible by construction.** LinkedIn rows live in the
shared corpus so dedup and embed-once still work, but a user sees them only while their own proxy
credential is healthy. Applied as a `WHERE` clause, not a post-filter - filtering after `LIMIT`
would hand short pages to exactly the users who have no credential.

**The per-user tier still needs a global governor** (section 5.2b). Per-user proxies give IP
diversity and handle ban risk; they do nothing about the platform issuing hundreds of concurrent
requests, or a herd forming when users share an interval. Hence a concurrency ceiling, jitter
applied per scheduling decision (not once at signup, or a restart re-converges everyone), and a
kill switch - the alternative during an incident is editing 50 users' configs while LinkedIn blocks.

Execution is deliberately not wired: the per-user loop is an ordinary queue job, and `libs/queue` is
Phase 2. Nothing half-built runs.

### CI now lints

`ruff check .` added as a step, after noticing ruff had only ever been run by hand - which means it
had only ever run on the files someone remembered to name. Rules are selected explicitly rather than
inherited from ruff's defaults, which change between releases. `services/embedder` carries per-file
ignores with reasons: its findings are deliberate choices in deployed, gate-tested code, and one
flagged line (`bulk_s == bulk_s`) is an intentional NaN check that a "fix" would have broken.

Migration `2f362ab1e0c5`. 140 tests passing.

### Step 3b — the remaining stores, plus a dedup layer step 3 had missed

**The important part is the gap, not the port.** Reading the reference's `processed` table for 3b
showed it indexes `app_canonical` and matches on `canonical = ? OR app_canonical = ?` — the resolved
**Apply URL**. Step 3's `find_duplicate` only compared `corpus.canonical_url`, so a LinkedIn posting
whose Apply button resolves to a Greenhouse board would have landed as a **second row** alongside the
one the ATS poll found directly. That is the precise case canonicalisation exists for, and it was
missing.

Fixed: `corpus.application_url` + `application_canonical_url`, and a fourth dedup layer checked in
**both directions** — the candidate's apply target against stored rows' own URLs, and the
candidate's own URL against stored rows' apply targets. Checking one direction only would leave half
the collisions undetected while looking like it worked.

The stores themselves:

| Reference | Here |
|---|---|
| `ats_tokens.json` | `ats_tokens` table, lowercased, dead boards retained not deleted |
| `ats_etags.json` + `.lock` | `ats_etags` table, **row-level upsert, no lock** |
| `applied.jsonl` | `user_job_state.applied_at` / `.cover_built` |
| `labels.jsonl` | `user_labels`, `source='llm'` |
| `feedback.jsonl` | `user_labels`, `source='feedback'`, `weight=5` |

**`ats_etags` is the one place the port is a fix rather than a translation.** The reference guards a
whole-map JSON rewrite with a file spinlock whose `_acquire_lock()` returns `False` on timeout and
lets the caller proceed anyway — so a contended write silently drops another writer's keys, and even
holding the lock the whole-map replace lets two ATS gathers clobber each other. Row-level upsert on
`(ats, token)` removes both by construction. Proven by an interleaved-write test, not asserted.

**Feedback turned out not to be a separate store** — it is a label with a different provenance and
weight. The reference keeps it apart and blends it at train time by duplicating rows five times so
the weighting survives sklearn Pipelines; recording `source` and `weight` on the row makes the blend
a query instead.

New package **`libs/tenant/`**, a deliberate addition to `CODE_STRUCTURE.md` §2's list. That list
predates the repository layer and has no home for per-user data access: `libs/corpus` documents
itself as the shared half, `libs/db` is where the schema lives. Putting tenant-scoped queries in
either would make the shared/per-user boundary invisible at the call site. Now it is visible in the
import path — `libs.corpus` takes no `user_id` at all, `libs.tenant` cannot be used without one.

**T1 landed** (`TEST_STRATEGY.md`, release blocker): two users, overlapping jobs, every read path
returns only the caller's rows — applied log, labels, and scores.

Also caught in review: `cover_built` was generated as `NOT NULL` with no server default, which fails
against any table that already has rows. It would have worked today only because the table is empty,
which is not a migration strategy. Added `server_default`.

Migration `fccd4d3a84ff`, applied to the live database (11 tables). 109 tests passing.

### Corpus repository — the storage half of the `store.py` port (Phase 1 step 3)

`libs/corpus/repository.py`, 14 behaviour tests, T2 green throughout. Dedup (url → canonical →
content_key, narrowing), ingest, description/embedding lifecycle, and the retention sweep.

**A schema correction the port surfaced.** The first draft made `corpus.canonical_url` unique and
`NOT NULL`. Both wrong, and only writing the repository exposed it:

- The reference keys `all_links` on **`url`** — `canonical` and `content_key` are *indexed, not
  unique*. Dedup is a query performed **before** insert, not a constraint. Making canonical unique
  looks tighter but converts a case the reference tolerates into a crash mid-harvest.
- Both derived keys must be **nullable**, because the key functions return `None` for input they
  cannot use. `NOT NULL` would make such a job impossible to store at all — silently dropping real
  postings rather than admitting them undeduplicated, which is the wrong direction to fail.

Migration `fe23e709fd46`, applied to the live database.

Two invariants the tests pin, both silent if broken: `first_seen_at` is never overwritten on
re-discovery (or a re-listed posting stays permanently fresh and un-trimmable), and a vector can
never outlive its text — changing a description clears the embedding, and `set_embedding()` refuses
a vector whose digest no longer matches. That last one is a real race, since bulk embedding takes
minutes while `refresh.py` runs concurrently, and the `(model, dim, version)` assert cannot catch it:
the vector space is right, the content is wrong.

Decided while building it: `CorpusRepository` takes no `user_id` at all (shared by construction,
per-user repositories are separate classes), and the reference's materialised `.load()` mode is
dropped — it was an artifact of the JSON store that preceded SQLite, and would mean an O(history)
memory pattern in a multi-tenant service.

### CI — three bugs that made the pipeline report success without checking

Verified every job by running it the way the workflow does, against a scratch database. Two of the
three made a check pass while checking nothing:

1. **pytest could not import `libs/` in CI.** The workflow calls the `pytest` console script; only
   `python -m pytest` puts the working directory on `sys.path`. Every test importing `libs.*` would
   have failed at collection in CI while passing locally — the worst split, because local green is
   exactly what gets checked before pushing. Fixed with `pythonpath = ["."]`.
2. **The drift check could never fail.** It globbed `*drift-check*.py`, which Alembic never writes —
   `file_template` slugifies the hyphen to `drift_check`. grep errored, the `if` read false, pass.
3. **The first rewrite still passed when Alembic itself failed** — a failed `generated=$(ls …)`
   assignment did not abort under `set -e`.

Proved the fix rather than assuming it: injected a canary column into the corpus model, confirmed
the check fails, reverted, confirmed green. A drift check that cannot fail is worse than none,
because it is also reassuring.

### T2 — the dedup regression suite, and the pure-function half of the `store.py` port

Phase 1 step 2. `libs/corpus/keys.py` is a faithful port of `canonical_url` and `content_key` from
`job-search/pipeline/lib/store.py`, with **66 tests** proving it.

**The port was split in two, and that is the whole reason T2 could come first.** The dedup
*semantics* are pure functions; the SQLite→Postgres move is *storage*. Separating them means T2 was
written and green before any storage work started — which is what the roadmap asks for and would
have been impossible had the two been ported together.

The proof has three layers:

1. **Golden cases** generated by *executing* the reference, not by reading its rules and writing
   down what they appear to say. A misreading transcribed into a test is confirmed by that test
   forever. Regeneration: `tests/golden/generate_dedup_golden.py`.
2. **Behavioural tests** restating each rule as intent, so the suite explains *why*.
3. **Differential fuzz** — 40,000 seeded random inputs through both implementations, **0
   divergences**. Skips where the reference repo is absent (including CI), which is why the goldens
   are committed rather than generated at test time.

**Found: the reference's own comment is wrong.** It claims `"Remote" == "Remote - EMEA" == ""`. It
does not — `remote` is stripped from the location but `EMEA` survives, so the two key differently
and do **not** collapse. Pinned by a test named after the discrepancy, because otherwise someone
eventually "fixes" the code to match the comment and silently changes what counts as a duplicate.

### Embedder deployed on the platform box

`services/embedder` now runs on `169.58.16.241` as the **platform's own instance**. The legacy
deployment serving `job-search` and `andras-job-search` lives on a different machine and was not
touched — decision D6 is parallel copies, decommission nothing until the platform provably matches.

Gate results, run in order per `services/embedder/README.md`:

| Gate | Result |
|---|---|
| 1 — backend-verified health | ✅ `verified: true` in ~45 s, `model=BAAI/bge-base-en-v1.5 dim=768` |
| 2 — full suite against the live stack | ✅ **23 passed, 1 skipped** (up from 15/9 with no stack) |
| 5 — priority lane under a training flood | ✅ included in gate 2 |
| 4 — peak RSS vs the 2 GB budget | ✅ **832 MB peak** against 2048 MB, 0 errors |

Gate 4 also showed `cpus: 1.5` on the TEI container was the binding constraint — a ceiling tuned for
the box that shared CPU with two single-tenant stacks, leaving ~6.5 of 8 vCPU idle here. Raised to
**4** and re-measured rather than assumed:

| | cpus 1.5 | cpus 4 | |
|---|---|---|---|
| Bulk cold start (2,000 texts) | 589.1 s | **296.7 s** | 1.99× faster |
| Score latency, median | 3.795 s | **1.586 s** | 2.39× faster |
| p95 | 4.862 s | **2.603 s** | 1.87× |
| max | 9.101 s | **5.158 s** | 1.76× |
| Peak RSS | 832 MB | **810 MB** | ↓ |
| Errors | 0 | 0 | |

**2.67× the CPU bought 2× the throughput.** Sub-linear, because TEI's CPU backend caps its internal
batch at 8 regardless of how many cores it has. Extrapolating, 4 → 6 would likely return under 1.3×
for 50% more contention — so **4 is the landing spot and going further is not worth it**. That
conclusion is the reason to record the numbers rather than just the change.

Peak RSS went *down* slightly, which is consistent: more CPU means less queuing, so fewer texts are
resident at once.

⚠ **Re-measure once the other services compete for the box.** This number is tuned to a machine that
is currently almost empty — precisely the condition that stops being true.

The single skip is `test_parity` (TEI vectors vs in-process `fastembed`). **That gate is moot for the
platform** and was skipped deliberately: parity exists to decide *match-or-retrain* for classifiers
that already exist, and the platform has none — every user's model will be trained on TEI vectors
from day one. It still matters for the legacy box, which is why the test stays in the suite.

Tests were run in a throwaway `python:3.12-slim` container attached to `embedder-net` rather than a
host venv. That needs no `sudo` (`python3-venv` is not installed) and, more usefully, exercises the
path consumers actually use — the gateway by service name, not the loopback host port.

### Postgres schema — eight tables, Alembic-managed (Phase 1 step 1)

`libs/db`. Applied to the live database and verified: re-running autogenerate produces **0
operations**, so models and database agree exactly.

Three decisions that are not transcriptions of the design docs:

- **Added `jobs.finished_at`.** `SCHEDULER_PLAN.md` §3's DDL omits it, but §5's fairness ordering
  selects `MAX(finished_at)` per user per lane. Round-robin does not work without it.
- **`job_id` foreign keys are `RESTRICT`, not `CASCADE`.** Written as CASCADE first, then corrected:
  cascading there would let a corpus retention sweep silently delete `applied_at`, which §4 calls
  the record a user would be angriest to lose. `user_id` still cascades, so account deletion stays
  one statement.
- **Config split by access pattern.** The four fields the 60-second dispatcher reads (`active`,
  `next_run_at`, `notify_interval_min`, `timezone`) are indexed columns on `users`; everything else
  the config form collects is JSONB on `user_config`.

Closes **G5** (Alembic from the first table, no hand-applied DDL ever) and **G7** (every timestamp is
`TIMESTAMPTZ`, so per-day counters can reset at the user's local midnight rather than UTC's).

`tests/test_schema.py` is the seed of **T1** — written now because the roadmap puts the isolation
test at the moment the first per-user table exists, not once there is a repository layer to test
through. CI runs it against a real Postgres 17 service, plus a drift check that fails if a model
changed without a migration.

### Postgres deployed

`deploy/compose.yml`. Loopback-only port, `${POSTGRES_PASSWORD:?}` so the stack refuses to start
without a password rather than defaulting to a guessable one, `mem_limit: 4g` because the box runs
no swap, `shm_size: 1g` because the 64 MB default makes parallel query workers fail with errors that
read like disk problems.

### Legal review cleared

GDPR posture, scraping ToS, and Anthropic's terms on subscription credentials in a hosted service
all confirmed cleared. The **implementation** obligations they imply are scheduled into Phase 3 —
see `docs/saas/ROADMAP.md`. A review passing and the obligations being built are different things.

## 2026-07-26

### Step 0b — capacity measured, D4 confirmed

23 GiB RAM with 22 GiB available, 8 vCPU, 382 GB free disk, ~337 MiB resident. Roughly 3× headroom
against the projection. Recorded in `PLATFORM_ARCHITECTURE.md` §11 along with the caveat that
matters more than the verdict: **neither single-tenant stack was on the box**, so the ~3.8 GB that
made the D6 arithmetic tight has not been paid yet.

Surfaced three follow-ups: the embedder needed standing up here (done 2026-07-27), the box has **no
swap**, and Postgres was not installed (done 2026-07-27).

### `embedder/` → `services/embedder/`

Its own commit, so a bisect lands on it cleanly. **15 passed / 9 skipped before and after,
identical** — the proof the move was inert.

Nothing relative needed editing: the compose build context, the Dockerfile `COPY` paths, and
`conftest.py`'s `sys.path` insert are all computed relative to files that moved together, and the
compose project name is pinned. Only things hardcoding a path from the repo root changed.
`CODE_STRUCTURE.md` §6 now records which surfaces those actually were, because the pre-move guess
was wrong in the direction that matters.

### Step 0a — repo layout and boundary enforcement

`libs/` and `services/` per `CODE_STRUCTURE.md` §2, as empty packages whose `__init__` docstrings
record why each exists. `import-linter` wired into CI on the first commit: **services may not import
each other**, **libs may not import services**. Every service is named in the contract from the
start, including still-empty ones, so a new service is covered the moment it has code rather than
whenever someone remembers to add it.

Boundaries are trivial to establish now and painful to retrofit once eight services import each
other. A rule a linter checks survives a late night; a rule in a document is a suggestion.

### Platform design

`docs/saas/` — twelve documents settling the shared/per-user split, container topology, storage
sizing, scheduler, tenant safety, onboarding, and operations. Start at
`docs/saas/PLATFORM_ARCHITECTURE.md`.
