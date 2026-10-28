"""Small JSON files in DATA_DIR."""
import json
import os
from pathlib import Path

from . import config


def path(name: str) -> Path:
    return config.DATA_DIR / name


def load(name: str, default):
    try:
        return json.loads(path(name).read_text())
    except FileNotFoundError:
        return default


def save(name: str, data) -> None:
    target = path(name)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    os.replace(tmp, target)
