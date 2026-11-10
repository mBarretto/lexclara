"""Structural edits on the block tree, plus the full deterministic check."""

from __future__ import annotations

from . import lc95, remissoes
from .model import Block, Document, Finding
from .numbering import renumber


def _siblings(doc: Document, uid: str) -> list[Block]:
    parent = doc.parent_of(uid)
    return parent.children if parent is not None else doc.blocks


def insert_after(doc: Document, uid: str, kind: str | None = None, text: str = "") -> Block:
    """Insert a new sibling right after ``uid`` and renumber the document."""
    siblings = _siblings(doc, uid)
    idx = next(i for i, b in enumerate(siblings) if b.uid == uid)
    new = Block(kind=kind or siblings[idx].kind, text=text)
    siblings.insert(idx + 1, new)
    if doc.doc_type == "ato_normativo":
        renumber(doc)
    return new


def delete(doc: Document, uid: str) -> None:
    siblings = _siblings(doc, uid)
    siblings[:] = [b for b in siblings if b.uid != uid]
    if doc.doc_type == "ato_normativo":
        renumber(doc)


def set_text(doc: Document, uid: str, text: str) -> None:
    block = doc.find(uid)
    if block is None:
        raise KeyError(uid)
    block.text = text


def check_all(doc: Document) -> list[Finding]:
    """Run every deterministic agent. Fast enough to run on each keystroke pause."""
    findings = lc95.check(doc)
    if doc.doc_type == "ato_normativo":
        findings += remissoes.check_references(doc)
    return findings
