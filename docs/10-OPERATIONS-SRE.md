# Operations and SRE plan

## Service model

The system is a weekly batch service plus static public artifacts. Operations must make three states unambiguous: the watch succeeded and produced no observations; it produced observations awaiting review; or it did not complete. The public feed’s silence is never used as an availability metric.

## Service level indicators and objectives

| SLI | V1 objective | Window |
|---|---|---|
| Scheduled-run start | 100% start within 2h of weekly schedule | rolling 8 weeks |
| Attempt-eligible-source completeness | ≥99% attempted per run | each run |
| Eligible-source successful retrieval | report, no fixed promise; alert if <95% or >5pp below trailing baseline | each run |
| Pipeline completion | ≥99% successful runs, excluding declared maintenance | rolling quarter |
| Review acknowledgment | 95% of new observations opened within 2 business days | rolling quarter |
| High-impact second review | 90% within 3 business days; none publish early | rolling quarter |
| Static artifact availability | 99.9% | monthly |
| Status freshness | public `status.json` updated within 30m of run terminal state | each run |
| Correction acknowledgment | Sev-1 1h; Sev-2 4 business hours | per incident |
| Recovery | RPO ≤24h after successful run; RTO ≤4h | restore drill |

Do not define an SLO for “all policy changes detected”; the system cannot observe unpublished/internal changes. Track known missed-change reports separately.

“Attempt eligible” is the canonical, committed registry state: active, not withdrawn or a gap, in-date human verification, and permitted by an in-date, evidenced robots/terms/fetch-policy decision. The decision records reviewer, checked date, evidence reference, outcome, and reason. One shared predicate controls watcher selection and publisher eligibility; unverified, expired/recheck-due, policy-ineligible, rejected, withdrawn, and gap sources fail closed. Failed retrievals remain in the denominator. A source leaves the denominator only through a dated, reviewed registry change; the run receipt stores the registry revision and the exact numerator/denominator list.

### Pre-V1 bootstrap evidence

V1 requires eight consecutive scheduled production-like cycles. A cycle is eligible to count only after a dated October 16 operational-baseline receipt proves that the V1 attempt denominator, canonical dated robots/terms/fetch-policy decisions, in-date verification, shared watcher/publisher eligibility enforcement, resolution of all six planning-time active-but-unfetchable sources to reachable equally official surfaces or structured gaps, complete fetch evidence, time/pin retention, active-PDF comparison path, review/publication eligibility, corrections, public/internal copy boundary, status semantics, signed atomic release, persistent deployment, and off-host backup controls are installed and tested. Each cycle must pass the per-run attempt, status-freshness, backup, and safety gates; schedule-start performance is evaluated across the full eight-week window. Rolling-quarter review and second-review objectives are instrumented and reported over every available eligible event with numerator, denominator, and confidence/coverage caveat, but V1 does not claim a mature quarterly SLO result until 13 weeks of post-instrumentation history exist. Static availability is measured for the calendar months in which the release candidate is hosted. Any material semantic change to a baseline control restarts the sequence at the next eligible run. Any Sev-1, falsely green status, unreviewed publication, lost evidence, or uncontained Sev-2 also restarts the affected bootstrap gate after remediation. Rehearsal runs before the baseline never count.

## Run lifecycle

1. Preflight disk, time, database integrity, registry/schema validity, lock, and backup destination.
2. Create immutable run record and expose `running` without implying success.
3. Fetch sources with host pacing; persist every attempt; isolate source failures.
4. Normalize/compare and enqueue observations; validate counts and invariants.
5. Mark run `complete`, `partial`, or `failed` with reason; publish health status atomically.
6. Notify reviewers of queue state; never publish observations from automation.
7. Create encrypted backup, verify checksum, apply retention, and emit run receipt.
8. After human review, build, validate, sign, and atomically promote a release.

## Alerting

Page the service owner for unreviewed publication, database integrity failure, release hash mismatch, evidence exposure, signing/deploy compromise, or public health state that falsely reports success. Business-hours alert for run failure/partial completion, >5pp retrieval drop, source failure streak reaching threshold, review queue oldest age >2 business days, backup failure, certificate/domain expiration, accessibility regression, or partner contract break.

Alerts must say impact, evidence, immediate safe action, and run/source IDs. Avoid page body content and free-form personal data in notifications.

## Required runbooks

- scheduled run failed or hung;
- source blocked, redirected, unstable, or possibly removed;
- database locked/corrupt/full disk;
- review queue backlog or reviewer unavailable;
- wrong source or unreviewed item published;
- correction/withdrawal and partner notification;
- static host/domain/certificate failure;
- signing/deployment credential compromise;
- backup, clean restore, and point-in-time recovery;
- release rollback without erasing correction history;
- privacy/security report and evidence quarantine.

Each runbook names trigger, severity, first safe action, decision owner, commands, verification, communication, and post-incident follow-up. Commands default to stopping publication rather than skipping evidence checks.

## Backup and disaster recovery

After every complete/partial run, create an encrypted SQLite backup using the database backup mechanism, export registry revision and release manifest, hash the bundle, and store it in a separate failure domain. Retain 8 weekly and 12 monthly copies; retain published-item evidence under the data policy. Quarterly, restore the newest backup to a clean environment, run integrity/schema checks, rebuild the current release, and compare artifact hashes. V1 requires one observed restore within RTO.

## Change management

All production changes use reviewed Git commits, passing gates, a migration/rollback note, and a release receipt. Database migrations are forward-tested on a backup copy; destructive migrations are forbidden in V1. Normalizer changes include corpus delta review. Schema changes follow the compatibility policy. Emergency changes may shorten review time but may not bypass safety invariants; they receive retrospective review within one business day.

## Capacity and cost

Track fetch duration/bytes, database growth, artifact size, review minutes, backup time, and hosting/build cost. Warning thresholds: run duration >4h, store >50 GiB, backup >1h, oldest review >2 business days, or median weekly human work >4h. Respond first by improving source quality and review ergonomics; do not automate legal judgment to reduce workload.

## Operational readiness review

Before V1: persistent runner, storage, scheduler, static host, and off-host backup are provisioned; on-call owner and backup named; access and secrets reviewed; monitoring tested by fault injection; runbooks exercised; eight consecutive cycles satisfy the bootstrap rule; queue never relies on one unavailable reviewer; restore, signing-key recovery, deployment, and rollback are demonstrated; public status accurately reflects failed, partial, quiet, and stale runs.
