# Product requirements — V1.0

## Problem

Organizations already maintain trusted transgender identity-document guidance, but official pages change, disappear, redirect, or silently become stale. Their current detection mechanism is periodic manual rereading or a user reporting harm after guidance has failed. A generic webpage-change alert does not preserve the changed passage, distinguish outages from drift, expose coverage gaps, or provide a governance-safe publication path.

The user need is not another guide. It is: **“Tell our editors which underlying official source deserves attention, show reproducible evidence, and do not claim what the change means.”**

## Goals and measures

- Attempt ≥99% of attempt-eligible sources on each scheduled weekly run and preserve reproducible evidence for every completed attempt. Attempt eligibility is the dated registry decision defined in the master plan; a prior failure cannot silently remove a source from the denominator.
- Publish zero records without named review and zero high-impact records without independent second review.
- Make source verification, gaps, run health, and correction state impossible to omit from consumer artifacts.
- Sustain eight weekly bootstrap cycles with no Sev-1 or uncontained Sev-2 incident; begin rolling-quarter reporting without claiming a full-quarter result before 13 weeks exist.

## Non-goals

- Interpreting law, recommending an action, or determining legal effect.
- Certifying that a source is legally complete or current.
- Monitoring private portals, collecting user stories, or identifying feed readers.
- Predicting changes or scoring jurisdictions.
- Replacing A4TE, Trans Lifeline, Namesake, legal-aid lawyers, or community organizations.

## Personas and jobs

| Persona | Job | Current failure |
|---|---|---|
| Advocacy content editor | decide which guidance page to re-check | manually rereads everything or learns from harmed users |
| Legal-aid clinic coordinator | notice a relevant local change quickly | generic alerts are noisy and nationwide feeds overwhelm |
| Source verifier | confirm the watched URL represents the claimed source class | machine liveness is mistaken for authority |
| Change reviewer | decide whether an observation merits publication | page-scale diffs and noise cause fatigue |
| Service operator | know whether silence is healthy | fresh site generation can hide failed crawls |
| Downstream developer | ingest stable, scoped records | undocumented schema changes break automation |

## User stories

- As an editor, I can subscribe only to my jurisdictions and receive a cited changed passage so I know where to begin human research.
- As a verifier, I can accept, reject, skip, or repair a source while leaving a named and dated audit trail.
- As a reviewer, I cannot accidentally publish an unclassified or unsigned observation.
- As a second reviewer, I can approve or return a high-impact item without overwriting the first reviewer’s record.
- As an operator, I can distinguish “nothing reviewed” from “watch did not run” and identify stale sources.
- As a consumer, I can withdraw trust in a corrected item without losing its history.

## P0 requirements

| ID | Requirement | Acceptance criteria |
|---|---|---|
| SRC-01 | Human-verified active registry | Every active source has verifier name/date and evidence; rejected, expired, recheck-due, or otherwise ineligible entries cannot be watched or support new publication; every core gap is explicit. |
| SRC-02 | Honest fetch policy and eligibility | HTTPS verification, descriptive UA, robots and terms honored, bounded requests; the canonical registry records a dated, evidenced allow/deny decision used by one watcher/publisher predicate. Blocked sources become gaps rather than evasive fetches, and all six planning-time active-but-unfetchable sources are replaced with reachable equally official surfaces or made structured gaps before the operational baseline. |
| DET-01 | Reproducible change detection | A successful fetch stores timestamp, redirect chain, status, raw-byte hash, normalized-text hash, and bounded content; fixtures prove cosmetic churn suppression without hiding text changes. |
| DET-02 | Health versus change separation | Fetch failure never becomes content drift; consecutive failures can create an unclassified `possibly_removed` observation; public health state names last attempted and successful runs. |
| REV-01 | Named human review | Only confirmed records with named reviewer, time, note, and human-set significance can publish; this is enforced in types, storage, CLI, and tests. |
| REV-02 | Independent high-impact review | `substantive` records require a second qualified reviewer who is not the first reviewer before publication. A return records reason and preserves both decisions. |
| PUB-01 | Stable public contract | Versioned JSON and RSS exist globally and per jurisdiction; schema, examples, compatibility policy, deterministic IDs, verification/health metadata, and a signed release manifest with documented verification and key lifecycle pass conformance tests. |
| PUB-02 | Correction and withdrawal | A published record can be corrected or withdrawn without deletion; feeds expose status, supersession link, reason, decision maker, and time. |
| PRIV-01 | Data minimization | No accounts, subscriber list, analytics, cookies, pixels, third-party resources, or sensitive search logging exist in the public product. |
| OPS-01 | Operable weekly service | Idempotent scheduled run, locking, bounded retry, alerting, backups, restoration, and operator runbooks meet the SLOs in `10-OPERATIONS-SRE.md`. |
| ACC-01 | Accessible public/review surfaces | Automated and manual WCAG 2.2 AA checks pass; all status and diffs work without color, mouse, sound, or JavaScript. |
| PDF-01 | Bounded PDF comparison | Every active PDF source yields versioned extraction and passage comparison or a labeled, reproducible human comparator using retained original bytes; unsupported or low-confidence output cannot publish an empty or invented passage. |
| I18N-01 | Reviewed Spanish metadata | Stable navigation, status labels, limitations, and correction instructions have reviewed Spanish equivalents; legal/source content is not machine-translated and stale translations fail closed to labeled English. |

## P1 requirements

| ID | Requirement | Acceptance criteria |
|---|---|---|
| REV-03 | Accessible static review bundle | A reviewer can inspect evidence and decide from keyboard/screen reader without installing the CLI; writes remain authenticated operator actions. |
| INT-01 | Consumer delivery helpers | Conditional GET, sample Slack/email bridge, and webhooks evaluated without collecting subscriber identities. |

## P2 / post-V1 candidates

Territories, international documents, browser-rendered sources, institution-hosted private mirrors, source relationship graphs, richer review UI, and additional document classes. None may enter V1 if it delays P0 evidence.

## Metrics

**Leading:** verified-source burn-down; median diff review minutes; false-drift dismissal rate; stale-source count; weekly cycle completion; schema conformance; remediation aging.

**Lagging:** missed-change discoveries; correction rate; incidents; cost per watched source.

Do not optimize alert count, page views, subscriber identity, or “coverage” without gap quality. More alerts can mean a worse system.

## Open questions

- Which decisions qualify a reviewer for source verification versus high-impact review?
- Is `substantive` the right public label, or should V2 use non-legal “attention priority” language?
- What observed outage distribution should set per-source removal thresholds?
- Which Spanish surfaces can be maintained with a named reviewer after each change?
