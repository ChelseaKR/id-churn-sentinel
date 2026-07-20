## Summary

Describe the consumer outcome, affected public artifacts, and intentionally excluded scope.

## Acceptance criteria

- Linked issue or acceptance criteria:
- ISO/IEC 25010 characteristics affected:

## Public-interest risk and rollback

- Could this change misstate source authority, hide a gap, classify legal significance, or publish unreviewed evidence?
- Rollback or correction plan:

## Verification

- [ ] `make verify` passes all seven merge gates locally.
- [ ] Positive, boundary, replay/idempotency, outage, and fail-closed tests are included as applicable.
- [ ] Detection still emits only machine observations: unclassified, unreviewed, and unpublishable.
- [ ] Classification and publication still require a named human reviewer.
- [ ] Source eligibility remains fail closed; missing, future-dated, expired, denied, or unreviewed verification/fetch-policy evidence cannot become eligible.
- [ ] Reachability failures remain visible in the attempt denominator and are not called drift.
- [ ] Every public source record retains explicit verification state; candidate URLs are not presented as authoritative without current human verification.
- [ ] Registry, coverage, schema, and static feed/site artifacts are regenerated or explicitly unaffected.
- [ ] Public copy says only what the evidence supports and never interprets law, gives advice, or guarantees completeness.
- [ ] Documentation, data/schema compatibility, observability, threat-model impact, and rollback are updated or marked not applicable with a reason.
- [ ] No secret, credential, personal case data, private reviewer contact data, or unsafe operational detail is committed.
- [ ] Code-owner review covers workflow, registry, eligibility, publication, schema, and safety-gate changes.

## Reviewer notes

Record residual risks, corrections required before merge, and follow-up work that remains before V1.
