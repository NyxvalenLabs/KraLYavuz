from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from typing import Dict, Iterable, Optional, Tuple

from .models import CloneCheckResult, CloneCheckStatus, RedirectResult
from .domain_validation import normalize_domain, validate_search_results
from .exclude_domains import mark_excluded_results
from .providers import (
    CloneCheckerProvider,
    RedirectProvider,
    RedirectProviderError,
    SearchProviderError,
    YandexSearchProvider,
)
from .scoring import score_search_results
from .whitelist import WhitelistStore


RISK_STATUSES = frozenset({"Klon", "Klon adayı"})


class CloneCheckerService:
    query_suffixes = ("giriş", "güncel giriş", "resmi site")

    def __init__(
        self,
        providers: Iterable[CloneCheckerProvider] = (),
        redirect_provider: Optional[RedirectProvider] = None,
        whitelist: Optional[WhitelistStore] = None,
    ) -> None:
        configured = tuple(providers)
        self.providers = configured or (YandexSearchProvider(),)
        self.redirect_provider = redirect_provider or RedirectProvider()
        self.whitelist = whitelist or WhitelistStore()

    def keywords_for(self, brand_name: str) -> Tuple[str, ...]:
        brand = brand_name.strip()
        return tuple(f"{brand} {suffix}" for suffix in self.query_suffixes)

    def check(self, brand_name: str, main_domain: str = "") -> CloneCheckResult:
        brand = brand_name.strip()
        if not brand:
            return CloneCheckResult(
                brand_name="",
                status=CloneCheckStatus.NOT_STARTED,
                message="Kontrol için marka adı girin.",
            )
        if main_domain.strip() and not normalize_domain(main_domain):
            return CloneCheckResult(
                brand_name=brand,
                status=CloneCheckStatus.ERROR,
                message="Geçerli bir ana domain girin.",
            )
        results = []
        errors = []
        for keyword in self.keywords_for(brand):
            for provider in self.providers:
                try:
                    provider_results = provider.search(keyword)
                except SearchProviderError as exc:
                    errors.append(str(exc))
                    continue
                results.extend(provider_results)
        if not results:
            message = errors[0] if errors else "Arama sonucu bulunamadı."
            return CloneCheckResult(brand, CloneCheckStatus.ERROR, message)

        redirect_results: Dict[str, RedirectResult] = {}
        redirect_errors = set()
        unique_urls = tuple(dict.fromkeys(item.url for item in results))
        worker_count = min(6, len(unique_urls))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(self.redirect_provider.check, url): url
                for url in unique_urls
            }
            for future in as_completed(futures):
                url = futures[future]
                try:
                    redirect_results[url] = future.result()
                except RedirectProviderError:
                    redirect_errors.add(url)

        enriched_results = tuple(
            replace(item, redirect=redirect_results.get(item.url)) for item in results
        )
        filtered_results = mark_excluded_results(enriched_results)
        scored_results = score_search_results(brand, filtered_results)
        validated_results = validate_search_results(main_domain, scored_results)
        whitelisted_results = self.whitelist.mark_results(validated_results)
        risky_results = tuple(
            item
            for item in whitelisted_results
            if item.clone_result and item.clone_result.status in RISK_STATUSES
        )
        checked_count = len(unique_urls) - len(redirect_errors)
        return CloneCheckResult(
            brand_name=brand,
            status=CloneCheckStatus.COMPLETED,
            message=(
                f"{len(results)} arama sonucu bulundu; "
                f"{checked_count}/{len(unique_urls)} URL kontrol edildi; "
                f"{len(risky_results)} riskli sonuç gösteriliyor."
            ),
            results=risky_results,
        )
