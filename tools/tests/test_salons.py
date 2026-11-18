"""Tests de tools/salons.py : HTML sauvegardes de algeriaexhibitions.dz (aucun acces reseau)."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import salons  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
RULES = json.loads((Path(__file__).resolve().parents[1] / "rules.json").read_text(encoding="utf-8"))


def read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def test_parse_dates_variants():
    assert salons.parse_dates("16–18 novembre 2026") == ("2026-11-16", "2026-11-18")
    assert salons.parse_dates("28 oct. – 7 nov. 2026") == ("2026-10-28", "2026-11-07")
    assert salons.parse_dates("Du 17 déc. au 26 déc. 2026") == ("2026-12-17", "2026-12-26")
    assert salons.parse_dates("5 février 2027") == ("2027-02-05", "2027-02-05")
    assert salons.parse_dates("30 déc. 2026 – 3 janv. 2027") == ("2026-12-30", "2027-01-03")
    assert salons.parse_dates("bientôt") == ("", "")


def test_event_urls_from_calendar_and_home():
    urls = salons.event_urls(read("calendrier.html")) | salons.event_urls(read("agenda.html"))
    assert "https://www.algeriaexhibitions.dz/manifestations/tashil-2026" in urls
    assert "https://www.algeriaexhibitions.dz/manifestations/woodtech-2026" in urls
    assert all("/manifestations/" in u for u in urls)
    assert len(urls) >= 10


def test_parse_event_with_organizer_contacts():
    url = "https://www.algeriaexhibitions.dz/manifestations/tashil-2026"
    e = salons.parse_event(read("event_tashil-2026.html"), url)
    assert e["id"] == "tashil-2026"
    assert e["name"] == "TASHIL 2026"
    assert e["title"].startswith("Salon du Crédit")
    assert (e["start"], e["end"]) == ("2026-11-16", "2026-11-18")
    assert e["pavilion"] == "Gourara"
    assert e["organizer"] == "SARL NETINOU"
    assert e["emails"] == ["contact@example.org"]
    assert "0555000001" in e["phones"] and "0555000002" in e["phones"]
    assert e["website"] == "https://tasshiil.com/"
    assert e["register_url"] == "https://plateforme.tasshiil.com/FR/form-visitor.awp"
    assert e["source_url"] == url
    assert "Mohammadia" in e["place"]
    assert e["hours"] == "10:00"


def test_generic_safex_contacts_are_not_organizer_contacts():
    e = salons.parse_event(read("event_woodtech-2026.html"), "https://www.algeriaexhibitions.dz/manifestations/woodtech-2026")
    assert "event@safex.dz" not in e["emails"]
    assert all(not p.startswith("02379") and not p.startswith("+21323") for p in e["phones"])
    assert e["emails"] == ["info@example.org"]
    assert e["organizer"] == "SARL CGCOM EVENT"


def test_hostess_score_wide_public_vs_professional():
    wide = salons.hostess_score("Salon International du Mariage, de la Beauté et de la Mode", "", RULES)
    pro = salons.hostess_score("Salon de la Sous-Traitance Industrielle", "Industrie, Production & Manufactures", RULES)
    assert 8 <= wide <= 10
    assert 4 <= pro <= 6
    assert salons.hostess_score("Salon X", "", RULES) == 5  # inconnu : neutre


def test_dates_first_of_month_and_empty():
    assert salons.parse_dates("1er novembre 2026") == ("2026-11-01", "2026-11-01")
    assert salons.parse_dates("1er – 3 nov. 2026") == ("2026-11-01", "2026-11-03")
    assert salons.parse_dates("") == ("", "")


def test_arabic_keywords_use_word_boundaries():
    rules = {"salon_keywords": {"wide": [], "pro": ["مال"]}}
    assert salons.hostess_score("معرض كمال وشمال", "", rules) == 5          # ni « كمال » ni « شمال »
    assert salons.hostess_score("معرض المال والأعمال", "", rules) == 5      # « المال » = « مال » : 1 mot-cle pro -> 5
    assert salons._kw_hits("معرض المال", ["مال"]) == 1
    assert salons._kw_hits("معرض كمال", ["مال"]) == 0


def test_audience_label():
    assert salons.audience(9) == "large public"
    assert salons.audience(5) == "professionnel"
    assert salons.audience(6) == "mixte"


def test_upcoming_window_keeps_ongoing_and_next_30_days():
    today = date(2026, 11, 18)
    items = [
        {"id": "a", "start": "2026-11-16", "end": "2026-11-18"},   # dernier jour
        {"id": "b", "start": "2026-11-10", "end": "2026-11-17"},   # termine
        {"id": "c", "start": "2026-12-17", "end": "2026-12-26"},   # dans 29 j
        {"id": "d", "start": "2026-12-19", "end": "2026-12-28"},   # dans 31 j
    ]
    assert [i["id"] for i in salons.in_window(items, today, days=30)] == ["a", "c"]


def test_new_salon_alert_keys_and_text():
    sent: list[str] = []
    items = [
        {"id": "tashil-2026", "name": "TASHIL 2026", "title": "Salon du Crédit", "start": "2026-11-16", "end": "2026-11-18",
         "pavilion": "Gourara", "organizer": "SARL NETINOU", "phones": ["0555000001"], "emails": ["x@y.dz"],
         "register_url": "https://r.example/", "hostess_score": 5, "audience": "professionnel"},
    ]
    alerted: list = []
    n = salons.alert_new_salons(items, alerted, "tok", "1", sender=lambda _t, _c, m: sent.append(m) or "")
    assert n == 1 and "salon:tashil-2026" in alerted
    assert "TASHIL 2026" in sent[0] and "0555000001" in sent[0]
    # deuxieme passage : plus rien
    assert salons.alert_new_salons(items, alerted, "tok", "1", sender=lambda _t, _c, m: sent.append(m) or "") == 0
    assert len(sent) == 1


def test_failed_or_capped_alerts_stay_unseen():
    items = [{"id": f"s{i}", "name": f"S{i}"} for i in range(7)]
    seen: list = []
    assert salons.alert_new_salons(items, seen, "tok", "1", sender=lambda *_a: "HTTP 500") == 0
    assert seen == []                                       # Telegram en panne : rien n'est memorise
    sent: list = []
    assert salons.alert_new_salons(items, seen, "tok", "1", sender=lambda *_a: sent.append(1) or "") == salons.ALERT_CAP
    assert len(seen) == salons.ALERT_CAP                    # le reste attend le passage suivant
    assert salons.alert_new_salons(items, seen, "", "", sender=lambda *_a: "") == 0
    assert len(seen) == 7                                   # sans secrets : memorise en silence


def test_site_down_never_erases_salons(tmp_path, monkeypatch):
    monkeypatch.setattr(salons, "DATA", tmp_path)
    (tmp_path / "salons.json").write_text('[{"id": "keep"}]', encoding="utf8")
    def down(_u):
        raise OSError("down")
    assert salons.run_salons(fetcher=down, pause=0, force=True) == 1
    assert json.loads((tmp_path / "salons.json").read_text(encoding="utf8")) == [{"id": "keep"}]
    # deja tente aujourd'hui : pas de nouveau passage sans force
    assert salons.run_salons(fetcher=down, pause=0) == 0


def test_page_failure_keeps_previous_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(salons, "DATA", tmp_path)
    url = "https://www.algeriaexhibitions.dz/manifestations/tashil-2026"
    old = {"id": "tashil-2026", "name": "OLD", "start": "2026-11-16", "end": "2026-11-18", "source_url": url}
    (tmp_path / "salons.json").write_text(json.dumps([old]), encoding="utf8")
    page = '<a href="%s">x</a>' % url
    def fetcher(u):
        if u == url:
            raise OSError("503")
        return page
    from datetime import datetime
    assert salons.run_salons(now=datetime(2026, 11, 18, 8, 0, tzinfo=salons.ALGIERS), fetcher=fetcher, sender=lambda *_a: "", pause=0, force=True) == 0
    assert json.loads((tmp_path / "salons.json").read_text(encoding="utf8"))[0]["name"] == "OLD"


def test_first_run_is_silent():
    sent: list[str] = []
    n = salons.alert_new_salons([{"id": "z", "name": "Z"}], [], "tok", "1", sender=lambda *_a: sent.append("x") or "", first_run=True)
    assert n == 0 and not sent


def test_site_json_contains_no_secret_or_personal_fields():
    e = salons.parse_event(read("event_tashil-2026.html"), "https://www.algeriaexhibitions.dz/manifestations/tashil-2026")
    blob = json.dumps(e, ensure_ascii=False)
    assert "himeur" not in blob.lower() and "amar" not in blob.lower()
