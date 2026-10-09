"""Launcher search: deterministic tiered fuzzy ranking.

Only these fields are indexed: title, group path, URL (host + full), tags.
Usernames, passwords, notes and custom fields are never indexed.

Ranking tiers for the *title* (higher is better, gaps of >=100 so that the
usage boost (<= 60) can only reorder comparable matches):

    1000 exact          900 prefix        800 word prefix
     700 substring      650 all tokens    600 initials/acronym
     400..499 subsequence (compactness)   <= 300 typo-tolerant (rapidfuzz)

Matches in group path / URL / tags use the same tiers scaled by 0.5, so a
strong title match always outranks a secondary-field match.
"""

from __future__ import annotations

import bisect
import heapq
import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import urlsplit

from .kdbx import EntrySummary

try:
    from rapidfuzz import fuzz, process
except ImportError:  # pragma: no cover - rapidfuzz is a hard dependency in packaging
    fuzz = process = None


def normalize(text: str) -> str:
    """Casefold and strip diacritics; keeps ordinary Unicode letters (e.g. CJK)."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(text.casefold().split())


_WORD_SPLIT = re.compile(r"[\s\-_./:@()\[\]|,;+]+")


def url_host(url: str) -> str:
    if not url:
        return ""
    u = url if "://" in url else "https://" + url
    try:
        host = urlsplit(u).hostname or ""
    except ValueError:
        host = ""
    return host[4:] if host.startswith("www.") else host


@dataclass(frozen=True)
class Result:
    summary: EntrySummary
    score: float
    matched: str  # "title" | "group" | "url" | "tag" | "recent"


class _Item:
    __slots__ = ("s", "title", "title_words", "initials", "others", "key", "starts")

    def __init__(self, s: EntrySummary):
        self.s = s
        self.title = normalize(s.title)
        self.title_words = [w for w in _WORD_SPLIT.split(self.title) if w]
        # Initials also split camelCase in the original title (GitHub -> gh).
        raw_words = [w for w in _WORD_SPLIT.split(s.title) if w]
        initials = []
        for w in raw_words:
            parts = re.findall(r"[A-Z]?[a-z0-9\u00c0-\uffff]+|[A-Z]+(?![a-z])", w) or [w]
            initials.extend(p[0] for p in parts if p)
        self.initials = normalize("".join(initials))
        others = []
        if s.group_path:
            others.append(("group", normalize(s.group_path)))
        host = url_host(s.url)
        if host:
            others.append(("url", normalize(host)))
        for t in s.tags:
            others.append(("tag", normalize(t)))
        self.others = [(kind, text, [w for w in _WORD_SPLIT.split(text) if w]) for kind, text in others]
        self.key = (self.title, s.id)
        # First characters of every indexed word: the candidate set for 1-character queries.
        starts = {w[0] for w in self.title_words}
        starts.update(w[0] for _, _, ws in self.others for w in ws)
        if self.initials:
            starts.add(self.initials[0])
        self.starts = frozenset(starts)


class _Query:
    __slots__ = ("q", "tokens", "nospace", "subseq")

    def __init__(self, q: str):
        self.q = q
        self.tokens = [t for t in q.split(" ") if t]
        self.nospace = q.replace(" ", "")
        # Leftmost start, then the earliest occurrence of each next character:
        # the same match as the lazy "a.*?b.*?c", but possessive, so failing
        # lines cost linear time instead of backtracking.
        self.subseq = None
        if len(self.nospace) >= 2:
            c0, rest = self.nospace[0], self.nospace[1:]
            self.subseq = re.compile(re.escape(c0) + "".join(f"[^{re.escape(c)}\n]*+{re.escape(c)}" for c in rest))


def _tier(qq: _Query, text: str, words: list[str], initials: str = "") -> float:
    if not text:
        return 0.0
    q = qq.q
    if text == q:
        return 1000.0
    if text.startswith(q):
        return 900.0 - min(len(text) - len(q), 50) * 0.5
    pos = text.find(q)
    if pos >= 0:
        if any(w.startswith(q) for w in words):
            return 800.0 - min(len(text), 50) * 0.2
        return 700.0 - min(pos, 50)
    tokens = qq.tokens
    if len(tokens) > 1 and all(t in text for t in tokens):
        return 650.0
    if initials and len(qq.nospace) >= 2 and initials.startswith(qq.nospace):
        return 600.0
    if qq.subseq is not None:
        m = qq.subseq.search(text)
        if m:
            span = m.end() - m.start()
            return 400.0 + 99.0 * (len(qq.nospace) / span) - min(m.start(), 20) * 0.5
    return 0.0


class SearchIndex:
    """In-memory index over non-secret summaries. Cleared on lock."""

    def __init__(self, summaries: list[EntrySummary], previous: "SearchIndex | None" = None):
        # Reuse analysed items for unchanged summaries so re-indexing after a
        # save of a large vault costs little more than the comparison.
        prev = previous._by_id if previous is not None else {}
        items = []
        for s in summaries:
            if s.in_trash:
                continue
            old = prev.get(s.id)
            items.append(old if old is not None and old.s == s else _Item(s))
        self._items = items
        self._by_id = {it.s.id: it for it in self._items}
        self._titles = [it.title for it in self._items]
        by_initial: dict[str, list[int]] = {}
        for i, it in enumerate(self._items):
            for c in it.starts:
                by_initial.setdefault(c, []).append(i)
        self._by_initial = {c: frozenset(rows) for c, rows in by_initial.items()}
        self._row_of = {it.s.id: i for i, it in enumerate(self._items)}
        # Newline-separated blobs let C-level regex find matching rows without
        # a Python loop over every entry (normalized text never contains "\n").
        self._title_blob, self._title_starts = _blob(it.title for it in self._items)
        self._init_blob, self._init_starts = _blob(it.initials for it in self._items)
        # Secondary texts (group paths, tags, often hosts) repeat across many
        # entries: they are scored once per distinct text.
        text_rows: dict[str, list[int]] = {}
        for i, it in enumerate(self._items):
            for _, text, _ in it.others:
                rows = text_rows.setdefault(text, [])
                if not rows or rows[-1] != i:
                    rows.append(i)
        self._texts = list(text_rows)
        self._text_rows = [text_rows[t] for t in self._texts]
        self._text_blob, self._text_starts = _blob(self._texts)

    def __len__(self) -> int:
        return len(self._items)

    def all(self) -> list[EntrySummary]:
        return [it.s for it in self._items]

    def get(self, entry_id: str) -> EntrySummary | None:
        it = self._by_id.get(entry_id)
        return it.s if it else None

    def search(self, query: str, *, usage: dict[str, float] | None = None, limit: int = 50) -> list[Result]:
        usage = usage or {}
        q = normalize(query)
        if not q:
            recent = sorted((self._by_id[i] for i in usage if i in self._by_id), key=lambda it: (-usage[it.s.id], it.key))
            return [Result(it.s, 0.0, "recent") for it in recent[:limit]]
        qq = _Query(q)
        if len(qq.nospace) == 1:
            # One character only matches entries with a word starting with it (`_Item.starts`).
            allowed = self._by_initial.get(qq.nospace, frozenset())
            if len(allowed) <= 4000:  # small pools: scoring each entry beats scanning the blobs
                pool = [self._items[i] for i in sorted(allowed)]
                best = {id(it): (sc, where, it) for sc, where, it in _score(qq, pool)}
            else:
                best = self._score_all(qq, usage, limit, allowed)
        else:
            best = self._score_all(qq, usage, limit)
        # Typo tolerance on titles when exact-ish matches are scarce.
        if fuzz is not None and len(q) >= 4 and len(best) < limit:
            for title, score, idx in process.extract(q, self._titles, scorer=fuzz.ratio, limit=limit, score_cutoff=72):
                it = self._items[idx]
                if id(it) not in best:
                    best[id(it)] = (score * 3.0, "title", it)
        out = [(sc + min(60.0, usage.get(it.s.id, 0.0)), where, it) for sc, where, it in best.values()]
        top = heapq.nsmallest(limit, out, key=lambda x: (-x[0], x[2].key))
        return [Result(it.s, sc, where) for sc, where, it in top]

    def _title_scores(self, qq: "_Query", gate: float, limit: int, allowed) -> tuple[dict[int, float], bool]:
        """Row -> title `_tier`, for every row where it is positive.

        Stops early (returning False) once `limit` rows score above `gate`,
        the best any row from a later band can reach.
        """
        blob, starts = self._title_blob, self._title_starts
        out: dict[int, float] = {}
        _substring_tiers(qq, blob, starts, out)
        if allowed is not None:
            out = {r: sc for r, sc in out.items() if r in allowed}
        if sum(1 for sc in out.values() if sc > gate) >= limit:
            return out, False
        _token_tiers(qq, blob, starts, out)
        if len(qq.nospace) >= 2:
            pat = re.compile("^" + re.escape(qq.nospace), re.M)
            for row, _ in _first_per_row(self._init_blob, self._init_starts, pat):
                if starts[row] != _line_end(blob, starts, row):  # empty titles never match
                    out.setdefault(row, 600.0)
        # Title subsequences score <= 499 and secondary fields <= 500, plus the boost.
        if sum(1 for sc in out.values() if sc > 560.0) >= limit:
            return out, False
        _subseq_tiers(qq, blob, starts, out)
        return out, True

    def _score_all(self, qq: "_Query", usage: dict[str, float], limit: int, allowed=None) -> dict[int, tuple[float, str, "_Item"]]:
        """Exact unboosted scores for every row that can still reach the top `limit`.

        Equivalent to `_score` over all entries, but bands that cannot reach
        the top `limit` (even with the maximum usage boost) are never scored.
        `allowed` optionally restricts the result to a set of rows.
        """
        items = self._items
        # Title substring hits score >= 650; secondary fields score <= 500.
        gate = (650.0 if len(qq.tokens) > 1 else 600.0) + 60.0
        titles, complete = self._title_scores(qq, gate, limit, allowed)
        if not complete:
            # Every returned row scores >= 600 by title; secondary fields score <= 500.
            return {id(items[r]): (sc, "title", items[r]) for r, sc in titles.items()}
        tb, ts = self._text_blob, self._text_starts
        text_sc: dict[int, float] = {}
        _substring_tiers(qq, tb, ts, text_sc)
        _token_tiers(qq, tb, ts, text_sc)
        _subseq_tiers(qq, tb, ts, text_sc)
        memo = {self._texts[i]: sc * 0.5 for i, sc in text_sc.items()}
        best: dict[int, tuple[float, str, _Item]] = {}

        def full(row: int, base: float) -> None:
            it, where = items[row], "title"
            for kind, text, _ in it.others:
                sc = memo.get(text, 0.0)
                if sc > base:
                    base, where = sc, kind
            if base > 0:
                best[id(it)] = (base, where, it)

        for row, sc in titles.items():
            full(row, sc)
        # Boosted rows can overtake the cut-off below, so score them individually.
        for eid in usage:
            row = self._row_of.get(eid)
            if row is not None and id(items[row]) not in best and (allowed is None or row in allowed):
                full(row, 0.0)
        levels: dict[float, list[int]] = {}
        for i, sc in text_sc.items():
            levels.setdefault(sc * 0.5, []).append(i)
        added = 0
        for level in sorted(levels, reverse=True):
            if added >= limit:
                break  # `limit` unboosted rows already outrank every remaining one
            for i in levels[level]:
                for row in self._text_rows[i]:
                    it = items[row]
                    if id(it) in best or (allowed is not None and row not in allowed):
                        continue  # title hit, boosted, or placed at a higher level
                    where = next(kind for kind, t, _ in it.others if memo.get(t) == level)
                    best[id(it)] = (level, where, it)
                    added += 1
        return best


# The tier helpers below compute exactly what `_tier` returns for each line of
# a blob, from C-level regex scans; each fills only rows not yet scored, in
# `_tier`'s precedence order.

# Characters `_WORD_SPLIT` splits on, as they can occur in normalized text.
_SEP_CHARS = frozenset(" -_./:@()[]|,;+")


def _line_end(blob: str, starts: list[int], row: int) -> int:
    return starts[row + 1] - 1 if row + 1 < len(starts) else len(blob)


def _substring_tiers(qq: _Query, blob: str, starts: list[int], out: dict[int, float]) -> None:
    """Exact (1000), prefix (900-), word-prefix (800-) and substring (700-) tiers."""
    q, lq = qq.q, len(qq.q)
    can_word = not _WORD_SPLIT.search(q)  # words never contain separators
    find, seps = blob.find, _SEP_CHARS
    for row, m in _first_per_row(blob, starts, re.compile(re.escape(q))):
        st, p = starts[row], m.start()
        end = _line_end(blob, starts, row)
        n = end - st
        if p == st:
            out[row] = 1000.0 if n == lq else 900.0 - min(n - lq, 50) * 0.5
            continue
        if can_word:
            w = p
            while w >= 0 and blob[w - 1] not in seps:
                w = find(q, w + 1, end)
            if w >= 0:
                out[row] = 800.0 - min(n, 50) * 0.2
                continue
        out[row] = 700.0 - min(p - st, 50)


def _token_tiers(qq: _Query, blob: str, starts: list[int], out: dict[int, float]) -> None:
    if len(qq.tokens) < 2:
        return
    rows: set[int] | None = None
    for tok in qq.tokens:
        r = {row for row, _ in _first_per_row(blob, starts, re.compile(re.escape(tok)))}
        rows = r if rows is None else rows & r
        if not rows:
            return
    for row in rows:
        out.setdefault(row, 650.0)


def _subseq_tiers(qq: _Query, blob: str, starts: list[int], out: dict[int, float]) -> None:
    if qq.subseq is None:
        return
    n = len(qq.nospace)
    for row, m in _first_per_row(blob, starts, qq.subseq):
        if row not in out:
            out[row] = 400.0 + 99.0 * (n / (m.end() - m.start())) - min(m.start() - starts[row], 20) * 0.5


def _score(qq: _Query, pool: list) -> list[tuple[float, str, _Item]]:
    """Reference scorer: best of the title tier and half the secondary-field tiers."""
    scored = []
    memo: dict[str, float] = {}
    for it in pool:
        best = _tier(qq, it.title, it.title_words, it.initials)
        where = "title"
        for kind, text, words in it.others:
            sc = memo.get(text)
            if sc is None:
                sc = memo[text] = _tier(qq, text, words) * 0.5
            if sc > best:
                best, where = sc, kind
        if best > 0:
            scored.append((best, where, it))
    return scored


def _blob(texts) -> tuple[str, list[int]]:
    starts, parts, pos = [], [], 0
    for t in texts:
        starts.append(pos)
        parts.append(t)
        pos += len(t) + 1
    return "\n".join(parts), starts


def _first_per_row(blob: str, starts: list[int], pattern: re.Pattern):
    """Yield (row, match) for the leftmost match in each matching line.

    Patterns never match across "\n" (`.` excludes it), so each match is the
    one `pattern.search(line)` would return.
    """
    pos = 0
    while True:
        m = pattern.search(blob, pos)
        if not m:
            return
        yield bisect.bisect_right(starts, m.start()) - 1, m
        nl = blob.find("\n", m.start())
        if nl < 0:
            return
        pos = nl + 1


class UsageTracker:
    """In-memory recency/frequency of copies in this unlocked session only."""

    def __init__(self) -> None:
        self._counter = 0
        self._scores: dict[str, float] = {}

    def record(self, entry_id: str) -> None:
        self._counter += 1
        # Recency dominates; frequency adds a little. Bounded so it can't bury strong matches.
        self._scores[entry_id] = min(60.0, self._scores.get(entry_id, 0.0) * 0.5 + 30.0) + self._counter * 1e-6

    def scores(self) -> dict[str, float]:
        return dict(self._scores)

    def clear(self) -> None:
        self._scores.clear()
        self._counter = 0
