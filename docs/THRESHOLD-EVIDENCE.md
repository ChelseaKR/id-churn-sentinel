# `REMOVAL_THRESHOLD`: what the data can and cannot tell us

**Status: the threshold is still a guess. This document is the audit that establishes
that, and what it would take to stop being true.**

`docs/ROADMAP.md` M2 has carried an open item since the milestone was written: re-derive
`REMOVAL_THRESHOLD` from observed outage lengths rather than defending an educated guess.
This is the attempt. It did not succeed, and the reason it did not succeed is worth more
than a number would have been.

The short version: **this repository has never retained an observation that spans two
days.** Every fetch it has ever recorded happened inside one seventy-four-minute window on
2026-07-13. You cannot measure how long an outage lasts from that, and a threshold derived
from it would be a guess wearing the costume of a measurement — which is the specific
failure this project exists to avoid.

What the audit *did* establish is that the threshold was measuring the wrong quantity. That
part is fixed. See [What changed](#what-changed-as-a-result).

---

## 1. What observation history actually exists

Everything below is read out of `var/sentinel.db` and `sources/registry.json` as they stand.

### One session, seventy-four minutes

| Measure | Value |
| --- | --- |
| Distinct calendar days with any recorded fetch | **1** (2026-07-13) |
| First recorded snapshot | 2026-07-13T18:43:26Z |
| Last recorded snapshot | 2026-07-13T19:57:57Z |
| Longest first-to-last span for **any** source | **1.18 hours** |
| Sources with observations on two different days | **0** |
| Snapshots retained | 395, across 146 sources |

The 395 snapshots are not a time series. They are three back-to-back passes in one sitting:
124 sources hold three snapshots each, one holds two, and 21 hold one. The committed
`sources/baseline-hashes.json` was generated at 19:58:22Z the same evening, from the same
session.

### The tables that would hold outage evidence are empty

| Table | Rows | What it would have told us |
| --- | --- | --- |
| `fetch_attempts` | **0** | per-attempt timestamp, ok/not, status, error — the raw material |
| `watch_runs` | **0** | when each pass ran, what it attempted |
| `run_sources` | **0** | per-source outcome per run |
| `run_observations` | **0** | which runs produced which changes |
| `snapshots` | 395 | successful fetches only, one session |
| `source_health` | 152 | current streak only — no history |
| `changes` | 2 | both `content_drift`, both unreviewed |

The emptiness has a mundane cause and it is not data loss. Those tables were created by
schema migrations applied **2026-07-14T06:02Z and later — the day *after* the only session
that ever fetched anything.** No run with per-attempt evidence recording has ever executed.

### Nothing since has observed anything either

The scheduled workflow has run five times (2026-07-20 through 2026-08-17). It runs
`sentinel baseline check`, which compares committed hashes and writes no attempt evidence;
and it has had nothing to attempt regardless, because eligibility is fail-closed on human
verification and no source has been human-verified yet. The 2026-08-17 run fails loudly for
exactly this reason, which is PR #25's fail-closed fix working as designed.

`var/` is `.gitignore`d, the store has never been committed, and the workflow caches
dependencies but not the database. So even had the workflow run `watch`, each run would
have started from an empty store, streaks would have reset every week, and no streak could
ever have reached any threshold at all.

### Every failure we have ever observed is right-censored

Seven sources recorded a failure, all of them inside a **two-minute-and-forty-eight-second
window** at the end of the session (19:54:18Z–19:57:06Z):

| Source | Streak | Failure mode | Ever fetched successfully? |
| --- | --- | --- | --- |
| `us-ssa-ss5-form` | 3 | HTTP 403 | no |
| `us-ssa-number-card` | 3 | HTTP 403 | no |
| `ny-courts-name-change` | 3 | HTTP 403 | no |
| `ny-doh-vital-records` | 3 | HTTP 403 | no |
| `ca-cdph-vital-records` | 3 | TLS `CERTIFICATE_VERIFY_FAILED` | no |
| `il-sos-drivers-license` | 3 | read timeout | no |
| `mo-courts` | 1 | HTTP 500 | yes (18:49:37Z) |

**We have never once observed a source recover from a failure.** Every outage in the record
was still ongoing when observation stopped. A duration distribution estimated from zero
uncensored observations is not an estimate; there is no quantity there to estimate.

---

## 2. Can the failure modes be characterised separately?

Not from this. But the composition of what we have is itself informative, and it argues
against a single global number for a different reason than expected.

Five of the seven failures — the four `403`s and the TLS chain failure — are **persistent
access-policy or configuration conditions, not outages.** A WAF that refuses our
User-Agent and a certificate chain that does not verify return the identical result on
every attempt and do not self-resolve. A consecutive-failure counter escalates them with
probability 1, at *any* threshold. They are registry and fetch-policy problems, and
`_handle_failure` already says so in as many words when it declines to escalate a source
that has never once been fetched successfully.

That leaves two candidate transients — one `HTTP 500` and one read timeout — neither of
which was observed resolving. Two right-censored observations do not make a distribution,
and they certainly do not make four (DNS / TLS / timeout / HTTP-status) distributions.

Per-failure-mode thresholds remain the right shape of answer, and this is a real argument
for them: the chronic-403 population and the flaky-server population want different
treatment, and `docs/ROADMAP.md` §10 already asks whether the threshold should be
per-source for exactly this reason. **But the data to set them does not exist**, and
inventing four unmeasured numbers in place of one would be strictly worse.

The registry's own census (all candidates probed once, 2026-07-13) is consistent with this
picture and equally unable to settle it: it is a single instantaneous availability reading
per source, not a duration.

---

## 3. What the audit *did* establish: the units were wrong

This is a finding, it is derived from the retained data, and it is fixed.

`REMOVAL_THRESHOLD` counts **runs**. The comment defending it claimed that three runs "at
the weekly cadence this tool runs at, means roughly three weeks of a source answering
nothing at all". Nothing in the code connected those two statements. The escalation
compared a counter against a constant; no clock was involved anywhere.

The retained data shows the gap concretely rather than hypothetically:

> **Six sources reached `consecutive_failures = 3` inside seventy-four minutes**, because
> `watch` was run three times in one sitting. By the constant's own documented reasoning
> those six had been silent for three weeks. They had been silent for three minutes.

Only an accident kept that from producing six false `possibly_removed` records: all six had
failed on every attempt and so had no baseline, and the escalation correctly refuses to
claim a page "possibly disappeared" when it was never once seen. Any source that had a
baseline and then failed three times in an afternoon *would* have escalated — and a
backfill, a retry loop, a CI matrix, or an operator re-running `watch` to check something
each produce exactly that shape.

A false "this legal pathway may have been removed" is not a small error in a feed that
legal-aid workers and trans people make filing decisions against.

---

## 4. What changed as a result

No threshold was re-derived, because no threshold could honestly be re-derived. What
changed is that the rule now measures the thing it always claimed to measure, and the
repository stops claiming a measurement it does not have.

1. **`REMOVAL_THRESHOLD` stays 3, and stays labelled a guess.** Its comment now records
   that the re-derivation was attempted and why it failed, and points here.
2. **`MIN_REMOVAL_SILENCE` (14 days) is a new, additional condition.** An escalation now
   requires `removal_threshold` consecutive failures **and** at least that much elapsed
   silence. This is explicitly **not** a measured outage length and must never be cited as
   one — it is arithmetic on this tool's own declared weekly cadence, where the third
   consecutive failure of a weekly job falls about fourteen days after the first. It makes
   the threshold's documented meaning true instead of assumed. At the intended weekly
   cadence it changes nothing; it only blocks escalations manufactured by running faster.
3. **`source_health.streak_started_at`** (migration 8) records when a streak began. Without
   it the health table could not express a duration at all, so a year of faithful weekly
   runs would still have yielded no outage lengths from it.
4. **The escalation record reports the duration next to the count**, so the reviewer reads
   "3 consecutive failed fetches, silent for 14 days" rather than a count whose meaning
   depends on a cadence they have to already know.
5. **A streak with no recorded start does not escalate on the count alone.** Unknown is not
   "long enough". This delays an escalation by one cycle rather than manufacturing one, and
   it is the direction to err in.

---

## 5. What must be retained for this to be answerable

In dependency order. The first item blocks everything after it.

1. **Human-verify sources so runs have something to attempt.** Eligibility is fail-closed
   on human verification, and no candidate has been verified. Until that changes, every run
   attempts nothing and accrues no observation of any kind, forever. This is not a
   data-retention problem and no amount of infrastructure works around it.
   (`make verify-sources`, `docs/VERIFYING.md`.)
2. **Run `sentinel watch`, not only `sentinel baseline check`.** Only `watch` writes
   `watch_runs`, `run_sources` and `fetch_attempts`. `baseline check` compares committed
   hashes and records no attempt evidence — it is a drift check, not an observation.
3. **Persist the store across runs.** `var/sentinel.db` is gitignored and the workflow
   runner is ephemeral, so a CI-only deployment starts from zero every week and can never
   accumulate a streak. Either run the weekly pass on a host that keeps `var/`
   (`make watch-weekly`, which the roadmap already names the primary path), or carry the
   database between scheduled runs deliberately.
4. **Do not prune `fetch_attempts`.** It is the evidence table: one row per source per run
   with `attempted_at`, `ok`, `http_status` and the literal `error`. An outage distribution
   is a `GROUP BY` over it. Snapshots may be pruned for size; attempts are the measurement.
5. **Record recoveries, not just failures.** The estimand is the length of a *completed*
   outage, so the useful event is the transition back to success. With the retained history
   at zero such events, the first thirty or so are worth more than any volume of censored
   streaks.

### And a methodological caveat that more patience alone will not fix

**A weekly sampler cannot measure sub-weekly outages.** At one probe per week, every
observed outage length is a multiple of a week, and a two-day outage and a six-day outage
are indistinguishable — both appear as either zero failures or one. If the question is
"how long does a routine government-site outage actually last", weekly sampling cannot
answer it *in principle*, however many years it runs.

So there are two honest options, and they should be chosen between deliberately:

- **Accept that the threshold's unit is runs**, treat it as a policy choice about how many
  weekly confirmations a reviewer should require, and stop describing it as a duration; or
- **Measure availability on a separate, finer-grained, much cheaper channel** — a periodic
  `HEAD`-only liveness probe, robots-respecting and rate-limited per host, recording only
  reachability and status. That measures outage duration properly without re-fetching or
  re-hashing content, and it is the only design in reach that could produce per-failure-mode
  thresholds.

Until one of those happens, `REMOVAL_THRESHOLD = 3` remains what it has always been: a
reasonable guess, labelled as a guess, now at least guarding the quantity it names.

---

*Audit performed 2026-08-19 against the store and registry as committed. Every figure here
is reproducible from `var/sentinel.db` and `sources/registry.json`; none was estimated,
extrapolated, or filled in.*
