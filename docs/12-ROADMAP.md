# V1.0 delivery roadmap — historical/paused

> **Commercial activity hold — July 14, 2026.** The dates, staffing assumptions,
> recruiting, pilots, partnerships, funding, support, and release gates below are
> preserved as historical forecasts, not current work or commitments. Only
> noncommercial public-interest research, documentation, safety analysis, and
> open-source technical work may continue under
> [`COMMERCIAL-STATUS.md`](./COMMERCIAL-STATUS.md).

This is the execution roadmap for V1.0. [`ROADMAP.md`](./ROADMAP.md) remains the implementation history and prior milestone record.

## Capacity model

Assumes one product/technical lead at 80% for 28.5 weeks (114 days), a 0.5 FTE implementation engineer for 20 weeks (50 days), 0.5 FTE verification/operations for 16 weeks (40 days), 0.2 FTE governance/review operations through release (29 days), and 20 budgeted specialist days across counsel, security, accessibility, and language. The 169-day P0 backlog assigns exactly 114 product/engineering, 34 verification/operations, 6 governance, and 15 specialist days. Product/engineering therefore uses 69.5% of 164 days; verification/operations 85% of 40; governance 20.7% of 29; specialists 75% of 20. Re-estimate remaining work at each gate; if any role's forecast rises above 85%, add capacity or move the date. P1 cuts cannot remove PDF comparison for active sources, Spanish V1 metadata, signing, retention, or deployment.

## Historical phase 1 — prove the foundation (Jul 13–Sep 25, 2026)

| Outcome | Owner | Dependencies | Exit evidence |
|---|---|---|---|
| Two design partners recruited and baseline defined | Product | outreach, pilot agreement | 2 LOIs and mapped workflow baseline |
| Active source registry human-verified | Verification lead | qualified reviewers, verification CLI | all active sources named/dated; exceptions resolved |
| Verification/evidence schema and tests corrected | Engineering | registry authority decision | required evidence reference, dated robots/terms/fetch-policy decision, shared watcher/publisher eligibility predicate, migration, and derived anti-fabrication tests pass |
| Public health distinguishes quiet/partial/failed/stale | Engineering | run model and schema | status fixtures and staging output |
| Persistent production topology provisioned | Operations + engineering | host/storage decision | runner, encrypted volume, scheduler, backup target, staging promotion receipt |
| V1 safety governance adopted | Governance lead | counsel/community panel | reviewer policy, conflict policy, release authority |
| Architecture/data contracts frozen for RC | Technical lead | ADR and schema review | accepted V1 boundary and migration plan |

**Risks:** reviewer availability, machine-checked sources proving wrong, and partner procurement delay. Mitigate with federal/high-use sources first, parallel repair queue, and an unpaid shadow pilot that does not require vendor onboarding.

## Historical phase 2 — close controls and run pilot (Sep 14–Nov 13, 2026)

| Outcome | Owner | Dependencies | Exit evidence |
|---|---|---|---|
| Independent high-impact review and correction chain | Engineering + governance | reviewer policy, schema migration | adversarial E2E tests and tabletop |
| Versioned status/release manifests and atomic publish | Engineering | hosting configuration | reproducible staging release and rollback |
| Active PDFs yield safe passage comparison | Engineering + verification | retained bytes, extractor/manual workflow | every active PDF passes extraction or reproducible manual-comparison rehearsal |
| Public/internal note boundary enforced | Engineering + governance | schema and copy policy | legacy-note audit and publisher negative tests pass |
| Operable service | Service owner | alerts, backup target, runbooks | fault injection and restore within RTO |
| Six-week partner pilot | Product + partners | mapped sources, support | weekly scorecards and signed closeout |
| Accessibility/security/legal review | specialist owners | release candidate surfaces | reports with blocker/critical issues closed |
| Spanish V1 metadata reviewed | Language + accessibility leads | stable message catalog | translator/independent review and stale-string fail-closed receipt |

**Risks:** a quiet real-world period provides few alerts; use clearly labeled synthetic exercises. Never manufacture public change records. The accessible static review bundle and optional delivery helpers are the first cuts if critical-path evidence slips; active-PDF comparison is not cuttable while PDFs remain active.

## Historical phase 3 — prove operations and release (Oct 19, 2026–Jan 29, 2027)

Freeze and receipt the complete operational baseline by October 16, then run eight consecutive eligible production-like cycles through December 14. Use the holiday contingency and January 4–15 RC window for clean migration, golden-corpus, security, accessibility, signing, deployment, restore, and rollback tests. A pre-baseline rehearsal or a sequence invalidated by a material control change does not count. Reconcile pilot findings with requirements, close or explicitly block every P0, and hold the joint release review by January 29. Release only if every must-pass checklist item has evidence. Otherwise publish a dated hold decision and revised critical path.

## Historical later options — not authorized

- accessible static reviewer bundle and richer PDF/visual comparison beyond the bounded V1 path;
- additional design partners and service integrations;
- territories or new document classes only with governance/capacity review;
- per-source adaptive outage thresholds from observed data;
- independent nonprofit/fiscal-home governance and sustainable public funding;
- daily cadence only if user evidence shows weekly latency causes harm and crawling remains responsible.

## Milestones and decision gates

| Gate | Forecast | Go condition | Hold / kill condition |
|---|---|---|---|
| G1 verified foundation | Sep 25, 2026 | 2 partners; source/evidence schema, persistent runner, and attempt denominator proven | source truth is ambiguous or deployment has no durable evidence store |
| G2 pilot start | Oct 5, 2026 | exact entry bundle complete: `PM-02`, `GOV-02`, `SRC-01/03/04/05`, `ENG-03/04`, and `PDF-01`; partners and frozen cases are ready, safe dual-review rehearsal and status are visible, and an active-PDF path is testable | partner expects legal advice or constituent data handling |
| G2b operational baseline | Oct 16, 2026 | all controls listed in the operations bootstrap-eligibility rule are installed, tested, and receipted before the Oct 19 cycle, including dated source-policy decisions, shared watcher/publisher eligibility enforcement, and replacement-or-gap disposition for all six planning-time active-but-unfetchable sources | missing/unstable control, an unresolved active-but-unfetchable source, or material semantic change; rehearsal runs cannot count |
| G3 scope lock | Nov 13, 2026 | pilot complete; all P0 implemented/testable; no unresolved architecture blocker | dual review/correction/public-copy boundary cannot be represented safely |
| G4 bootstrap close | Dec 14, 2026 | eight weekly receipts meet pre-V1 evidence rule | falsely green status, evidence loss, or safety incident invalidates the sequence |
| G5 RC | Jan 15, 2027 | implementation complete; specialist blocker findings closed | Sev-1/2 open or restore/signing/atomic publish fails |
| G6 V1.0 | Jan 29, 2027 | release checklist and partner/ops outcomes pass | no standing partner use, source verification incomplete, or safety invariant fails |

## Outcome review cadence

Weekly delivery review covers requirement burn-up, verification, pilot signals, operations, risks, and explicit scope cuts. Governance reviews any harm signal immediately. At each gate, the owner records evidence and decision; schedule pressure is not evidence.
