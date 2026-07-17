# Security policy

id-churn-sentinel publishes information that trans people may act on when
correcting identity documents. Security here is inseparable from user safety:
the worst-case failure is not a leaked credential — the tool holds none — it
is a **wrong or manipulated published change** that sends someone to the wrong
office, or a subscriber list that becomes a list of trans people. Please read
the threat model in
[`docs/06-SECURITY-PRIVACY-THREAT-MODEL.md`](docs/06-SECURITY-PRIVACY-THREAT-MODEL.md)
and [`docs/RESPONSIBLE-TECH-AUDITS.md`](docs/RESPONSIBLE-TECH-AUDITS.md).

## Supported versions

This is a pre-1.0 technical alpha; there is no tagged release yet. Security
fixes land on `main` and, once one exists, the latest tagged release.

| Version | Supported |
| ------- | --------- |
| `main` / latest tag | ✅ |
| older tags | ❌ |

## Reporting a vulnerability

**Email ckellyreif@gmail.com** with `id-churn-sentinel security` in the
subject — this is the primary channel today: the repo is private, and GitHub's
private vulnerability reporting ("Report a vulnerability" under the *Security*
tab) is not functional on a private free-plan repo. If the repo becomes
public, GitHub PVR becomes the preferred channel and this section will be
reordered. Expect an acknowledgement within a few days; this is a volunteer
project on a commercial hold, so please be patient and do not disclose
publicly until a fix is available.

## What we consider a vulnerability

In addition to the usual (RCE, injection, dependency compromise, secret
exposure), the following are **first-class** security bugs here:

- **Any path by which unreviewed or dismissed drift reaches a published
  artifact** (the feed, `changes.json`, the site) — the safety property gate 6
  exists to hold.
- **Any path by which a change is classified `substantive` without a named
  human** — gate 7's property.
- **Any path by which a source renders to a consumer without its verification
  status** — every source is currently unverified, and hiding that is a lie
  by omission.
- **Any path by which the fetcher's politeness or integrity posture degrades**:
  spoofed User-Agent, disabled certificate verification, robots.txt bypass, or
  unbounded sockets against government servers.
- **Any tampering with the committed published output in `docs/`** — with no
  CI in the loop, the committed bytes are the served bytes.

## Our commitments

- Regressions of the publication-safety gates are fixed with the highest
  priority; if a gate goes red, the response is to stop, not to weaken it.
- We credit reporters who want credit, and respect those who want anonymity.
- The runtime has zero dependencies (stdlib only); the dev toolchain is locked
  (`uv.lock`) and scanned (pip-audit in `make verify` and CI, CodeQL for
  Python and the workflows themselves, gitleaks in pre-commit, TruffleHog
  full-history sweeps) — noting that scheduled workflows are subject to the
  account's Actions spending limit, which is why every gate is reproducible
  locally with `make verify`.
