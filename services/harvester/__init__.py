"""One shared scrape for everyone, plus a per-user LinkedIn loop. De-personalised ``pipeline/``.

The same bytes serve every user, so N duplicate fetches are pure waste and N× the ban risk against
sources that are already grey-area. Config comes from a *union* of user configs, deduplicated into
a query set and spent against a fan-out budget with round-robin selection (HARVESTER_PLAN.md).

**The proxy split is the one place shared/per-user is not clean** (PLATFORM_ARCHITECTURE.md §6,
decision D3 revised). Every user brings their own proxy credential and it is used strictly for
their own fetches — never pooled, never rotated across users. That kills the free-rider problem
and the residential-proxy ToS exposure by construction. The split lands on whether a tier needs a
proxy at all:

===========================================  ======  ===========
Tier                                         Proxy?  Ownership
===========================================  ======  ===========
Direct ATS (Greenhouse, Ashby, Lever, …)     no      shared
Board APIs (Remotive, Himalayas, …)          no      shared
JobSpy Indeed / Google                       no      shared
JobSpy LinkedIn                              **yes** **per-user**
===========================================  ======  ===========

LinkedIn rows still land in the shared corpus so dedup and embed-once keep working, but a user
sees them only while they hold a healthy proxy credential — a single filter predicate on
``corpus.source``, not a data partition.

Restart blast radius: new jobs stop arriving; existing feeds are unaffected.

Scoring does **not** happen in the harvest loop — that split is deliberate
(CODE_STRUCTURE.md §3). ``sources/`` is lifted nearly as-is from the reference instance.
"""
