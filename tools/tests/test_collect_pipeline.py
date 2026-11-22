"""Couverture du pipeline de tools/collect.py : notation (golden), rejets, dedoublonnage, plafonds 300 / 14 jours, status.json."""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import collect as c  # noqa: E402

RULES = json.loads((c.HERE / "rules.json").read_text(encoding="utf8"))
GOLDEN = json.loads((Path(__file__).parent / "golden_cases.json").read_text(encoding="utf8"))
GOLDEN_NOW = datetime.strptime(GOLDEN["now"], "%Y-%m-%dT%H:%M")
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=c.ALGIERS)
GOOD = "Agence recrute 5 animateurs pour le salon SAFEX Alger, envoyez CV rh@event.dz"


def item(text, url="https://x.dz/1", platform="web", date="", **extra):
    return dict({"text": text, "url": url, "platform": platform, "date": date}, **extra)


def rec(uid, found, score=5, event=False):
    return {"uid": uid, "found_at": found, "score": score, "event": event, "text": uid}


# ----------------------------------------------------------------------------- notation : rejeu de golden_cases.json


@pytest.mark.parametrize("case", GOLDEN["cases"], ids=lambda k: k["text"][:40])
def test_golden_case_replays_exactly(case):
    r = c.score_offer(case["text"], case["date"], RULES, GOLDEN_NOW, job_site=case["job_site"])
    for key in ("score", "event", "years", "scam", "reasons"):
        assert r[key] == case["expect"][key], key
    assert r["score"] == case["expect"]["score"]
    assert bool(r["event"]) == case["expect"]["event"]


# ----------------------------------------------------------------------------- raisons de rejet


@pytest.mark.parametrize("text,reason", [
    ("court", "texte trop court"),
    ("Venez visiter le salon Dentex à la Safex d'Alger jusqu'au 5 Juin", "annonce sans recrutement"),
])
def test_reject_reasons(text, reason):
    assert c.reject_reason(item(text), RULES, NOW) == reason


def test_scam_offers_are_rejected_with_an_explicit_reason():
    text = "Agence recrute 5 animateurs pour le salon SAFEX Alger, payer 5000 DA de frais de dossier avant l'entretien"
    assert c.reject_reason(item(text), RULES, NOW) == "arnaque: frais de dossier"


def test_reject_reason_below_min_score_mentions_threshold():
    got = c.reject_reason(item("Nous recrutons un comptable pour un cabinet à Oran, envoyez votre CV"), RULES, NOW)
    assert got == f"note < {RULES['min_score']}"


def test_build_record_drops_rejected_and_keeps_good():
    assert c.build_record(item("court"), "S", RULES, NOW) is None
    r = c.build_record(item(GOOD), "SRC", RULES, NOW)
    assert r and r["source"] == "SRC" and r["event"] and r["uid"] == c.uid_of(item(GOOD))
    assert r["emails"] == "rh@event.dz" and r["status"] == "new"
    assert set(r) >= {"uid", "found_at", "source", "platform", "score", "url", "phones", "emails", "text", "status"}


# ----------------------------------------------------------------------------- identifiants et dedoublonnage


def test_uid_is_stable_by_url_and_falls_back_to_text_for_channel_roots():
    assert c.uid_of(item("a", url="https://x.dz/1")) == c.uid_of(item("different text", url="https://x.dz/1"))
    assert c.uid_of(item("a", url="https://x.dz/1")) != c.uid_of(item("a", url="https://x.dz/2"))
    # une racine de canal ne distingue pas deux messages : on se rabat sur le texte
    assert c.uid_of(item("premier message", url="https://t.me")) != c.uid_of(item("second message", url="https://t.me"))
    assert c.uid_of(item("Meme  Texte !", url="https://t.me")) == c.uid_of(item("meme texte", url="https://t.me"))


def test_merge_dedupes_same_uid_keeping_first_seen_and_latest_content():
    old = [dict(rec("a", "2026-11-08T10:00", score=4), text="v1")]
    new = [dict(rec("a", "2026-11-10T12:00", score=7), text="v2")]
    out = c.merge(old, new, NOW)
    assert len(out) == 1 and out[0]["text"] == "v2" and out[0]["score"] == 7
    assert out[0]["found_at"] == "2026-11-08T10:00"


# ----------------------------------------------------------------------------- plafonds : 14 jours et 300 offres


def test_keep_window_is_14_days_with_inclusive_boundary():
    assert (c.KEEP_DAYS, c.MAX_OFFERS) == (14, 300)
    edge = (NOW - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M")
    just_over = (NOW - timedelta(days=14, minutes=1)).strftime("%Y-%m-%dT%H:%M")
    out = c.merge([rec("edge", edge), rec("over", just_over)], [], NOW)
    assert [o["uid"] for o in out] == ["edge"]


def test_cap_of_300_keeps_events_then_best_scores():
    fresh = NOW.strftime("%Y-%m-%dT%H:%M")
    many = [rec(f"n{i}", fresh, score=i % 9) for i in range(400)]
    evs = [rec(f"e{i}", fresh, score=3, event=True) for i in range(5)]
    out = c.merge(many, evs, NOW)
    assert len(out) == 300
    assert all(o["event"] for o in out[:5]) and not any(o["event"] for o in out[5:])
    assert all(o["score"] >= 1 for o in out[5:])          # les notes les plus basses sont celles qu'on coupe


def test_merge_ignores_old_entries_without_uid():
    assert c.merge([{"found_at": "2026-11-10T10:00", "score": 5}], [], NOW) == []


# ----------------------------------------------------------------------------- status.json


def test_status_json_schema_and_counts(tmp_path, monkeypatch):
    monkeypatch.setattr(c, "DATA", tmp_path)
    monkeypatch.setattr(c, "PAUSE", (0, 0))
    monkeypatch.setattr(c, "collect_source", lambda kind, src: [item(GOOD, url="https://x.dz/" + src["id"])])
    assert c.run(NOW) == 0
    st = json.loads((tmp_path / "status.json").read_text(encoding="utf8"))
    assert set(st) == {"generated_at", "offers", "events", "sources"}
    assert st["generated_at"] == "2026-11-10T12:00"
    offers = json.loads((tmp_path / "offers.json").read_text(encoding="utf8"))
    assert st["offers"] == len(offers) and st["events"] == sum(1 for o in offers if o["event"])
    for s in st["sources"]:
        assert {"id", "name", "kind", "ok", "cards", "kept", "error"} <= set(s)
        assert s["kind"] in ("page", "telegram", "search")
    assert any(s["ok"] and s["kept"] == 1 for s in st["sources"])


def test_failed_source_error_is_truncated(tmp_path, monkeypatch):
    monkeypatch.setattr(c, "DATA", tmp_path)
    monkeypatch.setattr(c, "PAUSE", (0, 0))

    def boom(kind, src):
        raise RuntimeError("x" * 500)

    monkeypatch.setattr(c, "collect_source", boom)
    c.run(NOW)
    st = json.loads((tmp_path / "status.json").read_text(encoding="utf8"))
    assert all(len(s["error"]) <= 200 for s in st["sources"])
