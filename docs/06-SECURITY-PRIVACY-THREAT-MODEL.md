# Security, privacy, and threat model

> **Commercial activity hold — July 14, 2026.** Noncommercial security, privacy,
> threat-modeling, and open-source technical work may continue. References to
> pilot contacts, partners, customers, support, contracts, or external operations
> are historical scenarios; no such activity or relationship is represented as
> active. See [`COMMERCIAL-STATUS.md`](./COMMERCIAL-STATUS.md).

## Security objective

Protect evidence integrity and reviewer authority while minimizing the existence of sensitive data. The most important privacy control is that the service does not know who reads transgender identity-document updates.

## Assets

- source registry and verification trail;
- raw snapshots, normalized evidence, and hashes;
- reviewer identities and decisions;
- signing and deployment credentials;
- public feed integrity and availability;
- pilot contact information;
- the trust of trans people and partner organizations.

## Adversaries and failure sources

- an attacker seeking to suppress, forge, or redirect policy-change evidence;
- a compromised government page or DNS/hosting path;
- a malicious or careless maintainer/reviewer;
- a hostile scraper attempting to infer readers or partners;
- supply-chain and CI compromise;
- ordinary operator error, disk failure, clock skew, or stale automation;
- well-intentioned feature work that creates accounts, tracking, or legal claims.

## Threats and V1 controls

| Threat | Impact | Controls | Verification |
|---|---|---|---|
| Registry URL replaced with lookalike | false authority and missed change | HTTPS, government-domain policy, named verification, commit review, redirect review | registry negative tests + source audit |
| Soft 404/bot wall hashed as policy | false reassurance | content sanity rules, human verification, title/body fixtures, quarantine | adversarial fixture suite |
| Evidence tampering | unreproducible or forged alert | append-only decisions, content hashes, signed release manifest, protected backups | restore/recompute exercise |
| Reviewer credential misuse | unsafe publication | local least privilege, separate actors, dual review, immutable actor/time, no shared accounts | access review + dual-actor test |
| CI/deploy compromise | forged public feed | pinned actions, least-privilege token, protected branch/environment, manifest verification | dependency/permission audit |
| Subscriber surveillance | targeting artifact | no account, analytics, cookies, third-party requests, or subscriber database | scan published bytes and code |
| Raw page contains personal data | accidental exposure | bounded private storage, pre-publication screening, quarantine, redacted excerpts | seeded PII canary tests |
| SSRF/path injection via registry/content | internal access or overwrite | HTTPS allow policy, block local/private IPs after resolution, redirect revalidation, safe filenames, no content execution | SSRF/path traversal tests |
| Decompression/large-body bomb | resource exhaustion | byte/time/redirect/decompression limits, temp quotas | fuzz and boundary tests |
| Denial of service against sources | public harm / block | weekly pacing, per-host limits, descriptive UA, robots/terms compliance | config tests + run audit |
| Rollback or stale publish | old data appears current | monotonic release IDs, run health, atomic promotion, freshness alerts | rollback simulation |

## Privacy design

### Prohibited data

Do not collect subscriber IPs at the application layer, email addresses, account profiles, search terms, individual document histories, trans status, location, or referral analytics. Hosting providers may retain access logs; document that residual risk and choose the shortest available retention. Self-hosting must not convert logs into product analytics.

### Operator and pilot data

Store only names necessary for accountable review, work contact details, role, and decisions. Separate pilot contacts from evidence storage. Limit access to the service owner and governance lead; delete pilot contacts when the relationship ends unless the partner opts into ongoing operational contact.

## Trust boundaries and hardening

- The crawler runs with no deployment secret and no access to internal networks.
- Publication reads an approved release view; it cannot invoke the fetcher or mutate review state.
- Signing/deployment credentials are short-lived where supported and inaccessible to pull requests.
- A pinned public trust manifest binds each release key ID to purpose, owner role, algorithm, validity interval, and predecessor/successor. Release signatures require the active release-signing key; unknown, expired, revoked, wrong-purpose, or stale-revocation states fail verification rather than warn-and-pass.
- The offline signing-key runbook covers generation, two-person activation, encrypted custody, rotation overlap, verifier distribution, revocation, compromise recovery, and partner notification. The crawler and pull-request CI never receive signing material.
- Backups are encrypted, access logged, and restore-tested; encryption keys are stored separately.
- Logs use source IDs and stable error classes, not page bodies or reviewer free text.
- Dependencies remain minimal, pinned, audited, and covered by an update policy.

## Incident classification

- **Sev-1:** unreviewed/forged publication, sensitive-person data exposure, signing-key compromise, or unsafe legal-direction claim. Freeze publication immediately; acknowledge within one hour.
- **Sev-2:** missed change due to product defect, wrong verified source, corrupted evidence, sustained public outage, or materially misleading health state. Acknowledge within four business hours.
- **Sev-3:** isolated source failure, accessibility regression without blocked critical path, or partner integration break. Acknowledge next business day.

Every Sev-1/2 produces a preserved timeline, containment record, partner notification decision, correction/withdrawal when needed, and blameless postmortem. Evidence integrity takes priority over uptime.

## Security release gates

- threat-model review by someone other than the primary implementer;
- no critical/high unresolved vulnerability in shipped code or hosting configuration;
- secrets and permissions audit; dependency and workflow pinning review;
- SSRF, traversal, hostile-content, PII-canary, and authorization tests pass;
- backup restore, clean-environment release-manifest verification, rotation, revocation, and compromised-key recovery demonstrated;
- incident tabletop covers compromised source, malicious review, and privacy exposure.
