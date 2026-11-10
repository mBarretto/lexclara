// LexClara web editor. The browser holds the document; the server parses,
// checks and runs agents. No framework, no build step.

const state = { doc: null, findings: [], selected: null, results: {} };
const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const DISPOSITIVOS = new Set(["artigo", "paragrafo", "inciso", "alinea", "item"]);

async function api(path, body) {
  const res = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch { /* not JSON */ }
    throw new Error(msg);
  }
  const type = res.headers.get("content-type") || "";
  return type.includes("json") ? res.json() : res.text();
}

function setState(s) {
  state.doc = s.document;
  state.findings = s.findings;
  render();
}

// ---------- tree helpers ----------
function* walk(blocks, depth = 0, parent = null) {
  for (const b of blocks) {
    yield { b, depth, parent };
    yield* walk(b.children, depth + 1, b);
  }
}
const find = (uid) => { for (const { b } of walk(state.doc.blocks)) if (b.uid === uid) return b; return null; };
const DOC_UID = "__doc__";
function unitOf(uid) {
  // A communication is analysed as a whole; a normative act article by article.
  if (state.doc.doc_type === "comunicacao") {
    return { uid: DOC_UID, kind: "comunicacao", label: "Comunicação inteira", text: "", children: state.doc.blocks };
  }
  for (const { b } of walk(state.doc.blocks)) {
    if (b.kind === "artigo" && [...walk([b])].some((x) => x.b.uid === uid)) return b;
  }
  return find(uid);
}
const uidsIn = (block) => new Set([...walk([block])].map((x) => x.b.uid));
const textOf = (block) => [...walk([block])].map(({ b }) => `${b.label} ${b.text}`).join("\n");

// ---------- document view ----------
function render() {
  const root = $("#doc");
  root.innerHTML = "";
  const unit = state.selected ? unitOf(state.selected) : null;
  const unitUids = unit ? uidsIn(unit) : new Set();
  for (const { b, depth } of walk(state.doc.blocks)) {
    const el = document.createElement("div");
    el.className = `block k-${b.kind}`;
    if (DISPOSITIVOS.has(b.kind)) el.style.marginLeft = `${depth * 24}px`;
    if (b.uid === state.selected) el.classList.add("selected");
    if (unitUids.has(b.uid)) el.classList.add("unit-selected");
    el.dataset.uid = b.uid;

    if (b.label) {
      const label = document.createElement("span");
      label.className = "label";
      label.textContent = b.label;
      el.append(label);
    }
    const text = document.createElement("span");
    text.className = "text";
    text.contentEditable = "true";
    text.spellcheck = true;
    text.textContent = b.text;
    text.addEventListener("focus", () => select(b.uid, false));
    text.addEventListener("blur", () => { if (text.textContent !== b.text) edit("set_text", b.uid, { text: text.textContent }); });
    el.append(text);

    const marks = document.createElement("span");
    marks.className = "marks";
    for (const sev of ["erro", "aviso", "info"]) {
      if (state.findings.some((f) => f.block_uid === b.uid && f.severity === sev)) {
        const d = document.createElement("span");
        d.className = `dot ${sev}`;
        d.title = sev;
        marks.append(d);
      }
    }
    el.append(marks);
    el.addEventListener("click", () => select(b.uid));
    root.append(el);
  }
  renderPanel();
}

function select(uid, rerender = true) {
  if (state.selected === uid) return;
  state.selected = uid;
  if (rerender) render(); else {
    document.querySelectorAll(".block").forEach((el) => el.classList.toggle("selected", el.dataset.uid === uid));
    const unit = unitOf(uid);
    const ids = unit ? uidsIn(unit) : new Set();
    document.querySelectorAll(".block").forEach((el) => el.classList.toggle("unit-selected", ids.has(el.dataset.uid)));
    renderPanel();
  }
}

async function edit(op, uid, extra = {}) {
  try {
    setState(await api("/api/edit", { document: state.doc, op, uid, ...extra }));
  } catch (e) { alert(e.message); }
}

// ---------- side panel ----------
function renderPanel() {
  const panel = $("#panel");
  if (!state.selected || !find(state.selected)) {
    panel.innerHTML = `<div class="empty">Selecione um bloco para ver as verificações e chamar os agentes.</div>`;
    return;
  }
  const unit = unitOf(state.selected);
  const ids = uidsIn(unit);
  const findings = state.findings.filter((f) => ids.has(f.block_uid));
  const title = unit.label || (unit.kind === "paragrafo_texto" ? "Parágrafo" : unit.kind);
  const key = resultKey(unit);
  const res = state.results[key] || {};

  panel.innerHTML = `
    <h3>${esc(title)} <span class="muted">${esc(unit.text.slice(0, 60))}${unit.text.length > 60 ? "…" : ""}</span></h3>
    <div class="agents" style="flex-direction:row;flex-wrap:wrap">
      <button data-op="insert">+ Inserir abaixo</button>
      <button data-op="delete">Excluir bloco</button>
    </div>
    <h3>Verificações automáticas <span class="muted">(LC 95/1998 e remissões)</span></h3>
    ${findings.length ? findings.map(findingHtml).join("") : `<div class="muted">Nenhum problema encontrado.</div>`}
    <h3>Agentes</h3>
    <div class="agents">
      <button data-agent="tecnica">Revisar técnica legislativa</button>
      <button data-agent="cidada">Versão cidadã verificada</button>
      <button data-agent="compreensao">Testar compreensão com leitores simulados</button>
    </div>
    <div id="results">
      ${res.tecnica ? tecnicaHtml(res.tecnica) : ""}
      ${res.cidada ? cidadaHtml(res.cidada) : ""}
      ${res.compreensao ? compreensaoHtml(res.compreensao) : ""}
      ${res.error ? `<p class="error">${esc(res.error)}</p>` : ""}
    </div>`;

  panel.querySelectorAll("[data-fix]").forEach((btn) => btn.addEventListener("click", () => {
    const f = findings[Number(btn.dataset.fix)];
    edit("apply_fix", f.block_uid, { fix: f.fix });
  }));
  panel.querySelector('[data-op="insert"]').addEventListener("click", () => edit("insert_after", state.selected, { text: "" }));
  panel.querySelector('[data-op="delete"]').addEventListener("click", () => {
    const uid = state.selected; state.selected = null; edit("delete", uid);
  });
  panel.querySelectorAll("[data-agent]").forEach((btn) => btn.addEventListener("click", () => runAgent(btn.dataset.agent, unit, btn)));
  panel.querySelector("[data-accept]")?.addEventListener("click", () => {
    edit("replace_unit", unit.uid, { text: res.tecnica.redacao_sugerida });
  });
}

const resultKey = (unit) => `${unit.uid}:${textOf(unit)}`;

function findingHtml(f, i) {
  return `<div class="finding ${f.severity}">
    <div>${esc(f.message)}</div>
    <div class="rule">${esc(f.rule)}</div>
    ${f.fix ? `<button data-fix="${i}">Aplicar: “${esc(f.fix.new)}”</button>` : ""}
  </div>`;
}

async function runAgent(name, unit, btn) {
  const key = resultKey(unit);
  const res = (state.results[key] ||= {});
  btn.disabled = true;
  const original = btn.textContent;
  btn.textContent = "Trabalhando…";
  delete res.error;
  try {
    const body = { document: state.doc, uid: unit.uid };
    if (name === "compreensao" && res.cidada) body.versao = res.cidada.rodadas.at(-1).versao;
    const out = await api(`/api/agent/${name}`, body);
    res[name] = out;
    if (name === "compreensao" && !res.cidada) delete res.cidada;
  } catch (e) {
    res.error = e.message;
  } finally {
    btn.disabled = false;
    btn.textContent = original;
    renderPanel();
  }
}

function tecnicaHtml(r) {
  const probs = r.problemas.map((p) => `<div class="finding aviso">
      <div><em>“${esc(p.trecho)}”</em></div>
      <div>${esc(p.problema)}</div>
      <div><strong>Sugestão:</strong> ${esc(p.sugestao)}</div>
      <div class="rule">${esc(p.regra)}</div></div>`).join("");
  return `<div class="result"><h3>Técnica legislativa</h3>
    ${probs || `<div class="muted">Sem problemas de redação relevantes.</div>`}
    ${r.redacao_sugerida ? `<p><strong>Redação sugerida</strong></p><pre>${esc(r.redacao_sugerida)}</pre>
      <p class="muted">${esc(r.observacao)}</p>
      <button class="primary" data-accept>Aceitar redação</button>` : ""}
  </div>`;
}

function versaoText(v) {
  let s = `${v.resumo}\n\n${v.explicacao}\n\nQuem é afetado: ${v.quem_e_afetado}`;
  if (v.o_que_fazer.length) s += `\n\nO que fazer:\n• ${v.o_que_fazer.join("\n• ")}`;
  if (v.prazos.length) s += `\n\nPrazos:\n• ${v.prazos.join("\n• ")}`;
  if (v.termos.length) s += `\n\nPalavras:\n${v.termos.map((t) => `• ${t.termo}: ${t.explicacao}`).join("\n")}`;
  return s;
}

const VEREDITO = { fiel: "fiel ao texto oficial", fiel_com_ressalvas: "fiel com ressalvas", infiel: "infiel ao texto oficial" };

function cidadaHtml(r) {
  const rounds = r.rodadas.map((rd, i) => {
    const v = rd.verificacao;
    const atoms = v.atomos.map((a) => `<div class="atom"><span class="st ${a.status}">${a.status}</span>
       · ${esc(a.tipo)} (${esc(a.dispositivo)}): ${esc(a.enunciado)}${a.status !== "preservado" ? ` — <em>${esc(a.comentario)}</em>` : ""}</div>`).join("");
    const extra = v.acrescimos.map((x) => `<div class="atom"><span class="st omitido">acréscimo</span> ${esc(x)}</div>`).join("");
    const last = i === r.rodadas.length - 1;
    return `<details class="round" ${last ? "open" : ""}>
      <summary>Rodada ${i + 1}: <span class="verdict ${v.veredito}">${VEREDITO[v.veredito]}</span></summary>
      ${last ? `<pre>${esc(versaoText(rd.versao))}</pre>` : ""}
      <div class="muted">Verificação de fidelidade (${v.atomos.length} proposições normativas):</div>
      ${atoms}${extra}
    </details>`;
  }).join("");
  return `<div class="result"><h3>Versão cidadã</h3>
    <p class="muted">Explicação para publicar ao lado do texto oficial. Um agente escreve, outro verifica cada obrigação, prazo e sujeito; se houver objeção, o primeiro reescreve.</p>
    ${rounds}</div>`;
}

function compreensaoHtml(r) {
  const byReader = {};
  for (const x of r.resultados) (byReader[x.leitor] ||= {})[x.versao] = x;
  const cell = (x) => x ? `${x.acertos}/${x.total}${x.nao_sei ? ` <span class="muted">(${x.nao_sei} não sei)</span>` : ""}
      <div class="bar" style="width:${(100 * x.acertos) / x.total}%"></div>` : "–";
  const rows = Object.entries(byReader).map(([nome, v]) =>
    `<tr><td>${esc(nome)}</td><td>${cell(v.oficial)}</td><td>${cell(v.cidada)}</td></tr>`).join("");
  const notes = r.resultados.map((x) => `<div class="finding info"><strong>${esc(x.leitor)}</strong>, texto ${x.versao === "oficial" ? "oficial" : "cidadão"}:
      “${esc(x.comentario)}”${x.palavras_dificeis.length ? `<div class="rule">Palavras difíceis: ${esc(x.palavras_dificeis.join(", "))}</div>` : ""}</div>`).join("");
  const qs = r.questionario.perguntas.map((p, i) => `<div class="atom">${i + 1}. ${esc(p.pergunta)}
      <span class="muted">Resposta: ${esc(p.alternativas[p.correta])} (${esc(p.dispositivo)})</span></div>`).join("");
  return `<div class="result"><h3>Teste de compreensão</h3>
    <table class="scores"><thead><tr><th>Leitor</th><th>Texto oficial</th><th>Versão cidadã</th></tr></thead><tbody>${rows}</tbody></table>
    <p class="muted">Leitores simulados por modelo de linguagem. São uma estimativa barata para priorizar textos; não substituem o teste com o público exigido pela Lei 15.263/2025, art. 5º, XVIII.</p>
    <details><summary>Perguntas (escritas a partir do texto oficial)</summary>${qs}</details>
    <details><summary>O que cada leitor disse</summary>${notes}</details>
  </div>`;
}

// ---------- toolbar ----------
async function loadExamples() {
  const examples = await api("/api/examples");
  const sel = $("#example");
  sel.innerHTML = examples.map((e, i) => `<option value="${i}">${esc(e.id)}</option>`).join("");
  sel.addEventListener("change", () => open(examples[sel.value]));
  await open(examples[0]);
}

async function open(ex) {
  state.selected = null;
  state.results = {};
  setState(await api("/api/parse", { text: ex.text, doc_type: ex.doc_type }));
}

$("#btn-paste").addEventListener("click", () => $("#paste-dialog").showModal());
$("#paste-dialog").addEventListener("close", () => {
  if ($("#paste-dialog").returnValue === "ok" && $("#paste-text").value.trim()) {
    open({ text: $("#paste-text").value, doc_type: $("#paste-type").value });
  }
});
$("#btn-renumber").addEventListener("click", () => edit("renumber", null));
$("#btn-export").addEventListener("click", async () => {
  $("#export-text").value = await api("/api/render", state.doc);
  $("#export-dialog").showModal();
});

loadExamples();
