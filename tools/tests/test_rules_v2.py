"""Regles v2 partagees avec l'appli (core/rank) : filtres durs, darija / arabizi, mots-cles tous dans rules.json."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import collect as c  # noqa: E402

RULES = json.loads((c.HERE / "rules.json").read_text(encoding="utf8"))
NOW = datetime(2026, 11, 18, 9, 0)


def score(text, date="", job_site=False):
    return c.score_offer(text, date, RULES, NOW, job_site=job_site)


def kept(text, date=""):
    return c.is_kept(score(text, date), RULES)


# ----------------------------------------------------------------------------- experience exigee > 2 ans : filtre dur


@pytest.mark.parametrize("text,years", [
    ("Confirmé / Expérimenté (3 À 5 Ans)", 3),
    ("Débutant / Junior (1 À 2 Ans)", 1),
    ("2 à 4 années d'expérience", 2),
    ("entre 1 et 3 ans d'expérience", 1),
    ("Expérience exigée : 4 ans", 4),
    ("5 years of experience required", 5),
    ("خبرة لا تقل عن 3 سنوات", 3),
    ("خبرة خمس سنوات", 5),
    ("خبرة سنتين على الأقل", 2),
    ("خبرة ٥ سنوات", 5),
    ("خبرة من 3 إلى 5 سنوات", 3),
    ("trois ans d'expérience minimum", 3),
    ("une expérience allant de moins d'un an à 5 ans", 0),
])
def test_required_years_v2(text, years):
    assert c.required_years(text) == years


@pytest.mark.parametrize("text", [
    "Agence forte de 10 ans d'expérience recrute des hôtesses",
    "Forte d'une expérience de 12 ans, notre agence recrute",
    "Société avec 8 ans d'expérience sur le marché recrute",
    "شركة لها 10 سنوات خبرة في السوق توظف",
    "Recrute hôtesse, âge 18 à 30 ans, sans expérience",
    "Recrute serveur, âgé entre 20 et 35 ans",
])
def test_company_age_is_not_required_experience(text):
    assert c.required_years(text) == 0


def test_more_than_two_years_is_rejected():
    r = score("Chef de projet événementiel, recrute, Confirmé / Expérimenté (3 À 5 Ans), Alger")
    assert not c.is_kept(r, RULES) and r["reasons"][0].startswith("experience") and r["event"] and r["years"] == 3
    assert not kept("مطلوب موظفة استقبال بمعرض الجزائر الدولي، خبرة لا تقل عن 3 سنوات")
    assert kept("Chef de projet événementiel, recrute, Débutant / Junior (1 À 2 Ans), Alger")
    assert kept("Agence forte de 10 ans d'expérience recrute des hôtesses pour un salon à Alger")


# ----------------------------------------------------------------------------- villes lointaines : filtre dur


@pytest.mark.parametrize("text", [
    "Recrute animateur pour salon à Djelfa, 4 jours, hébergement assuré",
    "Evénementiel : recrute régisseur et animateurs pour festival à Béjaïa",
    "Recrute 10 hôtesses pour événement à Oran, tenue fournie",
    "مطلوب منشطين لمعرض في وهران، الجزائر",
    "مطلوب منشطين لمعرض بولاية معسكر",
])
def test_far_city_is_rejected(text):
    r = score(text)
    assert not c.is_kept(r, RULES) and r["reasons"][0].startswith("loin")


@pytest.mark.parametrize("text", [
    "Recrute vendeur Bab Ezzouar et Oran, envoyer CV",
    "Recrute hôtesses pour foire à Gué de Constantine, 2 jours",
    "On recrute serveurs à Bab El Oued, temps partiel",
    "مطلوب منشطين لمعسكر شتوي للأطفال بالعاصمة",
    "Recrute téléconseiller en télétravail, société basée à Oran",
    "Recrute hôtesses pour le salon à Blida, départ d'Alger",
])
def test_near_or_remote_is_not_far(text):
    assert kept(text)


# ----------------------------------------------------------------------------- arnaques, argent, documents


@pytest.mark.parametrize("text", [
    "Recrute hôtesses salon, envoyez la copie de votre carte d'identité",
    "Recrute vendeurs à Alger, gain facile depuis le téléphone",
    "مطلوب موظفين بالعاصمة، ادفع مبلغ التسجيل",
    "Recrute animateurs à Alger, frais de formation à payer",
])
def test_money_or_documents_is_scam(text):
    r = score(text)
    assert r["scam"] and not c.is_kept(r, RULES)


@pytest.mark.parametrize("text", [
    "Recrute vendeuse à Alger, sans frais d'inscription",
    "محل بالحراش يوظف بائعة، بدون رسوم التسجيل",
    "Recrute hôtesse à Alger, aucun frais de dossier",
])
def test_negated_fee_is_not_scam(text):
    assert score(text)["scam"] == [] and kept(text)


@pytest.mark.parametrize("text", [
    "Brand ambassador pour lancement de produit, Alger, 18 à 30 ans",
    "Wedding planner débutante, formation assurée, Alger",
    "Ambassadrice de marque pour une dégustation à Bab Ezzouar",
])
def test_event_roles_are_a_hiring_signal(text):
    assert kept(text), score(text)


# ----------------------------------------------------------------------------- darija / arabizi / arabe / anglais


@pytest.mark.parametrize("text", [
    "Rah nkhdmou animatris f stand tae cosmetique f dar el beida, 2500 da",
    "khasna hotess l salon f safex had semana",
    "نوظف مضيفات لمعرض بالعاصمة",
    "We are hiring brand promoters for a product launch event in Bab Ezzouar",
    "Recruiting event hostesses for exhibition in Algiers",
    "مطلوب سرفور في باب الزوار",
])
def test_dialects_are_understood(text):
    assert kept(text), score(text)


@pytest.mark.parametrize("text", [
    "salam khawti n7awes 3la khedma serveur wela vendeur f bab ezzouar",
    "راني نحوس على خدمة كمنشط في العاصمة",
    "je cherche un travail de serveur à Alger",
])
def test_dialect_job_seekers_are_excluded(text):
    assert not kept(text)


def test_lexicon_lives_in_rules_json():
    for key in ("event_words", "event_negative", "experience", "aliases", "negations", "weak_locations",
                "remote_words", "context_only", "places", "categories", "rank", "max_experience_years"):
        assert RULES.get(key), key
    assert not hasattr(c, "EVENT_WORDS"), "les listes ne doivent plus etre en dur dans collect.py"


def test_every_far_wilaya_of_the_distance_table_is_a_far_city():
    far = {c.normalize(w) for w in RULES["far_cities"]}
    for p in RULES["places"]:
        if p["kind"] == "wilaya" and p["km"] > RULES["far_km"]:
            assert any(c.normalize(a) in far for a in p["aliases"]), p["id"]


def test_places_table_covers_algiers_and_wilayas():
    communes = [p for p in RULES["places"] if p["kind"] == "commune"]
    wilayas = [p for p in RULES["places"] if p["kind"] == "wilaya"]
    assert len(communes) >= 57 and len(wilayas) >= 57
    assert next(p for p in communes if p["id"] == "bab_ezzouar")["km"] == 0
    assert all(p["ar"] and p["fr"] and p["aliases"] for p in RULES["places"])
