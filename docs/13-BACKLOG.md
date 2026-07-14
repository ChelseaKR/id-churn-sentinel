# V1.0 prioritized backlog

Estimates are ideal engineering/operations days and exclude elapsed specialist or partner review. `P0` blocks V1.0; `P1` is cut before a P0 slips. Owners are roles from the master plan.

The P0 estimates sum to **169 ideal days**. Role allocation follows the named
owner (shared security/operations items are allocated to the specialist role):

| Capacity group | Assigned P0 days | Funded days | Utilization |
|---|---:|---:|---:|
| Product + engineering | 114 | 164 | 69.5% |
| Verification + operations | 34 | 40 | 85.0% |
| Governance | 6 | 29 | 20.7% |
| Legal + security + accessibility + language | 15 | 20 | 75.0% |

The 85% verification/operations load is the hard ceiling, not spare capacity.
Any new operational P0 adds named capacity or moves the date.

## Product, research, and governance

| Item | Pri | Est. | Owner | Depends on | Acceptance / linked requirement |
|---|---:|---:|---|---|---|
| PM-01 recruit two design partners and sign pilot scope | P0 | 5 | Product | pilot brief | two LOIs, contacts, no-constituent-data terms (`PILOT-01`) |
| PM-02 baseline partner workflow and content-source map | P0 | 3 | Product | PM-01 | comparable time/quality baseline, frozen eight-case manifest and ordered reserve, and fixed comprehension answer key for each partner |
| GOV-01 convene paid community safety panel | P0 | 3 | Governance | budget | ≥3 qualified members, conflicts and authority documented |
| GOV-02 adopt reviewer qualifications, independence, and escalation | P0 | 3 | Governance | GOV-01, counsel | calibration set passes; policy approved (`REV-01/02`) |
| GOV-03 counsel review of product claims, crawling, excerpts, privacy | P0 | 2 | Legal reviewer | PRD/data map | written issues disposition; blockers closed |
| RES-01 run 12–16 workflow interviews and synthesize | P0 | 8 | Product | recruitment | evidence-linked findings and decisions, no sensitive transcripts in repo |
| PIL-01 operate six-week shadow pilot and closeout | P0 | 12 | Product | PM-02, GOV-02, SRC-01/03/04/05, ENG-03/04, PDF-01 | each partner completes eight frozen matched investigations and 10 fixed comprehension attempts; partner-level medians, real/exercise strata, no-posthoc-exclusion receipt, thresholds, and both closeout signatures pass (`PILOT-01`); this is the exact G2 entry bundle, and OPS-04 plus every other OPS item are explicitly not entry dependencies |

## Source and evidence operations

| Item | Pri | Est. | Owner | Depends on | Acceptance / linked requirement |
|---|---:|---:|---|---|---|
| SRC-01 verify every active source | P0 | 5 | Verification | GOV-02 | all active entries named/dated; skips remain ineligible (`SRC-01`) |
| SRC-02 resolve rejects, recheck-due, and active-but-unfetchable entries | P0 | 5 | Verification | SRC-01 | repair with reachable equally official source or structured gap; all six planning-time unfetchable active sources, including SS-5, have dated disposition before the operational baseline (`SRC-01/02/PDF-01`) |
| SRC-03 add verification expiry, fetch-policy decision, and eligibility rules | P0 | 2 | Engineering | GOV-02 | canonical registry stores dated robots/terms/fetch-policy reviewer, evidence, outcome and reason; redirect/authority/content sanity events trigger recheck; one predicate fails closed for unverified/recheck-due/policy-ineligible sources (`SRC-01/02`) |
| SRC-04 require verification evidence reference and migrate registry | P0 | 3 | Engineering | SRC-03 | schema/CLI reject blank evidence; clean-clone migration preserves provenance (`SRC-01`) |
| SRC-05 replace fixed zero-verification test with derived invariant | P0 | 2 | Engineering | SRC-04 | legitimate verification stays green while fabricated or incomplete records fail (`SRC-01`) |
| DATA-01 add normalizer/extractor version to provenance | P0 | 2 | Engineering | architecture | every snapshot/release trace has versions (`DET-01`) |
| DATA-02 complete hostile/golden fixture corpus | P0 | 4 | Engineering | threat model | all cases in test plan represented (`DET-01/02`) |
| DATA-03 replace five-snapshot pruning with time/pin retention | P0 | 4 | Engineering | ENG-01, OPS-02 | 24-month expiry, publication/correction/incident pins, backup-before-delete, restore boundary tests (`DET-01/PUB-02/OPS-01`) |
| DATA-04 persist complete fetch evidence | P0 | 4 | Engineering | ENG-01 | redirect chain, status, raw/normalized hashes, byte bound/truncation, MIME and extraction outcome survive restore (`DET-01`) |
| PDF-01 implement bounded PDF extraction and manual comparator | P0 | 5 | Engineering | DATA-01/04 | every active PDF produces a cited passage comparison or labeled reproducible manual receipt; empty normalization cannot publish (`PDF-01/DET-01`) |

## Engineering and publication controls

| Item | Pri | Est. | Owner | Depends on | Acceptance / linked requirement |
|---|---:|---:|---|---|---|
| ENG-01 introduce migration framework and V1 entities | P0 | 4 | Engineering | data model | clean/current backup migrations and rollback rehearsal pass |
| ENG-02 persist run/attempt/source-health status | P0 | 4 | Engineering | ENG-01, SRC-03 | watcher uses the shared eligibility predicate; exact attempted/eligible numerator and denominator plus quiet, partial, failed, stale states are tested (`DET-02/SRC-02`) |
| ENG-03 expose `status.json` and accurate site health | P0 | 3 | Engineering | ENG-02 | generated time cannot masquerade as successful watch (`DET-02/PUB-01`) |
| ENG-04 enforce independent high-impact approval | P0 | 5 | Engineering | GOV-02, ENG-01 | same actor rejected; insufficient review unpublishable (`REV-02`) |
| ENG-05 add correction/withdrawal/supersession state | P0 | 5 | Engineering | ENG-01, GOV-03 | immutable history and cycle-safe links (`PUB-02`) |
| ENG-06 atomic release and signed release manifest | P0 | 4 | Engineering | ENG-03/05, SEC-03, SRC-03 | publisher rejects unverified, expired/recheck-due, policy-ineligible, rejected, withdrawn, and gap sources; interrupted build leaves old release intact; clean verifier validates hashes/signature (`PUB-01/OPS-01/SRC-02`) |
| ENG-07 schema/compatibility fixtures and migration guide | P0 | 3 | Engineering | ENG-03/05 | all artifacts conform; old major-1 fixture reads (`PUB-01`) |
| ENG-08 PII screening/quarantine before diff publication | P0 | 3 | Engineering | data policy | seeded canary never publishes (`PRIV-01`) |
| ENG-10 separate internal rationale from constrained public copy | P0 | 4 | Engineering | GOV-03, ENG-01 | arbitrary CLI/registry notes stay private; legacy public fields audited; legal-claim terms fail closed (`REV-01/PUB-01/PRIV-01`) |
| QA-01 enforce per-target safety-critical branch coverage | P0 | 3 | Engineering | V1 module boundaries | versioned target manifest covers registry/fetch/detect/changes/store/publish/verify, V1 health/retention/signing/migrations, and state-changing CLI handlers; CI reports each target and fails any below 95% while retaining ≥90% overall; no waiver |
| ENG-09 accessible static review bundle | P1 | 6 | Engineering | ENG-04 | keyboard/screen-reader review; writes remain privileged (`REV-03`) |

## Security, accessibility, and operations

| Item | Pri | Est. | Owner | Depends on | Acceptance / linked requirement |
|---|---:|---:|---|---|---|
| SEC-01 SSRF/redirect/path/body hardening and tests | P0 | 4 | Engineering | threat model, SRC-03 | private targets/traversal/bombs blocked; live-policy audit proves UA/TLS/robots/terms decisions match canonical eligibility records (`SRC-02/PRIV-01`) |
| SEC-02 CI, secrets, dependency, and host-permission review | P0 | 2 | Security | RC config | no critical/high findings; least privilege evidenced |
| SEC-03 release-signing trust and key lifecycle | P0 | 3 | Security + engineering | hosting decision | pinned trust manifest, verifier, custody, rotation overlap, revocation and compromise rehearsal pass (`PUB-01/OPS-01`) |
| ACC-01 remediate automated/manual WCAG audit | P0 | 4 | Accessibility | RC surfaces | no critical/serious core-task issue (`ACC-01`) |
| I18N-01 reviewed Spanish stable metadata | P0 | 4 | Language lead | message catalog | two-person review and stale-string gate (`I18N-01`) |
| OPS-01 implement run lock, bounded retry, alerts, and health checks | P0 | 4 | Operations | ENG-02 | fault injection raises correct alert (`OPS-01`) |
| OPS-02 encrypted backup plus clean restore command/runbook | P0 | 3 | Operations | ENG-01 | RPO/RTO drill passes (`OPS-01`) |
| OPS-03 complete and exercise incident/correction/rollback runbooks | P0 | 4 | Operations | ENG-05/06 | three table-tops, actions/evidence recorded (`OPS-01`) |
| OPS-04 complete eight consecutive weekly bootstrap cycles | P0 | 8 | Operations | OPS-01/02/05 | per-run gates pass; rolling objectives report available sample without premature quarter claim (`OPS-01`) |
| OPS-05 provision persistent runner and deployment path | P0 | 5 | Operations + engineering | architecture, SEC-02 | named host/volume/scheduler/static host/off-host backup; staging promotion, rollback and recovery receipts (`OPS-01`) |

## Launch and commercial validation

| Item | Pri | Est. | Owner | Depends on | Acceptance / linked requirement |
|---|---:|---:|---|---|---|
| GTM-00 run reproducible competitor refresh | P0 | 2 | Product | research protocol | dated queries plus first-party repository/operations evidence recorded; Namesake's canonical-source PDF extracted-text/line-diff monitor and daily issue workflow explicitly assessed; every material overlap receives a documented build/partner/buy decision, unknowns, and positioning change |
| GTM-01 test budget owner and pricing in eight conversations | P0 | 4 | Product | RES-01 | buyer, alternative, procurement, willingness evidence recorded |
| GTM-02 partner onboarding/integration kit | P0 | 3 | Product | schema freeze | mapping template, examples, limitations, support path |
| GTM-03 secure standing integration and funding path | P0 | 5 | Product | PIL-01 | ≥1 standing workflow plus payer/committed sponsor path |
| REL-01 assemble traceability and release receipts | P0 | 3 | Product | all P0 | every checklist item dated, owned, linked |
| REL-02 multidisciplinary go/hold decision | P0 | 1 | Release authority | REL-01 | signed decision; hold if any must-pass fails |

## Execution order

Critical path: `PM-01 → PM-02 → PIL-01 → GTM-03`, `GOV-01 → GOV-02 → ENG-04/10 → OPS-04`, `SRC-04 → SRC-01/02 → PDF-01`, and `ENG-01 → DATA-03/04 → ENG-02/05 → SEC-03/ENG-06 → OPS-05 → QA-01 → RC evidence`. Source verification runs in parallel but must finish before pilot alerts are treated as production-like. PIL-01 uses only its exact entry bundle above and never waits on OPS-04. Limit engineering work in progress to two items and research/operations work in progress to two items.
