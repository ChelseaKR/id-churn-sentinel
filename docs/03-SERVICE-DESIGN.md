# Service design

## Service promise

ID Churn Sentinel promises a reviewed, reproducible observation that an identified official-source candidate changed. It does not promise legal completeness, legal meaning, or an unchanged jurisdiction. Every customer-facing moment must preserve that distinction.

## Actors

- **Service operator:** schedules runs, handles failures, publishes approved releases.
- **Source verifier:** confirms that a URL and claimed authority/document class match.
- **First reviewer:** reads evidence and classifies whether the observation merits attention.
- **Independent reviewer:** approves high-impact publication or returns it.
- **Partner editor:** investigates the underlying official source and decides whether guidance changes.
- **Community-governance lead:** handles harm reports, language, appeals, and release holds.

## Happy path

1. A verified source is selected from the registry.
2. The fetcher honors policy, retrieves bounded bytes, and records the attempt.
3. Normalization and extraction produce a versioned representation and hashes.
4. No drift: source health advances; no change claim is created.
5. Drift: old and new evidence are retained; an unreviewed observation with passage diff enters the queue.
6. First reviewer dismisses noise or confirms it with a named decision and non-legal significance label.
7. A high-impact item receives independent review; ordinary editorial items follow the configured single-review rule.
8. Publisher validates the release set, emits static artifacts and signed manifest, then atomically promotes them.
9. Partner consumes jurisdiction feed and independently investigates legal/content implications.
10. Metrics record service behavior, never reader identity.

## Exception paths

| Event | Required behavior | Forbidden shortcut |
|---|---|---|
| Fetch fails | record health failure; keep prior baseline; escalate sustained failure as unclassified `possibly_removed` | hash error page as new policy |
| Redirects | retain chain; require human review before registry URL changes | silently follow and rewrite authority |
| Source becomes unstable | quarantine; inspect visible churn; replace only with an equally official, honest source or record gap | remove visible text heuristically to silence alerts |
| PDF cannot extract | retain original/hash; label comparison limitation; route manual review | invent an empty normalized document |
| Reviewer conflict | return to queue; escalate to governance lead; record dissent | majority-vote away uncertainty without receipt |
| Published error | freeze affected item, publish correction/withdrawal and notify known institutional contacts | delete or mutate history |
| Watch did not run | public health state becomes stale; page/feed must not imply “no changes” | refresh `generated_at` alone |
| Partner reports missed change | open Sev-2 investigation; preserve report; evaluate source/normalizer/coverage gap | silently add a source and close issue |

## Service blueprint

| Stage | Partner-visible | Backstage | Evidence |
|---|---|---|---|
| Onboard | scope, limitations, feed mapping | verify sources and partner contacts | mapping receipt, pilot consent |
| Observe | health timestamp and source status | fetch, normalize, store, compare | run manifest, hashes, logs |
| Review | no item yet | human decision and dual review | immutable decision records |
| Publish | JSON/RSS/site item | conformance, manifest signing, atomic deploy | release manifest |
| Act | partner editorial ticket | optional support, no advice | partner-owned ticket reference |
| Correct | correction/withdrawal visible | incident and governance workflow | supersession chain, postmortem |

## Support model

Public documentation and a security/correction address are always available. Pilot partners receive business-day operational support; support may explain fields and evidence but must not interpret the law. Sev-1 safety or privacy reports page the service owner. All other requests receive acknowledgment within two business days.

## Manual work and automation boundary

Fetch, hash, diff, validation, release generation, and health alerting may be automated. Source authority, change significance, high-impact approval, correction language, and legal implications remain human decisions. Automation can order a queue by age or jurisdiction but may not infer legal severity.

