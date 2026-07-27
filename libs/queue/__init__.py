"""The four-lane job queue — claim, retry, dead-letter. Postgres, not Redis.

Lanes: ``notify`` / ``train`` / ``docgen`` / ``label`` (SCHEDULER_PLAN.md). One queue with lanes
rather than four queues, so fairness across users is a single ordering decision instead of four
independent ones that interact badly.

**No Redis** (PLATFORM_ARCHITECTURE.md §3). ``SELECT ... FOR UPDATE SKIP LOCKED`` is comfortably
fast below ~50 users, and — the real argument — it is transactional with the data the jobs operate
on, so there is no window where the queue says done and the database says not. Revisit above ~100
users or ~100 jobs/s, not before.

**Restart safety is this package's job, and it is what makes one-container deploys safe.** A job
is claimed transactionally and an unfinished claim expires back into the queue, so killing a
worker mid-job costs a retry and never a lost or duplicated user-visible action
(SCHEDULER_PLAN.md §4, CODE_STRUCTURE.md §5). Workers may be restarted at any moment; that is a
design input, not an incident.
"""
