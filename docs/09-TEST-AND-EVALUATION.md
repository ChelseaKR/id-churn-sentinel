# Test and evaluation strategy

> **Commercial activity hold — July 14, 2026.** Automated, synthetic, and
> noncommercial technical evaluation may continue. Partner recruitment, pilot
> participation, paid external evaluation, and customer-facing validation
> described here are historical plans and are paused under
> [`COMMERCIAL-STATUS.md`](./COMMERCIAL-STATUS.md).

## Quality claim

Tests must demonstrate the safety properties and operating outcome, not merely code coverage. Network-independent unit and contract tests gate every change; controlled live checks and human evaluations supply evidence the fixture suite cannot.

## Test layers

| Layer | Scope | Target / gate |
|---|---|---|
| Unit | registry, normalization, hashes, state transitions, serialization, health math | ≥95% branch coverage separately for every target in the safety-critical manifest; ≥90% overall |
| Property/fuzz | hostile HTML, encodings, URL/path inputs, state transitions, XML/JSON escaping | no crash, traversal, execution, or invalid publishable state |
| Component | fetcher with local server, SQLite migrations, review workflow, publisher | every P0 happy and failure path |
| Contract | JSON Schema, RSS, stable IDs, compatibility, status/release manifest | old V1 fixtures remain readable; current bytes conform |
| End-to-end | registry→fetch→diff→review→dual approval→atomic publish→correct | deterministic offline scenario plus staging rehearsal |
| Security/privacy | SSRF, redirects, size limits, PII canary, secrets, workflow permissions, no tracking | zero critical/high; prohibited-data scans pass |
| Accessibility | automated plus keyboard, screen reader, zoom/reflow/forced colors | WCAG 2.2 AA; no blocker/critical/serious core-task defect |
| Operational | lock/retry, partial outage, stale health, backup/restore, rollback, disk full | SLO alerts fire; RPO/RTO demonstrated |
| Human evaluation | source-verifier calibration, reviewer agreement, partner comprehension | thresholds below |

## Requirement traceability

Every P0 requirement in `01-PRD.md` has at least one automated test where automatable and a named release receipt where human evidence is necessary. Test names or markers include the requirement ID. `13-BACKLOG.md` owns implementation; `15-V1-RELEASE-CHECKLIST.md` records final results.

| Requirement | Backlog owner item(s) | Minimum V1 verification |
|---|---|---|
| `SRC-01` | SRC-01/02/03/04/05 | registry eligibility/evidence/migration tests plus named verification/export audit and derived anti-fabrication invariant |
| `SRC-02` | SRC-02/03, ENG-02/06, SEC-01 | robots/TLS/UA/redirect/private-IP tests, dated live-policy audit, all-six-unfetchable resolution receipt, and shared watcher/publisher eligibility negative tests |
| `DET-01` | DATA-01/02/03/04, PDF-01 | golden corpus, complete fetch fields, pinned-retention/restore boundary, PDF/manual comparison, and reproducible release item |
| `DET-02` | ENG-02/03 | failure/removal/quiet/partial/stale state E2E matrix |
| `REV-01` | GOV-02, ENG-04/10 | type/storage/CLI/publisher invariant tests, internal/public-copy separation, and reviewer calibration |
| `REV-02` | ENG-04 | distinct-actor negative tests plus high-impact tabletop |
| `PUB-01` | ENG-03/06/07/10, SEC-03 | JSON/RSS/schema/signed-manifest compatibility, public-copy, key-lifecycle, and atomic-publish suite |
| `PUB-02` | ENG-05 | correction/withdrawal/supersession E2E and cycle detection |
| `PRIV-01` | ENG-08/10, SEC-01/02 | prohibited-byte, PII-canary, internal-note, tracking, and host-log review |
| `OPS-01` | OPS-01/02/03/04/05, SEC-03 | fault injection, eight weekly receipts, persistent-host/deploy, signing recovery, restore/rollback evidence |
| `ACC-01` | ACC-01 | automated scan plus keyboard/screen-reader/zoom manual audit |
| `PILOT-01` | PM-01/02, PIL-01 | frozen per-partner case manifests, eight paired investigations per partner, real/exercise strata, 10 fixed comprehension attempts per partner, partner-level medians, use, and safety thresholds |
| `PDF-01` | DATA-01/04, PDF-01 | every active PDF has extraction/manual comparator receipt; low-confidence/empty paths fail closed |
| `I18N-01` | I18N-01 | translator plus independent review, locale/accessibility checks, and stale-string fail-closed test |

## Golden and adversarial fixtures

Maintain before/after fixtures for meaningful sentence changes, markup-only churn, navigation movement, time/rotating widgets, reordered equivalent lists, Unicode and encodings, truncation, soft 404, bot wall, redirect to unrelated authority, status error, timeout, TLS failure, PDF revision/extraction failure, disappearance/recovery, and correction/withdrawal. Each declares expected raw hash, normalized hash, observation kind, publish eligibility, and user-visible evidence.

Golden fixtures are versioned with the normalizer. A normalizer update runs old and new versions across the corpus and emits a reviewable delta report. A change that would erase visible policy text or create broad false drift blocks merge.

## Safety-critical branch-coverage manifest

`QA-01` owns a versioned manifest containing `core/registry.py`, `core/fetch.py`, `core/detect.py`, `core/changes.py`, `core/store.py`, `core/publish.py`, `core/verify.py`, every V1 health/retention/signing/migration module, and every state-changing CLI handler. A state-changing handler may be measured as a named function or extracted into a separately measured module; it may not disappear into a whole-file aggregate. CI emits the branch numerator, denominator, and percentage for every manifest target and fails if any target is below 95%, even when aggregate coverage exceeds 90%. New code in an existing safety domain enters the manifest in the same change. There is no release waiver.

## Safety invariant tests

- Constructing machine-observed data with human significance is impossible.
- Raw SQL cannot violate reviewer/significance constraints.
- Publisher rejects unreviewed, dismissed, stale-source, missing-verification, and insufficiently reviewed high-impact records.
- First and second reviewer identities must differ.
- Corrected/withdrawn records remain addressable and point to the superseding decision.
- Fetch failure cannot update the successful-content baseline.
- A failed/incomplete run cannot present a fresh “all clear” status.
- No artifact can omit source verification, gaps, disclaimer, or health context.
- No public byte contains analytics, cookies, tracking parameters, third-party resources, or seeded personal data.

## Live-source testing

Live network checks are operational diagnostics, never merge gates: source liveness, TLS, redirect/title sanity, and optional double-fetch stability. They use the production UA/pacing and record results without modifying verification. Before registering a source, a human reads normalized text and runs stability checks over separated intervals when feasible. Never weaken TLS or impersonate a browser to make a test green.

## Human evaluation thresholds

- At least three qualified verifiers independently assess a 20-source calibration set; ≥90% agreement, with every disagreement resolved into clearer policy or an explicit escalation path.
- Reviewers classify a 30-observation set; ≥80% agreement on publish/dismiss and 100% adherence to non-advice language.
- Each pilot partner completes the same five fixed comprehension tasks at entry and closeout and answers at least 8 of exactly 10 attempts correctly; missed attempts remain in the denominator.
- Each pilot partner completes its frozen eight-investigation matched set. Calculate the paired percentage reduction for each case, take the median separately for each partner, and require both medians to be ≥50%; never pool partners or drop a pair post hoc. Report the four sealed historical/synthetic cases separately from live cases and fixed-order reserve substitutions.

Agreement is a diagnostic, not truth. Disagreement involving harm or scope escalates to governance rather than being averaged away.

## Performance and resilience

Test at twice the V1 model: 400 sources, 10 MiB response cap, 200 observations, and 52 jurisdiction artifacts. Release generation should finish in <5 minutes on the supported CI/host and use bounded memory. Simulate 30% source failure, rate limiting, one hung host, corrupt database copy, full disk, interrupted publish, and stale run lock; unaffected evidence remains valid and no partial release promotes.

## Release test sequence

1. lint/format/type checks and full offline suite;
2. aggregate coverage plus the `QA-01` per-target ≥95% branch report, security audit, schema and safety gates;
3. clean-database migration and golden-corpus replay;
4. staging end-to-end plus correction and rollback;
5. backup/restore and manifest recomputation;
6. accessibility/manual review and security threat-model review;
7. verify the dated operational-baseline receipt, then review eight consecutive eligible-cycle receipts; reject pre-baseline or sequence-invalidated runs and label rolling-quarter metrics as partial-window evidence;
8. release checklist sign-off.

Flaky safety tests are release blockers. Quarantine is permitted only for non-safety tests with an owner, issue, reproduction evidence, and expiry date.
