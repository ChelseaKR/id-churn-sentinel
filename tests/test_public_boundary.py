"""Current-tip publication-boundary and vendored-standards contracts."""

from __future__ import annotations

import json
import re
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).parents[1]
STANDARDS = ROOT / "docs" / "standards"
PRIVATE_IDENTIFIERS = (
    "trans-" + "docs-" + "navigator",
    "self-" + "osint-" + "monitor",
)
EXPECTED_STANDARDS_SHA256 = {
    "ACCESSIBILITY-STANDARD.md": "93b9afc0c838fad129289128a798c392e22d32f55a84407694a6d953c8681131",
    "AI-DEVELOPMENT-MEASUREMENT-STANDARD.md": "aeeb72b735d6a2703ce211911fce6f5986e2605bebe8a6cedec968b2197da478",
    "AI-EVALUATION-STANDARD.md": "6234ebd92459bedb104a00b29cc844746da9a0f947c245548f720f671eaecdb7",
    "CI-CD-STANDARD.md": "7f51b70e49bcafa37aae61485c29388ca77f36973934e3e31810db56aaaa2ea1",
    "CODE-QUALITY-STANDARD.md": "2c598e6556bc665e743729318a61fe44c6734d3c088bab72d6ded043971c1e68",
    "DATA-GOVERNANCE-STANDARD.md": "817a83581336536e80a8820bc3f32cfaee590f67572fcfeb5472813da7b51fa4",
    "DOCUMENTATION-STANDARD.md": "be36d6477e252fee5508ffcd70ecfef2b223f46df7b6dc4fb36767b39712f2e0",
    "INCIDENT-RESPONSE-STANDARD.md": "a1919dff580b94d7954cb52d34d580e611562f4922f388cc8e67f135a8487347",
    "INTERNATIONALIZATION-STANDARD.md": "9de4dc0b2fd87823aea04fd4a5a6466129799ba1911a785c27aea3e7aafd8f44",
    "OBSERVABILITY-STANDARD.md": "5df2e531e9bb53cac124c6bae14004886e9ab72509b46078c505c07a928feeff",
    "PERFORMANCE-STANDARD.md": "6bc2e635d9303b97851bedc77b65c7ad480802d5c40a5c68036d6d610ac23ecd",
    "QUALITY-AND-METRICS-STANDARD.md": "5ea624eac05af1f33de89aee403506896c6cfbe446e7b56eeb1443f2067a098d",
    "README.md": "c9d9417abd7a114a97e7dcedf499ee076d468754785d8897792a67494940c558",
    "RELEASE-AND-VERSIONING-STANDARD.md": "333367e9e9cd2951d57de018e624a32b049dd5ee8d3decacdcfedc2c9ef7afdc",
    "RESPONSIBLE-TECH-FRAMEWORK.md": "40938200c0ccaa29c05833fd911509fe1d9512236c655306c44bae5dc83e66d3",
    "SECURITY-AND-SUPPLY-CHAIN-STANDARD.md": "ff863e2ed30d726e98d308c0096f71d1ae07839939dded18246a3593760565b2",
}


def _current_project_text() -> list[tuple[Path, str]]:
    documents: list[tuple[Path, str]] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT)
        if relative.parts[0] in {".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".venv"}:
            continue
        if relative.parts[:2] == ("docs", "standards"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        documents.append((relative, text))
    return documents


def test_current_project_text_has_no_private_source_references() -> None:
    findings: list[str] = []
    private_root = "/" + "STANDARDS"
    local_root = "/users/" + "chelsea/"
    for path, text in _current_project_text():
        lowered = text.lower()
        for identifier in PRIVATE_IDENTIFIERS:
            if identifier in lowered:
                findings.append(f"{path}: private repository identifier")
        if local_root in lowered:
            findings.append(f"{path}: local absolute path")
        if private_root in text:
            findings.append(f"{path}: private standards path")
    assert not findings, "\n".join(findings)


def test_vendored_standards_projection_is_complete_and_pinned() -> None:
    manifest = json.loads((STANDARDS / ".standards-manifest.json").read_text(encoding="utf-8"))
    declared = manifest["files"]
    assert manifest["schema_version"] == 1
    assert declared == sorted(EXPECTED_STANDARDS_SHA256)
    assert set(declared) == {path.name for path in STANDARDS.glob("*.md")}
    observed = {
        name: sha256((STANDARDS / name).read_bytes()).hexdigest()
        for name in EXPECTED_STANDARDS_SHA256
    }
    assert observed == EXPECTED_STANDARDS_SHA256
    assert "standards_version=v2.0.0" in (STANDARDS / ".standards-version").read_text(
        encoding="utf-8"
    )


def test_secret_scan_pins_its_runtime_and_never_floats_to_latest() -> None:
    # The property that matters is that the scanner runtime is pinned, not which
    # pin. Two fixes for the same Lob-detector regression landed independently:
    # this branch pinned back to 3.95.8 with every detector enabled, while `main`
    # pinned forward to 3.96.0 and excluded the one false-positive detector.
    # `main`'s is the newer decision and the one in the tree, so this asserts the
    # invariant both satisfy — the action's `version` input defaults to "latest",
    # which is what silently changed the scanner underneath a SHA-pinned action.
    workflow = (ROOT / ".github" / "workflows" / "trufflehog.yml").read_text(encoding="utf-8")
    assert "version: latest" not in workflow
    assert re.search(r'version:\s*"?3\.\d+\.\d+', workflow), "scanner runtime is not pinned"
    assert "extra_args: --only-verified" in workflow
