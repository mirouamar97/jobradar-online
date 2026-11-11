import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import collect as c  # noqa: E402

RULES = json.loads((c.HERE / "rules.json").read_text(encoding="utf8"))
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=c.ALGIERS)


def score(text, date=""):
    return c.score_offer(text, date, RULES, NOW)


def item(text, url="https://x.dz/1", platform="web", date=""):
    return {"text": text, "url": url, "platform": platform, "date": date}


def test_event_offer_scores_high_and_is_flagged():
    r = score("Nous recrutons des animateurs et hôtesses pour un stand au salon SILA, SAFEX Pins Maritimes. "
              "Envoyez votre CV au 0550 12 34 56")
    assert r["score"] >= 7 and r["event"]


def test_exhibition_ad_without_hiring_is_rejected():
    assert score("Venez visiter le salon Dentex à la Safex d'Alger jusqu'au 5 Juin")["score"] < 0


def test_job_seeker_and_girls_only_are_excluded():
    assert score("Bonjour, je cherche un emploi comme vendeur à Alger, merci")["score"] < 0
    assert score("Recrute hôtesse pour salon, pour filles uniquement")["score"] < 0


def test_far_city_is_penalised():
    assert score("Recrute technicien de maintenance, Oran, Algérie. Envoyer CV")["score"] < RULES["min_score"]


def test_look_alikes_are_not_events():
    assert not c.is_event("Créateur et animateur de contenu visuel 60 000 DA Bab Ezzouar")
    assert not c.is_event("nouveau stand up de l'humoriste")
    assert not c.is_event("Salon de coiffure recrute une coiffeuse")
    assert not c.is_event("Recrute vendeur, éventuellement avec expérience, standardiste accepté")
    assert c.is_event("CHEF DE PROJET ÉVÉNEMENTIEL")
    assert c.is_event("مطلوب منشطات لمعرض")


def test_required_years():
    assert c.required_years("Débutant / Junior (1 À 2 Ans)") == 1
    assert c.required_years("Confirmé / Expérimenté (3 À 5 Ans)") == 3
    assert c.required_years("Expérience minimum 5 ans") == 5
    assert c.required_years("Recrute hôtesse, âge 18 à 30 ans, sans expérience") == 0


def test_senior_event_offer_is_kept_but_flagged():
    rec = c.build_record(item("Chef de projet événementiel recrute, Confirmé / Expérimenté (3 À 5 Ans), Alger"),
                         "Emploitic", RULES, NOW)
    assert rec and rec["event"] and rec["senior"] and rec["years_required"] == 3


def test_scam_and_low_scores_are_dropped():
    assert c.build_record(item("Recrutement commercial, gagner de l'argent depuis chez vous, contactez-moi en privé"),
                          "S", RULES, NOW) is None
    assert c.build_record(item("Vente appartement F3 à Bab Ezzouar, appelez 0550123456"), "S", RULES, NOW) is None


def test_phones_and_emails():
    assert c.mobiles("٠٥٥٥ ١٢ ٣٤ ٥٦ و 00213 661 22 33 44") == ["0555 12 34 56", "0661 22 33 44"]
    assert c.mobiles("+213 (0) 770 11 22 33 / 0770-11-22-33") == ["0770 11 22 33"]
    assert c.mobiles("05551234567") == []
    assert c.emails("rh@event.dz NOREPLY@x.dz pub@x.dz logo@site.png rh@event.dz") == ["rh@event.dz"]


def test_personal_data_and_ui_noise_are_removed():
    t = c.clean_text("RECRUTEMENT URGENT Agent commercial situ… See more See translation Comment as Amar Himeur "
                     "Facebook Facebook Facebook")
    assert "Amar" not in t and "Himeur" not in t and "Comment as" not in t and "Facebook Facebook" not in t
    assert "RECRUTEMENT URGENT" in t
    # des mots normaux contenant Like / Share / Active ne sont pas abimes
    assert c.clean_text("shareholder unlike active") == "shareholder unlike active"


def test_extract_links_emploitic_style():
    html = """<a href="/offres-d-emploi/region">Offres d'emploi par région</a>
      <a href="/entreprises/x/offres-d-emploi/ventes/vendeur-abc">Vendeur showroom X Alger, Algérie Hier Débutant
      &lt;p&gt;&lt;strong&gt;X recrute un vendeur pour Bab Ezzouar&lt;/strong&gt;</a>"""
    cards = c.extract_links(html, "https://emploitic.com/offres", "/offres-d-emploi/")
    assert len(cards) == 1 and "<strong>" not in cards[0]["text"] and cards[0]["date"].lower() == "hier"
    assert cards[0]["url"].startswith("https://emploitic.com/entreprises/")


def test_extract_blocks_from_listing():
    html = """<html><body><nav>menu</nav>
      <div class="offer-card"><a href="/o/1"><h3>Animateur stand salon - Alger</h3></a>
        <p>Agence événementielle recrute 5 animateurs pour SAFEX. il y a 2 jours</p></div>
      <div class="offer-card"><a href="/o/2"><h3>Comptable senior</h3></a><p>10 ans d'expérience, Oran</p></div>
      </body></html>"""
    cards = c.extract_blocks(html, "https://site.dz/emploi")
    assert len(cards) == 2 and cards[0]["url"] == "https://site.dz/o/1" and cards[0]["date"] == "il y a 2 jours"


def test_parse_ddg_skips_ads_and_decodes_urls():
    html = """<div class="results">
      <div class="result result--ad"><h2><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fpub.example.com%2F">Pub</a></h2></div>
      <div class="result"><h2 class="result__title"><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.facebook.com%2Fgroups%2F1%2Fposts%2F2&amp;rut=a">Recrutement animateurs salon</a></h2>
        <a class="result__snippet" href="x">il y a 2 jours · Agence recrute animateurs SAFEX Alger</a></div>
      <div class="result"><h2><a class="result__a" href="https://example.dz/offre">Offre hôtesse Alger</a></h2></div></div>"""
    r = c.parse_ddg(html)
    assert [x["url"] for x in r] == ["https://www.facebook.com/groups/1/posts/2", "https://example.dz/offre"]
    assert "2 jours" in r[0]["date"]


def test_parse_telegram():
    html = """<div class="tgme_widget_message"><div class="tgme_widget_message_text">Recrute serveur Bab Ezzouar 0550123456</div>
      <a class="tgme_widget_message_date" href="https://t.me/Jobs_dz7/100"><time datetime="2026-10-29T10:00:00+00:00"></time></a></div>"""
    p = c.parse_telegram(html, "https://t.me/s/Jobs_dz7")
    assert len(p) == 1 and p[0]["url"].endswith("/100") and p[0]["date"].startswith("2026-10-29")


def test_ages():
    now = datetime(2026, 10, 27, 12)
    assert c.age_days("3 h", now) < 1
    assert c.age_days("il y a 2 jours", now) == 2
    assert c.age_days("hier", now) == 1
    assert 4.4 < c.age_days("22/10/2026", now) < 5.6
    assert c.age_days("", now) is None


def test_old_posts_are_rejected():
    assert score("Recrute serveur Bab Ezzouar envoyer cv", date="il y a 9 jours")["score"] < 0


def test_uid_is_stable_per_url_and_per_text():
    a = c.uid_of(item("texte a", url="https://x.dz/1"))
    assert a == c.uid_of(item("texte b", url="https://x.dz/1"))
    assert c.uid_of(item("Recrute animateur SAFEX", url="")) == c.uid_of(item("RECRUTE animateur  SAFEX !", url=""))


def _rec(uid, found, score=5, event=False):
    return {"uid": uid, "found_at": found, "score": score, "event": event, "text": uid}


def test_merge_keeps_first_seen_drops_old_and_puts_events_first():
    old = [_rec("a", "2026-11-09T10:00"), _rec("old", "2026-10-01T10:00"), _rec("b", "2026-11-09T11:00", score=9)]
    new = [_rec("a", "2026-11-10T12:00", score=6), _rec("ev", "2026-11-10T12:00", score=4, event=True)]
    out = c.merge(old, new, NOW)
    ids = [o["uid"] for o in out]
    assert "old" not in ids
    assert ids[0] == "ev"                      # evenementiel d'abord, meme avec une note plus basse
    assert ids.index("b") < ids.index("a")     # puis par note
    assert next(o for o in out if o["uid"] == "a")["found_at"] == "2026-11-09T10:00"   # premiere vue conservee
    assert len(c.merge([], [_rec(str(i), "2026-11-10T12:00") for i in range(400)], NOW)) == c.MAX_OFFERS


def test_run_survives_a_broken_source_and_keeps_old_data_when_all_fail(tmp_path, monkeypatch):
    monkeypatch.setattr(c, "DATA", tmp_path)
    monkeypatch.setattr(c, "PAUSE", (0, 0))
    good = [item("Agence recrute 5 animateurs pour le salon SAFEX Alger, envoyez CV rh@event.dz", url="https://x.dz/ok")]

    def fake(kind, src):
        if src["id"] == "emploitic":
            raise RuntimeError("HTTP 403")
        return [dict(g, platform="web") for g in good] if src["id"] == "algeriejob" else []

    monkeypatch.setattr(c, "collect_source", fake)
    assert c.run(NOW) == 0
    offers = json.loads((tmp_path / "offers.json").read_text(encoding="utf8"))
    status = json.loads((tmp_path / "status.json").read_text(encoding="utf8"))
    assert len(offers) == 1 and offers[0]["event"] and offers[0]["emails"] == "rh@event.dz"
    bad = next(s for s in status["sources"] if s["id"] == "emploitic")
    assert not bad["ok"] and "403" in bad["error"]
    assert status["events"] == 1

    # Toutes les sources en panne : on garde les anciennes offres au lieu d'ecraser le fichier.
    monkeypatch.setattr(c, "collect_source", lambda kind, src: (_ for _ in ()).throw(RuntimeError("offline")))
    c.run(NOW + timedelta(hours=1))
    assert len(json.loads((tmp_path / "offers.json").read_text(encoding="utf8"))) == 1


def test_published_files_contain_no_personal_data():
    data = c.DATA / "offers.json"
    if data.exists():
        text = data.read_text(encoding="utf8")
        for forbidden in ("Himeur", "Comment as", "himeuramar", "app_password"):
            assert forbidden not in text


@pytest.mark.parametrize("name", ["rules.json", "sources.json"])
def test_config_files_are_valid_json(name):
    assert json.loads((c.HERE / name).read_text(encoding="utf8"))
