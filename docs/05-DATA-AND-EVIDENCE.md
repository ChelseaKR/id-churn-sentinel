# Data and evidence plan

> **Commercial activity hold — July 14, 2026.** Noncommercial data-governance,
> privacy, and open-source technical work may continue. Pilot, partner, customer,
> external-review, and production-service assumptions in this plan are paused
> under [`COMMERCIAL-STATUS.md`](./COMMERCIAL-STATUS.md).

## Evidence principles

1. Preserve what the machine observed separately from what a human judged.
2. Never infer legal meaning, gender, anatomy, identity, or user intent.
3. A missing observation is not evidence of no change.
4. Every public claim must link to provenance and an append-only decision trail.
5. Collect no reader identity; minimize operator and research data.

## Canonical data classes

| Class | Examples | Sensitivity | Public? | Retention |
|---|---|---|---|---|
| Registry | URL, authority, jurisdiction, document class, gap | public operational | yes | history indefinitely |
| Verification | verifier, date, decision, bounded public statement, internal note, evidence ref | internal attribution | status and constrained statement public; evidence/internal note limited | active + 3 years |
| Fetch evidence | headers subset, redirects, bytes, hashes, timestamps | may contain unexpected page data | no | 24 months rolling, longer for published item |
| Observation | diff, error class, source transition | public after review | reviewed subset | indefinitely if published |
| Review | actor, decision, reason, time | internal attribution | named decision for published item | indefinitely |
| Run health | source attempt/success counts, duration, errors | operational | aggregate public | 24 months |
| Consumer telemetry | none | prohibited | no | not collected |

If a fetched page unexpectedly contains personal data, quarantine the snapshot, suppress its diff, assess incident scope, and prefer a non-personal official source. Raw evidence is never automatically public.

## Provenance chain

`release_id → publication_item → observation_id → old/new snapshot_id → fetch_attempt_id → run_id/source_id → registry revision`

The chain also records normalizer/extractor version, schema version, review decision IDs, and any superseding correction. Public manifests expose enough hashes and IDs to verify released artifacts without exposing private raw evidence.

The current alpha persists explicit normalizer/extractor contract versions on new snapshots
and successful fetch attempts. Its migration preserves older snapshots as `legacy-unknown`
instead of retroactively guessing a version. Snapshot-to-publication links and signed release
manifests remain V1 work, so this landed provenance segment is not yet the complete chain above.

## Source states

- `candidate`: proposed, ineligible for monitoring claims;
- `verified`: named human confirmed authority/document-class match and verification is in date;
- `rejected`: known wrong or misleading; cannot be active;
- `withdrawn`: intentionally removed with reason;
- `recheck_due`: verification expired or redirect/authority changed;
- `gap`: a required source class cannot be watched honestly, with controlled reason vocabulary.

Verification confirms source identity only. It does not certify legal accuracy, completeness, or current effect.

## Observation and review state machine

`observed → dismissed | first_confirmed → second_confirmed → published → corrected | withdrawn`

Editorial observations may move from `first_confirmed` to publication if policy permits. High-impact observations require `second_confirmed` by a different actor. No state transition mutates or deletes prior decisions. Machine code may create only `observed`.

## Data quality rules

- IDs and enums are closed and validated; timestamps are UTC RFC 3339.
- Source URLs are HTTPS government sources unless an exception is documented and approved.
- Snapshot hashes are SHA-256 over exact raw bytes and versioned normalized text separately.
- MIME, status, redirect chain, byte length, extraction result, and truncation are recorded.
- A normalized empty result, unexpected content type, title mismatch, or large text loss quarantines comparison.
- Published diffs are bounded and screened for unexpected personal data; full evidence is available only through controlled review.
- Schema and registry examples are generated/tested from the implementation, not maintained as unverifiable prose.

## Dataset and evaluation fixtures

Maintain versioned, legally redistributable fixtures for markup-only churn, content edits, reordered navigation, soft 404s, bot walls, redirect changes, PDF revisions, encoding errors, time-varying widgets, disappearance, recovery, and correction chains. Synthetic fixtures carry no personal data. Public government extracts are minimal and cited; full downloaded pages remain out of Git unless licensing and privacy review permit them.

## Retention and deletion

Published evidence and decisions are retained because deletion would invalidate the audit trail. Superseded content remains addressable with correction status. Unpublished raw snapshots expire after 24 months unless tied to a publication, correction, incident, or legal hold; operational logs expire after 90 days; pilot research notes after 12 months; recruitment contact data after pilot closeout. Deletion jobs produce aggregate receipts and never publish subject data. The existing count-based rule that keeps only five snapshots per source is explicitly nonconforming: V1 replaces it with time-based expiry plus immutable evidence pins, verifies an off-host backup before deletion, and tests restoration of both retained and expired-boundary cases.

## Public versus internal language

Free-form reviewer, registry, and verification rationale is internal by default. Public observation fields use a constrained template that reports only what the official surface visibly did—for example, “the cited passage added the words ‘court order’”—and always directs the consumer to its own legal/editorial review. A migration audits every legacy note before V1; no existing `--note` value becomes public without explicit reclassification. Terms such as “operative,” “requires,” “permits,” or “effective” are blocked from observation copy unless they are clearly attributed as a short quotation from the cited official source and pass legal-boundary review.

## Schema governance

The product lead owns semantics; engineering owns serialization; the governance reviewer approves safety-relevant field changes. Breaking changes require a new major, migration guide, dual-publish window, and design-partner notice. New optional fields require a minor version and forward-compatibility test. Enum additions are treated as potentially breaking for strict consumers and require explicit notice.

## Evidence-quality release checks

- 100% provenance-chain referential integrity;
- zero published items missing required review or source-verification context;
- all artifact hashes match the release manifest;
- correction/supersession graph is acyclic;
- fixtures reproduce expected detection under the pinned normalizer version;
- a clean restore can reproduce the current public release.
