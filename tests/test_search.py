"""Launcher search (T-09) and index performance invariants."""

import pytest

from keylane.core.kdbx import CustomField, EntryData, VaultDocument, make_composite
from keylane.core.search import SearchIndex, UsageTracker, normalize, url_host


@pytest.fixture(scope="module")
def doc():
    d = VaultDocument.create(make_composite("synthetic", None), name="T", kdf_target_ms=1)
    work = d.create_group("Work", None)
    infra = d.create_group("Infrastructure", work)
    d.create_group("Servers", infra)
    d.create_entry(EntryData(None, title="GitHub", username="octo", password="pw-1", url="https://github.com/login"))
    d.create_entry(EntryData(None, title="GitHub", username="work-octo", password="pw-2", url="https://github.com",
                             group_id=work))
    d.create_entry(EntryData(None, title="GitLab", username="lab", url="https://gitlab.com", tags=["dev", "code"]))
    d.create_entry(EntryData(None, title="Login portal", username="x", url="https://portal.example"))
    d.create_entry(EntryData(None, title="Hetzner Cloud", url="https://console.hetzner.cloud", group_id=infra))
    d.create_entry(EntryData(None, title="Notes only", username="secretuser", notes="zebra-in-notes",
                             fields=[CustomField("api", "customvalue", False)]))
    d.create_entry(EntryData(None, title="Café Crème", url="cafe.example"))
    trashed = d.create_entry(EntryData(None, title="Trashed Thing", url="https://trash.example"))
    d.recycle(trashed)
    return d


@pytest.fixture
def index(doc):
    return SearchIndex(doc.summaries())


def titles(results):
    return [r.summary.title for r in results]


def test_title_tiers_rank_exact_and_prefix_first(index):
    assert titles(index.search("github"))[:2] == ["GitHub", "GitHub"]
    assert titles(index.search("git"))[:3] == ["GitHub", "GitHub", "GitLab"]


def test_username_notes_custom_fields_and_password_do_not_match(index):
    assert index.search("secretuser") == []
    assert index.search("zebra") == []
    assert index.search("customvalue") == []
    assert index.search("pw-1") == []


def test_trash_is_excluded(index):
    assert "Trashed Thing" not in titles(index.search("trashed"))
    assert "Trashed Thing" not in titles(index.search("t"))


def test_group_url_and_tag_fields_match_below_titles(index):
    r = index.search("servers")  # no entry is in Servers itself
    assert r == []
    r = index.search("infrastructure")
    assert titles(r) == ["Hetzner Cloud"] and r[0].matched == "group"
    r = index.search("hetzner.cloud")
    assert titles(r)[0] == "Hetzner Cloud"
    r = index.search("dev")
    assert titles(r) == ["GitLab"] and r[0].matched == "tag"


def test_duplicate_titles_are_disambiguated_by_group(index):
    gh = [r.summary for r in index.search("github") if r.summary.title == "GitHub"]
    assert {s.group_path for s in gh} == {"", "Work"}
    assert len({s.id for s in gh}) == 2


def test_diacritics_and_case_are_folded(index):
    assert titles(index.search("cafe creme")) == ["Café Crème"]
    assert titles(index.search("CAFÉ"))[0] == "Café Crème"
    assert normalize("  Ünïcödé  Täxt ") == "unicode taxt"


def test_initials_and_subsequence(index):
    assert "Hetzner Cloud" in titles(index.search("hc"))
    assert "GitHub" in titles(index.search("gthb"))


def test_typo_tolerance(index):
    assert "GitHub" in titles(index.search("githbu"))


def test_no_match(index):
    assert index.search("qqqqzzzz") == []


def test_single_character_uses_word_starts(index):
    t = titles(index.search("c"))
    assert "Hetzner Cloud" in t and "Café Crème" in t
    # Letters inside a word are not 1-character matches ("Hetzner" contains z).
    assert index.search("z") == []


def test_incremental_narrowing_after_one_character_keeps_substring_matches(index):
    fresh = titles(SearchIndex(index.all()).search("gi"))
    index.search("g")
    typed = titles(index.search("gi"))
    assert typed == fresh
    assert "Login portal" in typed  # substring "gi" inside "login"


def test_incremental_narrowing_matches_fresh_search(index):
    for q in ["git", "gith", "githu", "github"]:
        assert titles(index.search(q)) == titles(SearchIndex(index.all()).search(q))


def test_usage_boost_reorders_comparable_matches_only(index):
    ids = {r.summary.group_path: r.summary.id for r in index.search("github") if r.summary.title == "GitHub"}
    u = UsageTracker()
    u.record(ids["Work"])
    r = index.search("github", usage=u.scores())
    assert r[0].summary.id == ids["Work"]
    # A boost never lifts a weak match above a strong title match.
    r = index.search("gitlab", usage=u.scores())
    assert r[0].summary.title == "GitLab"


def test_empty_query_returns_recent_only(index):
    assert index.search("") == []
    u = UsageTracker()
    gl = index.search("gitlab")[0].summary.id
    u.record(gl)
    r = index.search("", usage=u.scores())
    assert [x.summary.id for x in r] == [gl] and r[0].matched == "recent"


def test_limit(index):
    assert len(index.search("t", limit=2)) <= 2


def test_reindex_reuses_unchanged_items_and_picks_up_changes():
    doc = VaultDocument.create(make_composite("synthetic", None), name="T", kdf_target_ms=1)
    doc.create_entry(EntryData(None, title="GitLab", url="https://gitlab.com"))
    doc.create_entry(EntryData(None, title="Hetzner Cloud"))
    a = SearchIndex(doc.summaries())
    gid = next(s.id for s in doc.summaries() if s.title == "GitLab")
    data = doc.read_entry(gid)
    data.title = "Codeberg"
    doc.update_entry(data)
    b = SearchIndex(doc.summaries(), previous=a)
    unchanged = next(s.id for s in doc.summaries() if s.title == "Hetzner Cloud")
    assert b._by_id[unchanged] is a._by_id[unchanged]
    assert titles(b.search("codeberg")) == ["Codeberg"]
    assert b._by_id[gid] is not a._by_id[gid]
    assert [r.matched for r in b.search("gitlab")] == ["url"]


def test_fast_summaries_match_per_entry_summary(doc):
    fast = {s.id: s for s in doc.summaries(include_trash=True)}
    slow = {}
    for e in doc.root_group.iter("Entry"):
        if e.getparent().tag != "History":
            s = doc.summary(e)
            slow[s.id] = s
    assert fast == slow
    assert any(s.in_trash for s in fast.values())
    assert {s.group_path for s in fast.values()} >= {"", "Work", "Work / Infrastructure"}


def test_url_host():
    assert url_host("https://www.example.com/x") == "example.com"
    assert url_host("example.org") == "example.org"
    assert url_host("") == ""
    assert url_host("http://[bad") == ""


def _reference_search(index, query, usage, limit):
    """Brute force: score every entry with `_tier`, as the original engine did."""
    import heapq

    from keylane.core import search as S

    q = normalize(query)
    qq = S._Query(q)
    pool = index._items
    if len(qq.nospace) == 1:
        pool = [it for it in pool if qq.nospace in it.starts]
    best = {id(it): x for x in S._score(qq, pool) for it in [x[2]]}
    if S.fuzz is not None and len(q) >= 4 and len(best) < limit:
        for _, score, i in S.process.extract(q, index._titles, scorer=S.fuzz.ratio, limit=limit, score_cutoff=72):
            best.setdefault(id(index._items[i]), (score * 3.0, "title", index._items[i]))
    out = [(sc + min(60.0, usage.get(it.s.id, 0.0)), w, it) for sc, w, it in best.values()]
    top = heapq.nsmallest(limit, out, key=lambda x: (-x[0], x[2].key))
    return [(it.s.id, round(sc, 6), w) for sc, w, it in top]


def test_banded_search_equals_brute_force_reference():
    import random

    rnd = random.Random(7)
    words = ["alpha", "bravo", "cedar", "delta", "harbor", "koala", "lumen", "nova", "orbit", "pixel",
             "github", "gitlab", "arch", "wiki", "bank", "cloud", "nas", "café", "server", "mail"]
    d = VaultDocument.create(make_composite("synthetic", None), name="T", kdf_target_ms=1)
    groups = [None, d.create_group("Work", None), d.create_group("Personal", None)]
    groups.append(d.create_group("Infrastructure", groups[1]))
    for i in range(600):
        title = " ".join(rnd.choice(words) for _ in range(rnd.randint(1, 3)))
        if rnd.random() < 0.2:
            title = title.title().replace(" ", "")
        d.create_entry(EntryData(None, title=f"{title} {i}" if rnd.random() < 0.5 else title,
                                 url=f"https://{rnd.choice(words)}{i % 7}.example.com" if rnd.random() < 0.8 else "",
                                 tags=rnd.sample(["dev", "home", "bank", "demo"], rnd.randint(0, 2)),
                                 group_id=rnd.choice(groups)))
    index = SearchIndex(d.summaries())
    ids = [s.id for s in index.all()]
    queries = list("abcgkmnpx0-") + ["ar", "arc", "arch wiki", "gh", "git", "na", "ex", "com", "e c", "kl",
                                     "cedar pix", "infra", "wrk", "pers", "harbr", "bank ex", "zzzq", "cafe", "a b c"]
    for _ in range(150):
        w = rnd.choice(words)
        i = rnd.randrange(len(w))
        queries.append(w[i:i + rnd.randint(2, 6)])
        queries.append("".join(rnd.choice("abcdeiklmnoprst ") for _ in range(rnd.randint(2, 5))))
    queries = [q for q in queries if q.strip()]  # blank queries list recent entries instead
    for k, q in enumerate(queries):
        usage = {} if k % 3 else {rnd.choice(ids): rnd.uniform(1, 80) for _ in range(5)}
        for limit in (5, 50, 500):
            got = [(r.summary.id, round(r.score, 6), r.matched) for r in index.search(q, usage=usage, limit=limit)]
            assert got == _reference_search(index, q, usage, limit), (q, limit)
