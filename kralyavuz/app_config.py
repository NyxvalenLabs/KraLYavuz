import json
from pathlib import Path
from typing import Any, Dict, Iterable

from .platform_paths import CONFIG_PATH


DEFAULT_CONFIG = {"domains": []}


def load_config(path: Path = CONFIG_PATH) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(DEFAULT_CONFIG)
    if not isinstance(payload, dict):
        return dict(DEFAULT_CONFIG)
    config = dict(payload)
    domains = config.get("domains", [])
    config["domains"] = (
        [value for value in domains if isinstance(value, str) and value.strip()]
        if isinstance(domains, list)
        else []
    )
    return config


def save_config(config: Dict[str, Any], path: Path = CONFIG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def save_domain_config(
    domains: Iterable[str],
    synced_domains: Iterable[str],
    path: Path = CONFIG_PATH,
) -> Dict[str, Any]:
    """Update only the main and synced domain lists in the latest config."""
    config = load_config(path)
    config["domains"] = list(domains)
    config["synced_domains"] = list(synced_domains)
    save_config(config, path)
    return config


def ensure_config(path: Path = CONFIG_PATH) -> None:
    config = load_config(path)
    save_config(config, path)
