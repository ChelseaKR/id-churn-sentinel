# Definition of Done

This definition governs changes to the current public, headless CLI and committed static-output product. Passing it means a change is eligible for review and merge; it does not make ID Churn Sentinel V1-ready or turn its output into legal guidance.

## Merge auto-gate

`make verify` is the single local and CI merge gate. All seven stages must remain green:

1. Ruff lint, formatting, security rules, complexity, imports, and source-marker hygiene;
2. strict mypy type checking;
3. the full pytest suite with at least 90% branch coverage;
4. a blocking dependency vulnerability audit;
5. registry validation plus documentation/coverage reconciliation;
6. proof that no unreviewed change reaches a feed and no source reaches any public artifact without verification state; and
7. proof that the machine cannot classify a change's legal significance.

Network liveness, stability, and baseline checks are operational evidence, not merge gates. An outage is neither drift nor permission to remove a source from the attempt denominator.

## Review gate

- Detection may claim only that fetched content at a named URL changed and, where supported, show the changed passage and hashes.
- Detection output starts unclassified, unreviewed, and unpublishable. A named human supplies every significance and publication decision.
- No code, workflow, feed, site, or documentation interprets law, gives legal advice, guarantees completeness, or presents a machine-selected URL as authoritative.
- The shared source-eligibility rule fails closed. Eligibility requires an active source, current named human verification with evidence, and a current explicit fetch-policy allow decision with reviewer, evidence, reason, and dates. Missing, blank, rejected, denied, unreviewed, future-dated, or expired fields remain ineligible.
- The report-only shared eligibility predicate remains the sole planned rule. Until watcher and publisher wiring lands, public copy must say enforcement is not active; the wiring change must put both call sites behind that predicate without a bypass. Fetch failure stays visible in an eligible run's attempt denominator and does not silently improve coverage.
- An unverified registry entry is always labeled as a candidate in machine-readable and human-readable public output. Source verification is never inferred from HTTPS, a government hostname, a successful fetch, a title, or a matching phrase.
- Unreviewed records, anonymous reviews, conflicting identities, and unsupported schema states fail closed at every relevant type, storage, CLI, and publication boundary.
- Registry changes include the required verification/fetch-policy evidence or a named gap, preserve unique targets, and keep all derived coverage claims synchronized.
- Public JSON/RSS/HTML remains deterministic, schema-valid, correction-capable, and explicit about scope, source status, gaps, and the product's claim boundary.
- Changes affecting transgender people are reviewed for foreseeable misdirection, outing, harassment, accessibility, and disparate-impact risks; no personal case history is needed or accepted.
- Expensive-to-reverse architecture, public schema, governance, retention, or safety-boundary changes include an ADR and migration/correction plan.
- Workflow, registry, eligibility, publication, schema, and safety-test paths receive code-owner review.

## Current product shape and N/A declarations

- Hosted application controls: N/A for now; the product is a CLI plus committed static JSON, RSS, and HTML, not a request-serving application with accounts or tenancy.
- AI evaluation: N/A; no model, prompt, retrieval, prediction, or automated legal classification is used.
- Constituent and case-data retention: N/A by design; the product watches public institutional sources and must not ingest individual identity-document cases.
- Reviewer and verifier identity retention: applicable. Only a consented professional display name is collected for accountability; it is intentionally visible in the Git-backed registry/review record and public artifacts. Corrections supersede the registry or review record and regenerate current artifacts. A removal request receives governance/privacy review because public Git history and downstream copies may persist and cannot be silently promised deleted.
- Real-time safety monitoring: N/A; the watcher is periodic and makes no emergency, travel-safety, or legal-compliance guarantee.
- Full internationalization and WCAG 2.2 AA conformance are not current claims; they remain V1 release gates rather than silent assumptions.

## V1 release remains separate

V1 additionally requires current human verification for every active source, independent review for high-impact publication, design-partner shadow pilots, a stable feed and correction contract, eight qualifying weekly operating cycles, accessibility evidence, governance approval, and every must-pass item in `docs/15-V1-RELEASE-CHECKLIST.md`. No merge checklist may waive those requirements or convert an alpha artifact into authoritative guidance.

Last reviewed: 2026-07-13

Review cadence: quarterly and whenever eligibility, publication, public schema, or governance boundaries change.
