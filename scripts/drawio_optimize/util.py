"""Small shared helpers: ids, IO, hashing, colour palette."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

try:  # PyYAML is optional; specs may also be JSON
    import yaml

    HAVE_YAML = True
except Exception:  # pragma: no cover
    yaml = None  # type: ignore
    HAVE_YAML = False


def read_text(path: str | os.PathLike[str]) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def write_text(path: str | os.PathLike[str], text: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def load_structured(path: str | os.PathLike[str]) -> dict[str, Any]:
    text = read_text(path)
    if str(path).lower().endswith((".yaml", ".yml")):
        if not HAVE_YAML:
            raise RuntimeError(
                "PyYAML is required to read YAML specs; install it or use JSON input."
            )
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top-level document must be a mapping/object")
    return data


def dump_structured(data: Any, path: str | os.PathLike[str]) -> None:
    p = Path(path)
    if p.suffix.lower() in (".yaml", ".yml"):
        if not HAVE_YAML:
            raise RuntimeError("PyYAML is required to write YAML.")
        text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100)
    else:
        text = json.dumps(data, ensure_ascii=False, indent=2)
    write_text(p, text)


def sha256_file(path: str | os.PathLike[str]) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def slug(text: str, fallback: str = "item") -> str:
    s = re.sub(r"[^0-9A-Za-z_\u4e00-\u9fff-]+", "_", (text or "").strip())
    s = s.strip("_")
    return s[:48] or fallback


class IdAllocator:
    """Deterministic, collision-free ids (preserves existing ids)."""

    def __init__(self, used: set[str] | None = None):
        self.used = set(used or ())

    def reserve(self, value: str) -> str:
        self.used.add(value)
        return value

    def take(self, prefix: str = "n") -> str:
        i = 1
        while f"{prefix}{i}" in self.used:
            i += 1
        value = f"{prefix}{i}"
        self.used.add(value)
        return value


# ---- palette -------------------------------------------------------------

NODE_STYLES: dict[str, dict[str, Any]] = {
    "interface": {"fill": "#dae8fc", "stroke": "#6c8ebf", "font": "#10243a"},
    "controller": {"fill": "#d5e8d4", "stroke": "#82b366", "font": "#102a12"},
    "memory": {"fill": "#fff2cc", "stroke": "#d6b656", "font": "#3d2c00"},
    "datapath": {"fill": "#e1d5e7", "stroke": "#9673a6", "font": "#2b0f38"},
    "clock": {"fill": "#f8cecc", "stroke": "#b85450", "font": "#3b0a08"},
    "external": {"fill": "#f5f5f5", "stroke": "#666666", "font": "#1f1f1f"},
    "group": {"fill": "#fbfcfe", "stroke": "#adb5bd", "font": "#343a40"},
    "default": {"fill": "#ffffff", "stroke": "#495057", "font": "#111827"},
}

SIGNAL_STYLES: dict[str, dict[str, str]] = {
    "data": {"stroke": "#1d4ed8", "dash": ""},
    "control": {"stroke": "#b45309", "dash": "6 3"},
    "clock": {"stroke": "#6d28d9", "dash": "2 3"},
    "bus": {"stroke": "#0f766e", "dash": ""},
    "power": {"stroke": "#b91c1c", "dash": "1 3"},
    "default": {"stroke": "#334155", "dash": ""},
}


def parse_style(style: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in (style or "").split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
        else:
            out[part] = "1"
    return out


def style_dict_to_string(d: dict[str, Any]) -> str:
    parts = []
    for k, v in d.items():
        if v is None:
            continue
        if v == "" or v is True:
            parts.append(k)
        else:
            parts.append(f"{k}={v}")
    return ";".join(parts) + (";" if parts else "")


def merge_style(raw: str, updates: dict[str, Any]) -> str:
    d = parse_style(raw)
    for k, v in updates.items():
        if v is None:
            d.pop(k, None)
        else:
            d[k] = v
    return style_dict_to_string(d)
