# CLAUDE.md — id-churn-sentinel

Agent-facing contract for this repository, moved verbatim from the README's
"For Claude Code" section on 2026-07-19 per DOCUMENTATION-STANDARD §9
[DOC-18]: the README is the visitor's front door; the agent entrypoint,
guardrails, commands, and definition of done live here. The same guardrails
are stated for human readers throughout the README (the intro, "What it
does", "Gates", and "Honest limits").

- **Build entrypoint:** [`docs/ROADMAP.md`](./docs/ROADMAP.md) → *Implementation Plan* (M0–M5).
- **Hard guardrails:**
  1. **Never auto-classify legal significance.** The tool observes that bytes changed. A human decides what it means. This is enforced in four independent places (the detector has no vocabulary to classify; `reviewed_by` refuses an unnamed reviewer; a SQL `CHECK` constraint rejects a classified row with no reviewer; the CLI requires `--reviewer`) and gated by `make no-auto-classification`. A machine announcing *"Texas substantively changed its gender-marker policy"* on the strength of a sha256 comparison will be believed, and will sometimes be wrong.
  2. **Unreviewed drift never reaches the feed.** Gated by `make no-unreviewed-in-feed`. An item in the published feed propagates outward into advice real people act on.
  3. **A fetch failure is never drift.** Carry the old hash forward. An outage is not a content change. (But a *long* silence is escalated for human review rather than held forever — see `possibly_removed` in `core/detect.py`. Silence is not a safe default when a page may have been scrubbed.)
  4. **Never fabricate a source.** The registry holds government-domain candidates with explicit verification status; it does not call an unverified candidate authoritative. It is better to cover 21 jurisdictions honestly than to seed 300 URLs nobody has opened. If a source's robots.txt forbids us, or its TLS cannot be verified, the answer is to remove it and say so — not to route around it.
  5. **The feed requires no account, ever.** No auth, no email, no tracking, no analytics.
  6. **Do not assert what the law says.** Not in the feed, not in the docs, not in a helpful summary field. That job belongs to A4TE, Trans Lifeline, Namesake, and lawyers — and doing it badly is the harm.
  7. **Never add a source without running `sentinel sources check --twice` on it — and then reading its normalized text.** A page that re-rolls a widget on every request is a permanent false alarm, and one of those costs more trust than ten missing jurisdictions. Three were caught by `--twice`. Three *more* passed `--twice` cleanly and were caught only by reading the text (a live date, a session ticker, and a bot-wall served with HTTP 200). Both steps, every time.
  8. **Never hand-write a coverage number.** `sentinel coverage` derives them; `--check-docs` gates them. If a number in a doc is wrong, the fix is to run the command — never to edit the registry until the prose comes true.
  9. **Never flip `verified: true`. Not for any reason.** It is a named human's judgment that they opened a government page and confirmed it is the right one. An agent cannot have that judgment, and an entry marked verified by a machine is strictly worse than one marked unverified, because it will be believed. The registry enforces it (no `verified: true` loads without a named verifier and a date), and the honest way to help is to make the human's job cheaper — which is what `sentinel verify` and [`docs/VERIFYING.md`](./docs/VERIFYING.md) are.
- **Commands:** `make verify` · `make coverage` · `make watch-weekly` · `make publish` · `make serve` · `make sources-check` · `make sources-stability` · `make baseline-check` · `make verify-sources` (the human queue).
- **The published site is `docs/`, and it is served from the branch, not from CI.** `make publish` writes it; `make serve` renders it under the `/id-churn-sentinel/` subpath Pages actually uses. Never hand-edit a generated file in `docs/`, and never use a root-absolute link (`/feed.xml`) in the site — under a Pages subpath it silently points off-site. Both are gated.
- **Definition of done (M1):** every registry entry human-verified; a real weekly watch pass running; at least one reviewed change published to a feed an incumbent has actually subscribed to; all 7 gates green.
