# ADR 0003: append-only dual review and visible correction history

- Status: accepted for software foundation
- Date: 2026-07-14
- Owners: engineering and governance
- Review trigger: reviewer-authentication design, policy/rubric ratification, signed release work,
  or any change to public observation language or correction semantics

## Context

The alpha stored one mutable review directly on each observation and published its free-form
`--note`. That could not prove an independent high-impact decision, preserved no decision
history, exposed arbitrary operator rationale, and had no correction or withdrawal graph. A
substantive observation could therefore look complete after one person acted, while a later
edit could erase how the conclusion was reached.

## Decision

Review and lifecycle facts are separate append-only SQLite events. A first reviewer may confirm
or dismiss. A substantive confirmation remains unpublishable until a different named reviewer
records a confirmed independent decision with non-empty qualification and conflict-attestation
references. A returned independent decision is retained and blocks publication. In this
foundation it is a terminal negative decision for that immutable observation, not a hidden
"edit and resubmit" state; adding reconsideration would require a separately specified
append-only decision sequence. Database triggers restate the stage, prerequisite, and
distinct-actor rules and reject event update or deletion.

Corrections and withdrawals append one terminal lifecycle event to the original observation.
Corrections require a separately publishable replacement, use a controlled public reason code,
and form a database-enforced acyclic successor graph. Corrected and withdrawn observations stay
in schema-v2 JSON, RSS, and HTML; no history is deleted.

Free-form first- and second-review rationale remains private. Public observation copy is bounded
and rejects the legal-claim terms named by the product boundary. Registry notes are never
serialized. A legacy mutable review is treated as unaudited: its old note becomes private and
the observation remains unpublishable until a new V1 decision is recorded.

## Consequences and remaining gates

This closes the software paths for independent-review enforcement, immutable correction history,
cycle-safe supersession, and public/internal copy separation. It does not certify a reviewer as
qualified, authenticate an operator identity, ratify the governance policy, approve public copy
with counsel, execute a correction tabletop, sign a release, or audit a real legacy store. Those
remain blocking human, security, legal, and operational receipts. Qualification and conflict
references are provenance pointers, not proof merely because they are non-empty.
