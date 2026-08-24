import re
from dataclasses import replace
from typing import Iterable, Tuple
from urllib.parse import unquote, urlsplit

from .models import CloneCandidate, SearchResult


CLONE_CANDIDATE_THRESHOLD = 70


def _normalized_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", unquote(value).casefold())


def _hostname(url: str) -> str:
    hostname = (urlsplit(url).hostname or "").casefold().rstrip(".")
    return hostname[4:] if hostname.startswith("www.") else hostname


def _effective_url(result: SearchResult) -> str:
    return result.redirect.final_url if result.redirect else result.url


def _main_hostname(results: Tuple[SearchResult, ...]) -> str:
    official_results = [
        result
        for result in results
        if result.keyword.casefold().strip().endswith("resmi site")
    ]
    pool = official_results or list(results)
    if not pool:
        return ""
    primary = min(pool, key=lambda result: result.rank)
    return _hostname(_effective_url(primary))


def score_search_results(
    brand_name: str, results: Iterable[SearchResult]
) -> Tuple[SearchResult, ...]:
    items = tuple(results)
    brand = _normalized_text(brand_name)
    main_hostname = _main_hostname(tuple(item for item in items if not item.excluded))
    scored = []

    for item in items:
        if item.excluded:
            scored.append(replace(item, candidate=None))
            continue
        score = 0
        reasons = []
        if brand and brand in _normalized_text(item.url):
            score += 30
            reasons.append("Marka adı URL içinde")
        if brand and brand in _normalized_text(item.title):
            score += 30
            reasons.append("Title eşleşiyor")

        source_hostname = _hostname(item.url)
        effective_hostname = _hostname(_effective_url(item))
        if item.redirect and source_hostname and effective_hostname != source_hostname:
            score += 20
            reasons.append("Redirect farklı domaine gidiyor")
        if main_hostname and effective_hostname and effective_hostname != main_hostname:
            score += 20
            reasons.append("Ana site dışında farklı domain")

        candidate = CloneCandidate(
            url=item.url,
            rank=item.rank,
            title=item.title,
            score=score,
            reasons=tuple(reasons),
        )
        scored.append(replace(item, candidate=candidate))
    return tuple(scored)
