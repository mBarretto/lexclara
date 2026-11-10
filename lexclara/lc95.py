"""Deterministic checks of legislative drafting rules (LC 95/1998).

These rules are cheap, exact and explainable, so they run on every edit
without calling a model. Stylistic judgements that need reading
comprehension (one subject per article, clarity, ordem direta) are left to
the LLM agent in :mod:`lexclara.agents`.
"""

from __future__ import annotations

import re

from .model import Block, Document, Finding, Fix
from .numbering import canonical_label, number_of

# Acronyms that are common enough not to need expansion in a municipal text.
SIGLAS_CONSAGRADAS = {"CPF", "CNPJ", "RG", "CEP", "SP", "LGPD", "PDF", "SUS", "ISS", "IPTU", "ITBI", "LC"}

VAGUE_REF = re.compile(
    r"\b(?:artigo|par[áa]grafo|inciso|al[íi]nea|dispositivo)\s+(anterior|seguinte|precedente|acima|abaixo)\b",
    re.I,
)
REVOGACAO_GENERICA = re.compile(r"revogam-se\s+as\s+disposi[çc][õo]es\s+em\s+contr[áa]rio", re.I)
VIGENCIA = re.compile(r"entra(r[áa])?\s+em\s+vigor", re.I)
SIGLA = re.compile(r"\b([A-ZÁÉÍÓÚÂÊÔÃÕÇ]{2,})\b")
# A number or percentage written only in digits (not a date, law number or year).
NUMERO_SEM_EXTENSO = re.compile(r"(?<![\w./º°-])(\d{1,3}(?:\.\d{3})*(?:,\d+)?)\s*(%|por cento|dias?|meses|anos|horas)(?!\s*\()", re.I)


def check(doc: Document) -> list[Finding]:
    if doc.doc_type != "ato_normativo":
        return _check_siglas(doc)
    findings: list[Finding] = []
    findings += _check_structure(doc)
    findings += _check_numbering(doc)
    for block in doc.walk():
        findings += _check_block(block)
    findings += _check_siglas(doc)
    findings += _check_punctuation(doc)
    return findings


def _f(block: Block, severity: str, rule: str, message: str, excerpt: str = "", fix: Fix | None = None) -> Finding:
    return Finding(block_uid=block.uid, agent="lc95", severity=severity, rule=rule,
                   message=message, excerpt=excerpt, fix=fix)


def _check_structure(doc: Document) -> list[Finding]:
    out = []
    anchor = doc.blocks[0] if doc.blocks else None
    if anchor is None:
        return out
    if not any(b.kind == "ementa" for b in doc.blocks):
        out.append(_f(anchor, "erro", "LC 95/1998, art. 5º",
                      "Falta a ementa, que deve explicitar de modo conciso o objeto da lei."))
    arts = doc.articles()
    if arts and not any(VIGENCIA.search(b.text) for a in arts for b in a.walk()):
        out.append(_f(arts[-1], "erro", "LC 95/1998, art. 8º",
                      "Falta a cláusula de vigência (por exemplo, “Esta Lei entra em vigor na data de sua publicação.”)."))
    return out


def _check_numbering(doc: Document) -> list[Finding]:
    out = []
    arts = doc.articles()
    for i, art in enumerate(arts, start=1):
        n = number_of(art)
        expected = canonical_label("artigo", i, 0)
        if n != i:
            out.append(_f(art, "erro", "LC 95/1998, art. 10",
                          f"Numeração fora de sequência: “{art.label}” deveria ser “{expected}”.",
                          art.label, Fix(old=art.label, new=expected)))
        elif art.label.rstrip() != expected:
            out.append(_f(art, "aviso", "LC 95/1998, art. 10, I",
                          f"Artigos 1 a 9 usam numeração ordinal e, a partir do 10, cardinal seguida de ponto: use “{expected}”.",
                          art.label, Fix(old=art.label, new=expected)))
    for parent in doc.walk():
        groups: dict[str, list[Block]] = {}
        for c in parent.children:
            groups.setdefault(c.kind, []).append(c)
        for kind, group in groups.items():
            for i, child in enumerate(group, start=1):
                expected = canonical_label(kind, i, len(group))
                if number_of(child) != i or (kind == "paragrafo" and child.label.rstrip(" -–—") != expected.rstrip(" -–—")):
                    if kind == "paragrafo" and len(group) == 1:
                        msg = "Parágrafo isolado deve ser “Parágrafo único.”, e não “§ 1º”."
                        rule = "LC 95/1998, art. 10, IV"
                    else:
                        msg = f"Rótulo “{child.label}” fora do padrão ou da sequência: use “{expected}”."
                        rule = "LC 95/1998, art. 10"
                    out.append(_f(child, "aviso", rule, msg, child.label, Fix(old=child.label, new=expected)))
                elif kind == "inciso" and not re.match(r"^[IVXLCDM]+ [–-]$", child.label):
                    out.append(_f(child, "info", "LC 95/1998, art. 10, II (praxe)",
                                  f"Inciso costuma ser grafado com travessão: “{expected}”.",
                                  child.label, Fix(old=child.label, new=expected)))
    return out


def _check_block(block: Block) -> list[Finding]:
    out = []
    for m in VAGUE_REF.finditer(block.text):
        out.append(_f(block, "aviso", "LC 95/1998, art. 11, II, g",
                      f"Remissão vaga “{m.group(0)}”: indique expressamente o dispositivo (por exemplo, “art. 3º”).",
                      m.group(0)))
    if (m := REVOGACAO_GENERICA.search(block.text)):
        out.append(_f(block, "erro", "LC 95/1998, art. 9º",
                      "A cláusula de revogação deve enumerar expressamente as leis ou disposições revogadas; "
                      "“revogam-se as disposições em contrário” não é admitida.",
                      m.group(0)))
    if block.kind in ("artigo", "paragrafo", "inciso", "alinea", "item"):
        for m in NUMERO_SEM_EXTENSO.finditer(block.text):
            out.append(_f(block, "info", "LC 95/1998, art. 11, II, f",
                          f"Números e percentuais devem ser grafados por extenso: “{m.group(0)}”.",
                          m.group(0)))
    return out


def _check_siglas(doc: Document) -> list[Finding]:
    """First occurrence of an acronym must say what it stands for."""
    out = []
    seen: set[str] = set()
    for block in doc.walk():
        if block.kind in ("epigrafe", "fecho", "agrupador"):
            continue
        for m in SIGLA.finditer(block.text):
            sigla = m.group(1)
            if sigla in seen or sigla in SIGLAS_CONSAGRADAS or len(sigla) > 8 or sigla.isdigit():
                continue
            if _is_shouting(block.text, m.start()):
                continue
            seen.add(sigla)
            around = block.text[max(0, m.start() - 120): m.end() + 3]
            explained = "(" + sigla + ")" in around or around.rstrip().endswith(f"{sigla} (") \
                or re.search(rf"{sigla}\s*[(–-]\s*[A-Za-zÀ-ú]", block.text[m.start():m.end() + 30])
            if not explained:
                rule = "LC 95/1998, art. 11, II, e; Lei 15.263/2025, art. 5º"
                out.append(Finding(block_uid=block.uid, agent="lc95", severity="aviso", rule=rule,
                                   message=f"A sigla “{sigla}” aparece pela primeira vez sem o nome por extenso.",
                                   excerpt=sigla))
    return out


def _is_shouting(text: str, pos: int) -> bool:
    """Ignore all-caps runs such as 'A CÂMARA MUNICIPAL DE GARÇA APROVA'."""
    window = text[max(0, pos - 15): pos + 25]
    letters = [c for c in window if c.isalpha()]
    return len(letters) > 12 and sum(c.isupper() for c in letters) / len(letters) > 0.8


def _check_punctuation(doc: Document) -> list[Finding]:
    """Praxe: caput or paragraph that introduces incisos ends with a colon;
    incisos end with semicolon, the last one with a period."""
    out = []
    for parent in doc.walk():
        incisos = [c for c in parent.children if c.kind == "inciso"]
        if not incisos:
            continue
        if parent.text and not parent.text.rstrip().endswith(":"):
            out.append(_f(parent, "info", "Técnica legislativa (praxe)",
                          "O texto que introduz uma enumeração de incisos termina com dois-pontos."))
        for i, inc in enumerate(incisos):
            last = i == len(incisos) - 1
            t = inc.text.rstrip()
            if inc.children:
                continue
            if last and not t.endswith("."):
                out.append(_f(inc, "info", "Técnica legislativa (praxe)", "O último inciso termina com ponto."))
            if not last and not re.search(r";(\s*(e|ou))?$", t):
                out.append(_f(inc, "info", "Técnica legislativa (praxe)", "Incisos intermediários terminam com ponto e vírgula."))
    return out
