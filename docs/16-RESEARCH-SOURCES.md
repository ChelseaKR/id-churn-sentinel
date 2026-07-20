# Research sources and prior-art evidence

**Research date:** 2026-07-13. Recheck prior-art and policy sources before the V1 release decision. Public web research can show no dedicated tool was found; it cannot prove that none exists. None of the organizations named below is affiliated with, endorses, or uses this project.

## Need and incumbent workflows

- [Advocates for Trans Equality — ID Documents Center](https://transequality.org/documents): broad state/federal guidance and an explicit warning that laws and policies change; an organization whose public workflow illustrates the freshness problem, not a competitor to displace.
- [Trans Lifeline — ID Change Library](https://translifeline.org/resources/id-change-library/): community-maintained ID process resources; another illustration of the freshness problem.
- [Namesake](https://namesake.fyi/): open-source document-change assistance and the closest technical prior art in the domain. Its first-party [PDF-monitoring guide](https://github.com/namesakefyi/namesake/blob/main/docs/src/content/docs/guides/pdf-monitoring.mdx) documents fetching every PDF with a `canonicalUrl`, comparing extracted text with the local copy, and printing changed-line diffs; its [scheduled workflow](https://github.com/namesakefyi/namesake/blob/main/.github/workflows/pdf-monitor.yml) runs daily and opens or updates an issue for detected drift.
- [2022 U.S. Trans Survey Early Insights](https://ustranssurvey.org/wp-content/uploads/2023/11/2022-USTS-Early-Insights-Report_FINAL.pdf): large-scale evidence of ID mismatch and mistreatment burdens; use for problem context, not to infer an individual’s situation.

## Current official policy examples

- [U.S. Department of State — Sex Marker in Passports](https://travel.state.gov/content/travel/en/passports/passport-help/sex-marker.html): official federal source illustrating why authoritative pages and dated monitoring matter.
- [Federal Register](https://www.federalregister.gov/): official publication/API surface for federal rulemaking; monitoring a page is not equivalent to determining legal effect.
- [Social Security Administration — change name on Social Security record](https://www.ssa.gov/personal-record/change-name): official process surface and example of a federal document workflow.

The repository’s canonical source registry is `sources/registry.json`. Inclusion there is operational evidence only under its explicit verification state and must not be converted into a legal claim.

## Technical and governance standards

- [W3C — Web Content Accessibility Guidelines 2.2](https://www.w3.org/TR/WCAG22/): V1 accessibility baseline.
- [W3C — JSON Schema 2020-12](https://json-schema.org/draft/2020-12): public JSON contract basis.
- [RSS 2.0 Specification](https://www.rssboard.org/rss-specification): feed interoperability basis.
- [NIST Secure Software Development Framework 1.1](https://csrc.nist.gov/pubs/sp/800/218/final): secure development control reference.
- [NIST Privacy Framework](https://www.nist.gov/privacy-framework): data minimization and privacy-risk reference.
- [OWASP Server Side Request Forgery Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html): crawler input/network-boundary controls.
- [RFC 9309 — Robots Exclusion Protocol](https://www.rfc-editor.org/rfc/rfc9309): robots handling reference; terms and ethical pacing still require separate review.

## Adjacent tools and prior art

The July 13, 2026 scan found mature generic change monitoring, so the novelty claim is deliberately narrow:

| Candidate | Publicly evidenced capability | What V1 must test rather than assume |
|---|---|---|
| [Visualping](https://visualping.io/) | visual/text website-change monitoring, alerts, team and regulatory-monitoring use cases | whether it supplies a verified trans-ID source registry, jurisdiction feed contract, human legal-boundary review, named gaps, and correction history |
| [Distill](https://distill.io/docs/web-monitor/what-is-distill/) | scheduled local/cloud webpage monitoring and multi-channel alerts | whether configuration can reproduce source authority, public run health, retained evidence, independent review, and non-tracking publication |
| [changedetection.io](https://changedetection.io/) and its [open-source repository](https://github.com/dgtlmoon/changedetection.io) | self-hosted/SaaS webpage change detection, notifications, history, and API | whether building the domain/governance layer on it is safer and cheaper than maintaining the current small fetch/diff core |
| [Namesake PDF monitor guide](https://github.com/namesakefyi/namesake/blob/main/docs/src/content/docs/guides/pdf-monitoring.mdx) and [daily workflow](https://github.com/namesakefyi/namesake/blob/main/.github/workflows/pdf-monitor.yml) | domain-adjacent canonical-source PDF monitoring, extracted-text comparison, changed-line diffs, daily scheduling, and issue creation/update | whether to reuse, contribute to, or integrate this monitor; whether the remaining combined multi-jurisdiction verification, heterogeneous-source, run-health/gap, independent-review/correction, and public-feed contract creates enough incremental value to build |

Existing trans-ID guides and filing tools solve guidance or execution and should be treated as complementary projects and prior art. Namesake already solves a material freshness slice for its canonical-source PDFs. As of this dated scan, no purpose-built tool was found that publicly combines a multi-jurisdiction U.S. trans-ID registry with dated source verification and shared fetch/publication eligibility, text evidence across heterogeneous surfaces, honest gaps/run health, independent publication review and correction history, and a public no-reader-tracking feed. The novelty claim applies only to that **combined contract**. It is an observation about what was publicly findable, not proof that nothing similar exists.

### Reproducible prior-art refresh protocol

At scope lock and within 30 days of release, search the web and product directories for: `website change monitoring government regulation`, `legal change monitoring transgender ID`, `identity document policy monitoring API`, `transgender document policy feed`, and close variants. For every open-source or technically inspectable candidate, review first-party repositories, documentation, issue automation, and operations workflows—not only the marketing page. Record query, date, first 20 relevant results, candidate URL, project status, public feature evidence, hosting model, source-authority model, review/correction model, privacy surface, and a `present / absent / unknown` result for every differentiator above. Test at least Visualping, Distill, changedetection.io, current trans-ID guides, Namesake's PDF monitor and daily workflow, and any new direct candidate. Preserve screenshots or dated URLs in the private research evidence index; publish only non-sensitive conclusions. Each material overlap triggers a documented reuse/contribute/build decision and a revision of this document, not dismissal.

## Evidence-strength rules

- Prefer official primary sources for current policy and standards.
- Date every dynamic claim and preserve the accessed URL.
- Separate “the official page says” from “the product infers.”
- Never use advocacy copy to assert the law in a jurisdiction.
- Do not generalize survey population findings to an individual.
- Record comparable tools found after this review and update this document rather than minimizing them.
