# ID Churn Sentinel — V1.0 master plan

**Plan date:** 2026-07-13
**Forecast target release:** 2027-01-29
**Decision owner:** product lead
**Release authority:** product lead plus community-governance reviewer
**Status:** forward-looking open-source plan; implementation exists, but V1.0 readiness is not proven

## Outcome

V1.0 is a dependable freshness layer for organizations that maintain transgender identity-document guidance, operated as noncommercial open-source infrastructure. It monitors official public sources, preserves reproducible evidence, and publishes only human-reviewed observations about source changes. The outcome is to reduce downstream research effort without becoming legal advice, an authoritative directory, or a surveillance surface.

The V1.0 outcome is achieved only when the service can be operated safely for eight consecutive weekly cycles and its public artifacts are dependable enough for an organization to incorporate into a real editorial workflow. Shipping code alone does not satisfy the outcome.

## V1.0 boundary

V1.0 includes:

- the current 52-jurisdiction registry and its named gaps;
- human verification of every active source, with verifier and date;
- polite weekly fetch, stable normalization, retained evidence, passage diffs, and removal escalation;
- a human review queue and independent second review for high-impact publication;
- versioned aggregate and per-jurisdiction JSON/RSS feeds plus a no-tracking static site;
- source-health and last-successful-run status that distinguishes silence from a successful watch;
- correction, withdrawal, incident, backup, restore, and consumer-notification workflows;
- English product surfaces and Spanish metadata/navigation where reviewed translation can be maintained.

V1.0 excludes legal interpretation, individual guidance, automated significance classification, accounts or subscriber tracking, non-US jurisdictions, territorial expansion, automated browser evasion, unreviewed machine summaries, and a promise that silence means policy stability.

## Definition of done

All of the following are required:

1. **Evidence:** 100% of active sources have a named, dated human verification; rejected sources are repaired or become named gaps.
2. **Safety:** no unreviewed record can publish; `substantive` items require an independent second reviewer; corrections and withdrawals preserve history.
3. **Operations:** eight consecutive weekly cycles complete, ≥99% of attempt-eligible sources are attempted on each run, review begins within two business days, and backup restoration is demonstrated. Rolling-quarter objectives are instrumented during this bootstrap period and are not claimed as mature until a full 13-week window exists.
4. **Trust:** the public surface passes WCAG 2.2 AA review, has no third-party requests or first-party analytics, and clearly communicates verification, health, gaps, and limitations.
5. **Contract:** schema `1.0` has fixtures, conformance tests, a compatibility policy, and a documented deprecation process.
6. **Governance:** engineering, operations, community safety, accessibility, security/privacy, and legal-boundary owners provide named domain attestations; the product lead and panel-appointed community-governance reviewer then provide the two required ship/hold signatures. A domain attestation cannot substitute for either release-authority signature, and neither release authority can waive a domain blocker.

See [`15-V1-RELEASE-CHECKLIST.md`](./15-V1-RELEASE-CHECKLIST.md) for the evidence required at the release gate.

## Workstreams and owners

| Workstream | Directly responsible | Consulted | V1 deliverable |
|---|---|---|---|
| Source verification | Verification lead | community/legal reviewers | verified registry and exception log |
| Core engineering | Technical lead | security reviewer | feed contract, review controls, health status, tests |
| Safety and governance | Community-governance lead | counsel | two-person review rule, correction/appeal policy |
| Operations | Service owner | technical lead | runbooks, SLO dashboard, backup/restore receipt |
| Accessibility and language | Accessibility lead | Spanish reviewer | audit, remediations, reviewed metadata glossary |

One person may hold several roles, but the author of a high-impact decision cannot provide its required independent review.

## Critical path and calendar

| Phase | Dates | Exit |
|---|---|---|
| 0 — align | Jul 13–31, 2026 | named reviewers; final V1 boundary |
| 1 — verify and instrument | Aug 3–Sep 25 | active registry verified; evidence schema, health/status model, and persistent runner ready |
| 2 — close safety/contract gaps | Sep 14–Nov 13 | operational baseline freezes by Oct 16 with PDF comparison, dual review, correction/withdrawal, schema conformance, signing, retention, persistent deployment, and backup gates; remaining accessibility/language evidence closes by Nov 13 |
| 3 — eight-cycle evidence gate | Oct 19–Dec 14 | eight scheduled production-like weekly cycles plus closeout satisfy the bootstrap evidence rule |
| 4 — release candidate | Jan 4–15, 2027 | RC is frozen; accessibility, security, restore, signing, deployment, and load evidence complete |
| V1.0 decision | Jan 29, 2027 | all P0 and release checklist items pass or release is held |

A cycle counts toward the eight-cycle gate only after the Oct 16 operational-baseline receipt proves that every per-run safety, evidence, review/publication, health, retention, signing, deployment, and backup control is present. In particular, the canonical registry must hold the dated robots/terms/fetch-policy decision and in-date human verification used by the shared watcher/publisher eligibility predicate, and all six sources that were active but unfetchable at planning time must already have been replaced by reachable equally official surfaces or converted to structured gaps. A material change to any of those semantics or a failure named in the operations plan restarts the affected sequence; the team cannot count pre-control rehearsal runs. Dates are forecasts, not commitments; a failed safety gate moves the release.

## Cut rule

If capacity falls short, retain evidence integrity, PDF/manual comparison for active sources, review, operations, and Spanish V1 metadata; cut P1 UX and expansion first. Never trade away named verification, no-auto-classification, no-tracking, or correction history to protect a date.

An **attempt-eligible source** is active, not withdrawn or represented as a gap, has in-date human verification, and has an in-date, dated registry decision permitting the scheduled fetch under the documented robots/terms/fetch policy. The canonical record includes decision maker, checked date, evidence reference, outcome, and reason. One shared predicate controls watcher selection and publisher eligibility; an unverified, recheck-due, policy-ineligible, rejected, withdrawn, or gap source can neither enter the attempt denominator nor support a newly published observation. Prior retrieval failure never removes a source from this denominator; only a dated, reviewed registry decision can change eligibility. The six planning-time active-but-unfetchable sources, including the SS-5 PDF, must be replaced with reachable equally official surfaces or converted to structured gaps before the Oct 16 baseline—none may be silently excluded as an active source.

## Traceability

Requirement IDs live in [`01-PRD.md`](./01-PRD.md). Backlog items in [`13-BACKLOG.md`](./13-BACKLOG.md) name those IDs; tests in [`09-TEST-AND-EVALUATION.md`](./09-TEST-AND-EVALUATION.md) name the same IDs; the release checklist records the final evidence link. An item without this chain is not release-ready.

## Planning set

- [`01-PRD.md`](./01-PRD.md) — outcomes, users, requirements, acceptance criteria
- [`03-SERVICE-DESIGN.md`](./03-SERVICE-DESIGN.md) — end-to-end service and exception paths
- [`04-ARCHITECTURE.md`](./04-ARCHITECTURE.md) — components, contracts, storage, reliability
- [`05-DATA-AND-EVIDENCE.md`](./05-DATA-AND-EVIDENCE.md) — provenance, schemas, quality, retention
- [`06-SECURITY-PRIVACY-THREAT-MODEL.md`](./06-SECURITY-PRIVACY-THREAT-MODEL.md)
- [`07-GOVERNANCE-LEGAL-SAFETY.md`](./07-GOVERNANCE-LEGAL-SAFETY.md)
- [`08-ACCESSIBILITY-I18N.md`](./08-ACCESSIBILITY-I18N.md)
- [`09-TEST-AND-EVALUATION.md`](./09-TEST-AND-EVALUATION.md)
- [`10-OPERATIONS-SRE.md`](./10-OPERATIONS-SRE.md)
- [`12-ROADMAP.md`](./12-ROADMAP.md), [`13-BACKLOG.md`](./13-BACKLOG.md), and [`14-RISK-REGISTER.md`](./14-RISK-REGISTER.md)
- [`15-V1-RELEASE-CHECKLIST.md`](./15-V1-RELEASE-CHECKLIST.md) and [`16-RESEARCH-SOURCES.md`](./16-RESEARCH-SOURCES.md)
