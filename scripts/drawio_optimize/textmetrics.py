"""Text measurement and label wrapping for mixed Chinese/English labels.

Draw.io renders labels with the browser font stack.  We cannot query the
browser, so we measure with real font files when Pillow is available:
Latin runs use DejaVu Sans / Liberation Sans (close to the Helvetica default),
CJK runs use Noto Sans CJK (the usual Linux fallback).  A conservative safety
factor is applied.  When Pillow or the fonts are missing we fall back to a
per-character-class estimate and record that fact in ``backend`` so reports can
state the limitation instead of pretending to be exact.
"""
from __future__ import annotations

import unicodedata
from functools import lru_cache
from pathlib import Path

try:
    from PIL import ImageFont

    HAVE_PIL = True
except Exception:  # pragma: no cover
    ImageFont = None  # type: ignore
    HAVE_PIL = False

LATIN_FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "C:/Windows/Fonts/arial.ttf",
]
LATIN_BOLD_FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]
CJK_FONTS = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "C:/Windows/Fonts/msyh.ttc",
]

SAFETY = 1.06          # browser text is usually a little wider than PIL
LINE_HEIGHT = 1.24
NODE_PAD_X = 14.0      # per side, includes draw.io label spacing
NODE_PAD_Y = 10.0
EDGE_LABEL_PAD_X = 6.0
EDGE_LABEL_PAD_Y = 2.0


def is_cjk(ch: str) -> bool:
    o = ord(ch)
    return (
        0x2E80 <= o <= 0x9FFF
        or 0xAC00 <= o <= 0xD7AF
        or 0xF900 <= o <= 0xFAFF
        or 0xFF00 <= o <= 0xFFEF
        or 0x3000 <= o <= 0x303F
    )


def _first_existing(paths: list[str]) -> str | None:
    for p in paths:
        if Path(p).exists():
            return p
    return None


@lru_cache(maxsize=64)
def _font(path: str, size: int):
    return ImageFont.truetype(path, size)


class TextMeasurer:
    def __init__(self, font_size: float = 12.0, bold: bool = False):
        self.font_size = float(font_size)
        self.bold = bold
        self._latin = _first_existing(LATIN_BOLD_FONTS if bold else LATIN_FONTS)
        self._cjk = _first_existing(CJK_FONTS)
        if HAVE_PIL and self._latin and self._cjk:
            self.backend = f"pillow:{Path(self._latin).stem}+{Path(self._cjk).stem}"
        elif HAVE_PIL and self._latin:
            self.backend = f"pillow:{Path(self._latin).stem}+heuristic-cjk"
        else:
            self.backend = "heuristic"

    # -- single line -------------------------------------------------------
    def line_width(self, text: str) -> float:
        if not text:
            return 0.0
        px = int(round(self.font_size))
        if HAVE_PIL and px >= 4:
            total = 0.0
            run = ""
            run_cjk = False
            for ch in text:
                c = is_cjk(ch)
                if run and c != run_cjk:
                    total += self._run_width(run, run_cjk, px)
                    run = ""
                run += ch
                run_cjk = c
            if run:
                total += self._run_width(run, run_cjk, px)
            return total * SAFETY
        return sum(self._char_width(ch) for ch in text)

    def _run_width(self, run: str, cjk: bool, px: int) -> float:
        font_path = self._cjk if cjk and self._cjk else self._latin
        if font_path and HAVE_PIL:
            try:
                return float(_font(font_path, px).getlength(run))
            except Exception:
                pass
        return sum(self._char_width(ch) for ch in run)

    def _char_width(self, ch: str) -> float:
        size = self.font_size
        if ch == " ":
            return 0.28 * size
        if ch in "\t":
            return 0.6 * size
        if is_cjk(ch):
            return 1.0 * size
        if ch in "ilI.,:;'|!`":
            return 0.30 * size
        if ch in "mwMW@%":
            return 0.82 * size
        if ch.isupper():
            return 0.66 * size
        if ch.isdigit():
            return 0.56 * size
        if ch.islower():
            return 0.52 * size
        if unicodedata.east_asian_width(ch) in ("W", "F"):
            return 1.0 * size
        return 0.45 * size

    # -- wrapping ----------------------------------------------------------
    def wrap(self, text: str, max_width: float) -> list[str]:
        lines: list[str] = []
        for raw_line in str(text).split("\n"):
            if not raw_line:
                lines.append("")
                continue
            lines.extend(self._wrap_one(raw_line, max_width))
        return lines or [""]

    def _wrap_one(self, text: str, max_width: float) -> list[str]:
        if max_width <= 0 or self.line_width(text) <= max_width:
            return [text]
        tokens: list[str] = []
        buf = ""
        buf_cjk = None
        for ch in text:
            c = is_cjk(ch)
            if buf and (c != buf_cjk or c or ch == " "):
                tokens.append(buf)
                buf = ""
            buf += ch
            buf_cjk = c
        if buf:
            tokens.append(buf)

        out: list[str] = []
        cur = ""
        for tok in tokens:
            candidate = cur + tok
            if self.line_width(candidate.rstrip()) <= max_width or not cur:
                cur = candidate
                continue
            if tok.startswith(" "):
                out.append(cur.rstrip())
                cur = tok.lstrip()
                continue
            out.append(cur.rstrip())
            cur = tok
        if cur.strip():
            out.append(cur.rstrip())
        # hard-break anything still too wide (long identifiers / URLs)
        final: list[str] = []
        for line in out:
            if self.line_width(line) <= max_width:
                final.append(line)
                continue
            piece = ""
            for ch in line:
                if piece and self.line_width(piece + ch) > max_width:
                    final.append(piece)
                    piece = ""
                piece += ch
            if piece:
                final.append(piece)
        return final or [""]

    # -- blocks ------------------------------------------------------------
    def measure(self, text: str, max_width: float | None = None) -> tuple[float, float, list[str]]:
        lines = self.wrap(text, max_width) if max_width else str(text).split("\n")
        w = max((self.line_width(line) for line in lines), default=0.0)
        h = len(lines) * self.font_size * LINE_HEIGHT
        return w, h, lines

    def label_box(self, text: str, max_width: float | None = None) -> tuple[float, float, list[str]]:
        w, h, lines = self.measure(text, max_width)
        return w + 2 * EDGE_LABEL_PAD_X, h + 2 * EDGE_LABEL_PAD_Y, lines

    def node_size(
        self,
        label: str,
        min_w: float = 110.0,
        min_h: float = 48.0,
        max_w: float | None = None,
    ) -> tuple[float, float]:
        inner_max = (max_w - 2 * NODE_PAD_X) if max_w else None
        lines = self.wrap(label, inner_max) if inner_max else str(label).split("\n")
        text_w = max((self.line_width(line) for line in lines), default=0.0)
        text_h = len(lines) * self.font_size * LINE_HEIGHT
        w = max(min_w, text_w + 2 * NODE_PAD_X)
        h = max(min_h, text_h + 2 * NODE_PAD_Y)
        if max_w:
            w = min(w, max_w)
        return _round_up(w, 2), _round_up(h, 2)


def _round_up(v: float, step: float = 2.0) -> float:
    return float(int(v / step + 0.999) * step)


@lru_cache(maxsize=32)
def measurer(font_size: float = 12.0, bold: bool = False) -> TextMeasurer:
    return TextMeasurer(font_size, bold)
