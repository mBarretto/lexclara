"""LLM agents that work on one block (an article and its subtree, or a
paragraph of an official communication).

* ``tecnica``      – drafting review under LC 95/1998, art. 11, with a proposed text.
* ``cidada``       – a plain-language explanation that sits beside the official text.
* ``fidelidade``   – an adversarial check that the explanation did not change
                     any obligation, prohibition, deadline, subject or sanction.
* ``compreensao``  – a comprehension test: questions written from the official
                     text, answered by simulated readers who see only one version.

``versao_cidada_verificada`` chains the second and third agents in a loop:
draft, verify, redraft with the verifier's objections, verify again.

The official text is never changed by an agent. Every output is a suggestion
the author accepts or rejects.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from . import llm
from .model import Block, Document

# --------------------------------------------------------------------------
# Shared context
# --------------------------------------------------------------------------

PREAMBLE = """Você é um dos agentes do LexClara, um editor de textos legislativos e de \
comunicações oficiais do Município de Garça (SP). O LexClara apoia servidores, \
vereadores e assessores a escrever textos corretos segundo a Lei Complementar nº 95/1998 \
(técnica legislativa) e compreensíveis segundo a Lei nº 15.263/2025 (Política Nacional \
de Linguagem Simples), cujo critério é o resultado no leitor: o cidadão precisa conseguir \
encontrar, entender e usar a informação, e a compreensão deve ser testada com o público.

Regras que valem para todos os agentes:
- O texto oficial é a fonte. Nunca invente obrigações, direitos, prazos, valores ou exceções.
- Quando algo for ambíguo no texto oficial, diga que é ambíguo em vez de escolher uma leitura.
- Escreva em português do Brasil.

A seguir está o documento completo em edição. Os rótulos (Art. 1º, § 1º, I –, a)) \
identificam os dispositivos."""


def document_context(doc: Document) -> str:
    kind = "ato normativo" if doc.doc_type == "ato_normativo" else "comunicação oficial"
    return f"{PREAMBLE}\n\nTipo de documento: {kind}\n\n<documento>\n{doc.render()}\n</documento>"


DOC_UID = "__doc__"


def unit_of(doc: Document, uid: str) -> Block:
    """The unit an agent works on.

    In a normative act it is the whole article: a click on an inciso selects
    its article. An official communication is short and must be understood as
    a whole, so its unit is the entire text.
    """
    if doc.doc_type == "comunicacao":
        return Block(uid=DOC_UID, kind="paragrafo_texto", text=doc.render())
    if doc.doc_type == "ato_normativo":
        art = doc.article_of(uid)
        if art is not None:
            return art
    block = doc.find(uid)
    if block is None:
        raise KeyError(uid)
    return block


# --------------------------------------------------------------------------
# 1. Técnica legislativa (LLM part)
# --------------------------------------------------------------------------

class ProblemaTecnico(BaseModel):
    regra: str = Field(description="Fundamento, por exemplo 'LC 95/1998, art. 11, III, c'.")
    trecho: str = Field(description="Trecho exato do texto oficial em que está o problema.")
    problema: str
    sugestao: str


class RevisaoTecnica(BaseModel):
    problemas: list[ProblemaTecnico]
    redacao_sugerida: str = Field(
        description="Nova redação completa do dispositivo, com rótulos, ou string vazia se não houver mudança.")
    observacao: str = Field(description="Uma ou duas frases para o autor sobre a nova redação.")


TECNICA_ATO = """Sua tarefa: revisar a técnica legislativa do dispositivo indicado.

Verifique sobretudo o que regras automáticas não conseguem verificar:
- LC 95/1998, art. 11, I (clareza): palavras no sentido comum; frases curtas e concisas; \
ordem direta; uniformidade do tempo verbal, de preferência o presente ou o futuro simples \
do presente; sem preciosismo, neologismo ou adjetivação dispensável.
- LC 95/1998, art. 11, II (precisão): a mesma palavra para a mesma ideia ao longo do texto; \
sem expressões de duplo sentido.
- LC 95/1998, art. 11, III (ordem lógica): cada artigo trata de um único assunto ou princípio; \
o caput traz a norma geral, os parágrafos trazem aspectos complementares e exceções, e as \
enumerações vão em incisos, alíneas e itens.
- LC 95/1998, art. 7º: o dispositivo deve tratar do objeto da lei.

Não repita problemas de numeração, siglas, remissões ou pontuação; outro agente já os verifica.
Se o dispositivo trata de mais de um assunto, a redação sugerida pode desdobrá-lo em mais de \
um artigo; use números provisórios a partir do número atual (por exemplo, Art. 3º e Art. 3º-A).
Preserve exatamente o sentido jurídico. Se não houver problema relevante, devolva lista vazia \
e redação sugerida vazia."""

TECNICA_COMUNICACAO = """Sua tarefa: revisar esta comunicação oficial.

Verifique: se a informação mais importante vem primeiro (o que foi decidido e o que o \
destinatário deve fazer); se há prazo e consequência explícitos; ordem direta e frases curtas; \
termos técnicos ou jurídicos sem explicação; siglas sem o nome por extenso; voz passiva e \
nominalizações que escondem quem deve agir. Fundamente em 'Lei 15.263/2025, art. 5º' ou \
'Lei 13.460/2017' conforme o caso.

A redação sugerida substitui o texto do próprio ato, então deve manter o sentido jurídico \
e a precisão (números de processo, prazos, fundamentos legais). Se não houver problema \
relevante, devolva lista vazia e redação sugerida vazia. Separe os parágrafos da redação \
sugerida com uma linha em branco."""


def tecnica(doc: Document, uid: str) -> RevisaoTecnica:
    unit = unit_of(doc, uid)
    return llm.call(
        agent="tecnica",
        document_context=document_context(doc),
        instructions=TECNICA_ATO if doc.doc_type == "ato_normativo" else TECNICA_COMUNICACAO,
        prompt=f"Dispositivo a revisar:\n\n{unit.render()}",
        schema=RevisaoTecnica,
        effort="high",
    )


# --------------------------------------------------------------------------
# 2. Versão cidadã
# --------------------------------------------------------------------------

class Termo(BaseModel):
    termo: str
    explicacao: str


class VersaoCidada(BaseModel):
    resumo: str = Field(description="Uma frase: o que este dispositivo faz, dito para qualquer pessoa.")
    explicacao: str = Field(description="A explicação em linguagem simples, em poucas frases curtas.")
    quem_e_afetado: str
    o_que_fazer: list[str] = Field(description="Ações que alguém precisa tomar. Lista vazia se não houver.")
    prazos: list[str]
    termos: list[Termo] = Field(description="Palavras difíceis que permaneceram, com explicação.")


CIDADA = """Sua tarefa: escrever a versão cidadã do dispositivo indicado. Ela será publicada \
ao lado do texto oficial, como explicação, e não o substitui.

Leitor-alvo: um morador de Garça com ensino fundamental, que lê com alguma dificuldade e não \
conhece termos jurídicos. Use as técnicas de linguagem simples da Lei 15.263/2025 (art. 5º) e \
da ISO 24495-1: informação mais importante primeiro; frases curtas em ordem direta; voz ativa, \
dizendo quem faz o quê; palavras comuns; termos técnicos explicados; siglas por extenso; \
números escritos de forma clara; uma ideia por frase. Fale com o leitor quando ele for quem \
deve agir ("você").

Fidelidade é obrigatória: mantenha todo sujeito, obrigação, proibição, permissão, condição, \
prazo, valor e consequência do texto oficial. Não acrescente nada que o texto não diga, nem \
exemplos que pareçam regras. Se uma regra depende de outro dispositivo, diga isso em palavras \
simples. Se o texto oficial for ambíguo, diga que ainda não está definido."""


def cidada(doc: Document, uid: str, objecoes: list[str] | None = None,
           anterior: VersaoCidada | None = None) -> VersaoCidada:
    unit = unit_of(doc, uid)
    prompt = f"Dispositivo:\n\n{unit.render()}"
    if objecoes:
        prompt += (
            "\n\nUma versão anterior foi rejeitada pelo verificador de fidelidade.\n"
            f"Versão anterior:\n{anterior.model_dump_json(indent=1) if anterior else ''}\n\n"
            "Objeções do verificador:\n- " + "\n- ".join(objecoes)
            + "\n\nEscreva uma nova versão que resolva todas as objeções."
        )
    return llm.call(agent="cidada", document_context=document_context(doc), instructions=CIDADA,
                    prompt=prompt, schema=VersaoCidada, effort="medium")


def render_cidada(v: VersaoCidada) -> str:
    parts = [v.resumo, v.explicacao, f"Quem é afetado: {v.quem_e_afetado}"]
    if v.o_que_fazer:
        parts.append("O que fazer: " + "; ".join(v.o_que_fazer))
    if v.prazos:
        parts.append("Prazos: " + "; ".join(v.prazos))
    if v.termos:
        parts.append("Palavras: " + "; ".join(f"{t.termo}: {t.explicacao}" for t in v.termos))
    return "\n".join(parts)


# --------------------------------------------------------------------------
# 3. Fidelidade jurídica
# --------------------------------------------------------------------------

TipoAtomo = Literal["obrigacao", "proibicao", "permissao", "direito", "prazo", "condicao",
                    "excecao", "sancao", "competencia", "definicao", "remissao"]


class Atomo(BaseModel):
    tipo: TipoAtomo
    enunciado: str = Field(description="A proposição normativa, extraída do texto oficial.")
    dispositivo: str = Field(description="Rótulo do dispositivo de origem, por exemplo 'Art. 4º, I'.")
    status: Literal["preservado", "alterado", "omitido"]
    evidencia: str = Field(description="Trecho da versão cidadã que corresponde, ou string vazia.")
    comentario: str


class Fidelidade(BaseModel):
    atomos: list[Atomo]
    acrescimos: list[str] = Field(description="Afirmações da versão cidadã sem apoio no texto oficial.")
    veredito: Literal["fiel", "fiel_com_ressalvas", "infiel"]

    def objecoes(self) -> list[str]:
        out = [f"{a.dispositivo}: {a.tipo} {a.status} — {a.enunciado}. {a.comentario}"
               for a in self.atomos if a.status != "preservado"]
        out += [f"Acréscimo sem base no texto oficial: {x}" for x in self.acrescimos]
        return out


FIDELIDADE = """Sua tarefa: verificar, de forma adversarial, se a versão cidadã preserva o \
sentido jurídico do texto oficial. Você não escreveu a versão cidadã e não deve defendê-la.

Passo 1. Decomponha o texto oficial em proposições normativas atômicas: cada obrigação, \
proibição, permissão, direito, prazo, condição, exceção, sanção, competência (quem faz), \
definição e remissão. Uma proposição por item.
Passo 2. Para cada proposição, procure na versão cidadã o trecho correspondente e classifique:
- preservado: o mesmo sujeito, a mesma força (deve / pode / é vedado), o mesmo objeto, \
o mesmo prazo e a mesma condição;
- alterado: aparece, mas mudou algo (por exemplo, "deverá" virou "poderá", um prazo mudou, \
o sujeito mudou, uma condição ou exceção sumiu);
- omitido: não aparece.
Passo 3. Liste afirmações da versão cidadã que o texto oficial não sustenta.
Veredito: "fiel" se tudo estiver preservado e não houver acréscimos; "infiel" se alguma \
obrigação, proibição, prazo, sanção ou sujeito foi alterado ou omitido, ou se houver acréscimo \
que possa induzir o leitor a erro; "fiel_com_ressalvas" nos demais casos. Simplificar a \
linguagem não é alterar; perder uma condição é."""


def fidelidade(doc: Document, uid: str, versao: VersaoCidada) -> Fidelidade:
    unit = unit_of(doc, uid)
    prompt = (f"Texto oficial:\n\n{unit.render()}\n\n"
              f"Versão cidadã a verificar:\n\n{render_cidada(versao)}")
    return llm.call(agent="fidelidade", document_context=document_context(doc), instructions=FIDELIDADE,
                    prompt=prompt, schema=Fidelidade, effort="high")


class Rodada(BaseModel):
    versao: VersaoCidada
    verificacao: Fidelidade


class VersaoVerificada(BaseModel):
    rodadas: list[Rodada]

    @property
    def final(self) -> Rodada:
        return self.rodadas[-1]


def versao_cidada_verificada(doc: Document, uid: str, max_rodadas: int = 3) -> VersaoVerificada:
    """Draft → verify → redraft with objections, until the verifier accepts."""
    rodadas: list[Rodada] = []
    versao = cidada(doc, uid)
    for _ in range(max_rodadas):
        check = fidelidade(doc, uid, versao)
        rodadas.append(Rodada(versao=versao, verificacao=check))
        if check.veredito == "fiel":
            break
        versao = cidada(doc, uid, objecoes=check.objecoes(), anterior=versao)
    return VersaoVerificada(rodadas=rodadas)


# --------------------------------------------------------------------------
# 4. Teste de compreensão com leitores simulados
# --------------------------------------------------------------------------

class Pergunta(BaseModel):
    pergunta: str
    alternativas: list[str] = Field(description="Exatamente quatro alternativas.")
    correta: int = Field(description="Índice (0 a 3) da alternativa correta segundo o texto oficial.")
    dispositivo: str
    por_que_importa: str


class Questionario(BaseModel):
    perguntas: list[Pergunta]


QUESTIONARIO = """Sua tarefa: escrever um questionário de compreensão sobre o dispositivo \
indicado, a partir do texto oficial apenas.

Escreva de 3 a 5 perguntas de múltipla escolha sobre o que um cidadão precisa saber para \
usar a informação: quem deve fazer o quê, o que é proibido ou permitido, prazos, condições, \
exceções e consequências. Cada pergunta tem exatamente quatro alternativas plausíveis e uma \
só correta segundo o texto oficial. As alternativas erradas devem refletir leituras erradas \
comuns (confundir quem age, trocar "deve" por "pode", errar o prazo). Não faça perguntas que \
se respondem por conhecimento geral sem ler o texto. Escreva as perguntas em linguagem simples, \
para que a dificuldade esteja no texto avaliado e não na pergunta."""


class Leitor(BaseModel):
    id: str
    nome: str
    perfil: str


LEITORES = [
    Leitor(id="maria", nome="Dona Maria",
           perfil="68 anos, aposentada, estudou até a 4ª série, lê devagar e desiste de frases longas; "
                  "não conhece palavras jurídicas nem siglas; usa o celular para o WhatsApp."),
    Leitor(id="joao", nome="Seu João",
           perfil="45 anos, dono de uma mercearia, ensino médio completo, tem pressa e lê procurando "
                  "o que precisa fazer e até quando; conhece alvará e nota fiscal, mas não termos jurídicos."),
    Leitor(id="ana", nome="Ana",
           perfil="26 anos, servidora recém-chegada à Prefeitura, formada em administração, lê com "
                  "atenção, mas não tem formação jurídica."),
]


class Resposta(BaseModel):
    pergunta: int
    escolha: int = Field(description="Índice da alternativa escolhida (0 a 3), ou -1 para 'não sei'.")
    confianca: Literal["baixa", "media", "alta"]


class Leitura(BaseModel):
    respostas: list[Resposta]
    palavras_dificeis: list[str]
    comentario: str = Field(description="Em primeira pessoa e com a voz do leitor: o que ficou confuso.")


def _instr_leitor(leitor: Leitor) -> str:
    return f"""Sua tarefa: simular a leitura de um texto por uma pessoa real, para testar se o \
texto é compreensível para ela. Leia apenas o texto que vem na mensagem, como se fosse a \
única coisa que essa pessoa recebeu.

Quem lê: {leitor.nome}, {leitor.perfil}

Leia o texto como essa pessoa leria e responda às perguntas com o que ela entenderia. Não use \
o seu próprio conhecimento de direito nem de administração pública. Se a pessoa não \
conseguiria responder com segurança a partir do texto, escolha -1 ("não sei"); é melhor \
dizer "não sei" do que acertar por dedução que essa pessoa não faria. Liste as palavras ou \
expressões que essa pessoa não entenderia."""


LEITOR_CONTEXT = ("Você participa de um teste de compreensão de textos públicos do LexClara. "
                  "Você simula leitores reais e não tem acesso a nenhum outro texto além do que receber.")


class ResultadoLeitor(BaseModel):
    leitor: str
    versao: Literal["oficial", "cidada"]
    acertos: int
    total: int
    nao_sei: int
    palavras_dificeis: list[str]
    comentario: str


class TesteCompreensao(BaseModel):
    questionario: Questionario
    resultados: list[ResultadoLeitor]


def compreensao(doc: Document, uid: str, versao: VersaoCidada | None) -> TesteCompreensao:
    unit = unit_of(doc, uid)
    ctx = document_context(doc)
    q = llm.call(agent="questionario", document_context=ctx, instructions=QUESTIONARIO,
                 prompt=f"Dispositivo:\n\n{unit.render()}", schema=Questionario, effort="high")

    perguntas_txt = "\n\n".join(
        f"Pergunta {i}: {p.pergunta}\n" + "\n".join(f"  {j}) {alt}" for j, alt in enumerate(p.alternativas))
        for i, p in enumerate(q.perguntas)
    )
    textos = {"oficial": unit.render()}
    if versao is not None:
        textos["cidada"] = render_cidada(versao)

    resultados = []
    for nome_versao, texto in textos.items():
        for leitor in LEITORES:
            leitura = llm.call(
                # Readers must not see the rest of the document: they get only the
                # text under test, so the score reflects that text alone.
                agent=f"leitor-{leitor.id}", document_context=LEITOR_CONTEXT, instructions=_instr_leitor(leitor),
                prompt=f"Texto recebido:\n\n{texto}\n\nPerguntas:\n\n{perguntas_txt}",
                schema=Leitura, effort="low",
            )
            escolhas = {r.pergunta: r.escolha for r in leitura.respostas}
            acertos = sum(1 for i, p in enumerate(q.perguntas) if escolhas.get(i) == p.correta)
            nao_sei = sum(1 for i in range(len(q.perguntas)) if escolhas.get(i, -1) == -1)
            resultados.append(ResultadoLeitor(
                leitor=leitor.nome, versao=nome_versao, acertos=acertos, total=len(q.perguntas),
                nao_sei=nao_sei, palavras_dificeis=leitura.palavras_dificeis, comentario=leitura.comentario))
    return TesteCompreensao(questionario=q, resultados=resultados)
