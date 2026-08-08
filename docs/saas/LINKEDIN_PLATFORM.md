# The LinkedIn developer platform, and what it does not offer us

Researched 2026-08-08 against [developer.linkedin.com/product-catalog](https://developer.linkedin.com/product-catalog)
and [learn.microsoft.com/en-us/linkedin](https://learn.microsoft.com/en-us/linkedin/). Written because
LinkedIn is this platform's single most valuable source and its most fragile one, and because a
restricted or banned account is a cost the owner pays personally.

---

## 1. The finding, up front

**There is no LinkedIn API that reads or searches job postings. Not on any tier, not for any
partner, not for money.**

Every jobs-related product in the catalogue moves data **into** LinkedIn:

| Product | Direction | Who it is for |
|---|---|---|
| Job Posting API | **write** — post jobs to LinkedIn | ATSs, job distributors, aggregators |
| XML Feeds | **write** — ingest a customer's jobs | The same |
| Apply with LinkedIn | write — send an application | Employers' own career sites |
| Apply Connect | write — deliver applicants to an ATS | ATS partners |
| Recruiter System Connect | sync — ATS ↔ Recruiter | ATS partners |
| Talent Hub / CRM Connect | sync | HR systems |

The Job Posting overview states the purpose plainly: *"Jobs that are already created externally on
ATS, company site, or job board can be automatically ingested by LinkedIn."* LinkedIn is the
destination. There is no counterpart that lets a member's own agent read the board.

**Consequence for us:** the current approach — fetching the public job pages through the user's own
residential proxy — is not a shortcut past an API we have not bothered to apply for. It is the only
mechanism that exists for this use case. Applying for Talent Solutions would not help; we are not
an ATS and have no jobs to post.

---

## 2. What self-serve actually grants

Only three permissions are available without LinkedIn's explicit approval, all from the Consumer
line ([Getting Access](https://learn.microsoft.com/en-us/linkedin/shared/authentication/getting-access)):

| Permission | Flow | What it returns |
|---|---|---|
| `profile` | member auth | The signed-in member's name, headline, photo |
| `email` | member auth | Their primary email |
| `w_member_social` | member auth | Post, comment and like **as** that member |

Nothing about jobs. Nothing read-only about anybody else. `w_member_social` is a write scope and
irrelevant here.

**One thing worth having anyway:** `Sign in with LinkedIn using OpenID Connect` is self-serve and
would let a user sign up with LinkedIn and confirm their name and email without typing them. That
is an onboarding nicety, not a data source. It does not touch the harvest.

---

## 3. The gated programmes, and why none of them fit

- **Talent Solutions** — apply as an
  [ATS partner](https://business.linkedin.com/talent-solutions/ats-partners/partner-application).
  Built around a recruiter's seat and a customer's job slots. Requires being an applicant-tracking
  system with employer customers. We are the opposite end of the market.
- **Marketing / Advertising API** — approved partners only, and governed by a
  [restricted-use list](https://learn.microsoft.com/en-us/linkedin/marketing/restricted-use-cases).
  Ads data, not jobs.
- **Sales Navigator (SNAP)** — partner approval, `r_sales_nav_*` scopes. About finding *people*, not
  postings, and priced for sales organisations.
- **Compliance** — `r_compliance` / `w_compliance` are **closed**. The docs say access "may not be
  requested".
- **Learning API** — course content. Unrelated.

---

## 4. The one member-consented route: Member Data Portability (DMA)

The only place LinkedIn opens member data to a third party without a commercial partnership, and it
exists because the EU **Digital Markets Act** obliges it
([overview](https://learn.microsoft.com/en-us/linkedin/dma/member-data-portability/)).

- **Two products**: *Member* (a member builds an app for their own data) and
  *[3rd Party](https://learn.microsoft.com/en-us/linkedin/dma/member-data-portability/member-data-portability-3rd-party/)*
  (an app fetches a member's data **with that member's consent**). The second is the shape we
  would use.
- **Scope**: `r_dma_portability_self_serve`, granted by the member through a consent screen.
- **Hard geographic limit**: available **only to members in the EEA and Switzerland**. Nobody
  outside can consent or mint a token. A UK-based user could not use it.
- **What it returns**: the member's *own* archive — account history, articles, posts — accumulated
  from the moment consent is given, including historical data.

**What this does and does not buy us.** It does not return job listings, so it is not a harvest
route and does not reduce our reliance on fetching pages. What it plausibly does return is the
member's own activity — worth a follow-up read to establish whether saved jobs and application
history are included, because if they are, it is a legitimate, consented signal for the scorer that
we currently have no way to see. That is the single most interesting unknown on this page.

**It is also the only LinkedIn integration on the list that is philosophically aligned with this
product**: the user consents, the data is theirs, and LinkedIn sanctions it.

---

## 5. What we actually do, and the risk it carries

We fetch public job pages through each user's **own** residential proxy, per account, on that
account's metered bandwidth (`services/harvester/linkedin.py`, `libs/corpus/postings.py`). No
account fetches for another — enforced at harvest, and since 2026-08-08 at scoring too
(`libs.settings.PROXIED_SOURCES`).

**We are already being throttled, and the logs say so.** A single pass on 2026-08-08:

```
harvest.describe attempted=267 described=153 still_thin=113 linkedin=True
  outcomes={'li_ok': 240, 'li_http_429': 213, 'li_empty': 27, 'li_giveup': 27}
```

**213 HTTP 429s in one pass against 240 successes.** That is not a healthy margin, it is a source
telling us to slow down. The same pass shows `ran 66 quer(ies), 1288 link(s)` — 66 searches
producing 1,288 links, most of which we then try to describe individually.

Risks, in the order they are likely to bite:

1. **Rate limiting hardening into blocking.** Already visible. The proxy absorbs it today; a
   sustained 47% 429 rate is the kind of pattern that gets an IP range classified.
2. **Account restriction.** The searches run unauthenticated against the guest endpoints, so no
   member account is currently exposed. **This must stay true.** Any future change that signs in to
   improve results puts a real person's LinkedIn account at risk of restriction, and that account is
   the owner's.
3. **Terms.** The LinkedIn User Agreement prohibits automated scraping. The US case law
   (*hiQ v. LinkedIn*) turned on the CFAA and public data and did not bless scraping generally, and
   it says nothing about contract claims or about non-US jurisdictions. Treat "public" as a
   technical description, not a permission.
4. **Silent breakage.** `jobspy` and the guest endpoints are unversioned. LinkedIn changes them
   without notice and without a deprecation policy, unlike the documented APIs which have a
   [breaking change policy](https://learn.microsoft.com/en-us/linkedin/shared/breaking-change-policy).

---

## 6. What to do about it

Recommended, in priority order:

1. **Reduce request volume before LinkedIn reduces it for us.** A 47% 429 rate means we are asking
   for more than we are being given. The describe step is the expensive half — 1,288 links per
   pass, described one at a time. Worth measuring what fraction of described postings are ever
   delivered to anybody; if it is small, describe lazily at delivery instead of eagerly at harvest.
2. **Never authenticate the harvest.** Write it down as a rule, because the temptation is real:
   signing in returns better results and risks a real person's account. Guest endpoints only.
3. **Keep LinkedIn a per-account cost.** Already true and now enforced on both sides. It is also the
   fairness argument and the reason the platform can survive a ban of one proxy without taking
   everybody down.
4. **Read the Member Data Portability 3rd-party docs properly** and establish whether saved jobs and
   applications are in the archive. If they are, it is consented, sanctioned, EEA-legal signal we
   cannot get any other way — and it is the only growth path on this page that does not increase
   scraping.
5. **Add `Sign in with LinkedIn` for onboarding only.** Self-serve, no approval, removes typing from
   signup. Explicitly not a data source.
6. **Do not apply for Talent Solutions.** We would be rejected, and the application signals to
   LinkedIn exactly what we do.

---

## 7. Sources

- [LinkedIn Product Catalog](https://developer.linkedin.com/product-catalog)
- [LinkedIn API Overview](https://learn.microsoft.com/en-us/linkedin/)
- [Getting Access to LinkedIn APIs](https://learn.microsoft.com/en-us/linkedin/shared/authentication/getting-access)
- [Job Posting Overview](https://learn.microsoft.com/en-us/linkedin/talent/job-postings/job-posting-overview)
- [Member Data Portability APIs Overview](https://learn.microsoft.com/en-us/linkedin/dma/member-data-portability/)
- [Member Data Portability (3rd Party)](https://learn.microsoft.com/en-us/linkedin/dma/member-data-portability/member-data-portability-3rd-party/)
- [Restricted Uses of LinkedIn Marketing APIs](https://learn.microsoft.com/en-us/linkedin/marketing/restricted-use-cases)
- [Breaking Change Policy](https://learn.microsoft.com/en-us/linkedin/shared/breaking-change-policy)
