"""Genere tools/tests/golden_cases.json : cas de reference (texte -> note, evenementiel, annees, etc.) calcules par collect.py.

Le meme fichier est rejoue par l'extension navigateur (JobRadar Watcher, scoring.js) et par l'appli : une seule verite pour les regles.
    python tools/gen_golden.py          -> reecrit le fichier
    pytest tools/tests/test_golden.py   -> verifie qu'il est a jour
Aucune donnee personnelle : textes d'exemple inventes.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import collect  # noqa: E402

NOW = datetime(2026, 11, 18, 9, 0)
OUT = HERE / "tests" / "golden_cases.json"

# (texte, date, job_site)
SAMPLES: list[tuple[str, str, bool]] = [
    ("Agence événementielle recrute 5 animateurs et hôtesses pour le salon SAFEX, Pins Maritimes, débutants acceptés. Contact 0555 12 34 56", "", False),
    ("Recrute hôtesses d'accueil pour salon à Alger du 20 au 25 novembre, tenue fournie, envoyer CV à rh@agence-event.dz", "hier", False),
    ("نوظف مضيفات ومنشطات لمعرض في الصفائح البحرية الجزائر العاصمة، للتواصل 0661 22 33 44", "", False),
    ("Recrutement animateur(trice) de stand, salon des Pins Maritimes, Alger, expérience 1 à 2 ans", "", False),
    ("Event manager confirmé, 5 ans d'expérience minimum, Alger, recrute immédiatement", "", False),
    ("Chef de projet événementiel, recrute, Confirmé / Expérimenté (3 À 5 Ans), Alger", "", False),
    ("Chef de projet événementiel, recrute, Débutant / Junior (1 À 2 Ans), Alger", "", False),
    ("Recrute serveur et serveuse pour restaurant à Bab Ezzouar, CDD, contact 0770 11 22 33", "", False),
    ("Cherche vendeur en magasin d'électroménager, Alger centre, urgent, 0550 99 88 77", "", False),
    ("Centre d'appels recrute téléconseillers à Alger, débutants acceptés, salaire fixe + primes", "", False),
    ("Offre de stage animateur community manager, animation des réseaux sociaux, Alger", "", False),
    ("Animation de l'équipe de vente : responsable des ventes recrute à Alger, 5 ans d'expérience", "", False),
    ("Salon de coiffure recrute coiffeuse à Hydra, expérience 2 ans", "", False),
    ("Stand up comedy ce soir à Alger, venez nombreux, entrée libre", "", False),
    ("Vente de voiture à Alger, état neuf, prix à débattre 0555 00 11 22", "", False),
    ("Recrute personnel pour travail à domicile, gain facile, payer frais d'inscription 2000 DA avant de commencer", "", False),
    ("Recrute 10 hôtesses pour événement à Oran, tenue fournie", "", False),
    ("Recrute animateur pour salon à Djelfa, 4 jours, hébergement assuré", "", False),
    ("Hôtesse d'accueil recherchée pour congrès à l'hôtel Sofitel Alger, 2 jours", "il y a 2 jours", False),
    ("Hôtesse d'accueil recherchée pour congrès à l'hôtel Sofitel Alger, 2 jours", "il y a 20 jours", False),
    ("Hôtesse d'accueil recherchée pour congrès à l'hôtel Sofitel Alger, 2 jours", "2026-11-17", False),
    ("Hôtesse d'accueil recherchée pour congrès à l'hôtel Sofitel Alger, 2 jours", "2026-09-01", False),
    ("Promoteur(trice) des ventes pour street marketing à Alger, dégustation en grande surface", "", False),
    ("Brand ambassador pour lancement de produit, Alger, 18 à 30 ans", "", False),
    ("Wedding planner débutante, formation assurée, Alger", "", False),
    ("نوظف نادلا خبرة سنتين على الأقل في مطعم بالجزائر العاصمة", "", False),
    ("مطلوب موظفة استقبال بمعرض الجزائر الدولي، خبرة لا تقل عن 3 سنوات", "", False),
    ("تنشيط الدورات التكوينية: مكتب تكوين يوظف مكونا بالجزائر", "", False),
    ("Annonce : promotion sur les téléphones, venez au magasin à Alger", "", False),
    ("Besoin d'une personne pour garder mon enfant à Alger, 3 jours par semaine", "", False),
    ("Poste ouvert : Animateur(trice) de stand, ville Alger, source emploi", "", True),
    ("Technicien de maintenance, Alger, 3 ans d'expérience", "", True),
    ("Rejoignez notre équipe d'hôtesses événementielles : candidature à recrutement@event-pro.dz ou au +213 661 22 33 44", "", False),
    ("Recrute caissier(ère) pour supérette à Bab Ezzouar, 0771 22 33 44, expérience 6 ans", "", False),
    ("Recrute livreur avec permis B, moto fournie, Alger, 0555 11 22 33", "", False),
    ("Hôtesses et stewards recherchés pour le salon SILA, du 28 octobre au 7 novembre, Pins Maritimes", "", False),
    ("Recrutement massif sans diplôme, inbox pour plus d'infos, gain 100000 DA par semaine", "", False),
    ("Recherche développeur web, télétravail, 4 ans d'expérience, entreprise basée à Alger", "", False),
    ("Evénementiel : recrute régisseur et animateurs pour festival à Béjaïa", "", False),
    ("SAFEX salon : on recrute des hôtesses, merci d'envoyer photo et CV, Alger", "", False),
    ("Recrute assistante administrative débutante, Alger, urgent, 0550 12 12 12", "", False),
    ("Recruiting event hostesses for exhibition in Algiers, experience not required, send CV to hr@fair-dz.com", "", False),
]


def build() -> dict:
    rules = json.loads((HERE / "rules.json").read_text(encoding="utf8"))
    cases = []
    for text, date, job_site in SAMPLES:
        r = collect.score_offer(text, date, rules, NOW, job_site=job_site)
        cases.append({
            "text": text, "date": date, "job_site": job_site,
            "expect": {
                "score": r["score"], "event": r["event"], "years": r["years"], "scam": r["scam"], "reasons": r["reasons"],
                "mobiles": collect.mobiles(text), "emails": collect.emails(text),
                "is_event": collect.is_event(text), "required_years": collect.required_years(text),
                "normalized": collect.normalize(text),
            },
        })
    return {"now": NOW.strftime("%Y-%m-%dT%H:%M"), "cases": cases}


def main() -> int:
    data = json.dumps(build(), ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    OUT.write_text(data, encoding="utf8")
    print(f"{len(SAMPLES)} cas ecrits dans {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
