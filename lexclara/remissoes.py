"""Cross-reference (remissão) detection, resolution and stale-reference check.

Every reference such as "inciso II do art. 3º" or "§ 1º deste artigo" is
resolved to the block it points to and *bound* to that block's uid. After an
edit or a renumbering the reference is resolved again; if the text now lands
on a different block than the one it was bound to, the reference is stale and
a one-click fix is offered. No LLM is involved: this agent is deterministic.
"""

from __future__ import annotations

import re
from typing import Optional

from .model import Block, Document, Finding, Fix, Ref
from .numbering import int_to_roman, number_of, ordinal, roman_to_int

REF_RE = re.compile(
    r"(?:al[íi]nea\s+[\"“']?(?P<al>[a-z])[\"”']?\s*,?\s*d[oa]\s+)?"
    r"(?:inciso\s+(?P<inc>[IVXLCDM]+)\s*,?\s*(?:d[oa]\s+|(?=deste|desta)))?"
    r"(?:(?:§\s*(?P<par>\d+)\s*[º°o]?|(?P<pu>par[áa]grafo\s+[úu]nico))\s*,?\s*(?:d[oa]\s+)?)?"
    r"(?:caput\s+(?:d[oa]\s+)?)?"
    r"(?:art(?:igo|s?\.)\s*(?P<art>\d+)\s*[º°o]?|(?P<este>deste\s+artigo))?",
    re.I,
)

EXTERNAL_TAIL = re.compile(
    r"^\s*,?\s*(?:d[oa]s?|n[oa])\s+(?:Lei|Decreto|Constitui|C[óo]digo|Estatuto|Resolu|Emenda|LC\b|Medida)",
    re.I,
)


def _components(m: re.Match) -> Optional[dict]:
    comps = {k: v for k, v in m.groupdict().items() if v}
    if not comps:
        return None
    # A bare "§" with no number, or a lone "alínea" with nothing to anchor it,
    # is not useful; require at least one numbered component.
    if not any(k in comps for k in ("art", "par", "pu", "inc", "al")):
        return None
    return comps


def find_references(text: str) -> list[tuple[re.Match, dict, bool]]:
    found = []
    for m in REF_RE.finditer(text):
        if m.start() == m.end():
            continue
        comps = _components(m)
        if comps is None:
            continue
        external = bool(EXTERNAL_TAIL.match(text[m.end():]))
        found.append((m, comps, external))
    return found


def _child(parent: Block, kind: str, n: int) -> Optional[Block]:
    return next((c for c in parent.children if c.kind == kind and number_of(c) == n), None)


def resolve(doc: Document, ctx: Block, comps: dict) -> Optional[Block]:
    """Find the block that a parsed reference points to, from block ``ctx``."""
    if "art" in comps:
        n = int(comps["art"])
        article = next((a for a in doc.articles() if number_of(a) == n), None)
        explicit_article = True
    else:
        article = doc.article_of(ctx.uid)
        explicit_article = "este" in comps
    if article is None:
        return None

    container: Block = article
    if "pu" in comps or "par" in comps:
        n = 1 if "pu" in comps else int(comps["par"])
        container = _child(article, "paragrafo", n)
        if container is None:
            return None
    elif "inc" in comps and not explicit_article:
        # "inciso II" with no anchor: look in the paragraph we are in, if any.
        p = doc.parent_of(ctx.uid)
        while p is not None and p.kind not in ("paragrafo", "artigo"):
            p = doc.parent_of(p.uid)
        if p is not None and p.kind == "paragrafo" and _child(p, "inciso", roman_to_int(comps["inc"])):
            container = p

    target = container
    if "inc" in comps:
        target = _child(container, "inciso", roman_to_int(comps["inc"]))
        if target is None:
            return None
    if "al" in comps:
        target = _child(target, "alinea", ord(comps["al"].lower()) - 96)
    return target


def bind_references(doc: Document) -> None:
    """Resolve every reference once and remember the target's uid."""
    for block in doc.walk():
        block.refs = []
        for m, comps, external in find_references(block.text):
            target = None if external else resolve(doc, block, comps)
            block.refs.append(
                Ref(text=_trim(m.group(0)), start=m.start(), end=m.end(),
                    bound_uid=target.uid if target else None, external=external)
            )


def format_reference(doc: Document, target: Block) -> str:
    """Canonical reference text for ``target``: 'inciso II do § 1º do art. 3º'."""
    parts = []
    node: Optional[Block] = target
    while node is not None and node.kind != "artigo":
        n = number_of(node) or 0
        if node.kind == "alinea":
            parts.append(f'alínea "{chr(96 + n)}"')
        elif node.kind == "inciso":
            parts.append(f"inciso {int_to_roman(n)}")
        elif node.kind == "paragrafo":
            siblings = [c for c in (doc.parent_of(node.uid) or node).children if c.kind == "paragrafo"]
            parts.append("parágrafo único" if len(siblings) == 1 else f"§ {ordinal(n)}")
        node = doc.parent_of(node.uid)
    if node is not None:
        parts.append(f"art. {ordinal(number_of(node) or 0).rstrip('.')}")
    return " do ".join(parts)


def check_references(doc: Document) -> list[Finding]:
    findings: list[Finding] = []
    for block in doc.walk():
        previous = list(block.refs)
        new_refs: list[Ref] = []
        for m, comps, external in find_references(block.text):
            ref_text = _trim(m.group(0))
            prior = next((r for r in previous if r.text == ref_text), None)
            if prior is not None:
                previous.remove(prior)
            if external:
                new_refs.append(Ref(text=ref_text, start=m.start(), end=m.end(), external=True))
                continue
            current = resolve(doc, block, comps)
            bound_uid = prior.bound_uid if prior and prior.bound_uid else (current.uid if current else None)
            new_refs.append(Ref(text=ref_text, start=m.start(), end=m.end(), bound_uid=bound_uid))

            bound = doc.find(bound_uid) if bound_uid else None
            if bound is not None and (current is None or bound.uid != current.uid):
                fixed = format_reference(doc, bound)
                where = f"agora aponta para {current.label.strip()}" if current else "não aponta mais para nenhum dispositivo"
                findings.append(Finding(
                    block_uid=block.uid, agent="remissoes", severity="erro",
                    rule="LC 95/1998, art. 11, II, g",
                    message=(f"Remissão desatualizada após renumeração: “{ref_text}” {where}; "
                             f"o dispositivo originalmente citado é hoje “{fixed}”."),
                    excerpt=ref_text,
                    fix=Fix(old=ref_text, new=_keep_style(ref_text, fixed))))
            elif bound_uid and bound is None:
                findings.append(Finding(
                    block_uid=block.uid, agent="remissoes", severity="erro",
                    rule="LC 95/1998, art. 11, II, g",
                    message=f"A remissão “{ref_text}” apontava para um dispositivo que foi removido.",
                    excerpt=ref_text))
            elif current is None:
                findings.append(Finding(
                    block_uid=block.uid, agent="remissoes", severity="erro",
                    rule="LC 95/1998, art. 11, II, g",
                    message=f"A remissão “{ref_text}” não corresponde a nenhum dispositivo deste texto.",
                    excerpt=ref_text))
        block.refs = new_refs
    return findings


def _keep_style(old: str, new: str) -> str:
    """Keep 'Art.' capitalised if the original was."""
    if old[:1].isupper():
        return new[:1].upper() + new[1:]
    return new


def _trim(ref: str) -> str:
    return re.sub(r"[\s,]+(?:d[oa])?\s*$", "", ref.strip())
