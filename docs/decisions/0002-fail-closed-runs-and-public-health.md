# ADR 0002: Source eligibility, run receipts, and public health fail closed

**Status:** accepted
**Date:** 2026-07-14
**Owners:** engineering and operations

## Context

The alpha watcher accepted a caller-supplied source list, and the publisher labeled source
verification without enforcing it. A freshly generated site could therefore look current even
when no watch had run, and an unverified source could support a newly published observation.
The V1 registry now carries dated verification evidence and a dated fetch-policy decision, but
the committed migration is intentionally incomplete: no machine may invent the 152 human and
policy decisions needed to make the operational denominator nonzero.

The long-lived SQLite evidence store also had an additive one-off migration but no migration
ledger. V1 run health needs durable schema evolution, exact source sets, and a public contract
whose timestamps cannot be confused with site-build timestamps.

## Decision

- `core.eligibility.evaluate_source` is the sole dated predicate for both watcher selection and
  publication eligibility. Reachability is deliberately not part of the predicate; a prior
  failed retrieval remains in the next eligible run's denominator.
- The production watcher derives today's UTC policy date internally; operators cannot backdate
  an expired approval or future-date one that is not yet effective. Deterministic tests and
  non-publishing historical audits may inject a date. The watcher freezes the full registry
  revision and every scoped source's eligibility decision before fetching, then persists an
  attempt before crossing the network boundary. Ineligible sources cannot enter the numerator.
- Terminal run states are constrained: `quiet` means every eligible source was retrieved and no
  observation was created; `complete` means every source was retrieved and at least one
  observation was created; `partial` means all eligible sources were attempted and at least one
  retrieval failed; `failed` makes no completion claim. `stale` is a derived public state.
- The publisher rejects a newly reviewed observation if its source is missing, withdrawn,
  unverified, expired, rejected, fetch-policy-ineligible, or inconsistent with the canonical
  jurisdiction/document-class/URL tuple.
- Every created observation is atomically associated with its still-running watcher receipt.
  Terminalization derives the observation count from those associations and holds one SQLite
  writer transaction from exact-set reads through the state update.
- `status.json` has a closed version-1 schema and publishes controlled aggregate health only.
  It separates `generated_at`, last attempted run, and last successful run, including the exact
  eligible/attempted/successful source ID sets. A jurisdiction-scoped run cannot make aggregate
  health current, and scope is explicit in every run payload. Raw operational errors stay private.
- SQLite migrations have ordered versions and immutable checksums. Each migration's complete SQL
  and ledger row commit atomically; an unknown or changed applied migration prevents opening.

## Consequences

- The current committed registry has zero eligible sources. `sentinel watch` therefore records
  a failed zero-denominator receipt and exits nonzero. This is the intended honest state until
  named humans supply real evidence and policy decisions.
- Inventory, gaps, and an empty feed may still publish. No observation backed by those candidate
  URLs may publish.
- Page generation can refresh prose and inventory without manufacturing a successful-watch
  timestamp; consumers can distinguish stale, failed, partial, quiet, and running service state.
- Existing alpha databases migrate in place, but production rollout still requires a backup,
  restore rehearsal, and rollback receipt before any run counts toward V1.

## Rejected alternatives

- **Grandfather the alpha registry:** converts machine-seeded candidates into authority without
  evidence.
- **Exclude previously unreachable sources:** flatters retrieval metrics and hides persistent
  blind spots.
- **Publish source status but do not gate it:** allows a disclaimer to coexist with an actionable
  unverified item.
- **Use site generation time as health:** makes a rebuild look like a successful watch.
- **Publish raw errors:** creates an avoidable public channel for hostile remote text and local
  paths.

## Revisit criteria

Revisit the stale interval after measured weekly operations, and the public source-ID detail if
partners show a concrete security or interoperability concern. Do not weaken the shared predicate
or exact-set receipts without governance, operations, and migration review.
