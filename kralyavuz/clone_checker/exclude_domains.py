from dataclasses import replace
from typing import Iterable, Tuple
from urllib.parse import urlsplit

from .models import SearchResult


EXCLUDED_DOMAINS = frozenset(
    {
        "facebook.com",
        "instagram.com",
        "youtube.com",
        "twitter.com",
        "x.com",
        "tiktok.com",
        "wikipedia.org",
    }
)


def _hostname(value: str) -> str:
    candidate = value.strip()
    if not candidate:
        return ""
    parsed = urlsplit(candidate if "://" in candidate else f"//{candidate}")
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    return hostname[4:] if hostname.startswith("www.") else hostname


def is_excluded_domain(url: str) -> bool:
    hostname = _hostname(url)
    return any(
        hostname == domain or hostname.endswith(f".{domain}")
        for domain in EXCLUDED_DOMAINS
    )


def mark_excluded_results(
    results: Iterable[SearchResult],
) -> Tuple[SearchResult, ...]:
    marked = []
    for item in results:
        final_url = item.redirect.final_url if item.redirect else ""
        excluded = is_excluded_domain(item.url) or is_excluded_domain(final_url)
        marked.append(replace(item, excluded=excluded))
    return tuple(marked)
