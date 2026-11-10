"""Tests for the deterministic part of LexClara (no model calls)."""

from pathlib import Path

from fastapi.testclient import TestClient

from lexclara import ops
from lexclara.numbering import renumber
from lexclara.parser import parse
from lexclara.server import app

EX = Path(__file__).resolve().parent.parent / "examples"
PL = (EX / "pl_linguagem_simples.txt").read_text(encoding="utf-8")


def rules(doc):
    return {(doc.find(f.block_uid).label or doc.find(f.block_uid).kind, f.rule) for f in ops.check_all(doc)}


def test_parse_structure():
    doc = parse(PL)
    kinds = [b.kind for b in doc.blocks]
    assert kinds[:3] == ["epigrafe", "ementa", "preambulo"]
    arts = doc.articles()
    assert [a.label for a in arts] == [f"Art. {i}º" for i in range(1, 8)]
    art4 = arts[3]
    assert [c.label for c in art4.children] == ["I –", "II –", "III –", "Parágrafo único."]


def test_lc95_findings():
    doc = parse(PL)
    found = rules(doc)
    assert ("Art. 7º", "LC 95/1998, art. 9º") in found  # revogação genérica
    assert ("Art. 5º", "LC 95/1998, art. 11, II, g") in found  # "artigo anterior"
    assert any(r.startswith("LC 95/1998, art. 11, II, e") for _, r in found)  # SAAE sem extenso


def test_broken_reference():
    doc = parse(PL)
    msgs = [f.message for f in ops.check_all(doc) if f.agent == "remissoes"]
    assert any("§ 1º do art. 7º" in m and "não corresponde" in m for m in msgs)


def test_external_reference_is_not_resolved():
    doc = parse(PL)
    art3 = doc.articles()[2]
    assert any(r.external for r in art3.refs)


def test_stale_reference_after_insertion():
    doc = parse(PL)
    ops.insert_after(doc, doc.articles()[1].uid, text="Novo artigo.")
    stale = [f for f in ops.check_all(doc) if f.agent == "remissoes" and f.fix]
    assert len(stale) == 1
    assert stale[0].fix.old == "§ 1º do art. 3º"
    assert stale[0].fix.new == "§ 1º do art. 4º"


def test_renumber_labels():
    doc = parse("Art. 1º Um.\nArt. 3 Dois.\n§ 1º Único parágrafo.\nArt. 4º Esta Lei entra em vigor na data de sua publicação.")
    renumber(doc)
    arts = doc.articles()
    assert [a.label for a in arts] == ["Art. 1º", "Art. 2º", "Art. 3º"]
    assert arts[1].children[0].label == "Parágrafo único."


def test_ordinal_vs_cardinal():
    text = "\n".join(f"Art. {i}º Texto {i}." for i in range(1, 12))
    doc = parse(text)
    msgs = [f.fix.new for f in ops.check_all(doc) if f.rule == "LC 95/1998, art. 10, I"]
    assert "Art. 10." in msgs and "Art. 11." in msgs


def test_api_roundtrip_and_fix():
    client = TestClient(app)
    state = client.post("/api/parse", json={"text": PL, "doc_type": "ato_normativo"}).json()
    doc = state["document"]
    art2 = [b for b in doc["blocks"] if b["label"] == "Art. 2º"][0]
    state = client.post("/api/edit", json={"document": doc, "op": "insert_after", "uid": art2["uid"], "text": "Novo."}).json()
    fix = next(f for f in state["findings"] if f["agent"] == "remissoes" and f["fix"])
    state = client.post("/api/edit", json={"document": state["document"], "op": "apply_fix",
                                           "uid": fix["block_uid"], "fix": fix["fix"]}).json()
    assert not any(f["agent"] == "remissoes" and f["fix"] for f in state["findings"])


def test_comunicacao():
    doc = parse((EX / "despacho_exigencia.txt").read_text(encoding="utf-8"), "comunicacao")
    assert len(doc.blocks) == 6
    assert any("ART" in f.excerpt for f in ops.check_all(doc))


def test_accept_split_article():
    client = TestClient(app)
    doc = client.post("/api/parse", json={"text": PL, "doc_type": "ato_normativo"}).json()["document"]
    art3 = next(b for b in doc["blocks"] if b["label"] == "Art. 3º")
    state = client.post("/api/edit", json={"document": doc, "op": "replace_unit", "uid": art3["uid"],
                                           "text": "Art. 3º Primeiro assunto.\nArt. 3º-A Segundo assunto.\n§ 1º Detalhe."}).json()
    arts = [b for b in state["document"]["blocks"] if b["kind"] == "artigo"]
    assert [a["label"] for a in arts][:5] == ["Art. 1º", "Art. 2º", "Art. 3º", "Art. 4º", "Art. 5º"]
    assert arts[3]["text"] == "Segundo assunto."
    assert arts[2]["uid"] == art3["uid"]
    # The old § 1º of Art. 3º was rewritten away, so the reference to it is flagged.
    msgs = [f["message"] for f in state["findings"] if f["agent"] == "remissoes"]
    assert any("§ 1º do art. 3º" in m and "removido" in m for m in msgs)


def test_accept_rewrite_keeps_paragraph_binding():
    client = TestClient(app)
    doc = client.post("/api/parse", json={"text": PL, "doc_type": "ato_normativo"}).json()["document"]
    art3 = next(b for b in doc["blocks"] if b["label"] == "Art. 3º")
    state = client.post("/api/edit", json={"document": doc, "op": "replace_unit", "uid": art3["uid"],
                                           "text": "Art. 3º Texto novo.\n§ 1º Detalhe novo.\n§ 2º Outro."}).json()
    msgs = [f["message"] for f in state["findings"] if f["agent"] == "remissoes"]
    assert not any("§ 1º do art. 3º" in m for m in msgs)
