"""Fail-closed source eligibility shared by the watcher and publisher gates.

The committed registry may still contain zero eligible entries.  That is a readiness fact,
not permission to bypass the gate: the production watcher and publisher both call this module
and fail closed without inventing verification or fetch-policy decisions.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from typing import Any

from id_churn_sentinel.core.registry import (
    FETCH_POLICY_ALLOW,
    REJECTED,
    UNVERIFIED,
    VERIFIED,
    Registry,
    Source,
)
from id_churn_sentinel.errors import RegistryError

__all__ = [
    "EligibilityReport",
    "SourceEligibility",
    "eligibility_report",
    "evaluate_source",
    "parse_as_of",
    "registry_revision",
]


@dataclass(frozen=True, slots=True)
class SourceEligibility:
    """The reproducible result for one source on one date."""

    source_id: str
    as_of: date
    eligible: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EligibilityReport:
    """Registry-wide eligibility with exact denominators and reason counts."""

    as_of: date
    decisions: tuple[SourceEligibility, ...]

    @property
    def eligible(self) -> tuple[SourceEligibility, ...]:
        return tuple(decision for decision in self.decisions if decision.eligible)

    @property
    def attempt_source_ids(self) -> tuple[str, ...]:
        """The exact source set a watcher run may place in its attempt denominator."""

        return tuple(decision.source_id for decision in self.eligible)

    @property
    def ineligible(self) -> tuple[SourceEligibility, ...]:
        return tuple(decision for decision in self.decisions if not decision.eligible)

    @property
    def reason_counts(self) -> tuple[tuple[str, int], ...]:
        counts = Counter(reason for decision in self.ineligible for reason in decision.reasons)
        return tuple(sorted(counts.items()))


def parse_as_of(raw: str) -> date:
    """Parse a deterministic policy date; timestamps and locale dates are refused."""

    try:
        parsed = date.fromisoformat(raw)
    except ValueError as exc:
        raise RegistryError(f"eligibility date must be YYYY-MM-DD, got {raw!r}") from exc
    if parsed.isoformat() != raw:
        raise RegistryError(f"eligibility date must be YYYY-MM-DD, got {raw!r}")
    return parsed


def evaluate_source(source: Source, *, as_of: date) -> SourceEligibility:
    """Apply the single fail-closed V1 source predicate.

    Reachability is deliberately absent: a failed fetch remains in the attempt denominator.
    Only a dated, reviewed registry decision changes eligibility.
    """

    reasons: list[str] = []
    if not source.active:
        reasons.append("inactive")

    reasons.extend(_verification_reasons(source, as_of))
    reasons.extend(_policy_reasons(source, as_of))

    ordered = tuple(dict.fromkeys(reasons))
    return SourceEligibility(
        source_id=source.id,
        as_of=as_of,
        eligible=not ordered,
        reasons=ordered,
    )


def _verification_reasons(source: Source, as_of: date) -> tuple[str, ...]:
    verification = source.verification
    if verification.status == UNVERIFIED:
        return ("unverified",)
    if verification.status == REJECTED:
        return ("rejected",)
    if verification.status != VERIFIED:
        return ("verification-status-ineligible",)

    reasons: list[str] = []
    if not verification.verifier.strip():
        reasons.append("verification-verifier-missing")
    if not verification.at:
        reasons.append("verification-date-missing")
    else:
        verified_at = parse_as_of(verification.at)
        if verified_at > as_of:
            reasons.append("verification-not-yet-effective")
    if not verification.evidence.strip():
        reasons.append("verification-evidence-missing")
    if not verification.expires_at:
        reasons.append("verification-expiry-missing")
    elif parse_as_of(verification.expires_at) < as_of:
        reasons.append("verification-recheck-due")
    return tuple(reasons)


def _policy_reasons(source: Source, as_of: date) -> tuple[str, ...]:
    policy = source.fetch_policy
    if policy.outcome != FETCH_POLICY_ALLOW:
        return (
            "fetch-policy-unreviewed" if policy.outcome == "unreviewed" else "fetch-policy-denied",
        )

    required = (
        ("reviewer", "fetch-policy-reviewer-missing"),
        ("at", "fetch-policy-date-missing"),
        ("evidence", "fetch-policy-evidence-missing"),
        ("reason", "fetch-policy-reason-missing"),
        ("expires_at", "fetch-policy-expiry-missing"),
    )
    reasons = [reason for field, reason in required if not getattr(policy, field).strip()]
    if policy.at and parse_as_of(policy.at) > as_of:
        reasons.append("fetch-policy-not-yet-effective")
    if policy.expires_at and parse_as_of(policy.expires_at) < as_of:
        reasons.append("fetch-policy-recheck-due")
    return tuple(reasons)


def eligibility_report(registry: Registry, *, as_of: date) -> EligibilityReport:
    """Evaluate every registry entry without hiding failures from the denominator."""

    return EligibilityReport(
        as_of=as_of,
        decisions=tuple(evaluate_source(source, as_of=as_of) for source in registry.sources),
    )


def registry_revision(registry: Registry) -> str:
    """Hash the complete canonical registry meaning captured by a watcher receipt.

    This deliberately hashes more than the fields used by eligibility.  A URL, authority,
    checked result, note, or named gap changing between two runs is provenance even if the
    attempt denominator stays the same.  JSON canonicalization makes the digest independent
    of in-memory mapping order.
    """

    payload: dict[str, Any] = {
        "registry_version": registry.version,
        "sources": [
            {
                "id": source.id,
                "jurisdiction": source.jurisdiction,
                "document_class": source.document_class,
                "url": source.url,
                "authority": source.authority,
                "verified": source.verified,
                "notes": source.notes,
                "checked": dict(source.checked),
                "verification": {
                    "status": source.verification.status,
                    "verifier": source.verification.verifier,
                    "at": source.verification.at,
                    "note": source.verification.note,
                    "evidence": source.verification.evidence,
                    "expires_at": source.verification.expires_at,
                },
                "active": source.active,
                "fetch_policy": source.fetch_policy.to_dict(),
            }
            for source in registry.sources
        ],
        "gaps": [
            {
                "jurisdiction": gap.jurisdiction,
                "document_class": gap.document_class,
                "reason": gap.reason,
                "hosts": list(gap.hosts),
                "checked": gap.checked,
                "detail": gap.detail,
            }
            for gap in registry.gaps
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()
