# Accessibility and internationalization plan

**Owner:** Accessibility and language lead

**Language target:** reviewed Spanish V1 metadata by 2026-11-13

**Canonical declaration:** [`I18N.md`](./I18N.md)

## Standard and users

V1 targets WCAG 2.2 AA for the public site and any browser-based review bundle. The CLI follows equivalent keyboard, text, error-identification, and non-color principles. Design for screen-reader users, low-vision users, cognitive and motor disabilities, low bandwidth, older hardware, and people working under stress.

## Public artifact requirements

- semantic landmarks, one clear `h1`, logical headings, skip link, descriptive titles, and valid language metadata;
- tables have captions, headers, scopes, responsive alternatives, and no forced two-dimensional scroll for core meaning;
- status is expressed in words and programmatic text—not color, position, emoji, or icon alone;
- focus is visible; all controls are keyboard reachable; target size and focus-not-obscured meet WCAG 2.2;
- contrast passes AA, zoom to 400% retains content/function, and reflow works at 320 CSS pixels;
- links explain destination; dates include timezone; abbreviations and legal/technical terms are expanded;
- no automatic motion, timeout, refresh, audio, or third-party asset;
- RSS/JSON have accessible human documentation and do not require scripting to reach core content.

## Diff accessibility

Every diff provides a linear reading mode with explicit “removed” and “added” labels, source context, and unchanged surrounding text. Do not rely on red/green, side-by-side spatial comparison, or punctuation alone. Large changes have a summary of machine-observed structure (“12 lines added”) that makes no legal interpretation. Keyboard navigation moves between hunks; screen-reader testing verifies announcements in NVDA and VoiceOver.

## Review and CLI requirements

- prompts state current source, action, consequences, valid keys, and how to cancel without loss;
- destructive or publishing actions require reviewable confirmation and never use ambiguous defaults;
- errors identify the field, cause, and recovery; they do not erase the entered decision;
- terminal output supports `NO_COLOR`, plain text, predictable ordering, and file-based accessible review packets;
- no time-limited review session; progress is saved after each decision.

## Plain language

Keep the essential distinction visible: “This page changed” is not “the law changed.” Use short sentences, concrete timestamps, defined statuses, and examples. Disclaimers do not replace safe behavior and must not bury the primary meaning. Test with editors and community reviewers at varied technical literacy.

## Language scope

V1’s canonical evidence and source text remain in the language published by the authority. English is the canonical product language. Spanish V1 scope is reviewed navigation, status definitions, limitations, correction/reporting instructions, and integration basics. Do not machine-translate statutes, official-source content, diffs, reviewer judgments, or legal terms and present them as authoritative.

Translation workflow:

1. extract stable message IDs and glossary terms;
2. translator creates Spanish copy with source context;
3. a second reviewer familiar with trans and legal-information language reviews it;
4. accessibility QA tests expansion, reading order, and language switching;
5. every English source-string change marks translation stale until reapproved.

Use inclusive terms chosen with paid community reviewers. Do not infer language from location or browser and do not persist language choice server-side.

## Test matrix and release evidence

Automated HTML validity/accessibility scanning runs in CI, but V1 also requires manual keyboard-only review, 200%/400% zoom and reflow, high-contrast/forced-colors mode, VoiceOver/Safari, NVDA/Firefox or Chrome, and a low-bandwidth/no-CSS check. A disabled trans community reviewer completes the core tasks. All critical/serious defects and any blocker in source status, evidence, correction, or feed access must close before release.

Publish an accessibility statement naming tested scope, known limitations, contact method, response target, and last audit date. Retest quarterly and after template, navigation, diff, or translation changes.
