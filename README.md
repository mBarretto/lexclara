# LexClara

**An agentic, block-based editor for municipal legislative texts and official communications.**

LexClara helps the people who write a city's laws and letters (civil servants, city councillors and their legislative staff) produce texts that are correct under Brazilian legislative drafting rules *and* understood by the citizens they address. Each block of the text is a *dispositivo* (article, paragraph, inciso, alínea), and a small team of agents works on each block: deterministic checkers, a drafting reviewer, a plain-language writer, an adversarial fidelity verifier and a panel of simulated readers.

It is being built as the proposed solution in a **CPSI** (Contrato Público para Solução Inovadora, the Brazilian public procurement instrument for innovative solutions, LC 182/2021) for the municipality of **Garça, São Paulo**, and is designed to be reused by other municipalities.

> Status: working MVP (November 2026). The deterministic layer, the web editor and the agent pipeline run end to end. A full recorded run with `claude-opus-5-5` is in [`examples/demo_report.md`](examples/demo_report.md) and can be replayed without an API key; see [Reproducible demo](#reproducible-demo).

---

## Why this exists

Brazil's **Lei 15.263/2025** (National Plain Language Policy) applies to every level and branch of government. It defines plain language by its *effect on the reader*: the words, structure and layout must let a citizen easily **find, understand and use** the information (art. 4º, following ISO 24495-1:2023). It also requires that comprehension be **tested with the target audience** (art. 5º, XVIII). Older laws point the same way: Lei 13.460/2017 (public-service users' rights) and Lei 13.726/2018 (red-tape reduction).

At the same time, laws and decrees must follow **Lei Complementar 95/1998**, which fixes the structure (ementa, articles, paragraphs, incisos, alíneas, items), the numbering, the drafting style and the way provisions refer to each other.

Garça's problem statement for the CPSI names the gap: the civil servants who write despachos, requirements and notifications, and the legislative staff who draft bills, do so with no support, and the city has no way to know whether any text was understood. Training does not reach everyone and does not survive staff turnover. Two of the technological uncertainties listed in the problem statement are exactly what this project tests:

- **Legal fidelity.** Can we produce a comprehensible version of an official act without changing its legal meaning, with traceability to the official source?
- **Measuring comprehension.** Do automated comprehension assessments correlate with real comprehension by different reader profiles well enough to meet the legal testing requirement at scale?

## The concept

1. **The block is the dispositivo.** A text is parsed into the tree LC 95 defines. In a communication (despacho, notificação, exigência), the blocks are paragraphs and the whole letter is analysed as one unit.
2. **Agents attach to blocks, not to the document.** Selecting an inciso selects its article; the agents work article by article, with the whole document as context.
3. **The official text never changes without a human.** Every agent output is a finding or a suggestion with *Accept* / *Reject*. The plain-language version is published **beside** the official text and does not replace it.
4. **Cheap checks run on every edit; model calls run on demand.** Numbering, cross-references, acronyms and revocation clauses are checked deterministically. Judgement calls (one subject per article, clarity, fidelity, comprehension) go to Claude.

## Architecture

```
                         ┌──────────────────────────────────────────────┐
  plain text ──parse──▶  │  Document = tree of Blocks (LC 95 structure) │
                         │  each Block: uid · kind · label · text · refs│
                         └──────────────┬───────────────────────────────┘
                                        │
          every edit (no model)         │          on demand, per article (Claude)
      ┌─────────────────────────────────┼──────────────────────────────────────────┐
      ▼                                 ▼                                          ▼
 ┌──────────┐   ┌──────────────┐   ┌───────────┐   ┌─────────────────────────────────────────┐
 │ lc95     │   │ remissoes    │   │ tecnica   │   │ cidada ──▶ fidelidade ──(objections)──┐ │
 │ numbering│   │ resolve refs │   │ LC 95 art.│   │   ▲                                   │ │
 │ siglas   │   │ bind to uid  │   │ 11 review │   │   └───────────── redraft ◀────────────┘ │
 │ vigência │   │ detect stale │   │ + proposed│   │                      │ final version     │
 │ revogação│   │ refs after   │   │   text    │   │                      ▼                   │
 └──────────┘   │ renumbering  │   └───────────┘   │ questionario (from the OFFICIAL text)    │
                └──────────────┘                   │        │                                 │
                                                   │        ▼                                 │
                                                   │ simulated readers × {official, plain}   │
                                                   │ (each reader sees ONLY one text)        │
                                                   └─────────────────────────────────────────┘
```

### Deterministic agents (`lexclara/lc95.py`, `lexclara/remissoes.py`)

- **LC 95 checks**: missing ementa (art. 5º) or vigência clause (art. 8º); the generic "revogam-se as disposições em contrário" (art. 9º); ordinal numbering up to the ninth article and cardinal from the tenth (art. 10, I); a lone paragraph must be "Parágrafo único" (art. 10); vague references such as "artigo anterior" (art. 11, II, g); acronyms without their full name on first use (art. 11, II, e; Lei 15.263/2025, art. 5º); numbers written only in digits (art. 11, II, f); punctuation of enumerations (drafting practice).
- **Cross-references bound to blocks, not numbers.** Each reference ("inciso I do caput deste artigo", "§ 1º do art. 3º") is resolved once and bound to the target block's uid. When an amendment inserts or removes an article and the text is renumbered, the reference is resolved again; if its words now land on a different block, the editor reports it as stale and offers the corrected wording. References to other laws ("art. 5º da Lei Federal nº 15.263") are recognised as external and left alone.

### Claude agents (`lexclara/agents.py`)

| Agent | Input | Output | Effort |
|---|---|---|---|
| `tecnica` | one article (or the whole communication) | problems with legal basis (LC 95, art. 11: clarity, precision, logical order, one subject per article) and a proposed text the author can accept | high |
| `cidada` | one article | a plain-language explanation: summary, explanation, who is affected, what to do, deadlines, glossary | medium |
| `fidelidade` | official text + plain version | the official text decomposed into atomic normative propositions (obligation, prohibition, permission, right, deadline, condition, exception, sanction, competence, definition, reference), each marked *preserved / altered / omitted*, plus unsupported additions and a verdict | high |
| `questionario` | official text only | 3–5 multiple-choice questions about what a citizen needs to know to act, with distractors that encode common misreadings | high |
| `leitor-*` | **only** the text under test + the questions | answers (or "não sei"), confidence, hard words, a first-person comment | low |

**The draft–verify loop.** `versao_cidada_verificada` asks `cidada` for a draft, has `fidelidade` attack it, and sends the objections back for a redraft, for up to three rounds. The writer and the verifier never share a conversation; the verifier is told it did not write the text and must not defend it.

**The comprehension test.** Questions are written from the official text, so the answer key is the law, not the paraphrase. Three simulated readers (a 68-year-old retiree with four years of schooling, a shopkeeper in a hurry, a newly hired civil servant with no legal training) answer the same questions twice: once from the official text and once from the plain version. Each reader call contains only the text under test, never the rest of the document, so a reader cannot answer from context it would not have. The result is a small table: correct answers per reader, per version.

### Implementation notes

- **Structured outputs.** Every agent returns a Pydantic model through `client.beta.messages.parse(..., output_format=Model)`; the editor never parses free text.
- **Prompt caching.** The system prompt has two parts: a shared preamble with the whole document, marked `cache_control: ephemeral`, then the agent's own instructions. All per-article calls of all agents on the same document reuse the cached prefix.
- **Refusal fallback.** Calls opt into the server-side refusal fallback (`fallbacks: "default"`), so a false-positive safety decline is retried on a fallback model instead of failing the edit.
- **Record / replay.** `lexclara/llm.py` hashes each request (model, system, prompt, schema, effort) and stores the validated response under `examples/cache/`. `--replay` uses only recorded responses (no API key, fully deterministic); `--live` re-records.
- **Stateless server.** The browser holds the document; FastAPI parses, checks and runs agents. The front end is plain HTML and JavaScript with no build step.
- Model: `claude-opus-5-5` with adaptive thinking (override with `LEXCLARA_MODEL`).

## Example

The repository ships two **fictitious** texts written for the demo: a bill instituting a municipal plain-language policy (`examples/pl_linguagem_simples.txt`) and a requirement letter from a building-permits office (`examples/despacho_exigencia.txt`). Neither is a real document of the municipality.

Deterministic checks on the bill (`lexclara check examples/pl_linguagem_simples.txt`):

```
info   Art. 3º   Números e percentuais devem ser grafados por extenso: “10 dias”.         [LC 95/1998, art. 11, II, f]
aviso  Art. 5º   Remissão vaga “artigo anterior”: indique expressamente o dispositivo.     [LC 95/1998, art. 11, II, g]
erro   Art. 7º   “revogam-se as disposições em contrário” não é admitida.                  [LC 95/1998, art. 9º]
aviso  Art. 1º   A sigla “SAAE” aparece pela primeira vez sem o nome por extenso.          [LC 95/1998, art. 11, II, e]
erro   § 2º      A remissão “§ 1º do art. 7º” não corresponde a nenhum dispositivo.        [LC 95/1998, art. 11, II, g]
...
```

An amendment inserts a new article after Art. 2º. Everything after it is renumbered, and the editor catches the reference that silently changed meaning:

```
Parágrafo único (of the new Art. 5º):  “§ 1º do art. 3º”  →  “§ 1º do art. 4º”   [Aplicar]
```

The bill's Art. 3º is deliberately overloaded (a duty to write plainly, a ban on jargon, a support channel with a deadline, and an annual report, all in one sentence). No regular expression catches that; it is what the `tecnica` agent is for (LC 95, art. 11, III: one subject per article), and it is the first article the demo sends through the plain-version and comprehension agents.

### What the agents did in the recorded run

From [`examples/demo_report.md`](examples/demo_report.md), one run, model `claude-opus-5-5`:

- **Drafting review.** On Art. 3º, `tecnica` found the four subjects in one sentence and proposed splitting them into a caput, paragraphs with incisos, and two new articles (3º-A, 3º-B). It also found an ambiguity the author never intended: whether "no prazo de 10 dias" is the deadline to *create* the support channel or to *answer* questions. It flagged this for the author and left the wording alone, because picking one reading would change the law. On the requirement letter, it rewrote the text to lead with the decision and to list the two things the citizen must do, and it left a placeholder where the letter fails to say where to deliver the documents.
- **Fidelity loop.** Across the three units, the verifier extracted 8 to 13 normative propositions per unit. After the redrafts, none was altered or omitted. Its objections in early rounds were real: a summary that said the permit was stalled "because documents are missing", when one of the two problems was a mismatch of areas, and a glossary that narrowed "pessoa jurídica" to "empresa". The plain versions also say out loud what the official text leaves undefined: the 10-day deadline, the missing lead time for the notices in Art. 4º, II and III, and the broken reference "§ 1º do art. 7º".
- **Comprehension.** Correct answers out of five, official text → plain version:

  | Reader | Art. 3º | Art. 4º | Requirement letter |
  |---|---|---|---|
  | Dona Maria (68, four years of schooling) | 2 → 4 | 2 → 5 | 1 → 5 |
  | Seu João (shopkeeper, high school) | 3 → 5 | 4 → 5 | 5 → 5 |
  | Ana (new civil servant, college) | 4 → 5 | 5 → 5 | 5 → 5 |

  The gap is largest for the reader the law most wants to reach, and the official texts are already readable for the college-educated reader. These are simulated readers on fictitious texts in a single run, so they show what the instrument measures, not evidence that it measures it correctly (see below).
- **A weakness the run exposed.** The verifier counts every glossary definition not literally in the official text as an "unsupported addition", even when it is harmless ("prova de vida: procedure to confirm the retiree is alive"). For Art. 4º and the letter, the loop therefore ran all three rounds without a clean "fiel" verdict, although no normative proposition was lost. The next version will separate didactic explanations from normative additions.

## Quick start

Requires Python 3.10+.

```bash
git clone https://github.com/mBarretto/lexclara && cd lexclara
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest                                   # deterministic layer, no API key
lexclara check examples/pl_linguagem_simples.txt
lexclara serve                           # http://127.0.0.1:8000
```

To use the Claude agents, set `ANTHROPIC_API_KEY` in the environment or in a git-ignored `.env` file at the repository root.

### Reproducible demo

```bash
lexclara demo --live      # calls Claude, records every response in examples/cache/, writes examples/demo_report.md
lexclara demo --replay    # rebuilds the same report from the recorded responses, no API key needed
```

## What this is not (yet), and the research questions

- **Simulated readers are a proxy, not the test the law requires.** They are a cheap way to rank thousands of texts and to find the words and sentences most likely to fail. Whether their scores correlate with real readers of each profile is an open empirical question, and it is one of the hypotheses the CPSI is meant to test in the field: run the same questionnaires with real residents (in person, by phone and by messaging app) and measure the agreement per profile.
- **Fidelity checking is adversarial, not a proof.** The proposition-level decomposition makes the verifier's reasoning inspectable, so a legislative consultant can audit it, but the human author keeps the final decision.
- **The legal citations in the rules** (LC 95/1998 articles and paragraphs) should be reviewed by the Câmara's legislative staff before production use. The parser is heuristic and tuned on the formatting conventions of municipal bills.
- **Next steps for the CPSI:** ground `tecnica` and `cidada` in the municipality's own laws and decrees, with citations to the source; emendas (amendments) as diffs over blocks; publication of the plain version next to each act on the transparency portal; and a field protocol that compares simulated and real comprehension.

## Repository layout

```
lexclara/
  model.py       Block, Document, Finding: the block tree
  parser.py      plain text → LC 95 tree (tolerant, never rewrites)
  numbering.py   LC 95 art. 10 labels and renumbering
  lc95.py        deterministic drafting checks
  remissoes.py   cross-reference resolution, binding and stale detection
  ops.py         structural edits (insert, delete) and the full check
  agents.py      Claude agents and the draft–verify loop
  llm.py         Claude API wrapper with record/replay cache
  server.py      FastAPI endpoints
  cli.py         lexclara serve | demo | check
  static/        the web editor (HTML, CSS, JS)
examples/        fictitious bill and requirement letter; recorded responses
tests/           tests for the deterministic layer and the API
```

## Author and license

Marcos Barretto (Instituto Minerva; retired professor, Escola Politécnica da Universidade de São Paulo). MIT License.
