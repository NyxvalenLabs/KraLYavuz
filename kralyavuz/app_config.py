import json
from pathlib import Path
from typing import Any, Dict

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


def ensure_config(path: Path = CONFIG_PATH) -> None:
    config = load_config(path)
    save_config(config, path)
