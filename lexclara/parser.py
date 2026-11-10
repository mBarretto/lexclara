"""Parse plain text into a block tree following the LC 95/1998 structure.

The parser is deliberately tolerant: it keeps the labels exactly as written
(so the LC 95 checks can complain about them) and never rewrites text.
"""

from __future__ import annotations

import re

from .model import Block, Document, DocType

ROMAN = r"[IVXLCDM]+"

RE_ARTIGO = re.compile(r"^(Art\.?\s*\d+\s*[º°o]?(?:-[A-Z])?\.?|Artigo\s+\d+\s*[º°o]?(?:-[A-Z])?\.?)\s*[-–—]?\s*(.*)$", re.I)
RE_PARAGRAFO = re.compile(r"^(§\s*\d+\s*[º°o]?\.?|Par[áa]grafo\s+[úu]nico\.?)\s*[-–—]?\s*(.*)$", re.I)
RE_INCISO = re.compile(rf"^({ROMAN})\s*([-–—.)])\s*(.*)$")
RE_ALINEA = re.compile(r"^([a-z])\)\s*(.*)$")
RE_ITEM = re.compile(r"^(\d+)[.)]\s+(.*)$")
RE_AGRUPADOR = re.compile(r"^(T[ÍI]TULO|CAP[ÍI]TULO|SE[ÇC][ÃA]O|Se[çc][ãa]o|SUBSE[ÇC][ÃA]O|Subse[çc][ãa]o)\b", re.U)
RE_EPIGRAFE = re.compile(r"^(PROJETO DE LEI|LEI|DECRETO|EMENDA|RESOLU[ÇC][ÃA]O|PORTARIA|PROJETO DE RESOLU[ÇC][ÃA]O)\b.*\d", re.U)
RE_PREAMBULO = re.compile(r"(DECRETA|aprova|APROVA|promulga|PROMULGA|resolve|RESOLVE)\s*:?\s*$")
RE_CONSIDERANDO = re.compile(r"^CONSIDERANDO\b", re.I)
RE_JUSTIFICATIVA = re.compile(r"^(JUSTIFICATIVA|EXPOSI[ÇC][ÃA]O DE MOTIVOS)\s*$", re.I)
RE_FECHO = re.compile(r"^(C[âa]mara Municipal|Prefeitura Municipal|Pal[áa]cio|Sala das Sess[õo]es|Gar[çc]a,)", re.I)


def _normalize_lines(text: str) -> list[str]:
    lines = [ln.strip() for ln in text.replace("\r\n", "\n").split("\n")]
    return [ln for ln in lines if ln]


def parse(text: str, doc_type: DocType = "ato_normativo") -> Document:
    if doc_type == "comunicacao":
        return _parse_comunicacao(text)
    return _parse_ato(text)


def _parse_comunicacao(text: str) -> Document:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    blocks = [Block(kind="paragrafo_texto", text=" ".join(p.split())) for p in paragraphs]
    title = blocks[0].text[:80] if blocks else ""
    return Document(doc_type="comunicacao", title=title, blocks=blocks)


def _parse_ato(text: str) -> Document:
    doc = Document(doc_type="ato_normativo")
    lines = _normalize_lines(text)

    artigo: Block | None = None
    paragrafo: Block | None = None
    inciso: Block | None = None
    alinea: Block | None = None
    seen_preambulo = False
    in_justificativa = False
    in_fecho = False

    def top(block: Block) -> Block:
        doc.blocks.append(block)
        return block

    for ln in lines:
        if in_justificativa:
            top(Block(kind="justificativa", text=ln))
            continue
        if RE_JUSTIFICATIVA.match(ln):
            in_justificativa = True
            top(Block(kind="justificativa", label=ln.upper(), text=""))
            continue

        if not doc.blocks and RE_EPIGRAFE.match(ln):
            top(Block(kind="epigrafe", text=ln))
            doc.title = ln
            continue

        if not seen_preambulo and artigo is None:
            if RE_CONSIDERANDO.match(ln):
                top(Block(kind="considerando", text=ln))
                continue
            if RE_PREAMBULO.search(ln):
                seen_preambulo = True
                top(Block(kind="preambulo", text=ln))
                continue
            if RE_AGRUPADOR.match(ln) is None and RE_ARTIGO.match(ln) is None:
                # Text between epígrafe and preâmbulo is the ementa.
                prev = doc.blocks[-1] if doc.blocks else None
                if prev is not None and prev.kind == "ementa":
                    prev.text = f"{prev.text} {ln}"
                else:
                    top(Block(kind="ementa", text=ln))
                continue

        if in_fecho or (artigo is not None and RE_FECHO.match(ln)):
            in_fecho = True
            top(Block(kind="fecho", text=ln))
            continue

        if RE_AGRUPADOR.match(ln):
            top(Block(kind="agrupador", text=ln))
            continue

        if m := RE_ARTIGO.match(ln):
            artigo = top(Block(kind="artigo", label=_clean_label(m.group(1)), text=m.group(2)))
            paragrafo = inciso = alinea = None
            continue

        if artigo is None:
            top(Block(kind="ementa" if not seen_preambulo else "preambulo", text=ln))
            continue

        if m := RE_PARAGRAFO.match(ln):
            paragrafo = Block(kind="paragrafo", label=_clean_label(m.group(1)), text=m.group(2))
            artigo.children.append(paragrafo)
            inciso = alinea = None
            continue

        if m := RE_INCISO.match(ln):
            inciso = Block(kind="inciso", label=f"{m.group(1)} {m.group(2)}", text=m.group(3))
            (paragrafo or artigo).children.append(inciso)
            alinea = None
            continue

        if (m := RE_ALINEA.match(ln)) and inciso is not None:
            alinea = Block(kind="alinea", label=f"{m.group(1)})", text=m.group(2))
            inciso.children.append(alinea)
            continue

        if (m := RE_ITEM.match(ln)) and alinea is not None:
            alinea.children.append(Block(kind="item", label=f"{m.group(1)}.", text=m.group(2)))
            continue

        # Continuation line: glue to the deepest open block.
        target = alinea or inciso or paragrafo or artigo
        target.text = f"{target.text} {ln}".strip()

    from .remissoes import bind_references  # local import avoids a cycle

    bind_references(doc)
    return doc


def _clean_label(raw: str) -> str:
    return " ".join(raw.split())

