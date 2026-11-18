"""Tests de tools/discover.py : pages t.me/s/ synthetiques sauvegardees, aucun acces reseau."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import discover as d  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
RULES = json.loads((d.HERE / "rules.json").read_text(encoding="utf8"))
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
CANDIDATE_KEYS = {"id", "platform", "name", "url", "relevance", "posts", "first_seen", "last_checked", "status"}


def fixture(name: str) -> str:
    return (FIX / name).read_text(encoding="utf8")


class FakeFetch:
    """Repond par des fixtures ; enregistre chaque URL demandee. Jamais de reseau."""

    def __init__(self, pages: dict[str, str | None], errors: set[str] = frozenset()):
        self.pages, self.errors, self.calls = pages, errors, []

    def __call__(self, url: str):
        self.calls.append(url)
        assert url.startswith("https://t.me/s/"), url
        name = url.removeprefix("https://t.me/s/").split("?")[0]
        if name in self.errors:
            raise d.FetchError("HTTP 500")
        if "?before=" in url:
            return None
        return self.pages.get(name)


# ----------------------------------------------------------------------------- noms et liens


@pytest.mark.parametrize("name,ok", [
    ("EventJobsDz", True), ("abcde", True), ("a" * 32, True), ("Jobs_dz7", True),
    ("abcd", False), ("a" * 33, False), ("bad-name", False), ("name.with.dot", False), ("ÉvénementDz", False),
    ("joinchat", False), ("JobsAlgerBot", False), ("", False), ("abc de", False), ("x;rm -rf", False),
])
def test_valid_name(name, ok):
    assert d.valid_name(name) is ok


def test_extract_links_from_preview_page():
    names = d.extract_channel_names(fixture("tg_relevant.html"))
    assert names == ["EventJobsDz", "EventsForwardSrc", "HotessesAlgerJobs", "StandsSafexDz"]


def test_extract_ignores_invites_bots_short_long_and_foreign_hosts():
    text = ("https://t.me/joinchat/AAAAAEXAMPLE https://t.me/+Invite t.me/ab https://t.me/JobsAlgerBot "
            "https://example.com/t.me/NotAChannel https://t.me/ThisNameIsWayTooLongForTelegram12345 "
            "https://t.me/bad-name https://nott.me/Something1 mailto:x@t.me/EmailLike1")
    assert d.extract_channel_names(text) == []


def test_extract_handles_variants_and_dedupes_case_insensitively():
    text = "t.me/AlgerEvents https://T.ME/s/algerevents http://telegram.me/Other_Chan/12 www.t.me/Third_one"
    assert d.extract_channel_names(text) == ["AlgerEvents", "Other_Chan", "Third_one"]


def test_preview_url_rejects_invalid_names():
    assert d.preview_url("EventJobsDz") == "https://t.me/s/EventJobsDz"
    assert d.preview_url("EventJobsDz", 100) == "https://t.me/s/EventJobsDz?before=100"
    with pytest.raises(ValueError):
        d.preview_url("../../evil")
    with pytest.raises(ValueError):
        d.http_get("https://example.com/s/x", d.Throttle())


def test_names_from_offers():
    offers = [{"url": "https://t.me/Jobs_dz7/123", "text": "voir aussi t.me/OtherJobsDz"}, {"url": "https://x.dz"}]
    assert d.names_from_offers(offers) == ["Jobs_dz7", "OtherJobsDz"]


# ----------------------------------------------------------------------------- pertinence et seuils


def test_relevance_uses_existing_score_offer():
    posts = d.parse_telegram(fixture("tg_relevant.html"), "https://t.me/s/EventJobsDz")
    rel, n = d.compute_relevance(posts, d.eval_rules(RULES), NOW)
    assert n == 12 and rel == round(5 / 12, 3)
    expected = sum(1 for p in posts if d.score_offer(p["text"], p["date"], d.eval_rules(RULES), NOW)["score"]
                   >= RULES["min_score"])
    assert expected == 5


def test_relevance_irrelevant_and_empty():
    posts = d.parse_telegram(fixture("tg_irrelevant.html"), "u")
    assert d.compute_relevance(posts, RULES, NOW) == (0.0, 12)
    assert d.compute_relevance([], RULES, NOW) == (0.0, 0)
    assert d.compute_relevance([{"text": "  ", "date": ""}], RULES, NOW) == (0.0, 0)


def test_eval_window_is_30_days_but_rules_file_untouched():
    r = d.eval_rules(RULES)
    assert r["max_age_days"] == 30 and RULES["max_age_days"] == 7 and r["min_score"] == RULES["min_score"]
    old = [{"text": "Nous recrutons des animateurs pour un stand au salon, Alger. Envoyez CV",
            "date": (NOW - timedelta(days=45)).strftime("%Y-%m-%d")}]
    assert d.compute_relevance(old, r, NOW) == (0.0, 1)


@pytest.mark.parametrize("rel,posts,status", [
    (0.25, 10, "promoted"), (0.9, 30, "promoted"), (0.24, 10, "rejected"), (0.0, 30, "rejected"),
    (0.9, 9, "candidate"), (0.0, 3, "candidate"), (0.0, 0, "rejected"),
])
def test_promotion_threshold(rel, posts, status):
    assert d.decide_status(rel, posts) == status


def test_evaluate_channel_reads_second_page_and_decides():
    fetch = FakeFetch({"EventJobsDz": fixture("tg_relevant.html")})
    ev = d.evaluate_channel("EventJobsDz", fetch, RULES, NOW)
    assert (ev.posts, ev.relevance, ev.status) == (12, round(5 / 12, 3), "promoted")
    assert fetch.calls == ["https://t.me/s/EventJobsDz", "https://t.me/s/EventJobsDz?before=100"]


def test_evaluate_channel_without_public_preview_is_rejected():
    ev = d.evaluate_channel("SomeUserName", FakeFetch({}), RULES, NOW)
    assert (ev.posts, ev.status) == (0, "rejected")


# ----------------------------------------------------------------------------- regle des 30 jours


def cand(status, days_ago, name="ChannelOne"):
    c = d.make_candidate(name, NOW - timedelta(days=days_ago))
    c["status"] = status
    return c


def test_rejected_less_than_30_days_ago_is_not_reevaluated():
    assert d.should_evaluate(cand("rejected", 29), NOW) is False
    assert d.should_evaluate(cand("rejected", 30), NOW) is True
    assert d.should_evaluate(cand("rejected", 45), NOW) is True
    assert d.should_evaluate(cand("candidate", 1), NOW) is True
    assert d.should_evaluate(cand("promoted", 90), NOW) is False


def test_discover_skips_recent_rejection_even_when_mentioned_again():
    cands = {"tg:hotessesalgerjobs": cand("rejected", 5, "HotessesAlgerJobs")}
    fetch = FakeFetch({"Jobs_dz7": fixture("tg_relevant.html")})
    sources = {"telegram": [{"id": "tg_jobs_dz7", "name": "x", "channel": "Jobs_dz7"}]}
    d.discover(NOW, fetch, RULES, sources, [], cands)
    assert "https://t.me/s/HotessesAlgerJobs" not in fetch.calls
    assert cands["tg:hotessesalgerjobs"]["status"] == "rejected"


# ----------------------------------------------------------------------------- passage complet et schema


def run_discover(cands=None, errors=frozenset(), max_channels=25):
    pages = {"Jobs_dz7": fixture("tg_relevant.html"), "EventJobsDz": fixture("tg_relevant.html"),
             "HotessesAlgerJobs": fixture("tg_relevant.html"), "StandsSafexDz": fixture("tg_irrelevant.html"),
             "EventsForwardSrc": fixture("tg_few_posts.html"), "OtherJobsDz": fixture("tg_irrelevant.html")}
    fetch = FakeFetch(pages, set(errors))
    sources = {"pages": [], "searches": [], "telegram": [{"id": "tg_jobs_dz7", "name": "JOBS", "channel": "Jobs_dz7"}]}
    offers = [{"url": "https://t.me/Jobs_dz7/1", "text": "infos sur t.me/OtherJobsDz"}]
    cands = {} if cands is None else cands
    stats = d.discover(NOW, fetch, RULES, sources, offers, cands, max_channels)
    return cands, stats, fetch, sources


def test_discover_end_to_end_statuses():
    cands, stats, fetch, _ = run_discover()
    status = {c["name"]: c["status"] for c in cands.values()}
    # PromoShopDz / NewEventsDz : cites dans les pages lues (mentions), evalues dans le meme passage, sans apercu -> rejetes
    assert status == {"OtherJobsDz": "rejected", "EventJobsDz": "promoted", "EventsForwardSrc": "candidate",
                      "HotessesAlgerJobs": "promoted", "StandsSafexDz": "rejected", "PromoShopDz": "rejected",
                      "NewEventsDz": "rejected"}
    assert "tg:jobs_dz7" not in cands                         # canal deja connu : jamais candidat
    assert all(u.startswith("https://t.me/s/") for u in fetch.calls)
    assert stats["evaluated"] == 7 and stats["promoted"] == 2


def test_discover_respects_channel_cap():
    cands, stats, _, _ = run_discover(max_channels=2)
    assert stats["evaluated"] == 2 and len(cands) == 2


def test_fetch_error_keeps_channel_as_candidate():
    cands, stats, _, _ = run_discover(errors={"OtherJobsDz"})
    assert cands["tg:otherjobsdz"]["status"] == "candidate" and stats["errors"] == 1


def test_schema_of_candidates_json(tmp_path):
    cands, _, _, _ = run_discover()
    out = d.dump_candidates(cands, NOW)
    path = tmp_path / "candidates.json"
    path.write_text(json.dumps(out), encoding="utf8")
    data = json.loads(path.read_text(encoding="utf8"))
    assert set(data) == {"updated", "candidates"} and d.parse_iso(data["updated"]) == NOW
    for c in data["candidates"]:
        assert set(c) == CANDIDATE_KEYS
        assert c["platform"] == "telegram" and c["status"] in d.STATUSES
        assert c["id"] == "tg:" + c["name"].lower() and c["url"] == "https://t.me/s/" + c["name"]
        assert isinstance(c["relevance"], float) and 0 <= c["relevance"] <= 1 and isinstance(c["posts"], int)
        assert d.parse_iso(c["first_seen"]) and d.parse_iso(c["last_checked"])
    assert [c["status"] for c in data["candidates"]][:2] == ["promoted", "promoted"]
    assert d.load_candidates(path) == {c["id"]: c for c in data["candidates"]}


def test_load_candidates_drops_invalid_or_hostile_entries(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"candidates": [
        {"name": "$(curl evil)", "status": "candidate"}, {"name": "GoodName1", "status": "weird"},
        {"name": "GoodName2", "status": "candidate", "relevance": 7, "posts": -3, "extra": "x",
         "url": "https://evil.example", "first_seen": "2026-11-01T00:00:00Z", "last_checked": "2026-11-01T00:00:00Z"},
    ]}), encoding="utf8")
    loaded = d.load_candidates(path)
    assert list(loaded) == ["tg:goodname2"]
    c = loaded["tg:goodname2"]
    assert set(c) == CANDIDATE_KEYS and c["url"] == "https://t.me/s/GoodName2"
    assert c["relevance"] == 1.0 and c["posts"] == 0
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf8")
    assert d.load_candidates(bad) == {}


def test_first_seen_is_kept_across_runs():
    first, _, _, _ = run_discover()
    seen = first["tg:eventsforwardsrc"]["first_seen"]
    later = NOW + timedelta(days=7)
    fetch = FakeFetch({"EventsForwardSrc": fixture("tg_few_posts.html")})
    d.discover(later, fetch, RULES, {"telegram": []}, [], first)
    c = first["tg:eventsforwardsrc"]
    assert c["first_seen"] == seen and c["last_checked"] == d.iso(later)


def test_promoted_channels_are_added_to_sources_once():
    cands, _, _, sources = run_discover()
    added = d.add_promoted_to_sources(sources, cands)
    assert added == ["EventJobsDz", "HotessesAlgerJobs"]
    assert {"id": "tg_eventjobsdz", "name": "EventJobsDz (Telegram)", "channel": "EventJobsDz",
            "discovered": True} in sources["telegram"]
    assert d.add_promoted_to_sources(sources, cands) == []
    body = d.pr_body(added, cands)
    assert "https://t.me/s/EventJobsDz" in body and "0.42" in body


def test_throttle_waits_at_least_two_seconds():
    t = {"now": 0.0}
    slept = []

    def sleep(s):
        slept.append(s)
        t["now"] += s

    th = d.Throttle(interval=2.0, jitter=0.0, clock=lambda: t["now"], sleep=sleep)
    th.wait()
    t["now"] += 0.5
    th.wait()
    th.wait()
    assert slept == [pytest.approx(1.5), pytest.approx(2.0)]
    assert d.MIN_INTERVAL >= 2.0 and d.MAX_CHANNELS <= 25


def test_http_get_caps_size_and_does_not_follow_redirects():
    class Resp:
        def __init__(self, code, body=b""):
            self.status_code, self.body, self.closed = code, body, False

        def iter_content(self, chunk_size):
            for i in range(0, len(self.body), chunk_size):
                yield self.body[i:i + chunk_size]

        def close(self):
            self.closed = True

    class Session:
        def __init__(self, resp):
            self.resp, self.kwargs = resp, None

        def get(self, url, **kw):
            self.kwargs = kw
            return self.resp

    th = d.Throttle(interval=0, jitter=0)
    big = Session(Resp(200, b"a" * (d.MAX_BYTES + 500_000)))
    assert len(d.http_get("https://t.me/s/EventJobsDz", th, big)) == d.MAX_BYTES
    assert big.kwargs["allow_redirects"] is False and big.kwargs["timeout"] == d.TIMEOUT
    assert "JobRadarDiscover" in big.kwargs["headers"]["User-Agent"] and big.resp.closed
    assert d.http_get("https://t.me/s/EventJobsDz", th, Session(Resp(302))) is None
    with pytest.raises(d.FetchError):
        d.http_get("https://t.me/s/EventJobsDz", th, Session(Resp(503)))
