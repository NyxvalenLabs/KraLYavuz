from typing import Optional
from urllib.parse import urlsplit

import requests

from ..models import RedirectResult


class RedirectProviderError(RuntimeError):
    pass


class RedirectProvider:
    user_agent = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/134.0.0.0 Safari/537.36"
    )

    def __init__(self, session: Optional[requests.Session] = None) -> None:
        self.session = session

    def check(self, source_url: str) -> RedirectResult:
        parsed = urlsplit(source_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise RedirectProviderError(f"Geçersiz yönlendirme URL'si: {source_url}")

        client = self.session or requests.Session()
        owns_session = self.session is None
        response = None
        try:
            response = client.get(
                source_url,
                headers={
                    "User-Agent": self.user_agent,
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
                },
                timeout=10,
                allow_redirects=True,
                stream=True,
            )
            status_codes = tuple(item.status_code for item in response.history)
            if response.history:
                chain = [source_url]
                chain.extend(item.url for item in response.history[1:])
                chain.append(response.url)
            else:
                chain = [source_url]
            redirect_chain = tuple(dict.fromkeys(chain))
            return RedirectResult(
                source_url=source_url,
                status_codes=status_codes,
                redirect_chain=redirect_chain,
                final_url=response.url,
            )
        except requests.RequestException as exc:
            raise RedirectProviderError(
                f"Yönlendirme kontrolü başarısız ({source_url}): {exc}"
            ) from exc
        finally:
            if response is not None:
                response.close()
            if owns_session:
                client.close()
