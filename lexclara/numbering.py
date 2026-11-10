"""Numbering rules of LC 95/1998, art. 10, and automatic renumbering."""

from __future__ import annotations

import re
from typing import Optional

from .model import Block, Document

_ROMAN = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
          (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]


def int_to_roman(n: int) -> str:
    out = []
    for value, sym in _ROMAN:
        while n >= value:
            out.append(sym)
            n -= value
    return "".join(out)


def roman_to_int(s: str) -> int:
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total, prev = 0, 0
    for ch in reversed(s.upper()):
        v = values.get(ch, 0)
        total = total - v if v < prev else total + v
        prev = max(prev, v)
    return total


def number_of(block: Block) -> Optional[int]:
    """The number a block's label carries (Parágrafo único counts as 1)."""
    label = block.label
    if block.kind in ("artigo", "item"):
        m = re.search(r"\d+", label)
        return int(m.group()) if m else None
    if block.kind == "paragrafo":
        if re.match(r"par[áa]grafo\s+[úu]nico", label, re.I):
            return 1
        m = re.search(r"\d+", label)
        return int(m.group()) if m else None
    if block.kind == "inciso":
        m = re.match(r"[IVXLCDM]+", label)
        return roman_to_int(m.group()) if m else None
    if block.kind == "alinea":
        m = re.match(r"[a-z]", label)
        return ord(m.group()) - 96 if m else None
    return None


def ordinal(n: int) -> str:
    """LC 95, art. 10, I: ordinal up to nine, cardinal followed by period from ten."""
    return f"{n}º" if n <= 9 else f"{n}."


def canonical_label(kind: str, n: int, siblings: int) -> str:
    if kind == "artigo":
        return f"Art. {ordinal(n)}"
    if kind == "paragrafo":
        return "Parágrafo único." if siblings == 1 else f"§ {ordinal(n)}"
    if kind == "inciso":
        return f"{int_to_roman(n)} –"
    if kind == "alinea":
        return f"{chr(96 + n)})"
    if kind == "item":
        return f"{n}."
    return ""


def renumber(doc: Document) -> Document:
    """Rewrite every label from the block's position in the tree.

    Cross-references keep their ``bound_uid``, so references whose text now
    points to a different dispositivo are reported afterwards by
    :func:`lexclara.remissoes.check_references`.
    """
    art_n = 0
    for block in doc.blocks:
        if block.kind == "artigo":
            art_n += 1
            block.label = canonical_label("artigo", art_n, 0)
            _renumber_children(block)
    return doc


def _renumber_children(parent: Block) -> None:
    by_kind: dict[str, list[Block]] = {}
    for child in parent.children:
        by_kind.setdefault(child.kind, []).append(child)
    for kind, group in by_kind.items():
        for i, child in enumerate(group, start=1):
            child.label = canonical_label(kind, i, len(group))
            _renumber_children(child)
