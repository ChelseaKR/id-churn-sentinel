# V1.0 delivery roadmap

This is the execution roadmap for V1.0, with forecast dates from the July 13, 2026 plan. Dates are forecasts, not commitments. [`ROADMAP.md`](./ROADMAP.md) remains the implementation history and prior milestone record. P1 cuts cannot remove PDF comparison for active sources, Spanish V1 metadata, signing, retention, or deployment.

## Phase 1 — prove the foundation (Jul 13–Sep 25, 2026)

| Outcome | Owner | Dependencies | Exit evidence |
|---|---|---|---|
| Active source registry human-verified | Verification lead | qualified reviewers, verification CLI | all active sources named/dated; exceptions resolved |
| Verification/evidence schema and tests corrected | Engineering | registry authority decision | required evidence reference, dated robots/terms/fetch-policy decision, shared watcher/publisher eligibility predicate, migration, and derived anti-fabrication tests pass |
| Public health distinguishes quiet/partial/failed/stale | Engineering | run model and schema | status fixtures and staging output |
| Persistent production topology provisioned | Operations + engineering | host/storage decision | runner, encrypted volume, scheduler, backup target, staging promotion receipt |
| V1 safety governance adopted | Governance lead | counsel/community panel | reviewer policy, conflict policy, release authority |
| Architecture/data contracts frozen for RC | Technical lead | ADR and schema review | accepted V1 boundary and migration plan |

**Risks:** reviewer availability and machine-checked sources proving wrong. Mitigate with federal/high-use sources first and a parallel repair queue.

## Phase 2 — close controls (Sep 14–Nov 13, 2026)

| Outcome | Owner | Dependencies | Exit evidence |
|---|---|---|---|
| Independent high-impact review and correction chain | Engineering + governance | reviewer policy, schema migration | adversarial E2E tests and tabletop |
| Versioned status/release manifests and atomic publish | Engineering | hosting configuration | reproducible staging release and rollback |
| Active PDFs yield safe passage comparison | Engineering + verification | retained bytes, extractor/manual workflow | every active PDF passes extraction or reproducible manual-comparison rehearsal |
| Public/internal note boundary enforced | Engineering + governance | schema and copy policy | legacy-note audit and publisher negative tests pass |
| Operable service | Service owner | alerts, backup target, runbooks | fault injection and restore within RTO |
| Accessibility/security/legal review | specialist owners | release candidate surfaces | reports with blocker/critical issues closed |
| Spanish V1 metadata reviewed | Language + accessibility leads | stable message catalog | translator/independent review and stale-string fail-closed receipt |

**Risks:** a quiet real-world period provides few alerts; use clearly labeled synthetic exercises. Never manufacture public change records. The accessible static review bundle and optional delivery helpers are the first cuts if critical-path evidence slips; active-PDF comparison is not cuttable while PDFs remain active.

## Phase 3 — prove operations and release (Oct 19, 2026–Jan 29, 2027)

Freeze and receipt the complete operational baseline by October 16, then run eight consecutive eligible production-like cycles through December 14. Use the holiday contingency and January 4–15 RC window for clean migration, golden-corpus, security, accessibility, signing, deployment, restore, and rollback tests. A pre-baseline rehearsal or a sequence invalidated by a material control change does not count. Reconcile findings with requirements, close or explicitly block every P0, and hold the joint release review by January 29. Release only if every must-pass checklist item has evidence. Otherwise publish a dated hold decision and revised critical path.

## Later options — post-V1

- accessible static reviewer bundle and richer PDF/visual comparison beyond the bounded V1 path;
- additional consumer integrations;
- territories or new document classes only with governance/capacity review;
- per-source adaptive outage thresholds from observed data;
- independent nonprofit or fiscal-home governance;
- daily cadence only if user evidence shows weekly latency causes harm and crawling remains responsible.

## Milestones and decision gates

| Gate | Forecast | Go condition | Hold / kill condition |
|---|---|---|---|
| G1 verified foundation | Sep 25, 2026 | source/evidence schema, persistent runner, and attempt denominator proven | source truth is ambiguous or deployment has no durable evidence store |
| G2b operational baseline | Oct 16, 2026 | all controls listed in the operations bootstrap-eligibility rule are installed, tested, and receipted before the Oct 19 cycle, including dated source-policy decisions, shared watcher/publisher eligibility enforcement, and replacement-or-gap disposition for all six planning-time active-but-unfetchable sources | missing/unstable control, an unresolved active-but-unfetchable source, or material semantic change; rehearsal runs cannot count |
| G3 scope lock | Nov 13, 2026 | all P0 implemented/testable; no unresolved architecture blocker | dual review/correction/public-copy boundary cannot be represented safely |
| G4 bootstrap close | Dec 14, 2026 | eight weekly receipts meet pre-V1 evidence rule | falsely green status, evidence loss, or safety incident invalidates the sequence |
| G5 RC | Jan 15, 2027 | implementation complete; specialist blocker findings closed | Sev-1/2 open or restore/signing/atomic publish fails |
| G6 V1.0 | Jan 29, 2027 | release checklist and operational outcomes pass | source verification incomplete or a safety invariant fails |

## Outcome review cadence

Weekly delivery review covers requirement burn-up, verification, operations, risks, and explicit scope cuts. Governance reviews any harm signal immediately. At each gate, the owner records evidence and decision; schedule pressure is not evidence.
