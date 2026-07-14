"""Iteration-1 source eligibility: exact, dated, fail closed, and still honest about scope."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from id_churn_sentinel.cli import main
from id_churn_sentinel.core.eligibility import (
    eligibility_report,
    evaluate_source,
    parse_as_of,
)
from id_churn_sentinel.core.registry import (
    FETCH_POLICY_ALLOW,
    FETCH_POLICY_DENY,
    VERIFIED,
    FetchPolicyDecision,
    Registry,
    Source,
    Verification,
    load_registry,
)
from id_churn_sentinel.errors import RegistryError

AS_OF = date(2026, 7, 13)


def _policy(
    *, outcome: str = FETCH_POLICY_ALLOW, expires_at: str = "2027-07-13"
) -> FetchPolicyDecision:
    return FetchPolicyDecision(
        outcome=outcome,
        reviewer="Policy Reviewer",
        at="2026-07-12",
        expires_at=expires_at,
        evidence="evidence/policy/tx-dps-2026-07-12.json",
        reason="robots and published terms permit this bounded descriptive-UA fetch",
    )


def _eligible(source: Source, *, expires_at: str = "2027-07-13") -> Source:
    return replace(
        source,
        verified=True,
        verification=Verification(
            status=VERIFIED,
            verifier="Source Reviewer",
            at="2026-07-12",
            evidence="evidence/verification/tx-dps-2026-07-12.json",
            expires_at=expires_at,
        ),
        fetch_policy=_policy(),
    )


def test_a_fully_evidenced_in_date_source_is_eligible(source: Source) -> None:
    decision = evaluate_source(_eligible(source), as_of=AS_OF)

    assert decision.eligible
    assert decision.reasons == ()


def test_unverified_and_unreviewed_policy_fail_closed(source: Source) -> None:
    decision = evaluate_source(source, as_of=AS_OF)

    assert not decision.eligible
    assert decision.reasons == ("unverified", "fetch-policy-unreviewed")


def test_verification_evidence_expiry_and_policy_fields_are_required(source: Source) -> None:
    incomplete = replace(
        source,
        verified=True,
        verification=Verification(
            status=VERIFIED,
            verifier="Source Reviewer",
            at="2026-07-12",
        ),
        fetch_policy=FetchPolicyDecision(outcome=FETCH_POLICY_ALLOW),
    )

    reasons = evaluate_source(incomplete, as_of=AS_OF).reasons

    assert "verification-evidence-missing" in reasons
    assert "verification-expiry-missing" in reasons
    assert "fetch-policy-reviewer-missing" in reasons
    assert "fetch-policy-date-missing" in reasons
    assert "fetch-policy-evidence-missing" in reasons
    assert "fetch-policy-reason-missing" in reasons
    assert "fetch-policy-expiry-missing" in reasons


def test_expired_verification_or_policy_is_recheck_due(source: Source) -> None:
    verification_due = _eligible(source, expires_at="2026-07-12")
    policy_due = replace(_eligible(source), fetch_policy=_policy(expires_at="2026-07-12"))

    assert "verification-recheck-due" in evaluate_source(verification_due, as_of=AS_OF).reasons
    assert "fetch-policy-recheck-due" in evaluate_source(policy_due, as_of=AS_OF).reasons


def test_expiry_date_is_inclusive(source: Source) -> None:
    on_boundary = replace(
        _eligible(source, expires_at=AS_OF.isoformat()),
        fetch_policy=_policy(expires_at=AS_OF.isoformat()),
    )

    assert evaluate_source(on_boundary, as_of=AS_OF).eligible


def test_future_dated_decisions_are_not_yet_eligible(source: Source) -> None:
    future = replace(
        _eligible(source),
        verification=replace(_eligible(source).verification, at="2026-07-14"),
        fetch_policy=replace(_policy(), at="2026-07-14"),
    )

    assert evaluate_source(future, as_of=AS_OF).reasons == (
        "verification-not-yet-effective",
        "fetch-policy-not-yet-effective",
    )


def test_denied_or_inactive_sources_are_ineligible(source: Source) -> None:
    denied = replace(_eligible(source), fetch_policy=_policy(outcome=FETCH_POLICY_DENY))
    inactive = replace(_eligible(source), active=False)

    assert "fetch-policy-denied" in evaluate_source(denied, as_of=AS_OF).reasons
    assert "inactive" in evaluate_source(inactive, as_of=AS_OF).reasons


def test_a_prior_fetch_failure_does_not_remove_the_source_from_the_denominator(
    source: Source,
) -> None:
    unreachable = replace(
        _eligible(source),
        checked={"at": "2026-07-12", "status": 403, "reachable": False},
    )

    assert not unreachable.reachable
    assert evaluate_source(unreachable, as_of=AS_OF).eligible


def test_registry_report_keeps_every_source_in_the_denominator(
    source: Source, arizona_source: Source
) -> None:
    registry = Registry(version="1.0", sources=(_eligible(source), arizona_source))

    report = eligibility_report(registry, as_of=AS_OF)

    assert len(report.decisions) == 2
    assert len(report.eligible) == 1
    assert len(report.ineligible) == 1
    assert dict(report.reason_counts) == {"fetch-policy-unreviewed": 1, "unverified": 1}


def test_committed_registry_reports_the_real_zero_eligible_denominator() -> None:
    report = eligibility_report(load_registry(), as_of=AS_OF)

    assert len(report.decisions) == 152
    assert len(report.eligible) == 0
    assert dict(report.reason_counts)["unverified"] == 152
    assert dict(report.reason_counts)["fetch-policy-unreviewed"] == 152


def test_registry_parses_a_complete_policy_and_rejects_a_partial_one(tmp_path: Path) -> None:
    entry = {
        "id": "tx-dps-change-dl-id",
        "jurisdiction": "TX",
        "document_class": "drivers_license",
        "url": "https://www.dps.texas.gov/section/driver-license",
        "authority": "Texas Department of Public Safety",
        "verified": True,
        "verification": {
            "status": "verified",
            "verifier": "Source Reviewer",
            "at": "2026-07-12",
            "evidence": "evidence/source.json",
            "expires_at": "2027-07-12",
        },
        "fetch_policy": {
            "outcome": "allow",
            "reviewer": "Policy Reviewer",
            "at": "2026-07-12",
            "expires_at": "2027-07-12",
            "evidence": "evidence/policy.json",
            "reason": "reviewed robots and terms",
        },
        "notes": "test",
    }
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({"registry_version": "1.0", "sources": [entry]}), encoding="utf-8")

    loaded = load_registry(path).sources[0]
    assert evaluate_source(loaded, as_of=AS_OF).eligible

    entry["fetch_policy"] = {"outcome": "allow", "reviewer": "Policy Reviewer"}
    path.write_text(json.dumps({"registry_version": "1.0", "sources": [entry]}), encoding="utf-8")
    with pytest.raises(RegistryError, match=r"fetch_policy\.at is required"):
        load_registry(path)

    entry["fetch_policy"] = {
        **_policy().to_dict(),
        "at": "2026-02-30",
    }
    path.write_text(json.dumps({"registry_version": "1.0", "sources": [entry]}), encoding="utf-8")
    with pytest.raises(RegistryError, match=r"fetch_policy\.at must be an ISO date"):
        load_registry(path)


def test_cli_reports_the_exact_fail_closed_denominator(
    tmp_path: Path, source: Source, capsys: pytest.CaptureFixture[str]
) -> None:
    eligible = _eligible(source)
    raw_source = {
        "id": eligible.id,
        "jurisdiction": eligible.jurisdiction,
        "document_class": eligible.document_class,
        "url": eligible.url,
        "authority": eligible.authority,
        "verified": True,
        "verification": {
            "status": "verified",
            "verifier": eligible.verification.verifier,
            "at": eligible.verification.at,
            "evidence": eligible.verification.evidence,
            "expires_at": eligible.verification.expires_at,
        },
        "fetch_policy": eligible.fetch_policy.to_dict(),
        "notes": "test",
    }
    other = {
        **raw_source,
        "id": "ca-dmv",
        "jurisdiction": "CA",
        "url": "https://www.dmv.ca.gov/portal/x",
        "verified": False,
    }
    other.pop("verification")
    other.pop("fetch_policy")
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps({"registry_version": "1.0", "sources": [raw_source, other]}),
        encoding="utf-8",
    )

    assert main(["--registry", str(path), "sources", "eligibility", "--as-of", "2026-07-13"]) == 0
    output = capsys.readouterr().out
    assert "1/2 eligible" in output
    assert "unverified: 1" in output
    assert "fetch-policy-unreviewed: 1" in output
    assert "report only" in output

    path.write_text(
        json.dumps({"registry_version": "1.0", "sources": [raw_source]}),
        encoding="utf-8",
    )
    assert main(["--registry", str(path), "sources", "eligibility", "--as-of", "2026-07-13"]) == 0
    all_eligible_output = capsys.readouterr().out
    assert "1/1 eligible" in all_eligible_output
    assert "report only" in all_eligible_output


def test_bad_policy_date_is_refused() -> None:
    with pytest.raises(RegistryError, match="YYYY-MM-DD"):
        parse_as_of("07/13/2026")
