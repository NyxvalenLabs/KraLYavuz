from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Tuple

from ..app_config import load_config, save_config
from ..platform_paths import CONFIG_PATH
from .domain_validation import normalize_domain
from .models import CloneResult, SearchResult


@dataclass(frozen=True)
class WhitelistEntry:
    domain: str
    added_at: str


class WhitelistStore:
    config_key = "clone_whitelist"

    def __init__(self, config_path: Path = CONFIG_PATH) -> None:
        self.config_path = config_path

    def entries(self) -> Tuple[WhitelistEntry, ...]:
        payload = load_config(self.config_path).get(self.config_key, [])
        if not isinstance(payload, list):
            return ()
        entries = []
        seen = set()
        for item in payload:
            if not isinstance(item, dict):
                continue
            domain = normalize_domain(str(item.get("domain", "")))
            if not domain or domain in seen:
                continue
            seen.add(domain)
            entries.append(
                WhitelistEntry(domain, str(item.get("added_at", "")).strip())
            )
        return tuple(sorted(entries, key=lambda entry: entry.domain))

    def add_domain(self, value: str) -> str:
        domain = normalize_domain(value)
        if not domain:
            raise ValueError("Geçerli bir whitelist domaini bulunamadı.")
        entries = list(self.entries())
        if any(entry.domain == domain for entry in entries):
            return domain
        entries.append(
            WhitelistEntry(
                domain=domain,
                added_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            )
        )
        self._save(entries)
        return domain

    def remove_domain(self, value: str) -> bool:
        domain = normalize_domain(value)
        entries = list(self.entries())
        remaining = [entry for entry in entries if entry.domain != domain]
        if len(remaining) == len(entries):
            return False
        self._save(remaining)
        return True

    def is_whitelisted_url(self, value: str) -> bool:
        hostname = normalize_domain(value)
        domains = tuple(entry.domain for entry in self.entries())
        return self._matches(hostname, domains)

    def is_whitelisted_result(self, result: SearchResult) -> bool:
        domains = tuple(entry.domain for entry in self.entries())
        source_domain = normalize_domain(result.url)
        final_domain = normalize_domain(
            result.redirect.final_url if result.redirect else ""
        )
        return self._matches(source_domain, domains) or self._matches(
            final_domain, domains
        )

    def domain_for_result(self, result: SearchResult) -> str:
        if result.redirect and len(result.redirect.redirect_chain) > 1:
            final_domain = normalize_domain(result.redirect.final_url)
            if final_domain:
                return final_domain
        return normalize_domain(result.url)

    def mark_results(
        self, results: Iterable[SearchResult]
    ) -> Tuple[SearchResult, ...]:
        domains = tuple(entry.domain for entry in self.entries())
        marked = []
        for item in results:
            source_domain = normalize_domain(item.url)
            final_domain = normalize_domain(
                item.redirect.final_url if item.redirect else ""
            )
            if not (
                self._matches(source_domain, domains)
                or self._matches(final_domain, domains)
            ):
                marked.append(item)
                continue
            result_domain = (
                item.clone_result.final_domain
                if item.clone_result
                else normalize_domain(item.redirect.final_url if item.redirect else item.url)
            )
            clone_result = CloneResult(
                main_domain=item.clone_result.main_domain if item.clone_result else "",
                final_domain=result_domain,
                status="Güvenli",
                status_reason="Kaynak veya son hedef whitelist içinde.",
            )
            marked.append(replace(item, clone_result=clone_result))
        return tuple(marked)

    @staticmethod
    def _matches(hostname: str, domains: Iterable[str]) -> bool:
        return any(
            hostname == domain or hostname.endswith(f".{domain}")
            for domain in domains
        )

    def _save(self, entries: Iterable[WhitelistEntry]) -> None:
        config = load_config(self.config_path)
        config[self.config_key] = [
            {"domain": entry.domain, "added_at": entry.added_at}
            for entry in sorted(entries, key=lambda entry: entry.domain)
        ]
        save_config(config, self.config_path)
