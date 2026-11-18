"""Radar des salons d'Alger (M11.1) : lit algeriaexhibitions.dz (filiale du groupe SAFEX) et ecrit data/salons.json.

Pour chaque manifestation en cours ou a venir (30 jours) : nom, dates, pavillon, organisateur, ses telephones et son e-mail,
site officiel, lien d'inscription des visiteurs. Un `hostess_score` (0-10) estime l'interet pour un animateur / hotesse :
grand public (mariage, beaute, auto, alimentation, tourisme, livre, ameublement) 8-10, professionnel (industrie, finance,
electricite) 4-6. Lecture seule, donnees publiques de l'organisateur ; rien de personnel n'est ecrit.

    python tools/salons.py            -> met a jour data/salons.json (+ alerte Telegram pour chaque nouveau salon)
    python tools/collect.py --salons  -> meme chose
"""
from __future__ import annotations

import html as _html
import json
import logging
import os
import re
import sys
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
log = logging.getLogger("salons")

BASE = "https://www.algeriaexhibitions.dz"
UA = "JobRadarBot/1.0 (+https://mirouamar97.github.io/jobradar-online/)"
ALGIERS = timezone(timedelta(hours=1))
WINDOW_DAYS = 30
PAUSE = 1.0                      # secondes entre deux pages (les tests n'appellent pas le reseau)
ALERT_CAP = 5                    # au plus 5 alertes par passage
SAFEX_GENERIC = {"event@safex.dz"}

MONTHS = {
    "janvier": 1, "janv": 1, "jan": 1, "fevrier": 2, "fevr": 2, "fev": 2, "mars": 3, "avril": 4, "avr": 4, "mai": 5,
    "juin": 6, "juillet": 7, "juil": 7, "aout": 8, "septembre": 9, "sept": 9, "sep": 9, "octobre": 10, "oct": 10,
    "novembre": 11, "nov": 11, "decembre": 12, "dec": 12,
}


def norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


# ----------------------------------------------------------------------------- dates


def _part(p: str) -> tuple[int, int | None, int | None] | None:
    m = re.search(r"(\d{1,2})(?:er)?\s*([a-z]+)?\.?\s*(\d{4})?", p.strip())
    if not m:
        return None
    month = MONTHS.get(m.group(2) or "") if m.group(2) else None
    return int(m.group(1)), month, int(m.group(3)) if m.group(3) else None


def parse_dates(text: str) -> tuple[str, str]:
    """« 16–18 novembre 2026 », « 28 oct. – 7 nov. 2026 », « Du 17 déc. au 26 déc. 2026 » -> ('2026-11-16', '2026-11-18')."""
    t = norm(text).replace("—", "–").replace(" au ", " – ")
    t = re.sub(r"^\s*du\s+", "", t)
    parts = [p for p in re.split(r"\s*[–-]\s*", t) if p.strip()]
    parsed = [_part(p) for p in parts[:2]]
    if not parsed:
        return "", ""
    start, end = parsed[0], parsed[-1]
    if start is None or end is None:
        return "", ""
    end_month, end_year = end[1], end[2]
    if end_month is None or end_year is None:
        return "", ""
    start_month = start[1] or end_month
    start_year = start[2]
    if start_year is None:
        start_year = end_year - 1 if start_month > end_month else end_year
    try:
        d1 = date(start_year, start_month, start[0])
        d2 = date(end_year, end_month, end[0])
    except ValueError:
        return "", ""
    return d1.isoformat(), d2.isoformat()


# ----------------------------------------------------------------------------- scoring


def _kw_hits(text: str, words: list[str]) -> int:
    t = norm(text)
    n = 0
    for w in words:
        w = norm(w)
        if not w:
            continue
        if re.search(r"[\u0600-\u06ff]", w):
            hit = re.search(rf"(?<![\u0600-\u06ff])(?:ال)?{re.escape(w)}(?![\u0600-\u06ff])", t) is not None
        else:
            hit = re.search(rf"(?<![a-z]){re.escape(w)}(?:s|x)?(?![a-z])", t) is not None
        n += 1 if hit else 0
    return n


def hostess_score(title: str, sectors: str, rules: dict) -> int:
    """8-10 grand public, 4-6 professionnel, 5 si inconnu ; 6-7 si les deux (mixte)."""
    kw = rules.get("salon_keywords", {})
    text = f"{title} {sectors}"
    wide = _kw_hits(text, kw.get("wide", []))
    pro = _kw_hits(text, kw.get("pro", []))
    if wide and pro:
        return 7 if wide >= pro else 6
    if wide:
        return min(10, 8 + (wide - 1))
    if pro:
        return 4 if pro >= 3 else 5
    return 5


def audience(score: int) -> str:
    return "large public" if score >= 8 else "professionnel" if score <= 5 else "mixte"


# ----------------------------------------------------------------------------- lecture des pages


def _soup(markup: str):
    from bs4 import BeautifulSoup
    return BeautifulSoup(markup, "html.parser")


def event_urls(markup: str) -> set[str]:
    """Liens /manifestations/<slug> d'une page (accueil ou calendrier)."""
    found = set(re.findall(r'href="(https://www\.algeriaexhibitions\.dz/manifestations/[^"#?/]+)"', markup))
    return {u for u in found if not u.endswith("/manifestations")}


def _local_phone(raw: str) -> str:
    d = re.sub(r"\D", "", raw)
    if d.startswith("213") and len(d) >= 11:
        d = "0" + d[3:]
    return d


def _phones_from(text: str) -> list[str]:
    out: list[str] = []
    for chunk in re.split(r"[\s/,;|]+|(?<=\d)-(?=\d)", text):
        d = _local_phone(chunk)
        if 8 <= len(d) <= 10 and d.startswith("0"):
            out.append(d)
    return out


def _in_contact(tag) -> bool:
    for parent in tag.parents:
        cls = parent.get("class") if hasattr(parent, "get") else None
        if cls and any(c.startswith("evp-contact") for c in cls):
            return True
    return False


def parse_event(markup: str, url: str) -> dict:
    s = _soup(markup)
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    main = s.select_one("h1 .evp-title__main") or s.find("h1")
    name = main.get_text(" ", strip=True) if main else slug
    og = s.find("meta", attrs={"property": "og:title"})
    og_content = og.get("content") if og else None
    title = _html.unescape(str(og_content)) if og_content else name
    title = re.sub(rf"\s*[—-]\s*{re.escape(name)}\s*$", "", title).strip() or name

    when = s.select_one(".evp-when strong")
    start, end = parse_dates(when.get_text(" ", strip=True) if when else "")
    pav = ""
    for item in s.select(".evp-when__item"):
        if "Pavillon" in item.get_text():
            a = item.find("a")
            pav = a.get_text(" ", strip=True) if a else ""
    hours = ""
    for item in s.select(".evp-meta__item"):
        if "Horaires" in item.get_text():
            hours = item.get_text(" ", strip=True).replace("Horaires :", "").strip()

    place = ""
    for dt in s.find_all("dt"):
        if "Adresse" in dt.get_text():
            dd = dt.find_next("dd")
            place = dd.get_text(" ", strip=True) if dd else ""
            break

    org = s.select_one(".evp-contact__name")
    organizer = org.get_text(" ", strip=True) if org else ""

    phones: list[str] = []
    emails: list[str] = []
    for val in s.select(".evp-contact__value"):
        phones += _phones_from(val.get_text(" ", strip=True))
    for a in s.select('a[href^="tel:"], a[href^="mailto:"]'):
        if not _in_contact(a):
            continue
        href = str(a["href"])
        if href.startswith("tel:"):
            if not phones:
                phones += _phones_from(href[4:])
        else:
            mail = href[7:].split("?")[0].strip().lower()
            if mail and mail not in SAFEX_GENERIC and "@" in mail:
                emails.append(mail)
    phones = list(dict.fromkeys(phones))
    emails = list(dict.fromkeys(emails))

    website = ""
    register = ""
    for a in s.find_all("a"):
        label = a.get_text(" ", strip=True)
        href = str(a.get("href") or "")
        if not website and label.startswith("Site officiel"):
            website = href
        if not register and a.get("data-registration") == "visitor" and href.startswith("http"):
            register = href
    sectors = ", ".join(c.get_text(strip=True) for c in s.select(".evp-sectors .evp-chip"))
    score = hostess_score(f"{name} {title}", sectors, _rules())
    return {
        "id": slug.lower(), "name": name, "title": title, "start": start, "end": end, "place": place or "SAFEX, Pins Maritimes, Alger",
        "pavilion": pav, "hours": hours, "organizer": organizer, "phones": phones, "emails": emails, "website": website,
        "register_url": register, "source_url": url, "sectors": sectors, "audience": audience(score), "hostess_score": score,
    }


_RULES: dict | None = None


def _rules() -> dict:
    global _RULES
    if _RULES is None:
        _RULES = json.loads((HERE / "rules.json").read_text(encoding="utf-8"))
    return _RULES


# ----------------------------------------------------------------------------- fenetre et alertes


def in_window(items: list[dict], today: date, days: int = WINDOW_DAYS) -> list[dict]:
    """Salons en cours (y compris le dernier jour) ou qui commencent dans `days` jours, tries par date."""
    limit = today + timedelta(days=days)
    keep = []
    for i in items:
        try:
            start, end = date.fromisoformat(i["start"]), date.fromisoformat(i["end"])
        except (KeyError, ValueError):
            continue
        if end >= today and start <= limit:
            keep.append(i)
    return sorted(keep, key=lambda i: (i["start"], i["id"]))


def alert_text(e: dict) -> str:
    lines = [f"🎪 معرض جديد قريب — {e.get('name', '')}", str(e.get("title", "")),
             f"📅 {e.get('start', '')} → {e.get('end', '')} · {e.get('pavilion', '')}",
             f"👥 {e.get('audience', '')} ({e.get('hostess_score', 0)}/10)"]
    if e.get("organizer"):
        lines.append("المنظّم: " + e["organizer"])
    if e.get("phones"):
        lines.append("هاتف: " + " / ".join(e["phones"]))
    if e.get("emails"):
        lines.append("بريد: " + " / ".join(e["emails"]))
    if e.get("register_url"):
        lines.append("تسجيل الزوار: " + e["register_url"])
    return "\n".join(lines)[:3900]


def alert_new_salons(items: list[dict], seen: list, token: str, chat_id: str, sender, first_run: bool = False) -> int:
    """Une alerte Telegram par salon jamais signale (cle `salon:<id>` dans `seen`, qui est modifie).

    Une cle n'est memorisee que si l'alerte est partie, ou si on n'envoie rien volontairement (1er passage, pas de secrets) ;
    un echec Telegram ou le plafond par passage laissent le salon pour le passage suivant.
    """
    sent = 0
    silent = first_run or not (token and chat_id)
    for e in items:
        key = f"salon:{e['id']}"
        if key in seen:
            continue
        if silent:
            seen.append(key)
        elif sent < ALERT_CAP and sender(token, chat_id, alert_text(e)) == "":
            seen.append(key)
            sent += 1
    return sent


# ----------------------------------------------------------------------------- reseau


def fetch(url: str, timeout: int = 25) -> str:
    import requests
    r = requests.get(url, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml",
                                   "Accept-Language": "fr-FR,fr;q=0.9"}, timeout=timeout)
    r.raise_for_status()
    r.encoding = "utf-8"
    return r.text


def calendar_urls(today: date) -> list[str]:
    nxt = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
    return [BASE, f"{BASE}/calendrier?view=month&month={today.month}&year={today.year}",
            f"{BASE}/calendrier?view=month&month={nxt.month}&year={nxt.year}"]


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf8"))
    except (OSError, ValueError):
        return default


def _write_json(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf8")
    os.replace(tmp, path)


def run_salons(now: datetime | None = None, fetcher=fetch, sender=None, pause: float = PAUSE, force: bool = False) -> int:
    """Met a jour data/salons.json. Au plus un passage (reussi ou non) par jour, sauf `force` ou SALONS_FORCE=1."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    now = now or datetime.now(ALGIERS)
    today = now.date()
    DATA.mkdir(exist_ok=True)
    out = DATA / "salons.json"
    meta_path = DATA / "salons_meta.json"
    meta = _read_json(meta_path, {})
    if not (force or os.environ.get("SALONS_FORCE")) and today.isoformat() in (meta.get("fetched"), meta.get("attempted")):
        log.info("salons deja traites aujourd'hui")
        return 0
    first_run = not out.exists()
    previous = {e.get("source_url"): e for e in _read_json(out, []) if isinstance(e, dict)}

    urls: set[str] = set()
    for u in calendar_urls(today):
        try:
            urls |= event_urls(fetcher(u))
        except Exception as e:  # noqa: BLE001
            log.warning("calendrier %s: %s", u, type(e).__name__)
        time.sleep(pause)
    events: list[dict] = []
    failed = 0
    for u in sorted(urls):
        try:
            e = parse_event(fetcher(u), u)
            if not (e["start"] and e["end"]):
                raise ValueError("dates illisibles")
            events.append(e)
        except Exception as ex:  # noqa: BLE001 - une page en panne ne bloque pas les autres
            failed += 1
            log.warning("%s: %s", u, type(ex).__name__)
            if u in previous:           # on garde la derniere version connue plutot que de faire disparaitre le salon
                events.append(previous[u])
        time.sleep(pause)
    if not events:
        log.error("aucun salon lu (site indisponible ou page modifiee) : salons.json conserve tel quel")
        _write_json(meta_path, dict(meta, attempted=today.isoformat()))
        return 1
    window = in_window(events, today)
    _write_json(out, window)
    _write_json(meta_path, {"fetched": today.isoformat(), "pages": len(urls), "failed": failed})
    path = DATA / "alerted_salons.json"      # memoire propre aux salons (alerted.json est tronque par les offres)
    seen = _read_json(path, [])
    if sender is None:
        from collect import send_telegram as sender
    n = alert_new_salons(window, seen, os.environ.get("TG_BOT_TOKEN", ""), os.environ.get("TG_CHAT_ID", ""), sender, first_run)
    _write_json(path, seen[-500:])
    log.info("%d salons (%d alertes, %d pages en echec)", len(window), n, failed)
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(HERE))
    sys.exit(run_salons())
