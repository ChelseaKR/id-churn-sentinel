# ADR 0001: V1 is a reviewed freshness feed, not a legal guidance product

**Status:** accepted  
**Date:** 2026-07-13  
**Owners:** product and community-governance leads

## Context

Trans people and the organizations serving them face changing, fragmented identity-document processes. The repository can technically crawl, diff, classify, summarize, and publish many kinds of information. The highest-risk temptation is to turn a machine observation into a legal conclusion or personalized next step. That would duplicate trusted guidance providers, create unauthorized-practice and consumer-protection exposure, and make a silent or confident machine error directly actionable.

The present implementation already separates observed drift from named human review and publishes static, anonymous artifacts. V1 needs a clear boundary that determines which roadmap, architecture, and release work belongs.

## Decision

V1 is institutional infrastructure for guidance maintainers. It monitors human-verified official-source candidates, preserves raw/normalized evidence, produces passage-level observations, routes publication through named human review and independent review for high-impact items, and emits versioned public feeds with health, gaps, verification, and correction state.

V1 does not interpret law, advise individuals, auto-classify legal significance, infer identity, collect reader data, promise exhaustive detection, evade source restrictions, or replace an advocacy/legal-aid guide. The public open feed is the common factual layer; no integration or service work may influence observations or reveal readers.

## Consequences

### Positive

- reduces the narrow freshness burden while preserving incumbent expertise and trust;
- enables a small, auditable batch architecture with no account system;
- makes privacy-by-noncollection and reproducible evidence credible;
- creates clear safety tests and a tractable V1 release gate;
- positions incumbent guidance providers as collaborators.

### Costs and limits

- a human review bottleneck remains load-bearing and must be funded;
- the service cannot tell an individual what to do or whether a rule applies;
- policy changes not reflected on watched public surfaces remain invisible;
- honest gaps reduce apparent coverage;
- the project's value must come from institutional usefulness and reliability, not reader growth or proprietary legal content.

## Rejected alternatives

- **Direct-to-consumer guidance portal:** duplicates trusted incumbents and increases harm/legal/privacy risk.
- **LLM legal-change summaries:** opaque, unreproducible, and likely to be treated as legal authority.
- **Generic monitoring SaaS:** would obscure product-specific verification, governance, gaps, and anonymous public access.
- **Private enterprise-only feed:** weakens public auditability and creates unequal factual records.
- **Account-based notifications:** creates a sensitive list of readers without being necessary for RSS/JSON delivery.

## Revisit criteria

Revisit only if evidence shows the institutional freshness job is not valuable, a trusted incumbent assumes the capability, or legal/governance review finds the boundary still creates unacceptable harm. Expanding into guidance requires a separate product, governance model, legal review, data design, and ADR; it is not an incremental V1 feature.

