# User research and design-partner pilot

## Research decisions to make

Research is designed to answer five launch decisions: whether alerts enter a real editorial workflow; which evidence a reviewer needs; whether the significance vocabulary creates false legal authority; what response time is valuable; and who will pay to sustain a public, privacy-preserving feed.

## Participants

Recruit 12–16 interviews across:

- 4 national/state transgender advocacy content maintainers;
- 3 legal-aid or law-school clinic coordinators;
- 2 document-assistance product teams;
- 2 trans community reviewers with direct ID-change experience;
- 2 legal-information or policy librarians;
- 1–3 security/accessibility/operations specialists.

Pay community participants. Do not recruit people in an active document crisis for an infrastructure study. Collect role and workflow context, not trans status, legal history, or personal document data.

## Interview protocol

Ask participants to replay the most recent source update from discovery through publication. Observe tools, handoffs, time, uncertainty, and failure recovery. Then use a synthetic changed-source packet and ask them to decide what they would do. Avoid pitching until after the current workflow is understood.

Core questions:

1. How do you learn official guidance may have changed?
2. What evidence makes you open a content ticket rather than dismiss an alert?
3. What does an empty feed mean to you, and how could that interpretation be unsafe?
4. Who is allowed to judge significance and publish a correction?
5. Which integration requires the least maintenance: RSS, JSON, ticket, email, or Slack?
6. What would make you stop trusting the service?
7. What can your organization pay for without compromising editorial independence?

## Six-week shadow pilot

### Entry criteria

- two partners sign a plain-language pilot agreement and data-minimization statement;
- each names an editor, backup editor, and technical or operations contact;
- V1 schema draft, source-verification status, known gaps, and correction channel are visible;
- synthetic rehearsal proves no observation can bypass review;
- the pilot does not replace the partner’s existing verification process.

### Baseline week

Each partner selects 10–20 guidance pages and records its existing sources, re-check cadence, recent update effort, time per investigation, and escalation path. Before the first assisted exercise, freeze an eight-investigation matched evaluation set for that partner: four sealed historical/synthetic cases and four live-alert slots. Also freeze an ordered reserve of sealed historical cases. A live slot uses the next eligible live alert in arrival order; if no live alert is available by its predeclared cutoff, it is filled by the next reserve case in the frozen order. Each case has a pre-recorded baseline investigation time and an assisted investigation time for the same defined research task. Create a private mapping from partner content IDs to public `source_id`s; the sentinel does not store reader or constituent identity.

### Weeks 1–6

- Run the weekly watch on schedule and publish health status even if there are no reviewed items.
- Route relevant confirmed items plus deliberately labeled sealed historical/synthetic exercises during quiet weeks, following the frozen case order and live-slot cutoff rules; do not substitute cases after seeing performance.
- Partner records whether it opened a ticket, sources consulted, research minutes, decision, and confidence.
- Conduct a 20-minute weekly safety/operations review; never pressure a partner to change content to make the pilot look useful.
- Exercise one source outage, one correction, one schema-forward-compatible optional field, and one high-impact dual review.

Each partner completes the same five fixed comprehension tasks once near entry and once at closeout: explain source verification, empty-feed meaning, run health, correction/withdrawal state, and the boundary between observed page change and legal meaning. This produces exactly 10 scored attempts per partner. Score against a frozen answer key; remediation may occur between rounds, but a missed answer remains in the denominator.

### Success thresholds

- both partners complete at least five of six weekly cycles;
- each partner answers at least 8 of its 10 fixed comprehension attempts correctly;
- each partner completes all eight predeclared matched investigations: four sealed historical/synthetic cases and four live slots, with unavailable live slots filled only from the frozen reserve in order;
- for each partner and each investigation, calculate paired reduction as `(baseline minutes - assisted minutes) / baseline minutes`; each partner's median across its eight reductions is ≥50%; never pool partners, mix the comprehension denominator with routed-item counts, or exclude an observed pair after results are known;
- report real-alert and historical/synthetic results in separate strata as well as the fixed eight-case total;
- at least one real or rehearsal item enters each partner’s standard editorial workflow;
- at least one partner commits to a post-pilot integration or paid service agreement;
- zero unreviewed publication, privacy incident, or partner interpretation that the service provided legal advice after remediation.

### Stop / pivot conditions

Pause immediately for unreviewed publication, sensitive personal data collection, a material false claim of official authority, or evidence that an alert created direct risk. Pivot or stop after six weeks if partners do not use the evidence in their workflow, generic page monitors meet the job, median research time does not improve, or no sustainable payer appears.

## Analysis and receipts

Use coded notes with participant IDs, not names, in the research repository. Separate observed behavior from interpretation. Maintain a decision log connecting each confirmed finding to a requirement change. Pilot closeout contains each partner's frozen case manifest, live-slot substitutions, all eight paired measurements, partner-level median, real-versus-exercise strata, all 10 comprehension results, aggregate results that do not replace the partner-level gates, negative findings, incidents, unresolved risks, and partner sign-off; it contains no interview transcript or personal legal detail.
