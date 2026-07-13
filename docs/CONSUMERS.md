# Consumers — who this feed is for, and why they are the customers

> **Last verified: 2026-07-13 · Recheck cadence: per consumer conversation.**

## The thesis in one paragraph

Three organizations already do the hard part of trans ID-document guidance — the writing, the legal review, the plain language, the community trust — across every US jurisdiction. **All three publicly concede they cannot keep it current.** That is not a content problem, and it is not a coverage problem; both of those are solved. It is a *monitoring* problem, and nobody has built the monitor. This repo is the monitor. Its output is designed to be consumed by those organizations, wired into their editorial workflow, and never seen by an end user at all. **They are the customers. This is infrastructure, not a competitor.**

Building a 52nd guidance website would be the obvious move and the wrong one. The world does not need another page telling a trans person what documents to bring; it needs the four pages that already exist to be *right this week*.

## What the feed actually gives a consumer

Everything below is published to a static URL and consumable with **no account, no API key, no registration, and no email address**:

| Artifact | What it is |
|---|---|
| **`index.html`** | The human front door: what is watched, **what is not and why**, and the reviewed-change log. Accessible (WCAG 2.2 AA), no JavaScript, no third-party request of any kind. |
| **`changes.json`** | The versioned JSON feed. **This is the one you integrate.** Formal schema: [`docs/schema/changes-v1.schema.json`](./schema/changes-v1.schema.json). |
| **`feed.xml`** | RSS 2.0. Point any reader, Slack channel, or Zapier at it and a human sees new changes as they land. |
| **`changes-us-tx.json`** · **`feed-us-tx.xml`** | **One feed per jurisdiction.** An org that serves one state is not made to consume all 52. |
| **`sources.json`** | The inventory: every watched source **and every named gap**. This is what you map your own pages against. |

Every item is a change **a named human reviewed and confirmed**, at an **official government URL**, with **the passage that changed**. Nothing unreviewed is ever published.

## Before you build: what you can and cannot rely on

**`0 of 152 sources are human-verified`.** Every URL in our registry was fetched by our crawler and had its title read — a machine fact about a socket. **No human has confirmed that any given entry is the official page it claims to be**, and a machine cannot establish that: `courts.oregon.gov` serves a soft 404 (HTTP 200, body titled "404 Page Not Found") and `ecfr.gov` serves a bot-wall titled "Request Access" (also HTTP 200). So:

| You may rely on | You may **not** rely on |
|---|---|
| **The change records.** Each was reviewed and signed by a named human, cites an official URL, and carries the passage that changed and a reproducible hash. | **The registry as a directory of official pages.** It is a list of *candidates*. Do not republish our URLs as "the official page for X" — that is a claim nobody has made. |
| **The gaps.** Every (jurisdiction, document class) pair we do not watch is a named gap with the host that refused us. | **Our silence.** An empty feed means no human has confirmed a change. It is not a claim that nothing changed. |
| **`verification_status` being present, always.** It is a field on every source in every artifact, and a merge-blocking gate asserts it on the published bytes. | **`verification_status: "unverified"` meaning "probably fine".** It means *nobody has looked*. |

**Read it in one line, and put it in your pipeline:**

```sh
# every source we have NOT had a human confirm — today, that is all of them
curl -s https://<host>/sources.json | jq '.sources[] | select(.verification_status != "verified")'

# the counts, straight from the feed you are already polling
curl -s https://<host>/changes.json | jq '.registry_verification'
```

If you map a page of yours to one of our `source_id`s, **map it to the URL you already trust, not to ours** — and use our feed as an alarm on that URL, which is the job it is actually good at. When the burn-down finishes, `verification_status` flips to `verified` with the name of the person who confirmed it and the date they did, and this section shrinks.

## Integrator quickstart

**1. Poll the JSON. That is the whole integration.**

```sh
# every confirmed change, newest first
curl -s https://<host>/changes.json | jq '.changes[]'

# only the substantive ones, only in Texas
curl -s https://<host>/changes.json \
  | jq '.changes[] | select(.significance=="substantive" and .jurisdiction=="TX")'

# or skip the filtering: subscribe to the state you actually serve
curl -s https://<host>/changes-us-tx.json | jq '.changes[]'
```

**2. Dedupe on `id`.** It is deterministic in `(source_id, previous_hash, new_hash)`, so a re-run cannot hand you the same change under a new key, and an `id` cited in an email six months ago still resolves. Store it. Do not re-key on our timestamps.

**3. Map your pages to `source_id`s, once.**

```sh
curl -s https://<host>/sources.json | jq '.sources[] | {source_id, jurisdiction, document_class, url}'
```

**4. Read the gaps before you trust the silence.**

```sh
curl -s https://<host>/sources.json | jq '.gaps[] | {jurisdiction, document_class, reason}'
```

**5. Or do none of this and put the RSS in a Slack channel.** For a name-change clinic that is genuinely the right integration, and it costs nothing.

### What the fields mean

| Field | Meaning |
|---|---|
| `id` | Permanent dedupe key. Deterministic; safe forever. |
| `source_id` | The registry entry that moved. Join against `sources.json`. |
| `jurisdiction` | Two-letter state, `DC`, or `US` for federal (passport, SSA, Selective Service). |
| `document_class` | `birth_certificate` · `drivers_license` · `court_order_name_change` · `passport` · `social_security` · `selective_service`. A closed set. |
| `url` | The **official government page** that changed. Not a mirror, not our summary. Go and read it. |
| `observed_at` | When our crawler *saw* it — **not** when the agency made the change. A page can sit changed for a week before our weekly pass sees it, and we will never claim otherwise. |
| `previous_hash` / `new_hash` | sha256 of the normalized page text before/after. Reproducible: we keep the bytes, so any published diff can be verified independently months later. `new_hash` is `""` for `possibly_removed` — there is no new content, and inventing a hash for bytes we never received would be a lie. |
| `diff_excerpt` | **The passage that changed.** The reason this feed exists: `"+a court order is required"` is reviewable in thirty seconds; `"texas.gov changed"` is not. |
| `kind` | *What the machine observed.* `content_drift` = we fetched it and the text hashed differently. `possibly_removed` = we could not fetch it **at all**, N times running. |
| `significance` | *What a human judged.* `editorial` or `substantive`. **Never machine-set.** |
| `review_status` | Always `confirmed`. Unreviewed drift and dismissed noise never reach you. |
| `reviewer` | The **name of the human** who stands behind the item. Never null, never "automated". |
| `source_verification` | **Whether anyone has confirmed the URL in this item is the page it claims to be.** `{status, verifier, verified_at, note, statement}`; `status` is `unverified` \| `verified` \| `rejected` \| `withdrawn`. A different human from `reviewer`, doing a different job: `reviewer` read the *diff*; `source_verification` is about the *source*. Today every value is `unverified`. |

Two further top-level fields ship in `changes.json` and in every per-jurisdiction `changes-us-xx.json`:

| Field | Meaning |
|---|---|
| `registry_verification` | `{scope, sources, human_verified, unverified, rejected, statement}` — how much of the source list behind *this document* a human has actually confirmed. Read it before you treat any `url` here as authoritative. |
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

### `changes.json` (schema_version 1.0)

The normative document is [`docs/schema/changes-v1.schema.json`](./schema/changes-v1.schema.json) — JSON Schema 2020-12, and a merge-blocking test asserts that the schema and the code agree about every field and every enum, and that real published output validates against it. A schema that has drifted from its implementation is worse than none, because you built against it.

```jsonc
{
  "schema_version": "1.0",           // pin against the MAJOR; see the promise below
  "generated_at": "2026-07-13T12:00:00+00:00",
  "feed_url": "https://…",
  "jurisdiction": "TX",              // ONLY on a per-jurisdiction file. Its presence means
                                     // this document is SCOPED — an empty `changes` here is
                                     // a statement about Texas, not about the country.
  "disclaimer": "This feed reports that an official source page changed…",
  "changes": [
    {
      "id": "4ee6d95ecbc042c4",       // stable & deterministic: safe as a dedupe key forever
      "source_id": "tx-dps-change-dl-id",
      "jurisdiction": "TX",
      "document_class": "drivers_license",
      "url": "https://www.dps.texas.gov/…",   // the official page that changed
      "observed_at": "2026-07-11T09:04:11+00:00",
      "previous_hash": "…", "new_hash": "…",  // sha256 of the normalized text
      "diff_excerpt": "@@ -3,2 +3,3 @@\n bring a certified copy…\n+a court order is required…",
      "kind": "content_drift",         // content_drift | possibly_removed — MACHINE-OBSERVED
      "significance": "substantive",   // editorial | substantive — A HUMAN'S JUDGMENT
      "review_status": "confirmed",    // always. only confirmed records are ever published
      "reviewer": "Jane Doe",          // the human who stands behind this item
      "reviewed_at": "2026-07-12T14:20:00+00:00",
      "review_note": "TX now requires a court order for the sex field."
    }
  ]
}
```

**`significance` and `reviewer` are never machine-set** — see `docs/RESPONSIBLE-TECH-AUDITS.md` §B.

### The versioning promise

- **A major bump means a break, and nothing else does.** Removing a field, renaming one, changing a type, or removing an enum value bumps `schema_version` to `2.0`. Adding a new **optional** field does not — it bumps the minor, and a consumer that ignores unknown keys (which every sane JSON parser does) is unaffected. So: **pin on the major, ignore unknown keys, and you will not be broken by us.**
- **`id` is permanently stable** for a given `(source_id, previous_hash, new_hash)` transition.
- **`review_status` is always `confirmed`.** If you ever see another value, we broke a promise — open an issue.
- **The feed will never require a credential.**
- **Endpoint URLs are stable.** `changes.json`, `feed.xml`, `sources.json`, and `changes-us-xx.json` / `feed-us-xx.xml` for every jurisdiction. A per-jurisdiction feed exists **whether or not it has items yet** — a URL that only appears the day of the emergency is a URL nobody is subscribed to.

### The feed is currently empty, and that is a real state — not a broken build

As of the first baseline run (2026-07-13) the tool watches **152 sources across 52 of 52 jurisdictions**, and the feed contains **zero items**:

```jsonc
{ "schema_version": "1.0", "generated_at": "…", "changes": [] }
```

That is correct and it is deliberate. Every change the watcher detects is born `unclassified` / `unreviewed` and is held until a named human reviews it. None has been confirmed yet, so nothing is published — **and no change was manufactured to make the feed look alive.** `feed.xml` is valid RSS 2.0 with no `<item>` elements and an XML comment saying which of the two states it is in.

**What an empty feed means:** *no human has confirmed a change yet.*
**What an empty feed does NOT mean:** *nothing changed at any watched source.* Those are different sentences, and conflating them is precisely the wrong "no change" that this project treats as its primary safety failure. Silence from this feed is never evidence that a jurisdiction is unchanged — before you rely on it, read the **12 named gaps** in `sources.json` (or on the published site), which say exactly which (jurisdiction, document class) pairs we do not watch and which host refused us.

**Integrator guidance:** treat `changes: []` as a successful poll returning no new items. Pin on `schema_version`. Do not treat an empty `changes` array as an error condition, and do not alert your team on it.

## How each consumer would use it

### Advocates for Trans Equality (A4TE) — ID Documents Center
Covers 50 states + DC + 5 territories + 5 federal document classes, and asks users to email in corrections: *"Due to the ever-changing nature of state laws and policies, we are working to keep the ID Documents Center as up to date as possible. If you see something that needs updating, please contact us."*

**The offer:** replace the contact form with a queue. Map each of their jurisdiction/document pages to one or more `source_id`s. Poll `changes.json` weekly; when a `substantive` change lands for a mapped source, open a ticket against the corresponding page with the diff attached. Their editorial process is unchanged — they simply stop learning about staleness from users who already got bad advice.

**Why it's cheap for them:** one cron job, one mapping table, zero new content obligations. And they get the highest-value thing we have: `diff_excerpt` tells their writer *what sentence to look at*, so re-verification is minutes rather than a full page re-read.

### Trans Lifeline — ID Change Library
Volunteer-maintained since 2016, self-acknowledged incomplete (entries flagged *"Help Us Find It"*), no API, no export, **no last-updated dates**.

**The offer:** last-updated dates for free. Even without integration, `changes.json` gives their volunteers a triage list: *these six jurisdictions moved this quarter, start there.* Volunteer effort is their scarcest resource, and this converts "re-check everything, eventually" into "re-check these six, now." The `observed_at` timestamps also let them display a real freshness date, which is the single most useful thing missing from the library today.

### Namesake (namesake.fyi)
Open-source, well-engineered, fully supports **2 of 51 jurisdictions** (MA, RI); 47 are "No Support Yet."

**The offer:** the natural technical partner, and the easiest integration — they already have engineers and a data model. Two uses: (1) a regression alarm on their two supported jurisdictions (MA and RI are both in our seed registry for exactly this reason — even a well-engineered incumbent's *covered* jurisdictions need someone watching the underlying pages); (2) as they expand, our registry is a head start on *which official URLs are authoritative per jurisdiction and document class*, which is a surprising amount of the unglamorous work.

### Legal-aid organizations and law-school clinics
(Lambda Legal, TLDEF, Transgender Law Center, state legal-aid orgs, name-change clinics.)

**The offer:** an RSS feed in a Slack channel. A clinic that runs name-change days needs to know that the DMV changed its documentary requirements *before* twenty people show up with the old paperwork. This is the lowest-effort, highest-value integration, and it now involves **no filtering at all**: a clinic in Texas subscribes to `feed-us-tx.xml` and is done. Zero engineering. (Telling a legal-aid clinic to "just filter `changes.json`" was telling them to write code before they could read their own state — which is why the per-jurisdiction feeds exist.)

### Journalists and researchers
`changes.json` is a longitudinal record of *when* official ID-document guidance changed, with the passage and a reproducible hash. That is a primary source. The retained snapshots mean a claim in the feed can be independently verified months later — which is exactly what a journalist needs and exactly what "the page changed, trust us" cannot provide.

## No account, no email, no tracking — and this is a *tested* property, not a policy

There is no SDK, no auth, no rate limit, no account, and no signup form. That is not minimalism; it is the mitigation.

**Anyone who subscribes to a feed of trans identity-document law changes is, with high probability, a trans person or someone working directly with trans people. In the current US environment that list is a targeting artifact.** It could be subpoenaed, breached, sold, or handed over, and there is no security control that makes holding it safe. So we do not secure the list. **We never create it.**

- **No user model exists in the codebase.** There is nothing to log in to.
- **No tracking of any kind** in the published bytes: no analytics, no beacon, no pixel, no cookie, no UTM parameter — and the published *site* additionally makes **no third-party request at all**: no CDN script, no external stylesheet, no web font, no image. Every external request is a request that tells a third party who is reading about trans ID law, and a page that surveils the people it claims to protect would be a disgrace.
- **This is enforced, not promised.** `test_the_feed_requires_no_account_and_carries_no_tracking` and `test_the_published_site_makes_no_third_party_requests` assert it on the **published bytes** and run in the merge-blocking `feed_integrity` gate. If someone adds a font from Google, the build goes red.
- **Consequently we cannot report readership, and never will.** We do not know who consumes this or how many of you there are. That is a deliberate trade: the metric is worth less than the risk. If you integrate, we would love to hear from you — but nothing makes you tell us, and nothing observes you if you don't.

**The one honest limit:** we cannot control the access logs of whoever *hosts* the static files. Those logs will exist and will contain IP addresses. What we control is that they are not ours, are not enriched with identity, and are not required to consume anything. Fetch it over Tor if you want to; nothing about the feed makes that harder.

## The sustainability thesis

**The consumers are the customers.** The model, in order of preference:

1. **Grant-funded infrastructure.** This is the classic case for it: a small, boring, shared dependency that four organizations each need and none can justify building alone. The pitch is not "fund a trans ID website"; it is "fund the monitor that keeps the four existing ones from going stale," and it costs a fraction of any of them.
2. **Cost-shared maintenance.** If two or more orgs integrate it, a modest shared maintenance contract is easier to justify than four separate internal monitoring efforts — because that is precisely what it replaces.
3. **It survives neglect.** Zero runtime dependencies, SQLite, a static feed, a cron job. If funding never materializes, the marginal cost of keeping it running is a few dollars a month and the reviewer's time. It is designed to not die quietly, which is more than can be said for most civic-tech infrastructure.

**What would make this a failure:** building it, publishing it, and having no incumbent consume it. That is why M5 in `docs/ROADMAP.md` is *"one org wiring the feed into their content workflow"* — not "the feed exists," not "the feed is well-engineered." The feed existing is table stakes. Someone depending on it is the point.

**What would make it a success:** an A4TE writer finding out that Texas changed its DL page from a ticket in their queue, instead of from a user who already made the trip.
