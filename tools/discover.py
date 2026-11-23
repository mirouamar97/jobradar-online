#!/usr/bin/env python3
"""JobRadar Online - decouverte hebdomadaire de canaux Telegram PUBLICS (M10.4).

Sources (aucun compte, aucune API privee, aucune connexion) :
  * les liens t.me/<nom> presents dans data/offers.json ;
  * les pages d'apercu publiques https://t.me/s/<nom> des canaux deja connus (mentions, transferts).

Evaluation : les ~30 derniers messages de https://t.me/s/<nom>, notes par collect.score_offer (meme logique que le
collecteur, importee et non copiee). relevance = part des messages dont la note >= rules["min_score"].

Sortie : data/candidates.json (schema fixe, lu par l'appli) ; avec --update-sources, les canaux « promoted » sont
ajoutes a la liste Telegram de tools/sources.json (le workflow en fait une PR, jamais un push direct sur main).

Securite : le texte des canaux est une donnee NON FIABLE. Il n'est jamais execute ni injecte dans un shell ou du YAML ;
seuls des noms valides ^[A-Za-z0-9_]{5,32}$ sont gardes ; seules des URL https://t.me/s/<nom valide> sont demandees,
sans suivre de redirection, avec une taille de reponse plafonnee.
"""
from __future__ import annotations

import html as htmllib
import json
import logging
import os
import random
import re
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from collect import parse_telegram, score_offer  # noqa: E402  (reutilise la notation existante)

ROOT = HERE.parent
DATA = ROOT / "data"
log = logging.getLogger("discover")

# ----------------------------------------------------------------------------- reglages

USER_AGENT = "JobRadarDiscover/1.0 (+https://github.com/mirouamar97/jobradar-online; public channel previews only)"
MIN_INTERVAL = 2.0           # secondes minimum entre deux requetes (plus un petit alea)
TIMEOUT = 20                 # secondes
MAX_BYTES = 1_500_000        # taille maximale lue par reponse
MAX_CHANNELS = 25            # canaux evalues par passage
MAX_POSTS = 30               # messages evalues par canal
MAX_SEED_PAGES = 15          # canaux connus lus pour trouver des mentions
SEED_EXTRA_PAGES = 2         # pages plus anciennes lues en plus de la derniere, par canal connu
MAX_CANDIDATES = 500         # taille maximale de data/candidates.json
GLOBAL_DEADLINE_S = 12 * 60  # arret propre avant le timeout du workflow (20 min) : le fichier est ecrit
EVAL_MAX_AGE_DAYS = 30       # fenetre d'evaluation (le collecteur, lui, ne garde que 7 jours)

PROMOTE_RELEVANCE = 0.25
PROMOTE_MIN_POSTS = 10
REJECT_COOLDOWN_DAYS = 30

STATUSES = ("candidate", "rejected", "promoted")

# ----------------------------------------------------------------------------- noms et liens

NAME_RE = re.compile(r"^[A-Za-z0-9_]{5,32}$")
# Chemins reserves de t.me (pas des canaux) ; « c », « s », « + » sont deja exclus par la longueur ou le motif.
RESERVED = frozenset({
    "joinchat", "addstickers", "addemoji", "addtheme", "addlist", "setlanguage", "share", "proxy", "socks",
    "login", "confirmphone", "contact", "boost", "invoice", "giftcode", "iv", "telegram", "durov",
})
_LINK_RE = re.compile(
    r"(?<![A-Za-z0-9.\-/@])(?:https?://)?(?:www\.)?(?:t|telegram)\.me/(?:s/)?([A-Za-z0-9_]+)(?![A-Za-z0-9_+\-])",
    re.IGNORECASE)


def valid_name(name: str) -> bool:
    """Nom de canal public acceptable : motif strict, pas un chemin reserve, pas un bot."""
    return (isinstance(name, str) and NAME_RE.fullmatch(name) is not None
            and name.lower() not in RESERVED and not name.lower().endswith("bot"))


def extract_channel_names(text: str) -> list[str]:
    """Noms de canaux cites dans un texte/HTML (liens t.me, telegram.me, t.me/s/), sans doublon (casse ignoree)."""
    out: list[str] = []
    seen: set[str] = set()
    for m in _LINK_RE.finditer(text or ""):
        name = m.group(1)
        if valid_name(name) and name.lower() not in seen:
            seen.add(name.lower())
            out.append(name)
    return out


# Mentions « @nom » dans le texte des messages. Pas d'e-mail (caractere avant @), pas de nom trop court ou de bot.
_MENTION_RE = re.compile(r"(?<![A-Za-z0-9_./@+\-])@([A-Za-z0-9_]{5,32})(?![A-Za-z0-9_@])")
_TAG_RE = re.compile(r"<[^>]*>")
_BLOCK_RE = re.compile(r"(?is)<(script|style)\b.*?</\1\s*>")
# Mots CSS/JS qui ressemblent a « @nom » (@media, @keyframes...) : jamais des canaux.
_NOT_MENTIONS = frozenset({"media", "import", "keyframes", "charset", "supports", "font", "fontface", "namespace", "layer",
                           "container", "property", "viewport", "document", "page", "counter", "scope", "starting"})


def extract_mentions(html: str) -> list[str]:
    """Noms de canaux cites en texte brut (« @nom ») dans une page d'apercu, sans doublon (casse ignoree)."""
    text = htmllib.unescape(_TAG_RE.sub(" ", _BLOCK_RE.sub(" ", html or "")))
    out: list[str] = []
    seen: set[str] = set()
    for m in _MENTION_RE.finditer(text):
        name = m.group(1)
        if valid_name(name) and name.lower() not in _NOT_MENTIONS and name.lower() not in seen:
            seen.add(name.lower())
            out.append(name)
    return out


def names_in_page(html: str) -> list[str]:
    """Liens t.me puis mentions @nom d'une page d'apercu (les vraies pages ne lient presque jamais d'autres canaux)."""
    seen: set[str] = set()
    out: list[str] = []
    for n in extract_channel_names(html) + extract_mentions(html):
        if n.lower() not in seen:
            seen.add(n.lower())
            out.append(n)
    return out


def load_seeds(path: Path) -> list[str]:
    """Graines facultatives fournies par le proprietaire (tools/discovery_seeds.json) : {"telegram": ["nom", ...]}."""
    try:
        raw = json.loads(path.read_text(encoding="utf8"))
    except (OSError, json.JSONDecodeError):
        return []
    names = raw.get("telegram", []) if isinstance(raw, dict) else []
    out: list[str] = []
    for n in names if isinstance(names, list) else []:
        if isinstance(n, str) and valid_name(n) and n.lower() not in {o.lower() for o in out}:
            out.append(n)
    return out


def preview_url(name: str, before: int | None = None) -> str:
    if not valid_name(name):
        raise ValueError("nom de canal invalide")
    url = f"https://t.me/s/{name}"
    if before is not None:
        url += f"?before={int(before)}"
    return url


def candidate_id(name: str) -> str:
    return "tg:" + name.lower()


# ----------------------------------------------------------------------------- reseau (poli)


class FetchError(Exception):
    """Echec reseau ou HTTP inattendu : le canal n'est pas juge, il sera retente au prochain passage."""


@dataclass
class Throttle:
    """Au moins [interval] secondes (+ alea) entre deux requetes."""
    interval: float = MIN_INTERVAL
    jitter: float = 1.0
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    _last: float | None = field(default=None, init=False)

    def wait(self) -> None:
        if self._last is not None:
            delay = self.interval + random.uniform(0, self.jitter) - (self.clock() - self._last)
            if delay > 0:
                self.sleep(delay)
        self._last = self.clock()


def http_get(url: str, throttle: Throttle, session=None) -> str | None:
    """GET poli d'une page d'apercu t.me. Renvoie None si le canal n'a pas d'apercu public (redirection, 404)."""
    if not url.startswith("https://t.me/s/"):
        raise ValueError("seules les pages https://t.me/s/ sont autorisees")
    import requests
    throttle.wait()
    getter = session or requests
    try:
        r = getter.get(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "fr,ar;q=0.8,en;q=0.5"},
                       timeout=TIMEOUT, allow_redirects=False, stream=True)
    except requests.RequestException as e:
        raise FetchError(type(e).__name__) from e
    try:
        if r.status_code in (301, 302, 303, 307, 308, 404):
            return None          # utilisateur, bot, groupe prive ou nom inexistant : pas d'apercu public
        if r.status_code != 200:
            raise FetchError(f"HTTP {r.status_code}")
        buf = bytearray()
        for chunk in r.iter_content(chunk_size=65536):
            buf += chunk
            if len(buf) >= MAX_BYTES:
                break
        return bytes(buf[:MAX_BYTES]).decode("utf-8", errors="replace")
    finally:
        r.close()


Fetcher = Callable[[str], "str | None"]

# ----------------------------------------------------------------------------- evaluation

_POST_ID_RE = re.compile(r'data-post="[A-Za-z0-9_]{1,64}/(\d{1,12})"')


def oldest_post_id(html: str) -> int | None:
    ids = [int(x) for x in _POST_ID_RE.findall(html or "")]
    return min(ids) if ids else None


def eval_rules(rules: dict) -> dict:
    """Memes regles que le collecteur, mais fenetre d'age de 30 jours pour juger un canal."""
    return dict(rules, max_age_days=max(int(rules.get("max_age_days", 7)), EVAL_MAX_AGE_DAYS))


def compute_relevance(posts: Iterable[dict], rules: dict, now: datetime) -> tuple[float, int]:
    """(part des messages notes >= min_score, nombre de messages). Messages : {"text", "date"}."""
    posts = [p for p in posts if (p.get("text") or "").strip()]
    if not posts:
        return 0.0, 0
    threshold = rules["min_score"]
    good = sum(1 for p in posts if score_offer(p["text"], p.get("date", ""), rules, now)["score"] >= threshold)
    return round(good / len(posts), 3), len(posts)


def decide_status(relevance: float, posts: int) -> str:
    if posts >= PROMOTE_MIN_POSTS and relevance >= PROMOTE_RELEVANCE:
        return "promoted"
    if posts == 0 or posts >= PROMOTE_MIN_POSTS:
        return "rejected"        # pas d'apercu public, ou assez de messages et trop peu d'offres
    return "candidate"           # trop peu de messages pour juger : on reessaie la semaine suivante


@dataclass
class Evaluation:
    name: str
    posts: int
    relevance: float
    status: str
    pages: list[str]


def evaluate_channel(name: str, fetch: Fetcher, rules: dict, now: datetime) -> Evaluation:
    """Lit jusqu'a MAX_POSTS messages (2 pages au plus) et calcule la pertinence. Peut lever FetchError."""
    pages: list[str] = []
    html = fetch(preview_url(name))
    if html is None:
        return Evaluation(name, 0, 0.0, "rejected", pages)
    pages.append(html)
    posts = parse_telegram(html, preview_url(name))
    before = oldest_post_id(html)
    if len(posts) < MAX_POSTS and before and before > 1:
        older = fetch(preview_url(name, before))
        if older:
            pages.append(older)
            posts += parse_telegram(older, preview_url(name))
    relevance, n = compute_relevance(posts[:MAX_POSTS], eval_rules(rules), now)
    return Evaluation(name, n, relevance, decide_status(relevance, n), pages)


# ----------------------------------------------------------------------------- etat (candidates.json)


def iso(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def make_candidate(name: str, now: datetime) -> dict:
    return {"id": candidate_id(name), "platform": "telegram", "name": name, "url": preview_url(name),
            "relevance": 0.0, "posts": 0, "first_seen": iso(now), "last_checked": iso(now), "status": "candidate"}


def sanitize_candidate(c: dict) -> dict | None:
    """Recharge une entree existante en ne gardant que le schema fixe (entree invalide = ignoree)."""
    name = c.get("name") if isinstance(c, dict) else None
    if not valid_name(name or "") or c.get("status") not in STATUSES:
        return None
    try:
        rel, posts = float(c.get("relevance", 0)), int(c.get("posts", 0))
    except (TypeError, ValueError):
        return None
    return {"id": candidate_id(name), "platform": "telegram", "name": name, "url": preview_url(name),
            "relevance": max(0.0, min(1.0, rel)), "posts": max(0, posts),
            "first_seen": str(c.get("first_seen", "")), "last_checked": str(c.get("last_checked", "")),
            "status": c["status"]}


def should_evaluate(c: dict, now: datetime) -> bool:
    """Promu : deja propose. Rejete il y a moins de 30 jours : on ne le re-evalue pas."""
    if c["status"] == "promoted":
        return False
    if c["status"] == "rejected":
        checked = parse_iso(c.get("last_checked", ""))
        return checked is None or now - checked >= timedelta(days=REJECT_COOLDOWN_DAYS)
    return True


def apply_evaluation(c: dict, ev: Evaluation, now: datetime) -> dict:
    return dict(c, relevance=ev.relevance, posts=ev.posts, status=ev.status, last_checked=iso(now))


def load_candidates(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf8"))
    except (OSError, json.JSONDecodeError):
        log.warning("candidates.json illisible : on repart de zero")
        return {}
    out: dict[str, dict] = {}
    for c in raw.get("candidates", []) if isinstance(raw, dict) else []:
        s = sanitize_candidate(c)
        if s:
            out[s["id"]] = s
    return out


def dump_candidates(cands: dict[str, dict], now: datetime) -> dict:
    order = {"promoted": 0, "candidate": 1, "rejected": 2}
    # Vie privee : un nom sans apercu public (compte personnel, bot, groupe prive) n'est jamais publie dans ce fichier public.
    cands = {k: c for k, c in cands.items() if not (c["status"] == "rejected" and c["posts"] == 0)}
    items = sorted(cands.values(), key=lambda c: (order[c["status"]], -c["relevance"], c["id"]))
    if len(items) > MAX_CANDIDATES:      # on oublie d'abord les plus vieux rejets
        keep = [c for c in items if c["status"] != "rejected"]
        rej = sorted((c for c in items if c["status"] == "rejected"), key=lambda c: c["last_checked"], reverse=True)
        items = (keep + rej)[:MAX_CANDIDATES]
        items.sort(key=lambda c: (order[c["status"]], -c["relevance"], c["id"]))
    return {"updated": iso(now), "candidates": items}


# ----------------------------------------------------------------------------- sources.json


def known_channels(sources: dict) -> list[str]:
    return [s["channel"] for s in sources.get("telegram", []) if valid_name(s.get("channel", ""))]


def add_promoted_to_sources(sources: dict, cands: dict[str, dict]) -> list[str]:
    """Ajoute a sources["telegram"] chaque canal promu absent. Renvoie les noms ajoutes."""
    tg = sources.setdefault("telegram", [])
    have = {str(s.get("channel", "")).lower() for s in tg}
    ids = {str(s.get("id", "")) for s in sources.get("pages", []) + tg + sources.get("searches", [])}
    added: list[str] = []
    for c in sorted(cands.values(), key=lambda c: c["id"]):
        if c["status"] != "promoted" or c["name"].lower() in have:
            continue
        sid = "tg_" + c["name"].lower()
        if sid in ids:
            continue
        tg.append({"id": sid, "name": f"{c['name']} (Telegram)", "channel": c["name"], "discovered": True})
        ids.add(sid)
        have.add(c["name"].lower())
        added.append(c["name"])
    return added


# ----------------------------------------------------------------------------- passage complet


def names_from_offers(offers: list) -> list[str]:
    blob = "\n".join(f"{o.get('url', '')} {o.get('text', '')}" for o in offers if isinstance(o, dict))
    return extract_channel_names(blob)


def discover(now: datetime, fetch: Fetcher, rules: dict, sources: dict, offers: list,
             cands: dict[str, dict], max_channels: int = MAX_CHANNELS, extra_seeds: Iterable[str] = (),
             expired: Callable[[], bool] = lambda: False) -> dict:
    """Coeur testable (aucun acces disque). Modifie [cands] ; renvoie des statistiques."""
    known = {n.lower() for n in known_channels(sources)}
    stats = {"seed_pages": 0, "discovered": 0, "evaluated": 0, "errors": 0, "promoted": 0, "rejected": 0}
    found: list[str] = names_from_offers(offers) + [n for n in extra_seeds if valid_name(n)]

    seeds = known_channels(sources) + [c["name"] for c in cands.values() if c["status"] == "promoted"]
    for name in seeds[:MAX_SEED_PAGES]:
        before = None
        for _ in range(1 + SEED_EXTRA_PAGES):
            if expired():
                break
            try:
                html = fetch(preview_url(name, before))
            except FetchError as e:
                log.warning("graine %s : %s", name, e)
                stats["errors"] += 1
                break
            if html is None:
                break
            stats["seed_pages"] += 1
            found += names_in_page(html)
            nxt = oldest_post_id(html)
            if not nxt or nxt <= 1 or (before is not None and nxt >= before):    # fin de l'historique ou page qui ne recule pas
                break
            before = nxt

    def is_new(n: str) -> bool:
        return n.lower() not in known and candidate_id(n) not in cands

    queue: list[str] = []
    for n in found:
        if is_new(n) and n.lower() not in {q.lower() for q in queue}:
            queue.append(n)
    stats["discovered"] = len(queue)
    if not queue and stats["seed_pages"]:
        log.warning("rien de nouveau dans %d pages lues (mentions absentes ou deja connues) : ajoutez des graines dans tools/discovery_seeds.json",
                    stats["seed_pages"])
    # Puis les candidats a re-evaluer, le plus ancien controle d'abord.
    retry = sorted((c for c in cands.values() if should_evaluate(c, now) and c["name"].lower() not in known),
                   key=lambda c: c["last_checked"])
    queue += [c["name"] for c in retry]

    done: set[str] = set()
    while queue and stats["evaluated"] < max_channels:
        if expired():
            log.warning("delai global atteint : arret propre, les canaux restants seront juges au prochain passage")
            break
        name = queue.pop(0)
        if name.lower() in done:
            continue
        done.add(name.lower())
        cid = candidate_id(name)
        cur = cands.get(cid) or make_candidate(name, now)
        try:
            ev = evaluate_channel(name, fetch, rules, now)
        except FetchError as e:
            log.warning("%s : %s (sera retente)", name, e)
            stats["errors"] += 1
            if cid not in cands:
                cands[cid] = cur        # garde la trace de la decouverte, statut « candidate »
            continue
        stats["evaluated"] += 1
        cands[cid] = apply_evaluation(cur, ev, now)
        if ev.status in ("promoted", "rejected"):
            stats[ev.status] += 1
        log.info("%s : %d messages, pertinence %.2f -> %s", name, ev.posts, ev.relevance, ev.status)
        for page in ev.pages:           # mentions trouvees chez un candidat : evaluees dans ce passage si la limite le permet
            for n in names_in_page(page):
                if is_new(n) and n.lower() not in done and n.lower() not in {q.lower() for q in queue}:
                    queue.insert(0, n)
                    stats["discovered"] += 1
    return stats


def pr_body(added: list[str], cands: dict[str, dict]) -> str:
    lines = ["Canaux Telegram publics promus automatiquement par `tools/discover.py` "
             f"(pertinence >= {PROMOTE_RELEVANCE}, au moins {PROMOTE_MIN_POSTS} messages lus).", "",
             "| Canal | Pertinence | Messages |", "|---|---|---|"]
    for name in added:
        c = cands[candidate_id(name)]
        lines.append(f"| [{name}](https://t.me/s/{name}) | {c['relevance']:.2f} | {c['posts']} |")
    lines += ["", "Verifier chaque canal avant de fusionner. Pour refuser un canal : fermer la PR et passer son "
              "statut a `rejected` dans `data/candidates.json`."]
    return "\n".join(lines) + "\n"


def run(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--max-channels", type=int, default=MAX_CHANNELS)
    ap.add_argument("--out", default=str(DATA / "candidates.json"), help="fichier de sortie")
    ap.add_argument("--update-sources", action="store_true", help="ajoute les canaux promus a tools/sources.json")
    ap.add_argument("--pr-body", default="", help="ecrit ici le texte de PR si des canaux sont ajoutes")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    started = time.monotonic()
    now = datetime.now(timezone.utc)
    rules = json.loads((HERE / "rules.json").read_text(encoding="utf8"))
    sources_path = HERE / "sources.json"
    sources = json.loads(sources_path.read_text(encoding="utf8"))
    offers_path = DATA / "offers.json"
    offers = json.loads(offers_path.read_text(encoding="utf8")) if offers_path.exists() else []
    out = Path(a.out)
    cands = load_candidates(out)

    import requests
    session = requests.Session()
    throttle = Throttle()
    stats = discover(now, lambda url: http_get(url, throttle, session), rules, sources, offers, cands,
                     max(0, min(a.max_channels, MAX_CHANNELS)), load_seeds(HERE / "discovery_seeds.json"),
                     expired=lambda: time.monotonic() - started > GLOBAL_DEADLINE_S)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(dump_candidates(cands, now), ensure_ascii=False, indent=1) + "\n", encoding="utf8")
    log.info("bilan : %s", stats)

    added: list[str] = []
    if a.update_sources:
        added = add_promoted_to_sources(sources, cands)
        if added:
            sources_path.write_text(json.dumps(sources, ensure_ascii=False, indent=1) + "\n", encoding="utf8")
            log.info("ajoutes a sources.json : %s", ", ".join(added))
            if a.pr_body:
                Path(a.pr_body).write_text(pr_body(added, cands), encoding="utf8")
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf8") as f:
            f.write(f"added={len(added)}\n")
    return 0


if __name__ == "__main__":
    sys.exit(run())
