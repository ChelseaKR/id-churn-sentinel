# Governance, legal boundaries, and safety

## Governing principle

People affected by transgender identity-document policy must have real authority over product boundaries, not merely provide feedback. Legal and technical expertise support that authority; they do not replace it.

## Decision bodies

### Product and operations group

Product lead, technical lead, and service owner decide routine implementation and operations. They cannot waive safety invariants or self-approve a high-impact publication.

### Community safety panel

At least three paid members: a trans person with recent ID-change experience, an advocacy/legal-aid content maintainer, and a privacy/accessibility practitioner. Add counsel or a policy librarian when the issue requires it. Avoid one person “representing” all trans people; publish aggregate qualifications and conflicts, not personal histories.

Panel authority:

- appoint the community-governance release co-authority and issue blocking findings within the panel’s safety remit;
- define reviewer qualifications and conflicts;
- adjudicate disputed high-impact publications and corrections;
- veto features that increase surveillance, outing, legal-authority, or access risk;
- review harm metrics and incident postmortems quarterly.

## Non-negotiable safety invariants

1. The service reports observations about public sources; it does not state law or advise an individual.
2. No machine assigns legal significance, summarizes legal meaning, or recommends action.
3. No unreviewed observation publishes; high-impact items have an independent second decision.
4. Source verification is explicit, named, dated, and narrower than legal validation.
5. Silence is never presented as proof that nothing changed.
6. Reader identity is neither required nor measured.
7. Gaps and uncertainty remain visible; coverage is never padded by evasion or weak sources.
8. Published mistakes are corrected transparently, never silently erased.

These invariants are release blockers and may change only through an ADR, community-panel approval, and a public migration note.

## Legal workstreams before V1

This plan identifies counsel-review areas; it is not legal advice.

| Area | Question | V1 action |
|---|---|---|
| Unauthorized practice / consumer protection | could labels or support be read as personalized legal interpretation? | counsel reviews copy, schema, reviewer notes, and support scripts; prohibit directive summaries |
| Copyright and public records | what source bytes/excerpts may be retained or republished? | retain minimal evidence privately; publish bounded diffs/citations; document fair-use/public-record rationale and takedown process |
| Website terms / robots | is automated access permitted and appropriately paced? | source registration includes terms/robots review; no circumvention or browser impersonation |
| Privacy | what laws/contracts govern reviewer and pilot contacts and host logs? | data map, retention schedule, vendor terms, incident notice matrix, no end-user data |
| Accessibility | which federal/state obligations or contract terms apply? | WCAG 2.2 AA is baseline; document audit and accommodation channel |
| Defamation / institutional harm | could alerts falsely accuse agencies or advocacy partners? | factual observation language, evidence citation, correction/right-of-reply path, no grading partners |
| Sanctions and government data | are any sources or services restricted? | counsel confirms hosting/fetch scope before expansion beyond US public sources |

## Reviewer policy

### Source verifier

Must demonstrate ability to distinguish issuing authority, jurisdiction, document class, and primary versus secondary source. Verification asks only whether the source matches the claimed category. Statutory interpretation is out of scope.

### First change reviewer

Must understand the evidence model, noise patterns, and non-advice boundary. They decide whether an observed change deserves partner attention and document uncertainty.

### Independent high-impact reviewer

Must be a different person without authorship or financial conflict; for potentially material process changes, use a lawyer, policy librarian, or qualified advocacy editor. They verify the publication claim is supported, not what the law ultimately is.

Annual training covers trans cultural competence, anti-harassment, privacy, accessibility, source evaluation, and incident reporting. Sample decisions are calibrated quarterly. Reviewer performance is assessed for quality and workload, not volume.

## Conflicts and funding

Publish funders and material partner relationships. A funder cannot suppress an observation, choose classifications, accelerate its own jurisdiction, or obtain reader data. Reviewers disclose agency/partner employment related to an item and recuse where independence is impaired. Paid private services subsidize the common feed but do not create a faster or sanitized factual record.

## Correction, appeal, and takedown

Anyone can report a wrong source, evidence problem, accessibility barrier, or harmful framing without creating an account. Acknowledgment targets follow incident severity. The governance lead may temporarily mark an item disputed, but permanent correction or withdrawal requires a reasoned decision and visible supersession. Raw evidence takedown requests are assessed for privacy, copyright, and audit needs; no personal data remains public merely to preserve provenance.

## Release and ongoing review

Engineering, operations, community safety, accessibility, security/privacy, and legal-boundary owners provide named domain attestations for their release evidence. Product and the panel-appointed community-governance reviewer are the two joint ship/hold authorities; both signatures are required, and neither may waive an unresolved domain blocker. Quarterly governance review examines missed changes, corrections, reviewer disagreement, source gaps, blocked hosts, partner outcomes, reviewer burden, accessibility defects, and funding conflicts. Any Sev-1 incident triggers an extraordinary panel review before publication resumes.
