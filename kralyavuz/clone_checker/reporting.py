import re
from typing import Dict, Iterable, List, Tuple
from urllib.parse import parse_qsl, unquote, urlsplit

from .domain_validation import normalize_domain
from .models import SearchResult


REPORTABLE_STATUSES = frozenset({"Klon", "Klon adayı"})
TARGET_QUERY_KEYS = frozenset(
    {"url", "target", "redirect", "redirect_url", "dest", "destination", "q"}
)


def _decode_url(value: str) -> str:
    decoded = value.strip()
    for _ in range(3):
        updated = unquote(decoded)
        if updated == decoded:
            break
        decoded = updated
    if decoded.startswith("//"):
        decoded = f"https:{decoded}"
    return decoded


def _is_tracking_url(url: str) -> bool:
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").casefold()
    path = parsed.path.casefold()
    is_yandex = hostname in {"yandex.com.tr", "yandex.com", "yandex.ru"} or hostname.endswith(
        (".yandex.com.tr", ".yandex.com", ".yandex.ru")
    )
    is_google = hostname.startswith("www.google.") or hostname.startswith("google.")
    return (is_yandex and any(part in path for part in ("/an/count", "/clck", "/redir"))) or (
        is_google and path == "/url"
    )


def resolve_report_url(url: str) -> str:
    candidate = _decode_url(url)
    if not _is_tracking_url(candidate):
        return candidate

    parsed = urlsplit(candidate)
    tracking_domain = normalize_domain(candidate)
    for key, value in parse_qsl(parsed.query, keep_blank_values=False):
        if key.casefold() not in TARGET_QUERY_KEYS:
            continue
        target = _decode_url(value)
        if urlsplit(target).scheme in {"http", "https"} and normalize_domain(
            target
        ) != tracking_domain:
            return target

    decoded = _decode_url(candidate)
    pattern = re.compile(
        r"(?:url|target|redirect|redirect_url|dest|destination)="
        r"(https?://[^&;\s\"'<>]+)",
        re.IGNORECASE,
    )
    for match in pattern.finditer(decoded):
        target = _decode_url(match.group(1)).rstrip(")],")
        if normalize_domain(target) and normalize_domain(target) != tracking_domain:
            return target
    return ""


def build_clipboard_report(results: Iterable[SearchResult]) -> str:
    groups: Dict[Tuple[str, str], List[Tuple[int, str]]] = {}
    seen_domains = set()

    for item in results:
        if not item.clone_result or item.clone_result.status not in REPORTABLE_STATUSES:
            continue
        url = resolve_report_url(item.url)
        domain = normalize_domain(url)
        if not url or not domain or domain in seen_domains:
            continue
        seen_domains.add(domain)
        groups.setdefault((item.search_engine, item.keyword), []).append((item.rank, url))

    sections = []
    for (search_engine, keyword), items in groups.items():
        lines = [f"{search_engine} - Aratılan kelime: {keyword}"]
        for rank, url in items:
            lines.extend(("", f"{rank}. sıra", f"   {url}"))
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def report_result_count(report: str) -> int:
    return sum(
        line.startswith(("   http://", "   https://"))
        for line in report.splitlines()
    )
