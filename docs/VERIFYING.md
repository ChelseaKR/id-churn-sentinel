# Verifying the registry — the one question, and how to answer 152 of them

> **Last verified: 2026-07-13 · Recheck cadence: per registry expansion.**
> Status today: **`0 of 152 sources are human-verified`.** Everything below exists to change that number.

## The question you are answering

For each source, exactly one question:

> **Is this URL the official page for this document class in this jurisdiction — yes or no?**

That is the whole job. Not "is the advice on it good", not "is it up to date", not "does the law it describes still apply". Just: **is this the right page, from the right authority, about the right document?**

## What you are NOT judging

This matters more than the question does, because the temptation to answer a bigger question is strong and answering it would break the tool.

- **You are not verifying what the law says.** This project never asserts what the law is. Not in the feed, not in the registry, not in a "helpful" note. If you find yourself reading the page to decide whether its *content* is correct, you have left the job. Advocates for Trans Equality, Trans Lifeline, Namesake and lawyers do that work; this repo is the monitor underneath it.
- **You are not judging whether the process is good, fair, or current.** A page describing a hostile policy accurately is a perfectly valid source. We watch it *because* it may change.
- **You are not judging whether the page is well-designed, complete, or easy to use.** A bad page at the right URL is still the right URL.
- **You are not fixing anything.** If an entry is wrong, reject it and say why. Someone (possibly you, later, deliberately) finds a replacement as a separate act.

**One honest exception, and it is the reason a person is doing this at all.** If a page is *not what our crawler thinks it is* — a bot-wall titled "Request Access" served with HTTP 200, a soft 404 that says "404 Page Not Found" in a page the server called a success, a "this page has moved" stub — **that is a rejection**, and it is exactly the thing a machine cannot see. Both of those examples are real, and both are in this registry's history.

## What "official" means here

- **The right authority.** The agency that actually issues or administers the document — a state's vital records office for a birth certificate, its DMV for a licence, its courts for a name change. A county page is not a statewide page. An advocacy group's summary, however good, is never a source.
- **A statute or administrative-code page is legitimate, and it claims less than an agency page.** Sixteen jurisdictions are watched via their statutes or admin code because the agency's own host blocks our crawler and **we do not spoof a User-Agent to get around that**. A statute page is *the law an agency administers*, not the agency's own process page — and the entry's `notes` say so. When you verify one of these, the question is still the same: **is this the right statute/rule for this document class in this jurisdiction?**
- **A landing page can be the deepest honest target.** Several states publish no statewide page for a document class at all (Texas's name-change process is county-level). Where the office page is all there is, the office page is what we watch, and the entry says so. That is a *yes*, not a *no* — the alternative is an invented deep link that 404s.

## Running it

```sh
# the whole queue, federal sources first (passport / SSA / Selective Service)
uv run sentinel verify --verifier "Your Name" --federal-first

# one state, in a sitting
uv run sentinel verify --verifier "Your Name" --jurisdiction TX

# one document class across every state (the fastest way to build a mental model —
# you learn what a real vital-records page looks like, then repeat it 50 times)
uv run sentinel verify --verifier "Your Name" --document-class birth_certificate

# a 20-minute sitting
uv run sentinel verify --verifier "Your Name" --limit 20

# what's left. No network, no prompts, no writes.
uv run sentinel verify --list
```

For each source you get one screen: the jurisdiction, the document class, the issuing authority we claim it belongs to, the URL, **the page's own `<title>`**, and the first few passages of its normalized text. Then:

| Key | Meaning |
|---|---|
| `y` | **Yes** — this is the official page. Records `verified: true` with **your name and today's date**. |
| `n` | **No** — this is not the official page. Asks you why (required), and whether to flag it for repair or record it as a named gap. |
| `s` | **Skip** — you are not sure, or you want to open it in a browser first. Nothing is written. Come back to it. |
| `q` | **Quit** — everything you have decided is already saved. |

**Skip freely.** A skipped source stays in the queue. A wrongly-confirmed source is worse than an unverified one, because it stops being questioned. If you are not sure, you are not sure — that is a `s`, not a `y`.

**Open the URL.** The excerpt is there to make the easy ones fast, not to replace looking. For anything you would not bet a stranger's day off work on, open it in a browser.

## The name is not optional

`sentinel verify` **will not record a verification without a name.** Not as bureaucracy — because `verified: true` means *"a person opened this URL and confirmed it is the official page"*, and with no name attached that sentence has no subject. An anonymous verification is indistinguishable from a machine's, and a machine's opinion here is exactly what the field exists to not be. The registry itself enforces the same rule: an entry claiming `verified: true` with no verifier and no date **does not load**.

The date is recorded for the same reason. A verification with no date can never go stale, which means it can never be re-checked — and government URLs move.

## Rejecting: repair, or gap

When you answer `n` you are asked for a reason, and then which of two things is true:

- **Flag for repair** (the default). The entry stays in the registry carrying its rejection, your name, and your reason, and is published **as `rejected`** — so nobody picks it up in the window before it is fixed. A wrong entry that is *known* to be wrong is far safer than one quietly deleted, because deleting it takes the finding with it.
- **Record as a gap** (`--gap`). Use this when there is no right page to substitute — the state simply does not publish one. The entry leaves the source list and becomes a **named gap** with reason `wrong-page`, which is what the gap list is for: *"we do not watch this, and here is why."* The tool refuses this if another source still watches the same (jurisdiction, document class) pair, because a gap that claims we are blind to something we can see is a false confession.

## How long 152 takes

Measured against the real thing, honestly:

- The **easy ones are ten to twenty seconds**: the title says *"Office of Vital Statistics | Kansas Department of Health and Environment"*, the excerpt is about ordering a birth certificate, the authority matches. `y`.
- The **hard ones are two to five minutes**: a statute page where you have to satisfy yourself that the chapter really is the one governing this document class; a landing page where you have to decide whether a deeper page exists; anything our crawler could not fetch, where you have to open a browser.
- Roughly a **fifth of them are hard**. So: **≈ 30 easy per 10 minutes, ≈ 30 hard at 3 minutes each.**

**Call it three and a half hours for all 152**, in sittings, resumable — the tool writes each decision to `sources/registry.json` immediately, so a crash or a `q` at source 90 costs nothing.

**Do the federal ones first** (`--federal-first`): passport, Social Security, Selective Service. They are six sources, they are the entries every jurisdiction's readers depend on, and they are twenty minutes.

## What changes when you finish

- `sentinel coverage` prints the burn-down, derived — nobody types it.
- The published site, `sources.json` and every feed stop saying **UNVERIFIED** next to that source and start saying **VERIFIED — confirmed by \<your name\> on \<date\>**.
- The README's gated `N of 152 sources are human-verified` number moves by itself, and the merge gate fails any doc that disagrees with it.
- **M1 closes**, and this project can stop leading with an apology.
