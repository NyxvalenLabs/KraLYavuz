from typing import List, Protocol

from ..models import SearchResult


class CloneCheckerProvider(Protocol):
    search_engine: str

    def search(self, keyword: str, limit: int = 10) -> List[SearchResult]:
        ...


from .search_provider import (
    GoogleSearchProvider,
    PlaywrightGoogleSearchFallback,
    SearchProviderError,
    YandexSearchProvider,
)
from .google_api_provider import GoogleApiSearchProvider
from .redirect_provider import RedirectProvider, RedirectProviderError

__all__ = [
    "CloneCheckerProvider",
    "GoogleApiSearchProvider",
    "GoogleSearchProvider",
    "PlaywrightGoogleSearchFallback",
    "RedirectProvider",
    "RedirectProviderError",
    "SearchProviderError",
    "YandexSearchProvider",
]
