"""Guards on the versioned rules directory (runs in CI before any deploy).

A published version is immutable: audit records reference it by version and
sha256, so editing v1 in place would make past decisions unexplainable.
To change rules, add rules_vN.json with a future effective_from and record
its hash in rules/published.json.
"""
import json

from drivecoach import rules
from tests.conftest import ROOT

RULES_DIR = ROOT / "rules"


def rule_files():
    return sorted(RULES_DIR.glob("rules_*.json"))


def test_every_rules_file_is_valid_and_named_after_its_version():
    assert rule_files()
    for path in rule_files():
        doc = rules.validate(json.loads(path.read_text()))
        assert path.name == f"rules_{doc['version']}.json"


def test_published_versions_are_immutable():
    published = json.loads((RULES_DIR / "published.json").read_text())
    on_disk = {json.loads(p.read_text())["version"]: rules.canonical_hash(json.loads(p.read_text()))
               for p in rule_files()}
    assert set(on_disk) == set(published), "add the new version's hash to rules/published.json"
    for version, digest in published.items():
        assert on_disk[version] == digest, f"rules {version} was modified after publication"


def test_effective_dates_are_unique():
    dates = [json.loads(p.read_text())["effective_from"] for p in rule_files()]
    assert len(dates) == len(set(dates))
