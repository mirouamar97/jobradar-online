"""Le lien profond jobradar://offer/<uid> exige des uid valides et uniques ; index.html doit les valider."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_every_published_offer_uid_is_a_unique_sha1_hex():
    offers = json.loads((ROOT / "data" / "offers.json").read_text(encoding="utf8"))
    uids = [o["uid"] for o in offers]
    assert all(re.fullmatch(r"[0-9a-f]{40}", u) for u in uids)
    assert len(uids) == len(set(uids))


def test_index_builds_deep_links_only_from_validated_uids():
    html = (ROOT / "index.html").read_text(encoding="utf8")
    assert "jobradar://offer/${uid}" in html
    assert "/^[0-9a-f]{40}$/" in html                       # validation avant ecriture dans le href
    for lang in ("ar:", "fr:", "en:"):
        assert lang in html.split("const L10N")[1][:200]


def test_site_sends_nothing_to_the_app():
    html = (ROOT / "index.html").read_text(encoding="utf8")
    assert "sendBeacon" not in html and "XMLHttpRequest" not in html
    assert len(re.findall(r"\bfetch\(", html)) == len(re.findall(r'fetch\("data/', html))  # lectures de data/ seulement


def test_only_http_urls_reach_offers_json():
    import sys
    sys.path.insert(0, str(ROOT / "tools"))
    import collect as c
    from datetime import datetime
    rules = json.loads((c.HERE / "rules.json").read_text(encoding="utf8"))
    now = datetime(2026, 11, 10, 12, 0, tzinfo=c.ALGIERS)
    text = "Agence recrute 5 animateurs pour le salon SAFEX Alger, envoyez CV rh@event.dz"
    bad = c.build_record({"text": text, "url": "javascript:alert(1)", "platform": "web", "date": ""}, "S", rules, now)
    good = c.build_record({"text": text, "url": "https://x.dz/1", "platform": "web", "date": ""}, "S", rules, now)
    assert bad is not None and bad["url"] == ""
    assert good["url"] == "https://x.dz/1"


def test_index_refuses_non_http_ad_links():
    html = (ROOT / "index.html").read_text(encoding="utf8")
    assert "const safeUrl" in html and "if (safeUrl(o.url))" in html
