# Consumers — technical integration guide and historical audience hypotheses

> **Commercial activity hold — July 14, 2026.** Technical documentation for the
> free public artifacts may remain available. The named organizations, proposed
> integrations, offers, outreach, partnerships, support, funding, and maintenance
> models below are historical hypotheses only. No organization is represented as
> a customer, partner, prospect, pilot participant, sponsor, or user. Do not use
> this file for outreach while [`COMMERCIAL-STATUS.md`](./COMMERCIAL-STATUS.md)
> remains in effect.

> **Historical research date: 2026-07-13.** No consumer conversation or outreach
> is authorized while the commercial hold remains active.

## The thesis in one paragraph

Existing organizations already do the hard part of trans ID-document guidance — the writing, legal review, plain language, community trust, and, in Namesake's case, a daily extracted-text monitor for canonical-source PDFs. Freshness is therefore not an untouched category. The historical hypothesis was that no public offering yet combined a multi-jurisdiction source registry, dated verification and fetch eligibility, heterogeneous text evidence, independent review/correction, named gaps/run health, and a public no-reader-tracking feed. The repository was designed to test that combined contract and possible integration with existing editorial or monitoring workflows, not replace them. **The named organizations were modeled as possible future consumers, partners, or build/partner/buy comparators; none is represented as having agreed to any role.**

Building a 52nd guidance website would be the obvious move and the wrong one. The world does not need another page telling a trans person what documents to bring; it needs the four pages that already exist to be *right this week*.

## Where the feed actually lives — copy-pasteable, working today

**There are two hosts, both free, both requiring nothing of you. The first one works right now, with nothing switched on.**

| Base URL | Status | Use it when |
|---|---|---|
| **`https://raw.githubusercontent.com/ChelseaKR/id-churn-sentinel/main/docs/`** | **Works today. Zero setup.** | Always. This is not a fallback or a hack — the published bytes are **committed to the repository**, so raw.githubusercontent.com serves every artifact straight off the `main` branch. No build, no CI, no Pages, no account. |
| **`https://chelseakr.github.io/id-churn-sentinel/`** | Works once GitHub Pages is switched on for this repo (Settings → Pages → *Deploy from a branch* → `main` / `/docs`). | You want a human-readable site to send someone to, or nicer content types. |

**Why the published output is committed, and why there is no CI deploy.** This repository's owner has an **account-wide GitHub Actions spending limit**, so an Actions-driven Pages build would simply never run — and a feed that only exists once somebody else's billing system agrees to run a job is a feed that does not exist. So the artifacts are committed and served **from the branch**, which is also why they live in `docs/` (branch-based Pages serves `/` or `/docs`, and nothing else). See [`docs/README.md`](./README.md).

**The endpoint paths are identical under both bases**, so switching from one to the other is a change to a base URL and nothing else:

```sh
BASE=https://raw.githubusercontent.com/ChelseaKR/id-churn-sentinel/main/docs
# or, once Pages is on:
# BASE=https://chelseakr.github.io/id-churn-sentinel

curl -s "$BASE/changes.json"          # the versioned JSON feed — integrate against this
curl -s "$BASE/feed.xml"              # RSS 2.0, every jurisdiction
curl -s "$BASE/changes-us-tx.json"    # just Texas
curl -s "$BASE/feed-us-tx.xml"        # just Texas, as RSS
curl -s "$BASE/sources.json"          # the inventory: what we watch, and every named gap
curl -s "$BASE/status.json"           # persisted watch health; not the page-build time
curl -s "$BASE/schema/changes-v2.schema.json"   # the normative shape of changes.json
curl -s "$BASE/schema/status-v1.schema.json"    # the normative shape of status.json
```

## What the feed actually gives a consumer

Everything below is published to a static URL and consumable with **no account, no API key, no registration, and no email address**:

| Artifact | Path | What it is |
|---|---|---|
| **The site** | `index.html` | The human front door: what is watched, **what is not and why**, and the reviewed-change log. No JavaScript and no third-party request of any kind are tested properties; full WCAG 2.2 AA audit and remediation remain a V1 gate, not a current conformance claim. |
| **The JSON feed** | `changes.json` | The versioned JSON feed. **This is the one you integrate.** Formal schema: [`docs/schema/changes-v2.schema.json`](./schema/changes-v2.schema.json). |
| **The RSS feed** | `feed.xml` | RSS 2.0. Point any reader, Slack channel, or Zapier at it and a human sees new changes as they land. |
| **One feed per jurisdiction** | `changes-us-tx.json` · `feed-us-tx.xml` | An org that serves one state is not made to consume all 52. `us-tx` for Texas, `us-dc` for DC, plain `us` for the federal bucket (passport, SSA, Selective Service). |
| **The inventory** | `sources.json` | Every watched source **and every named gap**. This is what you map your own pages against. |
| **Run health** | `status.json` | Last attempted and last successful watch, exact eligible/attempted/successful source-ID sets, completeness, and staleness. `generated_at` is only when this file was rendered. |
| **The schema** | `schema/changes-v2.schema.json` | JSON Schema 2020-12. Build against this, not against our source code. |
| **The health schema** | `schema/status-v1.schema.json` | Closed JSON Schema 2020-12 contract for `status.json`. |

Every item is a machine-observed change **a named human reviewed and confirmed**. Source authority is earned only when its `source_verification.status` is `verified` and the verification is in date. HTML/text items carry the changed passage; PDF and other binary items in the current alpha carry an explicit byte-change notice because extracted-text passage diffs are not implemented. Nothing unreviewed is ever published.

## Before you build: what you can and cannot rely on

**`0 of 152 sources are human-verified`.** Every URL in our registry was fetched by our crawler and had its title read — a machine fact about a socket. **No human has confirmed that any given entry is the official page it claims to be**, and a machine cannot establish that: `courts.oregon.gov` serves a soft 404 (HTTP 200, body titled "404 Page Not Found") and `ecfr.gov` serves a bot-wall titled "Request Access" (also HTTP 200). So:

| You may rely on | You may **not** rely on |
|---|---|
| **The change records' machine observation and named review receipt.** For text/HTML, each carries the changed passage; for binary content, it says only that bytes changed. Hash evidence remains reproducible while the relevant snapshots are among the newest five retained by the alpha. | **The registry as a directory of official pages.** It is a list of *candidates*. Do not republish our URLs as "the official page for X" unless `source_verification.status` is `verified` and the verification is still in date. |
| **The gaps.** Every (jurisdiction, document class) pair we do not watch is a named gap with the host that refused us. | **Our silence.** An empty feed means no human has confirmed a change. It is not a claim that nothing changed. |
| **`verification_status` being present, always.** It is a field on every source in every artifact, and a merge-blocking gate asserts it on the published bytes. | **`verification_status: "unverified"` meaning "probably fine".** It means *nobody has looked*. |

**Read it in one line, and put it in your pipeline:**

```sh
# every source we have NOT had a human confirm — today, that is all of them
curl -s $BASE/sources.json | jq '.sources[] | select(.verification_status != "verified")'

# the counts, straight from the feed you are already polling
curl -s $BASE/changes.json | jq '.registry_verification'
```

If you map a page of yours to one of our `source_id`s, **map it to the URL you already trust, not to ours** — and use our feed as an alarm on that URL, which is the job it is actually good at. When the burn-down finishes, `verification_status` flips to `verified` with the name of the person who confirmed it and the date they did, and this section shrinks.

## Integrator quickstart

Every snippet below uses `$BASE` — either of the two base URLs [above](#where-the-feed-actually-lives--copy-pasteable-working-today). Nothing here needs a key, an account, or a signup.

**1. Poll the JSON. That is the whole integration.**

```sh
# every confirmed change, newest first
curl -s $BASE/changes.json | jq '.changes[]'

# only the substantive ones, only in Texas
curl -s $BASE/changes.json \
  | jq '.changes[] | select(.significance=="substantive" and .jurisdiction=="TX")'

# or skip the filtering: subscribe to the state you actually serve
curl -s $BASE/changes-us-tx.json | jq '.changes[]'
```

**2. Dedupe on `id`.** It is deterministic in `(source_id, previous_hash, new_hash)`, so a re-run cannot hand you the same change under a new key, and an `id` cited in an email six months ago still resolves. Store it. Do not re-key on our timestamps.

**3. Map your pages to `source_id`s, once.**

```sh
curl -s $BASE/sources.json | jq '.sources[] | {source_id, jurisdiction, document_class, url}'
```

**4. Read the gaps before you trust the silence.**

```sh
curl -s $BASE/sources.json | jq '.gaps[] | {jurisdiction, document_class, reason}'
```

**5. Or do none of this and put the RSS in a Slack channel.** For a name-change clinic that is genuinely the right integration, and it costs nothing.

### Polling the RSS — including your state, and only your state

RSS is the zero-engineering path, and for most legal-aid orgs it is the *right* path. There is no push, no webhook, and no subscription: **you poll a static file.** Nobody is told that you did.

```sh
# the whole country
curl -s $BASE/feed.xml

# Texas only. An org that serves one state should never have to consume all 52.
curl -s $BASE/feed-us-tx.xml

# the federal bucket — passport, Social Security, Selective Service
curl -s $BASE/feed-us.xml
```

- **The slug is `us-` + the lowercased jurisdiction**: `feed-us-tx.xml`, `feed-us-ny.xml`, `feed-us-dc.xml`. The federal bucket is plain `feed-us.xml`. Both `.xml` (RSS) and `.json` forms exist for all 52.
- **Every jurisdiction's feed exists right now, whether or not it has any items yet.** A URL that only appears the day of the emergency is a URL nobody was subscribed to. Point your reader at it today.
- **Paste the URL into Slack, Feedly, Thunderbird, Zapier, or a cron job.** Slack's `/feed subscribe <url>` is a complete integration for a name-change clinic, and it takes about fifteen seconds.
- **A polite cadence is weekly**, which is how often the watcher itself runs. Polling every minute costs you nothing and tells you nothing new.
- **Deduping in RSS is on `<guid>`.** An active observation uses the same permanent `id` as JSON. A later correction or withdrawal uses a stable, hash-derived lifecycle-event GUID and its lifecycle decision time, so feed readers deliver the correction instead of suppressing it as an already-seen item (`isPermaLink="false"` — these are identifiers, not URLs). JSON keeps the original observation `id` and exposes lifecycle fields separately.
- **Each item's `<category>` elements** carry the jurisdiction, document class, machine-observed `kind`, `publication-status:<status>`, and `source-verification:<status>` — so a pipeline can filter RSS without parsing prose.
- **An empty feed is a correct feed.** It has zero `<item>` elements and an XML comment saying it is empty rather than broken. Do not alert your team on it. See below.

### What the fields mean

| Field | Meaning |
|---|---|
| `id` | Permanent dedupe key. Deterministic; safe forever. |
| `source_id` | The registry entry that moved. Join against `sources.json`. |
| `jurisdiction` | Two-letter state, `DC`, or `US` for federal (passport, SSA, Selective Service). |
| `document_class` | `birth_certificate` · `drivers_license` · `court_order_name_change` · `passport` · `social_security` · `selective_service`. A closed set. |
| `url` | The registry URL that changed. Treat it as a candidate unless `source_verification.status` is `verified` and in date; then it is the government page the named verifier confirmed for this scope. Go and read it. |
| `observed_at` | When our crawler *saw* it — **not** when the agency made the change. A page can sit changed for a week before our weekly pass sees it, and we will never claim otherwise. |
| `previous_hash` / `new_hash` | For HTML/text, sha256 of normalized text; for PDF/other binary content, sha256 of raw bytes. The alpha keeps the newest five snapshots per source, so this is a bounded recent evidence window, not a months-long archive. `new_hash` is `""` for `possibly_removed` — there is no new content, and inventing a hash for bytes we never received would be a lie. |
| `diff_excerpt` | For HTML/text, **the passage that changed**. For a PDF or other binary source in the current alpha, an explicit notice that the bytes changed and no text diff is available. Extracted-text PDF diffs are a V1 gate. |
| `kind` | *What the machine observed.* `content_drift` = we fetched it and the text hashed differently. `possibly_removed` = we could not fetch it **at all**, N times running. |
| `significance` | *What a human judged.* `editorial` or `substantive`. **Never machine-set.** |
| `review_status` | Always `confirmed`. Unreviewed drift and dismissed noise never reach you. |
| `reviewer` | The **name of the human** who stands behind the item. Never null, never "automated". |
| `review_note` | Bounded public observation copy. Free-form reviewer rationale is private and never serialized. |
| `independent_review_status` / `independent_reviewer` / `independent_reviewed_at` | `confirmed` plus a distinct named reviewer and time for substantive observations; null for editorial observations. A returned high-impact decision never publishes. |
| `publication_status` | `active`, `corrected`, or `withdrawn`. Corrected and withdrawn records remain visible. |
| `superseded_by` / `lifecycle_reason` / `lifecycle_actor` / `lifecycle_at` | The acyclic replacement link and controlled public decision receipt for a correction or withdrawal; empty/null while active. |
| `source_verification` | **Whether anyone has confirmed the URL in this item is the page it claims to be.** `{status, verifier, verified_at, note, statement}`; `note` is a compatibility field that is always `""` because free-form registry rationale is private. `status` is `unverified` \| `verified` \| `rejected` \| `withdrawn`. A different human from `reviewer`, doing a different job: `reviewer` read the *diff*; `source_verification` is about the *source*. Today every registry value is `unverified`, so no newly reviewed observation can publish. The publisher enforces the same dated predicate as the watcher and rejects unverified, rejected/withdrawn, recheck-due, fetch-policy-ineligible, missing, or identity-mismatched sources. |

Two further top-level fields ship in `changes.json` and in every per-jurisdiction `changes-us-xx.json`:

| Field | Meaning |
|---|---|
| `registry_verification` | `{scope, sources, human_verified, unverified, rejected, statement}` — how much of the source list behind *this document* a human has actually confirmed. A URL is only authoritative for this contract when its item-level `source_verification.status` is `verified` and the verification is in date. |
| `sources` | **The sources behind this feed, scoped the same way `changes` is, each with its `verification_status`.** They ship inside the feed and not only in `sources.json` for one reason: the feed is currently *empty*, so a consumer polling only this file would otherwise learn nothing about the registry's status and would assume the URLs behind it had been confirmed. They have not been. |

### What the review states mean

- **`unreviewed`** — a machine saw bytes move. **You will never see this**; it is withheld by a merge-blocking gate.
- **`dismissed`** — a human looked and decided it was noise (a nav reshuffle, a rotating footer). **You will never see this either.** Reviewed noise is still noise.
- **`confirmed`** — a named human looked at the diff and decided it was worth your attention. This is the only thing that reaches you, and `significance` tells you whether they thought it was `editorial` (wording, layout, a dead link) or `substantive` (the page now says something different about the process).

`possibly_removed` deserves its own sentence, because it is the item most likely to be misread. It does **not** say a page was taken down. It says: *we failed to fetch this N consecutive times, here is the literal error, and a human confirmed that is worth telling you.* Removed, blocked, and down are three different worlds, and the tool refuses to choose between them.

### What this tool will NEVER tell you

- **What the law is.** A change to a web page is not a change to the law, and a hash comparison cannot read law. That is *your* job — you have the writers, the legal review, and the community trust. This is the monitor underneath your work, not a replacement for it.
- **What a change means, without a human's name on it.** Auto-classification is forbidden in four independent enforcement layers and a merge-blocking gate (`docs/RESPONSIBLE-TECH-AUDITS.md` §B). A machine announcing *"Texas substantively changed its gender-marker policy"* on the strength of a sha256 comparison would be believed, and would sometimes be wrong.
- **What anyone should do.** No advice, ever. Not in the feed, not in a "helpful summary" field.
- **That nothing changed.** An empty `changes` array means *no human has confirmed a change yet*. It is **not** a claim that nothing moved. Policy can change by an internal directive that never touches a web page, and this tool sees none of it.
- **Who reads this feed.** We do not know and we cannot find out. See below.

### `changes.json` (schema_version 2.0)

The normative document is [`docs/schema/changes-v2.schema.json`](./schema/changes-v2.schema.json) — JSON Schema 2020-12, and a merge-blocking test asserts that the schema and the code agree about every field and every enum, and that real published output validates against it. Version 1 remains published as the pre-correction compatibility contract.

**It is published at the same base as the feed, so you can validate in CI without vendoring anything:**

```sh
curl -sO $BASE/schema/changes-v2.schema.json
curl -s  $BASE/changes.json > changes.json
check-jsonschema --schemafile changes-v2.schema.json changes.json   # or any 2020-12 validator
```

Validate the per-jurisdiction documents against **the same schema** — `changes-us-tx.json` is the same shape as `changes.json` plus a `jurisdiction` field naming its scope.

The following is a complete schema-valid illustrative record. Its source is deliberately shown as verified; V1 will not publish the same record with an unverified, stale, or fetch-policy-ineligible source.

```json
{
  "schema_version": "2.0",
  "generated_at": "2026-07-13T12:00:00+00:00",
  "feed_url": "https://github.com/ChelseaKR/id-churn-sentinel",
  "jurisdiction": "TX",
  "disclaimer": "This illustrative feed reports a reviewed machine observation at a source a named human verified for this scope. It does not assert what the law is and is not legal advice.",
  "registry_verification": {
    "scope": "TX",
    "sources": 1,
    "human_verified": 1,
    "unverified": 0,
    "rejected": 0,
    "statement": "One source is in scope and a named human verified it on 2026-07-10."
  },
  "changes": [
    {
      "id": "4ee6d95ecbc042c4",
      "source_id": "tx-dps-change-dl-id",
      "jurisdiction": "TX",
      "document_class": "drivers_license",
      "url": "https://www.dps.texas.gov/section/driver-license/how-change-information-your-driver-license-or-id-card",
      "observed_at": "2026-07-11T09:04:11+00:00",
      "previous_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "new_hash": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      "diff_excerpt": "@@ -1 +1 @@\n-bring a certified copy of the prior document\n+bring a certified copy of the court order",
      "kind": "content_drift",
      "significance": "substantive",
      "review_status": "confirmed",
      "reviewer": "Jane Doe",
      "reviewed_at": "2026-07-12T14:20:00+00:00",
      "review_note": "The reviewed passage changed; downstream legal and editorial review is required.",
      "independent_review_status": "confirmed",
      "independent_reviewer": "Morgan Lee",
      "independent_reviewed_at": "2026-07-12T16:00:00+00:00",
      "publication_status": "active",
      "superseded_by": null,
      "lifecycle_reason": "",
      "lifecycle_actor": null,
      "lifecycle_at": null,
      "source_verification": {
        "status": "verified",
        "verifier": "Alex Rivera",
        "verified_at": "2026-07-10",
        "note": "",
        "statement": "VERIFIED — Alex Rivera confirmed this URL for the stated jurisdiction and document class on 2026-07-10."
      }
    }
  ],
  "sources": [
    {
      "source_id": "tx-dps-change-dl-id",
      "jurisdiction": "TX",
      "document_class": "drivers_license",
      "url": "https://www.dps.texas.gov/section/driver-license/how-change-information-your-driver-license-or-id-card",
      "authority": "Texas Department of Public Safety",
      "verification_status": "verified",
      "human_verified": true,
      "verified_by": "Alex Rivera",
      "verified_at": "2026-07-10",
      "verification_statement": "VERIFIED — Alex Rivera confirmed this URL for the stated jurisdiction and document class on 2026-07-10.",
      "reachable_by_our_crawler": true,
      "notes": ""
    }
  ]
}
```

**`significance` and `reviewer` are never machine-set** — see `docs/RESPONSIBLE-TECH-AUDITS.md` §B.

### The versioning promise

- **A major bump means a break.** Version 2 adds independent-review and correction lifecycle fields; the v1 schema remains available for integrations that have not migrated. Pin the major and validate against its matching schema.
- **`id` is permanently stable** for a given `(source_id, previous_hash, new_hash)` transition.
- **`review_status` is always `confirmed`.** If you ever see another value, we broke a promise — open an issue.
- **The feed will never require a credential.**
- **Endpoint *paths* are stable.** `changes.json`, `feed.xml`, `sources.json`, `status.json`, their versioned schemas, and `changes-us-xx.json` / `feed-us-xx.xml` for every jurisdiction. A per-jurisdiction feed exists **whether or not it has items yet** — a URL that only appears the day of the emergency is a URL nobody is subscribed to.
- **Run health is a separate fact.** Read `status.json` before interpreting feed silence. Its `generated_at` never means a watch succeeded; use `state`, `last_attempted_run`, and `last_successful_run`. The contract is `schema/status-v1.schema.json`.
- **The two *base* URLs are both supported, and the raw one is not going away.** The raw base serves the committed bytes off the branch and needs nothing enabled; the Pages base serves the identical bytes. If a third host ever appears, the paths above will be identical there too — the base is the only thing you should ever have to change.
- **A `feed_url` field appears in every document.** It is the project's canonical home, so an item that reaches someone out of context can be traced back. It is not a fetch endpoint — use the bases above.

### The feed is currently empty, and that is a real state — not a broken build

As of the first baseline run (2026-07-13) the tool watches **152 candidate sources across 52 of 52 jurisdictions**, and the feed contains **zero items**. The live endpoint above is the canonical full payload. This compact fixture is a complete schema-valid empty state with one unverified candidate source; it is not a claim that the aggregate registry contains only one source:

```json
{
  "schema_version": "2.0",
  "generated_at": "2026-07-13T12:00:00+00:00",
  "feed_url": "https://github.com/ChelseaKR/id-churn-sentinel",
  "jurisdiction": "TX",
  "disclaimer": "This illustrative empty feed contains no human-confirmed changes. Its source is an unverified candidate and is not authoritative guidance.",
  "registry_verification": {
    "scope": "TX",
    "sources": 1,
    "human_verified": 0,
    "unverified": 1,
    "rejected": 0,
    "statement": "One candidate source is in scope and no named human has verified it."
  },
  "changes": [],
  "sources": [
    {
      "source_id": "tx-dps-change-dl-id",
      "jurisdiction": "TX",
      "document_class": "drivers_license",
      "url": "https://www.dps.texas.gov/section/driver-license/how-change-information-your-driver-license-or-id-card",
      "authority": "Texas Department of Public Safety",
      "verification_status": "unverified",
      "human_verified": false,
      "verified_by": "",
      "verified_at": "",
      "verification_statement": "UNVERIFIED — machine-checked, not human-confirmed. Do not treat this candidate as authoritative guidance.",
      "reachable_by_our_crawler": true,
      "notes": ""
    }
  ]
}
```

That is correct and it is deliberate. Every change the watcher detects is born `unclassified` / `unreviewed` and is held until a named human reviews it. None has been confirmed yet, so nothing is published — **and no change was manufactured to make the feed look alive.** `feed.xml` is valid RSS 2.0 with no `<item>` elements and an XML comment saying which of the two states it is in.

**What an empty feed means:** *no human has confirmed a change yet.*
**What an empty feed does NOT mean:** *nothing changed at any watched source.* Those are different sentences, and conflating them is precisely the wrong "no change" that this project treats as its primary safety failure. Silence from this feed is never evidence that a jurisdiction is unchanged — before you rely on it, read the **12 named gaps** in `sources.json` (or on the published site), which say exactly which (jurisdiction, document class) pairs we do not watch and which host refused us.

**Integrator guidance:** treat `changes: []` as a successful poll returning no new items. Pin on `schema_version`. Do not treat an empty `changes` array as an error condition, and do not alert your team on it.

## Historical integration hypotheses — no outreach active

### Advocates for Trans Equality (A4TE) — ID Documents Center
Covers 50 states + DC + 5 territories + 5 federal document classes, and asks users to email in corrections: *"Due to the ever-changing nature of state laws and policies, we are working to keep the ID Documents Center as up to date as possible. If you see something that needs updating, please contact us."*

**Historical integration hypothesis:** supplement the contact form with a queue. A possible future integrator could map jurisdiction/document pages to independently trusted `source_id`s, poll `changes.json`, and investigate cited observations using its own editorial process. This is not a proposal to, relationship with, or representation about A4TE.

**Historical implementation estimate:** the concept assumed one scheduled job and one mapping table. That estimate was not validated with A4TE and is not a claim about its systems, costs, needs, or interest.

### Trans Lifeline — ID Change Library
Volunteer-maintained since 2016, self-acknowledged incomplete (entries flagged *"Help Us Find It"*), no API, no export, **no last-updated dates**.

**Historical integration hypothesis:** a dated research queue, not a content-freshness certification. A possible future integrator could use `changes.json` to prioritize which mapped source pages to investigate. `observed_at` is only when this crawler saw the source content; it is not the agency's change date or a last-updated date for the library. This is not a proposal to, relationship with, or representation about Trans Lifeline.

### Namesake (namesake.fyi)
Open-source and well-engineered. Namesake already runs a scheduled daily monitor for PDFs with canonical source URLs, compares extracted text with its local copy, emits changed-line diffs, and opens or updates an issue when drift appears.

**Historical integration hypothesis:** a future, separately authorized build/partner/buy analysis would begin by assessing reuse or upstream contribution, not by assuming Namesake lacks monitoring. The unverified registry is not a head start on authoritative URLs. This is not a proposal to, relationship with, or representation about Namesake.

### Legal-aid organizations and law-school clinics
(Lambda Legal, TLDEF, Transgender Law Center, state legal-aid orgs, name-change clinics.)

**Historical integration hypothesis:** a clinic could place a public RSS feed in an internal channel, inspect cited observations, and use its own qualified process to decide whether its materials need review. The per-jurisdiction feeds illustrate a low-effort technical path. No clinic is represented as using, evaluating, or being solicited for this workflow.

### Journalists and researchers
The alpha `changes.json` is a reviewed record of when the crawler observed candidate-source content move. Text/HTML records include a passage diff; binary records currently do not. The newest-five snapshot window supports recent checking, but a durable longitudinal primary-source archive and months-later reproduction are V1 gates, not current claims. A journalist should treat a URL as authoritative only when its source verification is `verified` and in date, and should still inspect the source itself.

## No account, no email, no tracking — and this is a *tested* property, not a policy

There is no SDK, no auth, no rate limit, no account, and no signup form. That is not minimalism; it is the mitigation.

**Anyone who subscribes to a feed of trans identity-document law changes is, with high probability, a trans person or someone working directly with trans people. In the current US environment that list is a targeting artifact.** It could be subpoenaed, breached, sold, or handed over, and there is no security control that makes holding it safe. So we do not secure the list. **We never create it.**

- **No user model exists in the codebase.** There is nothing to log in to.
- **No tracking of any kind** in the published bytes: no analytics, no beacon, no pixel, no cookie, no UTM parameter — and the published *site* additionally makes **no third-party request at all**: no CDN script, no external stylesheet, no web font, no image. Every external request is a request that tells a third party who is reading about trans ID law, and a page that surveils the people it claims to protect would be a disgrace.
- **This is enforced, not promised.** `test_the_feed_requires_no_account_and_carries_no_tracking` and `test_the_published_site_makes_no_third_party_requests` assert it on the **published bytes** and run in the merge-blocking `feed_integrity` gate. If someone adds a font from Google, the build goes red.
- **There is nothing to subscribe *to*.** Both consumption paths are you fetching a static file. No webhook, no mailing list, no push, no registration — and therefore no list of who reads this.
- **Consequently we cannot report readership.** We do not know who consumes the artifacts or how many readers there are. The artifact does not require or request that a reader identify itself; nothing in the repository observes a reader who does not do so.

**The one honest limit, stated concretely rather than vaguely: the files are hosted on GitHub** (raw.githubusercontent.com, and github.io once Pages is on). **GitHub's access logs exist, and they contain the IP address of anyone who fetches a file** — including which per-jurisdiction feed they fetched, which is more revealing than the unscoped one, not less. We do not control those logs, we do not receive them, we cannot delete them, and no amount of care in this repository changes that.

What we *do* control, and do: the logs are **not ours**, are **not enriched with any identity**, and are **not required** in order to consume anything. There is no account to tie an IP to. If that residual risk matters for your threat model, fetch over Tor or a VPN, or mirror the artifacts once and serve them internally — nothing about the feed makes any of that harder, and mirroring is explicitly fine (MIT).

## Historical sustainability hypotheses — paused

The following models were planning hypotheses only; none is active, validated, offered, or authorized:

1. **Grant-funded infrastructure hypothesis.** The planning rationale considered whether a small shared dependency might fit future public-interest funding. No funding request or sponsor relationship exists.
2. **Cost-shared maintenance hypothesis.** The planning artifact considered shared maintenance only as an unvalidated possibility. No maintenance service or contract is offered.
3. **Low-dependency technical design.** Zero runtime dependencies, SQLite, and static artifacts reduce technical upkeep, but they do not create or imply an operating service, reviewer capacity, support commitment, or sustainability.

**Historical validation criterion:** the former plan treated lack of institutional use as a negative result and named one organization integrating the feed as an M5 outcome. That criterion is preserved for provenance; no organization is being recruited or represented as using the feed.

**Historical scenario:** the plan illustrated possible value with an A4TE writer receiving a mapped observation before a harmed user reported stale guidance. That hypothetical is not a claim about A4TE, its workflow, or any relationship.
