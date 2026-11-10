"""Agent orchestration tests with a fake model (no API calls)."""

from pathlib import Path

import pytest

from lexclara import agents, cli, llm
from lexclara.parser import parse

EX = Path(__file__).resolve().parent.parent / "examples"


@pytest.fixture
def fake_llm(monkeypatch):
    calls = []
    verdicts = iter(["infiel", "fiel"])

    def fake_call(*, agent, document_context, instructions, prompt, schema, effort="medium", mode=None):
        calls.append({"agent": agent, "context": document_context, "prompt": prompt})
        if schema is agents.RevisaoTecnica:
            return schema(problemas=[], redacao_sugerida="", observacao="")
        if schema is agents.VersaoCidada:
            return schema(resumo="r", explicacao="e", quem_e_afetado="q", o_que_fazer=[], prazos=["10 dias"], termos=[])
        if schema is agents.Fidelidade:
            v = next(verdicts)
            atom = agents.Atomo(tipo="prazo", enunciado="prazo de 10 dias", dispositivo="Art. 3º",
                                status="preservado" if v == "fiel" else "omitido", evidencia="", comentario="c")
            return schema(atomos=[atom], acrescimos=[], veredito=v)
        if schema is agents.Questionario:
            p = agents.Pergunta(pergunta="?", alternativas=["a", "b", "c", "d"], correta=2,
                                dispositivo="Art. 3º", por_que_importa="x")
            return schema(perguntas=[p, p])
        if schema is agents.Leitura:
            return schema(respostas=[agents.Resposta(pergunta=0, escolha=2, confianca="alta"),
                                     agents.Resposta(pergunta=1, escolha=-1, confianca="baixa")],
                          palavras_dificeis=["jargão"], comentario="ok")
        raise AssertionError(schema)

    monkeypatch.setattr(llm, "call", fake_call)
    return calls


def test_draft_verify_loop_redrafts_with_objections(fake_llm):
    doc = parse((EX / "pl_linguagem_simples.txt").read_text(encoding="utf-8"))
    uid = doc.articles()[2].children[0].uid  # a paragraph selects its article
    out = agents.versao_cidada_verificada(doc, uid)
    assert [r.verificacao.veredito for r in out.rodadas] == ["infiel", "fiel"]
    redraft = [c for c in fake_llm if c["agent"] == "cidada"][1]
    assert "Objeções do verificador" in redraft["prompt"] and "omitido" in redraft["prompt"]


def test_readers_see_only_the_text_under_test(fake_llm):
    doc = parse((EX / "pl_linguagem_simples.txt").read_text(encoding="utf-8"))
    versao = agents.VersaoCidada(resumo="r", explicacao="e", quem_e_afetado="q", o_que_fazer=[], prazos=[], termos=[])
    res = agents.compreensao(doc, doc.articles()[3].uid, versao)
    assert len(res.resultados) == 2 * len(agents.LEITORES)
    assert all(r.acertos == 1 and r.nao_sei == 1 and r.total == 2 for r in res.resultados)
    for c in fake_llm:
        if c["agent"].startswith("leitor-"):
            assert "<documento>" not in c["context"]
            assert "Art. 1º" not in c["prompt"]  # no other article leaks in


def test_communication_is_one_unit(fake_llm):
    doc = parse((EX / "despacho_exigencia.txt").read_text(encoding="utf-8"), "comunicacao")
    agents.tecnica(doc, doc.blocks[2].uid)
    assert "sob pena de arquivamento" in fake_llm[0]["prompt"] and "Interessado" in fake_llm[0]["prompt"]


def test_demo_report(fake_llm, tmp_path, monkeypatch):
    out = tmp_path / "report.md"
    monkeypatch.setattr(agents, "versao_cidada_verificada",
                        lambda d, u: agents.VersaoVerificada(rodadas=[agents.Rodada(
                            versao=agents.VersaoCidada(resumo="r", explicacao="e", quem_e_afetado="q",
                                                       o_que_fazer=[], prazos=[], termos=[]),
                            verificacao=agents.Fidelidade(atomos=[], acrescimos=[], veredito="fiel"))]))
    cli.demo(out)
    text = out.read_text(encoding="utf-8")
    assert "§ 1º do art. 3º” → “§ 1º do art. 4º" in text
    assert "| Dona Maria | 1/2 (1 não sei) | 1/2 (1 não sei) |" in text
