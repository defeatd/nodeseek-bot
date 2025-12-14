from __future__ import annotations

import copy
from pathlib import Path
import yaml


def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"rules yaml must be a mapping: {path}")
    return data


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if v is None:
            continue
        if k not in out:
            out[k] = copy.deepcopy(v)
            continue
        if isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = deep_merge(out[k], v)
            continue
        if isinstance(out[k], list) and isinstance(v, list):
            # keep order, unique
            seen = set()
            merged = []
            for item in out[k] + v:
                key = str(item)
                if key in seen:
                    continue
                seen.add(key)
                merged.append(item)
            out[k] = merged
            continue
        out[k] = copy.deepcopy(v)
    return out


def load_rules(base_path: Path, overrides_path: Path) -> dict:
    base = load_yaml(base_path)
    overrides = load_yaml(overrides_path)
    return deep_merge(base, overrides)


def save_overrides(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
    tmp.replace(path)
