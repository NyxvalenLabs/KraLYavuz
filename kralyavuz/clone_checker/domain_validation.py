from dataclasses import replace
from typing import Iterable, Tuple
from urllib.parse import urlsplit

from .models import CloneResult, SearchResult
from .scoring import CLONE_CANDIDATE_THRESHOLD


def normalize_domain(value: str) -> str:
    candidate = value.strip()
    if not candidate:
        return ""
    parsed = urlsplit(candidate if "://" in candidate else f"//{candidate}")
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    if hostname.startswith("www."):
        hostname = hostname[4:]
    try:
        return hostname.encode("idna").decode("ascii")
    except UnicodeError:
        return ""


def _belongs_to_domain(hostname: str, main_domain: str) -> bool:
    return bool(
        hostname
        and main_domain
        and (hostname == main_domain or hostname.endswith(f".{main_domain}"))
    )


def _scoring_fallback(result: SearchResult, final_domain: str) -> CloneResult:
    candidate = result.candidate
    is_candidate = bool(
        candidate and candidate.score >= CLONE_CANDIDATE_THRESHOLD
    )
    return CloneResult(
        main_domain="",
        final_domain=final_domain,
        status="Klon adayı" if is_candidate else "Normal sonuç",
        status_reason="Ana domain girilmedi; mevcut skor sonucu kullanıldı.",
    )


def validate_search_results(
    main_domain: str, results: Iterable[SearchResult]
) -> Tuple[SearchResult, ...]:
    normalized_main = normalize_domain(main_domain)
    validated = []

    for item in results:
        source_domain = normalize_domain(item.url)
        final_url = item.redirect.final_url if item.redirect else item.url
        final_domain = normalize_domain(final_url)

        if item.excluded:
            clone_result = CloneResult(
                main_domain=normalized_main,
                final_domain=final_domain,
                status="Hariç tutuldu",
                status_reason="Kaynak veya son hedef hariç domain listesinde.",
            )
        elif not normalized_main:
            clone_result = _scoring_fallback(item, final_domain)
        elif _belongs_to_domain(final_domain, normalized_main):
            clone_result = CloneResult(
                main_domain=normalized_main,
                final_domain=final_domain,
                status="Sorunsuz",
                status_reason="Son hedef ana domain veya alt domainiyle eşleşiyor.",
            )
        elif item.redirect and source_domain and final_domain != source_domain:
            clone_result = CloneResult(
                main_domain=normalized_main,
                final_domain=final_domain,
                status="Klon",
                status_reason="Yönlendirme ana domain dışında farklı bir domaine gidiyor.",
            )
        else:
            clone_result = CloneResult(
                main_domain=normalized_main,
                final_domain=final_domain,
                status="Klon adayı",
                status_reason="Sonuç ana domain dışında ve ana domaine yönlenmiyor.",
            )

        validated.append(replace(item, clone_result=clone_result))
    return tuple(validated)
