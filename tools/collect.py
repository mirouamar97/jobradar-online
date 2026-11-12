#!/usr/bin/env python3
"""JobRadar Online - collecteur horaire (GitHub Actions).

Lit uniquement des sources PUBLIQUES, sans compte ni mot de passe : sites d'emploi, canaux Telegram publics (t.me/s/...)
et recherche web (DuckDuckGo). Note chaque offre (priorite evenementiel / animation), retire arnaques et doublons,
ecrit data/offers.json (lu par le site ET par l'appli Android) et data/status.json (etat honnete de chaque source).
Aucune donnee personnelle, aucun envoi : lecture seule.
"""
from __future__ import annotations

import hashlib
import html as _html
import json
import logging
import random
import re
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = ROOT / "data"
log = logging.getLogger("collect")

ALGIERS = timezone(timedelta(hours=1))
KEEP_DAYS = 14
PAUSE = (1.5, 3.5)   # pause aleatoire entre deux sources (les tests la mettent a 0)
MAX_OFFERS = 300
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0 Safari/537.36")

# ----------------------------------------------------------------------------- texte


def normalize(text: str) -> str:
    """Minuscules, sans accents ni tachkil ; hamzas arabes unifiees (meme logique que l'appli)."""
    t = unicodedata.normalize("NFD", text)
    t = re.sub("[̀-ًͯ-ٰٟـ]", "", t).lower()
    t = t.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    t = t.replace("ة", "ه").replace("ى", "ي").replace("’", "'")
    return re.sub(r"\s+", " ", t).strip()


_LATIN = re.compile(r"[a-z0-9' \-]+")


def has(tn: str, word: str) -> bool:
    """Mot (latin : debut de mot ; arabe : inclusion), comme Scorer.has de l'appli."""
    w = normalize(word)
    if not w:
        return False
    if _LATIN.fullmatch(w):
        return re.search(r"(?<![a-z0-9])" + re.escape(w), tn) is not None
    return w in tn


def has_word(tn: str, word: str) -> bool:
    """Mot entier (+ pluriel/feminin simple) pour le latin : 'stand' ne trouve pas 'standard'."""
    w = normalize(word)
    if _LATIN.fullmatch(w):
        return re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?:s|e|es)?(?![a-z0-9])", tn) is not None
    return w in tn


_ARABIC_INDIC = "٠١٢٣٤٥٦٧٨٩"
_PERSIAN = "۰۱۲۳۴۵۶۷۸۹"


def ascii_digits(s: str) -> str:
    return "".join(str(_ARABIC_INDIC.index(c)) if c in _ARABIC_INDIC else str(_PERSIAN.index(c)) if c in _PERSIAN else c for c in s)


_PREFIX = r"(?<![\d+])(?:(?:\+|00)?213[\s.\-]?(?:\(0\)[\s.\-]?)?|0)"
_MOBILE = re.compile(_PREFIX + r"([567](?:[\s.\-]?\d){8})(?!\d)")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_BAD_MAIL = re.compile(r"(no-?reply|do-?not-?reply|mailer-daemon|^pub@|^ads@|publicite|newsletter)", re.I)


def mobiles(text: str) -> list[str]:
    out: list[str] = []
    for m in _MOBILE.finditer(ascii_digits(text)):
        n = "0" + re.sub(r"\D", "", m.group(1))
        pretty = f"{n[:4]} {n[4:6]} {n[6:8]} {n[8:]}"
        if pretty not in out:
            out.append(pretty)
    return out


def emails(text: str) -> list[str]:
    found = sorted({e.rstrip(".").lower() for e in _EMAIL.findall(text)})
    return [e for e in found if not _BAD_MAIL.search(e) and not e.endswith((".png", ".jpg", ".gif"))]


# Bruit d'interface repris des captures de pages (jamais de donnees personnelles dans le fichier publie).
_NOISE = re.compile(
    r"Comment as [^\n]{0,60}?(?=Facebook|$)|Online status indicator(?:\s*Active)?|See more|See translation|"
    r"(?:Facebook\s*){2,}|Voir la traduction|\b(?:Reply|Like|Share|Partager)\b", re.I)


def clean_text(text: str) -> str:
    t = _html.unescape(_html.unescape(text))
    t = re.sub(r"<[^>]{1,80}>", " ", t)
    t = _NOISE.sub(" ", t.replace(" ", " "))
    return re.sub(r"\s+", " ", t).strip()


# ----------------------------------------------------------------------------- age

_FR_MONTHS = {"janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6, "juillet": 7,
              "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12}
DATE_RE = re.compile(
    r"(il y a [^,\n]{1,20}|\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}|\d+\s*(?:h|j|min|jours?|heures?)\b|aujourd'hui|hier|"
    r"منذ [^\n]{1,15}|\d{1,2} (?:janvier|f[eé]vrier|mars|avril|mai|juin|juillet|ao[uû]t|"
    r"septembre|octobre|novembre|d[eé]cembre))", re.I)


def age_days(date_text: str, now: datetime) -> float | None:
    if not date_text:
        return None
    iso = re.match(r"(\d{4})-(\d{2})-(\d{2})", date_text.strip())
    if iso:
        try:
            return (now.replace(tzinfo=None) - datetime(int(iso[1]), int(iso[2]), int(iso[3]))).total_seconds() / 86400
        except ValueError:
            return None
    d = normalize(date_text)
    n = now.replace(tzinfo=None)
    m = re.search(r"(\d+)\s*(min|m\b|h|heure|hour|hr|j\b|jour|d\b|day|sem|w\b|week|يوم|ساع|اسبوع)", d)
    if m:
        v, u = int(m[1]), m[2]
        if u.startswith("min") or (u.startswith("m") and not u.startswith("mo")):
            return v / 1440
        if u.startswith(("h", "ساع")):
            return v / 24
        if u.startswith(("j", "d", "يوم")):
            return float(v)
        return v * 7.0
    if any(k in d for k in ("hier", "yesterday", "امس")):
        return 1.0
    if any(k in d for k in ("aujourd", "today", "اليوم", "a l'instant")):
        return 0.0
    m = re.search(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})", d)
    if m:
        y = int(m[3]) + (2000 if int(m[3]) < 100 else 0)
        try:
            return (n - datetime(y, int(m[2]), int(m[1]))).total_seconds() / 86400
        except ValueError:
            return None
    m = re.search(r"(\d{1,2}) (" + "|".join(_FR_MONTHS) + ")", d)
    if m:
        try:
            dt = datetime(n.year, _FR_MONTHS[m[2]], int(m[1]))
            if dt > n + timedelta(days=1):
                dt = dt.replace(year=n.year - 1)
            return (n - dt).total_seconds() / 86400
        except ValueError:
            return None
    return None


# ----------------------------------------------------------------------------- evenementiel et experience

EVENT_WORDS = [
    "evenementiel", "evenementielle", "evenement", "evenements", "event", "events", "event manager", "animateur", "animatrice",
    "animation", "hotesse", "hote d'accueil", "stand", "safex", "salon", "salons", "foire", "exposition", "degustation",
    "regisseur", "regie", "wedding planner", "festival", "congres", "street marketing", "brand ambassador",
    "ambassadeur de marque", "promoteur", "promotrice",
    "منشط", "منشطة", "تنشيط", "فعاليات",
    "ايفنت", "مضيفة", "مضيفات", "معرض", "معارض",
]
_EVENT_NEG = [re.compile(p) for p in (
    r"stand[ -]?up", r"salon de (?:coiffure|the|beaute|esthetique|massage)",
    r"anim\w* (?:de |du |des |d')?(?:contenu|digital|reseaux|social|community|3d|2d|motion|video|graphique|securite|hse)",
    r"animation (?:3d|2d|motion|graphique|video|digitale)",
    # gestion d'equipe / formation : pas de l'evenementiel (« animation de l'equipe de vente », « تنشيط الدورات »)
    r"anim\w* (?:de |d'|du |des )?(?:l'|la |les )?(?:equipe|equipes|personnel|collaborateurs|reseau|formation|formations|reunion|reunions|atelier|ateliers)",
    "تنشيط (?:ال)?(?:دورات|دوره|فريق|فرق|ورشات|ورشه|تكوين|اجتماعات)")]


def is_event(text: str) -> bool:
    tn = normalize(text)
    for neg in _EVENT_NEG:
        tn = neg.sub(" ", tn)
    return any(has_word(tn, w) for w in EVENT_WORDS)


_RANGE = re.compile(r"(\d{1,2})\s*(?:a|-|to)\s*(\d{1,2})\s*(?:ans?|years?)")
_CTX = [re.compile(p) for p in (
    r"(?:plus de|minimum|au moins|min\.?|superieure? a)\s*(\d{1,2})\s*(?:ans?|years?)",
    r"(\d{1,2})\s*\+?\s*(?:ans?|years?)\s*(?:d'|de )?(?:experience|exp)",
    r"experience\s*(?:de |d'au moins |minimum |professionnelle de )?(\d{1,2})\s*(?:ans?|years?)",
    "خبره\\s*(?:لا تقل عن |اكثر من |تفوق |تزيد عن )?(\\d{1,2})\\s*سنوات?",
    "(\\d{1,2})\\s*سنوات?\\s*خبره")]


def required_years(text: str) -> int:
    tn = normalize(text)
    best = 0
    vals = [int(m[1]) for m in _RANGE.finditer(tn)]
    for rx in _CTX:
        vals += [int(m[1]) for m in rx.finditer(tn)]
    for v in vals:
        if 1 <= v <= 15:      # 16 et plus = un age (« 18 a 30 ans »), pas une experience
            best = max(best, v)
    return best


# ----------------------------------------------------------------------------- notation

_CONTEXT_ONLY = {"safex", "salon", "stand", "evenementiel", "evenement", "sila"}


def score_offer(text: str, date: str, rules: dict, now: datetime) -> dict:
    """Meme principe que filtering.score_post du bot (+2 evenementiel, penalite d'experience)."""
    tn = normalize(text)
    res = {"score": 0, "reasons": [], "scam": [], "event": False, "years": 0}
    for w in rules["exclude"]:
        if has_word(tn, w) if _LATIN.fullmatch(normalize(w)) else has(tn, w):
            res.update(score=-100, reasons=["exclu: " + w])
            return res
    job_hits = [w for w in rules["job_words"] if has_word(tn, w)]
    role_hits = sorted(((w, p) for w, p in rules["roles"].items() if has_word(tn, w)), key=lambda x: -x[1])
    loc_hits = sorted(((w, p) for w, p in rules["locations"].items() if has_word(tn, w)), key=lambda x: -x[1])
    s = min(len(job_hits), 2) + sum(p for _, p in role_hits[:3])
    if loc_hits:
        s += loc_hits[0][1]
    else:
        far = [w for w in rules["far_cities"] if has_word(tn, w)]
        if far:
            s -= rules["far_penalty"]
            res["reasons"].append("loin: " + far[0])
    if mobiles(text) or emails(text):
        s += 1
    res["scam"] = [w for w in rules["scam_flags"] if has(tn, w)]
    if res["scam"]:
        s -= 2
    only_context = bool(role_hits) and all(normalize(w) in _CONTEXT_ONLY for w, _ in role_hits)
    if not job_hits and (not role_hits or only_context):
        res.update(score=-100, reasons=["annonce sans recrutement"])
        return res
    res["event"] = is_event(text)
    if res["event"]:
        s += 2
    res["years"] = required_years(text)
    if res["years"] >= 5:      # experience exigee : l'offre reste visible (drapeau « senior ») mais descend
        s -= 4
    elif res["years"] >= 3:
        s -= 2
    age = age_days(date, now)
    if age is not None and age > rules["max_age_days"]:
        res.update(score=-50, reasons=[f"ancien ({age:.0f} j)"])
        return res
    res["score"] = max(0, min(10, s))
    res["reasons"] += [w for w, _ in role_hits[:3]] + [w for w, _ in loc_hits[:1]]
    return res




# ----------------------------------------------------------------------------- extraction (BeautifulSoup)

BLOCK = ("article, li, [class*=card], [class*=Card], [class*=offer], [class*=offre], [class*=annonce], "
         "[class*=job], [class*=Job], [class*=item], [class*=post]")


def _soup(html: str):
    from bs4 import BeautifulSoup
    return BeautifulSoup(html, "html.parser")


def extract_links(html: str, base: str, contains: str) -> list[dict]:
    out, seen = [], set()
    for a in _soup(html).find_all("a", href=True):
        if contains not in a["href"]:
            continue
        text = clean_text(a.get_text(" ", strip=True))
        url = urljoin(base, str(a["href"]))
        if len(text) < 40 or url in seen:
            continue
        seen.add(url)
        m = DATE_RE.search(text)
        out.append({"text": text, "url": url, "date": m.group(0) if m else ""})
    return out


def extract_blocks(html: str, base: str) -> list[dict]:
    soup = _soup(html)
    for bad in soup(["script", "style", "noscript", "svg", "header", "footer", "nav", "form"]):
        bad.decompose()
    cands = [(el, clean_text(el.get_text(" ", strip=True))) for el in soup.select(BLOCK)]
    cands = [(el, t) for el, t in cands if 40 <= len(t) <= 2000]
    ids = {id(el) for el, _ in cands}
    out, seen = [], set()
    for el, text in cands:
        if any(id(c) in ids for c in el.find_all(True)):
            continue
        if text[:120] in seen:
            continue
        seen.add(text[:120])
        link = el if el.name == "a" else el.find("a", href=True)
        href = urljoin(base, str(link["href"])) if link and link.get("href") else base
        m = DATE_RE.search(text)
        out.append({"text": text, "url": href, "date": m.group(0) if m else ""})
    return out


def parse_telegram(html: str, base: str) -> list[dict]:
    out = []
    for msg in _soup(html).select(".tgme_widget_message"):
        body = msg.select_one(".tgme_widget_message_text")
        if not body:
            continue
        link = msg.select_one("a.tgme_widget_message_date")
        t = msg.select_one("time[datetime]")
        out.append({"text": clean_text(body.get_text(" ", strip=True)),
                    "url": link["href"] if link and link.get("href") else base, "date": t["datetime"] if t else ""})
    return out[::-1]


def real_url(href: str) -> str:
    if href.startswith("//"):
        href = "https:" + href
    if "duckduckgo.com/l/" in href:
        q = parse_qs(urlparse(href).query).get("uddg")
        if q:
            return unquote(q[0])
    return href


def parse_ddg(html: str) -> list[dict]:
    out = []
    for res in _soup(html).select(".result"):
        if "result--ad" in (res.get("class") or []):
            continue
        a = res.select_one("a.result__a")
        if not a:
            continue
        snip = res.select_one(".result__snippet")
        text = clean_text(a.get_text(" ", strip=True)) + " — " + clean_text(snip.get_text(" ", strip=True) if snip else "")
        m = DATE_RE.search(text)
        out.append({"text": text, "url": real_url(str(a.get("href", ""))), "date": m.group(0) if m else ""})
    return out


# ----------------------------------------------------------------------------- collecte


def fetch(url: str, timeout: int = 25) -> str:
    import requests
    r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "fr-FR,fr;q=0.9,ar;q=0.8,en;q=0.7"}, timeout=timeout)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or r.encoding
    return r.text


def collect_source(kind: str, src: dict) -> list[dict]:
    if kind == "page":
        html = fetch(src["url"])
        cards = extract_links(html, src["url"], src["link_contains"]) if src.get("link_contains") else extract_blocks(html, src["url"])
        return [dict(c, platform="web") for c in cards]
    if kind == "telegram":
        url = f"https://t.me/s/{src['channel']}"
        return [dict(c, platform="telegram") for c in parse_telegram(fetch(url), url)]
    if kind == "search":
        url = "https://html.duckduckgo.com/html/?q=" + quote_plus(src["query"]) + "&kl=fr-fr&df=w"
        return [dict(c, platform="search") for c in parse_ddg(fetch(url))]
    raise ValueError(kind)


def uid_of(item: dict) -> str:
    key = item["url"] if item["url"] and not item["url"].rstrip("/").endswith(("t.me", "emploitic.com")) else ""
    base = key or re.sub(r"[^\w]+", "", normalize(item["text"]))[:300]
    return hashlib.sha1(base.encode("utf8")).hexdigest()


def reject_reason(item: dict, rules: dict, now: datetime) -> str:
    """Pourquoi une carte n'est pas gardee ("" = gardee). Sert au diagnostic dans status.json."""
    text = item.get("text", "")
    if len(text) < 25:
        return "texte trop court"
    r = score_offer(text, item.get("date", ""), rules, now)
    if r["scam"]:
        return "arnaque: " + r["scam"][0]
    if r["score"] < 0:
        return (r["reasons"] or ["exclu"])[0].split(":")[0]
    if r["score"] < rules["min_score"]:
        return f"note < {rules['min_score']}"
    return ""


def build_record(item: dict, source_name: str, rules: dict, now: datetime) -> dict | None:
    text = item["text"]
    if len(text) < 25:
        return None
    r = score_offer(text, item.get("date", ""), rules, now)
    if r["score"] < rules["min_score"] or r["scam"]:
        return None
    phones = mobiles(text)
    return {
        "uid": uid_of(item), "found_at": now.strftime("%Y-%m-%dT%H:%M"), "source": source_name, "platform": item["platform"],
        "score": r["score"], "url": item["url"], "phones": ", ".join(phones), "emails": ", ".join(emails(text)),
        "text": text[:1500], "status": "new", "salary": "",
        "whatsapp_url": ("https://wa.me/213" + re.sub(r"\D", "", phones[0])[1:]) if phones else "",
        "gig_badge": "\U0001F3AA معارض وأحداث" if r["event"] else "",
        "event": r["event"], "years_required": r["years"], "senior": r["years"] >= 3,
    }


def merge(old: list[dict], new: list[dict], now: datetime) -> list[dict]:
    """Garde 14 jours, une entree par uid (la date de premiere vue est conservee), meilleures d'abord, 300 au plus."""
    by_uid = {o["uid"]: o for o in old if o.get("uid")}
    for n in new:
        prev = by_uid.get(n["uid"])
        if prev:
            n["found_at"] = prev.get("found_at", n["found_at"])
        by_uid[n["uid"]] = n
    cutoff = (now - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%dT%H:%M")
    kept = [o for o in by_uid.values() if str(o.get("found_at", "")) >= cutoff]
    kept.sort(key=lambda o: str(o.get("found_at", "")), reverse=True)                       # le plus recent d'abord...
    kept.sort(key=lambda o: (-int(bool(o.get("event"))), -int(o.get("score", 0))))           # ...puis evenementiel, puis note
    return kept[:MAX_OFFERS]


def run(now: datetime | None = None, only: str = "", verbose: bool = False) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    now = now or datetime.now(ALGIERS)
    rules = json.loads((HERE / "rules.json").read_text(encoding="utf8"))
    sources = json.loads((HERE / "sources.json").read_text(encoding="utf8"))
    DATA.mkdir(exist_ok=True)
    old_path = DATA / "offers.json"
    legacy = json.loads(old_path.read_text(encoding="utf8")) if old_path.exists() else []
    # Ancien format (sans champ « event ») : retire en vrai, il sera recollecte proprement ; texte toujours nettoye.
    old = [dict(o, text=clean_text(o.get("text", ""))) for o in legacy if "event" in o]
    plan = ([("page", s, s["name"]) for s in sources["pages"]] + [("telegram", s, s["name"]) for s in sources["telegram"]] +
            [("search", s, s["name"]) for s in sources["searches"]])
    new, status = [], []
    if only:
        plan = [p for p in plan if p[1]["id"] == only]
    for kind, src, name in plan:
        entry = {"id": src["id"], "name": name, "kind": kind, "ok": False, "cards": 0, "kept": 0, "error": ""}
        if src.get("enabled", True) is False:
            entry["error"] = "desactivee: " + src.get("note", "voir sources.json")
            entry["skipped"] = True
            status.append(entry)
            continue
        try:
            cards = collect_source(kind, src)
            entry["cards"] = len(cards)
            recs = [r for r in (build_record(c, name, rules, now) for c in cards) if r]
            entry["kept"] = len(recs)
            why: dict[str, int] = {}
            for c in cards:
                w = reject_reason(c, rules, now)
                if w:
                    why[w] = why.get(w, 0) + 1
            if why:
                entry["rejected"] = dict(sorted(why.items(), key=lambda kv: -kv[1]))
            if verbose:
                for c in cards:
                    w = reject_reason(c, rules, now) or "GARDEE"
                    print(f"[{w}] {c.get('date', '')} | {c.get('text', '')[:180]}")
            entry["ok"] = len(cards) > 0
            if not cards:
                entry["error"] = "aucun element lu (site change ou requete bloquee)"
            new += recs
        except Exception as e:  # noqa: BLE001 - une source en panne ne bloque pas les autres
            entry["error"] = f"{type(e).__name__}: {str(e)[:150]}"
            log.warning("%s: %s", name, entry["error"])
        status.append(entry)
        time.sleep(random.uniform(*PAUSE))
    if only:
        # Diagnostic d'une seule source : n'ecrit rien dans data/
        print(json.dumps(status, ensure_ascii=False, indent=1))
        return 0
    merged = merge(old, new, now)
    if not any(s["ok"] for s in status if not s.get("skipped")):
        log.error("aucune source n'a repondu : offers.json conserve tel quel")
        merged = legacy
    old_path.write_text(json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf8")
    (DATA / "status.json").write_text(json.dumps({
        "generated_at": now.strftime("%Y-%m-%dT%H:%M"), "offers": len(merged),
        "events": sum(1 for o in merged if o.get("event")), "sources": status,
    }, ensure_ascii=False, indent=1), encoding="utf8")
    log.info("%d offres (%d nouvelles), %d sources ok sur %d", len(merged), len(new), sum(s["ok"] for s in status), len([s for s in status if not s.get("skipped")]))
    return 0


if __name__ == "__main__":
    # python tools/collect.py                 -> collecte complete (GitHub Actions)
    # python tools/collect.py --only sfnemploi -v  -> diagnostic d'une source, rien n'est ecrit
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="id d'une source (voir sources.json)")
    ap.add_argument("-v", "--verbose", action="store_true", help="affiche chaque carte et la raison du rejet")
    a = ap.parse_args()
    sys.exit(run(only=a.only, verbose=a.verbose))
