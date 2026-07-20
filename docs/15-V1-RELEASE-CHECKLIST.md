# V1.0 release checklist

**Rule:** every must-pass item needs an owner, date, result, and evidence link in the release issue. “Planned,” “known,” and “works on my machine” are not passes. Domain owners attest their evidence; product and community-governance are the two joint release authorities and both sign the final ship/hold decision. Any unresolved blocking finding from community safety, security/privacy, accessibility, legal boundary, engineering integrity, or operations forces a hold.

## Requirement evidence index

The release issue must contain one row for each P0 ID: `SRC-01`, `SRC-02`, `DET-01`, `DET-02`, `REV-01`, `REV-02`, `PUB-01`, `PUB-02`, `PRIV-01`, `OPS-01`, `ACC-01`, `PDF-01`, and `I18N-01`. Each row links the accepted backlog item, automated test run where applicable, human evaluation/operational receipt, domain attester, and residual-risk decision. The domain checklist below cannot substitute for this one-to-one index.

## Product — must pass

- [ ] V1 scope/non-goals match PRD, architecture, and public copy.

## Sources and evidence — must pass

- [ ] Every active source has a named, dated human verification and nonblank evidence reference in the canonical Git registry; no rejected/recheck-due source is eligible.
- [ ] The verification authority/migration is unambiguous, and the test suite permits legitimate verified counts while rejecting fabricated/incomplete evidence.
- [ ] Every active source has an in-date, dated robots/terms/fetch-policy decision with reviewer, evidence, outcome, and reason; watcher and publisher use the same fail-closed eligibility predicate.
- [ ] All six sources that were active but unfetchable at planning time, including SS-5, were replaced with reachable equally official surfaces or converted to structured gaps before the operational baseline.
- [ ] Every uncovered core jurisdiction/document class is a structured, public named gap.
- [ ] Registry coverage and documentation are derived and consistent.
- [ ] Provenance chain is complete for 100% of release items.
- [ ] Normalizer/extractor version is recorded; golden corpus delta is reviewed.
- [ ] Raw snapshot retention, truncation, MIME, redirect, and quarantine behavior match policy.
- [ ] Count-based five-snapshot pruning is removed; time-based deletion preserves every publication/correction/incident/hold pin and proves backup-before-delete.
- [ ] Every active PDF produces a cited changed passage through bounded extraction or a reproducible labeled manual comparator; empty/low-confidence output cannot publish.
- [ ] No personal-data canary or unrestricted page body appears in public artifacts.

## Safety and governance — must pass

- [ ] No machine path can assign human significance, legal meaning, or advice.
- [ ] Unreviewed/dismissed observations are unpublishable in all artifact formats.
- [ ] High-impact items require two distinct, qualified reviewers.
- [ ] Free-form registry/reviewer/CLI rationale remains internal; all legacy public notes are audited and constrained observation copy cannot state legal effect.
- [ ] Correction, dispute, withdrawal, and supersession exercises preserve history.
- [ ] Community safety panel reviewed V1 claims, labels, gaps, and residual harms.
- [ ] Conflict disclosures are current; no funder or external organization has editorial control.
- [ ] Legal-boundary review covers UPL/consumer claims, excerpts/copyright, crawling/terms, privacy, and accessibility.

## Engineering and contract — must pass

- [ ] Full lint, formatting, strict typing, unit/component/E2E, and ≥90% overall coverage gates pass.
- [ ] The versioned safety-critical manifest covers registry, fetch, detect, changes, store, publish, verify, all V1 health/retention/signing/migration code, and state-changing CLI handlers; CI reports branch numerator/denominator separately for every target and every target meets ≥95%. This threshold has no release waiver.
- [ ] Clean install and clean/current-database migration pass on supported Python versions.
- [ ] Aggregate and 52 jurisdiction JSON/RSS artifacts validate against documented contracts.
- [ ] Schema-major compatibility fixtures and correction/status examples pass.
- [ ] Stable IDs, escaping, ordering, timezones, and deterministic rebuild are verified.
- [ ] Atomic publication and rollback exercises leave no mixed/partial release.
- [ ] Release manifest hashes and signature verify from a clean environment.
- [ ] Signing trust manifest, purpose binding, custody, rotation overlap, revocation, and compromise recovery are exercised.

## Security and privacy — must pass

- [ ] Independent threat-model review complete; no unresolved critical/high finding.
- [ ] SSRF, redirect, traversal, hostile content, resource-limit, and PII-canary tests pass.
- [ ] CI/actions/dependencies are pinned and audited; deployment permissions are least privilege.
- [ ] No secrets in Git, artifacts, logs, fixtures, issue templates, or release bundle.
- [ ] Published bytes contain no account, analytics, cookie, tracking parameter, or third-party request.
- [ ] Hosting-log retention and processor/vendor terms are documented.
- [ ] Sev-1 tabletop for forged/unreviewed publication and privacy exposure is complete.

## Accessibility and language — must pass

- [ ] Automated and manual WCAG 2.2 AA audit covers site, statuses, diffs, docs, and core review workflow.
- [ ] Keyboard, VoiceOver, NVDA, zoom/reflow, forced colors, and no-CSS core tasks pass.
- [ ] No blocker/critical/serious core-task accessibility defect remains.
- [ ] Accessibility statement and accommodation/report channel are live and tested.
- [ ] Spanish V1 metadata has translator and independent reviewer sign-off; stale translations fail closed to labeled English.

## Operations — must pass

- [ ] A dated Oct 16 operational-baseline receipt proves every bootstrap-eligibility control, including canonical dated source-policy decisions, in-date verification, shared watcher/publisher eligibility enforcement, and replacement-or-gap disposition for all six planning-time active-but-unfetchable sources; eight consecutive post-baseline weekly cycles pass every per-run gate, no material control change invalidates the sequence, and rolling-quarter metrics show available numerator/denominator without being represented as mature before a 13-week window.
- [ ] Public status correctly shows running, complete/quiet, partial, failed, and stale fixtures.
- [ ] Run lock, retry, pacing, alert routing, queue aging, and source-failure thresholds are exercised.
- [ ] Encrypted backup restores cleanly within RTO/RPO and reproduces release hashes.
- [ ] On-call service owner and independent reviewer backup are scheduled for first 30 days.
- [ ] Runbooks for source failure, wrong publication, correction, credential compromise, restore, and rollback are exercised.
- [ ] Named persistent runner, encrypted volume, scheduler, static host, off-host backup, staging promotion, and rollback path are provisioned and reproduced from the deployment runbook.
- [ ] Domain/certificate/host renewal and cost alerts are assigned.

## Documentation and support — must pass

- [ ] Public copy distinguishes observed page change, possible removal, verification, legal meaning, and silence.
- [ ] Integrator docs include schemas, examples, versioning, status, gaps, and corrections.
- [ ] Maintainers can explain product evidence without giving legal advice.
- [ ] Security, privacy, correction, accessibility, and general support contacts are monitored.
- [ ] The reproducible prior-art protocol, dated queries, first-party repository/operations evidence, candidates, and unknowns were rechecked within 30 days; Namesake's daily canonical-source PDF extracted-text/line-diff and issue workflow received an explicit reuse/contribute/build disposition.

## Final decision record

Record release candidate commit, artifact manifest, database/schema versions, host, checklist evidence index, known limitations, accepted residual risks, roll-forward/rollback owner, domain attestations from engineering, operations, community safety, accessibility, security/privacy, and legal-boundary reviewers, and the two final ship/hold signatures from product and community governance.

Any unchecked must-pass item produces a **hold**, not a conditional V1. Optional P1 work can be deferred with an owner and post-V1 milestone.
