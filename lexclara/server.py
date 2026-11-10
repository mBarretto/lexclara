"""HTTP API and static web editor.

The server is stateless: the browser holds the document and sends it with
each request. That keeps the MVP simple and makes every request replayable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

import anthropic
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import agents, llm, ops
from .model import Block, Document, DocType, Fix
from .numbering import renumber
from .parser import parse

ROOT = Path(__file__).resolve().parent
EXAMPLES = ROOT.parent / "examples"

app = FastAPI(title="LexClara")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


class ParseReq(BaseModel):
    text: str
    doc_type: DocType = "ato_normativo"


class EditReq(BaseModel):
    document: Document
    op: Literal["set_text", "insert_after", "delete", "renumber", "apply_fix", "replace_unit"]
    uid: Optional[str] = None
    text: str = ""
    fix: Optional[Fix] = None


class AgentReq(BaseModel):
    document: Document
    uid: str
    versao: Optional[agents.VersaoCidada] = None


def _state(doc: Document) -> dict:
    findings = ops.check_all(doc)
    return {"document": doc.model_dump(), "findings": [f.model_dump() for f in findings]}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/examples")
def examples() -> list[dict]:
    out = []
    for path, doc_type in (("pl_linguagem_simples.txt", "ato_normativo"), ("despacho_exigencia.txt", "comunicacao")):
        out.append({"id": path, "doc_type": doc_type, "text": (EXAMPLES / path).read_text(encoding="utf-8")})
    return out


@app.post("/api/parse")
def api_parse(req: ParseReq) -> dict:
    return _state(parse(req.text, req.doc_type))


@app.post("/api/check")
def api_check(doc: Document) -> dict:
    return _state(doc)


@app.post("/api/edit")
def api_edit(req: EditReq) -> dict:
    doc = req.document
    try:
        if req.op == "set_text":
            ops.set_text(doc, req.uid, req.text)
        elif req.op == "insert_after":
            ops.insert_after(doc, req.uid, text=req.text)
        elif req.op == "delete":
            ops.delete(doc, req.uid)
        elif req.op == "renumber":
            renumber(doc)
        elif req.op == "apply_fix":
            _apply_fix(doc, req.uid, req.fix)
        elif req.op == "replace_unit":
            _replace_unit(doc, req.uid, req.text)
    except KeyError as exc:
        raise HTTPException(404, f"block not found: {exc}") from exc
    return _state(doc)


def _apply_fix(doc: Document, uid: str | None, fix: Fix | None) -> None:
    block = doc.find(uid or "")
    if block is None or fix is None:
        raise KeyError(uid)
    if block.label == fix.old:
        block.label = fix.new
    else:
        block.text = block.text.replace(fix.old, fix.new, 1)
        for ref in block.refs:  # keep the binding of a repaired reference
            if ref.text == fix.old:
                ref.text = fix.new


def _replace_unit(doc: Document, uid: str | None, text: str) -> None:
    """Replace a block (an article, or a paragraph of a communication) with new text."""
    if doc.doc_type == "comunicacao":
        doc.blocks = parse(text, "comunicacao").blocks
        return
    block = doc.find(uid or "")
    if block is None:
        raise KeyError(uid)
    if block.kind != "artigo":
        block.text = text
        return
    fragment = parse(text, "ato_normativo")
    new_blocks = [b for b in fragment.blocks if b.kind == "artigo"]
    if not new_blocks:
        block.text = text
        return
    for b in new_blocks:
        for sub in b.walk():
            sub.refs = []  # re-bound against the whole document on the next check
    # References to the article, and to dispositivos that keep their place in
    # it, stay bound; anything that moved or vanished is reported by the check.
    new_blocks[0].uid = block.uid
    _keep_uids(block, new_blocks[0])
    idx = doc.blocks.index(block)
    doc.blocks[idx:idx + 1] = new_blocks
    renumber(doc)


def _keep_uids(old: Block, new: Block) -> None:
    for n in new.children:
        o = next((c for c in old.children if c.kind == n.kind and c.label == n.label), None)
        if o is not None:
            n.uid = o.uid
            _keep_uids(o, n)


@app.post("/api/agent/{name}")
def api_agent(name: Literal["tecnica", "cidada", "compreensao"], req: AgentReq) -> dict:
    try:
        if name == "tecnica":
            return agents.tecnica(req.document, req.uid).model_dump()
        if name == "cidada":
            return agents.versao_cidada_verificada(req.document, req.uid).model_dump()
        versao = req.versao or agents.versao_cidada_verificada(req.document, req.uid).final.versao
        return agents.compreensao(req.document, req.uid, versao).model_dump()
    except llm.ReplayMiss as exc:
        raise HTTPException(409, str(exc)) from exc
    except llm.Refused as exc:
        raise HTTPException(422, str(exc)) from exc
    except anthropic.APIError as exc:
        raise HTTPException(502, f"Claude API error: {exc}") from exc
    except TypeError as exc:  # raised by the SDK when no credentials are configured
        raise HTTPException(503, f"No Claude API credentials and no recorded response: {exc}") from exc


@app.post("/api/render", response_class=PlainTextResponse)
def api_render(doc: Document) -> str:
    return doc.render()
