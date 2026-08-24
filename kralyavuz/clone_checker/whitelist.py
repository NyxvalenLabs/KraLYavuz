from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from ..app_config import load_config, save_config
from ..platform_paths import CONFIG_PATH
from .domain_validation import normalize_domain
from .models import CloneResult, SearchResult


@dataclass(frozen=True)
class WhitelistEntry:
    domain: str
    added_at: str = ""
    is_synced: bool = False
    is_manual: bool = False

    @property
    def source(self) -> str:
        if self.is_synced and self.is_manual:
            return "Ana Liste + Manuel"
        if self.is_synced:
            return "Ana Domain Listesi"
        return "Manuel"


def normalize_domain_list(values: Iterable[str]) -> Tuple[str, ...]:
    domains: List[str] = []
    seen = set()
    for value in values:
        if not isinstance(value, str):
            continue
        domain = normalize_domain(value)
        if not domain or domain in seen:
            continue
        seen.add(domain)
        domains.append(domain)
    return tuple(domains)


class WhitelistStore:
    legacy_config_key = "clone_whitelist"
    manual_config_key = "manual_whitelist"
    synced_config_key = "synced_domains"
    config_key = manual_config_key

    def __init__(self, config_path: Path = CONFIG_PATH) -> None:
        self.config_path = config_path

    def entries(self) -> Tuple[WhitelistEntry, ...]:
        config = self._load_config_with_migration()
        manual_entries = {
            entry.domain: entry for entry in self._manual_entries(config)
        }
        synced_domains = set(self._synced_domains(config))
        domains = sorted(synced_domains | set(manual_entries))
        return tuple(
            WhitelistEntry(
                domain=domain,
                added_at=(
                    manual_entries[domain].added_at if domain in manual_entries else ""
                ),
                is_synced=domain in synced_domains,
                is_manual=domain in manual_entries,
            )
            for domain in domains
        )

    def add_domain(self, value: str) -> str:
        domain = normalize_domain(value)
        if not domain:
            raise ValueError("Geçerli bir whitelist domaini bulunamadı.")
        config = self._load_config_with_migration()
        entries = list(self._manual_entries(config))
        if any(entry.domain == domain for entry in entries):
            return domain
        entries.append(
            WhitelistEntry(
                domain=domain,
                added_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                is_manual=True,
            )
        )
        self._save_manual_entries(config, entries)
        return domain

    def remove_domain(self, value: str) -> bool:
        domain = normalize_domain(value)
        config = self._load_config_with_migration()
        entries = list(self._manual_entries(config))
        remaining = [entry for entry in entries if entry.domain != domain]
        if len(remaining) == len(entries):
            return False
        self._save_manual_entries(config, remaining)
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

    def _load_config_with_migration(self) -> Dict[str, Any]:
        config = load_config(self.config_path)
        if self.legacy_config_key not in config:
            return config

        current = list(self._manual_entries(config))
        legacy_config = {self.manual_config_key: config.get(self.legacy_config_key, [])}
        legacy = self._manual_entries(legacy_config)
        merged = {entry.domain: entry for entry in legacy}
        for entry in current:
            previous = merged.get(entry.domain)
            merged[entry.domain] = (
                entry
                if entry.added_at or previous is None
                else previous
            )
        config[self.manual_config_key] = self._serialize_manual_entries(merged.values())
        config.pop(self.legacy_config_key, None)
        save_config(config, self.config_path)
        return config

    def _manual_entries(self, config: Dict[str, Any]) -> Tuple[WhitelistEntry, ...]:
        payload = config.get(self.manual_config_key, [])
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
                WhitelistEntry(
                    domain=domain,
                    added_at=str(item.get("added_at", "")).strip(),
                    is_manual=True,
                )
            )
        return tuple(sorted(entries, key=lambda entry: entry.domain))

    def _synced_domains(self, config: Dict[str, Any]) -> Tuple[str, ...]:
        payload = config.get(self.synced_config_key, [])
        if not isinstance(payload, list):
            return ()
        return normalize_domain_list(payload)

    @staticmethod
    def _serialize_manual_entries(
        entries: Iterable[WhitelistEntry],
    ) -> List[Dict[str, str]]:
        return [
            {"domain": entry.domain, "added_at": entry.added_at}
            for entry in sorted(entries, key=lambda entry: entry.domain)
        ]

    def _save_manual_entries(
        self,
        config: Dict[str, Any],
        entries: Iterable[WhitelistEntry],
    ) -> None:
        config[self.manual_config_key] = self._serialize_manual_entries(entries)
        save_config(config, self.config_path)
