"""Block model for municipal legislative texts.

A *block* is one *dispositivo* in the sense of Lei Complementar 95/1998:
ementa, preâmbulo, artigo, parágrafo, inciso, alínea, item, plus the parts
that surround the normative text (epígrafe, considerandos, fecho,
justificativa). Blocks form a tree: incisos hang from the caput of an article
or from a paragraph, alíneas from incisos, items from alíneas.

For official communications (despachos, notificações, exigências) the same
model is used with a flat list of ``paragrafo_texto`` blocks.
"""

from __future__ import annotations

import uuid
from typing import Iterator, Literal, Optional

from pydantic import BaseModel, Field

BlockKind = Literal[
    "epigrafe",
    "ementa",
    "preambulo",
    "considerando",
    "agrupador",  # CAPÍTULO, Seção, Título
    "artigo",
    "paragrafo",
    "inciso",
    "alinea",
    "item",
    "fecho",
    "justificativa",
    "paragrafo_texto",  # free paragraph of an official communication
]

DocType = Literal["ato_normativo", "comunicacao"]

# Kinds that carry normative content and are numbered under LC 95.
DISPOSITIVOS = {"artigo", "paragrafo", "inciso", "alinea", "item"}


def new_uid() -> str:
    return uuid.uuid4().hex[:10]


class Ref(BaseModel):
    """A cross-reference (remissão) found in a block's text.

    ``bound_uid`` is the block the reference pointed to when it was first
    resolved. Labels change when the text is renumbered; uids do not. Comparing
    the two is how stale references are detected after an insertion.
    """

    text: str
    start: int
    end: int
    bound_uid: Optional[str] = None
    external: bool = False  # points to another law; not resolved here


Severity = Literal["erro", "aviso", "info"]


class Fix(BaseModel):
    """A concrete text replacement the author can accept with one click."""

    old: str
    new: str


class Finding(BaseModel):
    block_uid: str
    agent: str  # "lc95", "remissoes", "tecnica", ...
    severity: Severity
    rule: str  # legal basis, e.g. "LC 95/1998, art. 10, I"
    message: str
    excerpt: str = ""
    fix: Optional[Fix] = None


class Block(BaseModel):
    uid: str = Field(default_factory=new_uid)
    kind: BlockKind
    label: str = ""  # "Art. 3º", "§ 1º", "Parágrafo único.", "II –", "a)"
    text: str = ""
    children: list["Block"] = Field(default_factory=list)
    refs: list[Ref] = Field(default_factory=list)

    def display(self) -> str:
        return f"{self.label} {self.text}".strip() if self.label else self.text

    def walk(self) -> Iterator["Block"]:
        yield self
        for child in self.children:
            yield from child.walk()

    def render(self, indent: int = 0) -> str:
        """Plain-text rendering of this block and its subtree."""
        lines = [self.display()]
        for child in self.children:
            lines.append(child.render(indent + 1))
        return "\n".join(lines)


class Document(BaseModel):
    doc_type: DocType = "ato_normativo"
    title: str = ""
    blocks: list[Block] = Field(default_factory=list)

    def walk(self) -> Iterator[Block]:
        for b in self.blocks:
            yield from b.walk()

    def find(self, uid: str) -> Optional[Block]:
        return next((b for b in self.walk() if b.uid == uid), None)

    def parent_of(self, uid: str) -> Optional[Block]:
        for b in self.walk():
            if any(c.uid == uid for c in b.children):
                return b
        return None

    def article_of(self, uid: str) -> Optional[Block]:
        """The article that contains block ``uid`` (or the block itself)."""
        for art in self.walk():
            if art.kind == "artigo" and any(b.uid == uid for b in art.walk()):
                return art
        return None

    def articles(self) -> list[Block]:
        return [b for b in self.walk() if b.kind == "artigo"]

    def render(self) -> str:
        return "\n\n".join(b.render() for b in self.blocks)


Block.model_rebuild()
