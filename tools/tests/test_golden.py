"""golden_cases.json doit toujours correspondre a collect.py (l'extension et l'appli le rejouent)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gen_golden  # noqa: E402


def test_golden_file_is_up_to_date():
    on_disk = json.loads((Path(__file__).parent / "golden_cases.json").read_text(encoding="utf8"))
    assert on_disk == json.loads(json.dumps(gen_golden.build())), "relancer: python tools/gen_golden.py"


def test_golden_has_variety():
    cases = gen_golden.build()["cases"]
    scores = {c["expect"]["score"] for c in cases}
    assert any(c["expect"]["event"] for c in cases) and any(not c["expect"]["event"] for c in cases)
    assert any(c["expect"]["scam"] for c in cases)
    assert -100 in scores and any(s > 0 for s in scores)
    assert len(cases) >= 40
