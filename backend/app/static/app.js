const ui = {
  messages: document.querySelector("#messages"),
  form: document.querySelector("#messageForm"),
  input: document.querySelector("#messageInput"),
  send: document.querySelector("#sendButton"),
  reset: document.querySelector("#resetButton"),
  status: document.querySelector("#connectionStatus"),
  trace: document.querySelector("#trace"),
  cards: document.querySelector("#productCards"),
  lane: document.querySelector("#laneBadge"),
  turnCount: document.querySelector("#turnCount"),
  resultMeta: document.querySelector("#resultMeta"),
  inspector: document.querySelector("#inspectorContent"),
  disclosure: document.querySelector("#inventoryDisclosure"),
  catalogSummary: document.querySelector("#catalogSummary"),
  modelSummary: document.querySelector("#modelSummary"),
};

let conversationId = null;
let latestTurn = null;
let latestState = null;
let activeTab = "state";
let busy = false;

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
  })[character]);
}

function setStatus(label, kind = "ready") {
  ui.status.textContent = label;
  ui.status.className = `status-pill ${kind}`;
}

function setBusy(value) {
  busy = value;
  ui.send.disabled = value;
  ui.input.disabled = value;
  setStatus(value ? "Running workflow" : "Local demo ready", value ? "busy" : "ready");
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || `Request failed (${response.status})`);
  }
  if (response.status === 204) return null;
  return response.json();
}

function addMessage(role, text, meta = "") {
  const empty = ui.messages.querySelector(".empty-state");
  if (empty) empty.remove();
  const message = document.createElement("article");
  message.className = `message ${role}`;
  message.innerHTML = `${escapeHtml(text)}${meta ? `<span class="message-meta">${escapeHtml(meta)}</span>` : ""}`;
  ui.messages.append(message);
  ui.messages.scrollTop = ui.messages.scrollHeight;
}

function summarizeTrace(output) {
  if (output.lane) return `${output.lane} · vagueness ${output.vagueness}`;
  if (output.candidate_ids) return output.candidate_ids.length ? output.candidate_ids.join(", ") : "no state candidates";
  if (output.changed_paths) return output.changed_paths.length ? output.changed_paths.join(", ") : "no state change";
  if (output.top_products) return output.top_products.map((item) => `${item.product_id} ${item.score}`).join(" · ");
  if (output.hard_filters) return `${output.query || "review baseline"} · ${JSON.stringify(output.hard_filters)}`;
  if (output.card_count !== undefined) return `${output.card_count} cards · ${output.composer}`;
  if (output.product_count !== undefined) return `${output.product_count} products · ${output.review_count} retrieved reviews`;
  return JSON.stringify(output);
}

function renderTrace(turn) {
  ui.lane.textContent = turn.policy.lane;
  ui.lane.className = `lane-badge ${turn.policy.lane === "clarify-lane" ? "clarify" : "recommend"}`;
  ui.trace.innerHTML = turn.trace.map((node) => `
    <article class="trace-node ${node.owner === "RA-Rec" ? "rarec" : "spn"}">
      <span class="trace-dot" aria-hidden="true"></span>
      <div class="trace-top">
        <div><span class="trace-name">${escapeHtml(node.node_id)}</span> <span class="trace-role">${escapeHtml(node.owner)} · ${escapeHtml(node.role)}</span></div>
        <span class="trace-time">${Number(node.latency_ms).toFixed(1)} ms</span>
      </div>
      <p class="trace-summary">${escapeHtml(summarizeTrace(node.output_summary))}</p>
    </article>
  `).join("");
}

function formatPrice(value) {
  return value == null ? "Price unavailable" : `$${Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatSpecs(product) {
  const specs = [];
  if (product.storage_gb != null) specs.push(`${product.storage_gb} GB storage`);
  if (product.memory_gb != null) specs.push(`${product.memory_gb} GB RAM`);
  if (product.screen_inches != null) specs.push(`${product.screen_inches}\" display`);
  if (product.weight_grams != null) specs.push(`${product.weight_grams} g`);
  if (product.operating_system) specs.push(product.operating_system);
  return specs.join(" · ") || "Structured specifications unavailable";
}

function reviewExcerpt(text, limit = 320) {
  const normalized = String(text || "").replace(/\s+/g, " ").trim();
  return normalized.length > limit ? `${normalized.slice(0, limit - 1)}…` : normalized;
}

function renderCards(turn) {
  const cards = turn.final_response.product_cards || [];
  ui.resultMeta.textContent = cards.length ? `${cards.length} visible · top ${cards[0].ranking.score.total}` : "No result";
  if (!cards.length) {
    const message = turn.policy.lane === "clarify-lane"
      ? "This turn is asking for one more decision criterion."
      : "No local product satisfies all confirmed hard filters.";
    ui.cards.innerHTML = `<div class="empty-state compact"><span>${message}</span></div>`;
    return;
  }
  ui.cards.innerHTML = cards.map((card) => {
    const product = card.product;
    const ranking = card.ranking;
    const reviews = card.evidence_reviews || [];
    const evidence = reviews.length
      ? reviews.map((review) => `<div class="review-snippet">“${escapeHtml(reviewExcerpt(review.text))}” · ${review.rating}★ · evidence ${escapeHtml(review.review_id)}</div>`).join("")
      : '<div class="review-snippet">No matching review excerpt for the current criterion.</div>';
    return `
      <article class="product-card">
        <img class="product-image" src="${escapeHtml(product.image_url)}" alt="${escapeHtml(product.title)} product image" />
        <div>
          <span class="product-rank">RANK ${ranking.rank} · ${ranking.score.total} PTS</span>
          <h4 class="product-title">${escapeHtml(product.title)}</h4>
          <div class="product-price">${escapeHtml(formatPrice(product.price_usd))}</div>
          <div class="product-meta">${escapeHtml(formatSpecs(product))}</div>
          <div class="product-meta">${product.average_rating == null ? "Rating unavailable" : `${product.average_rating.toFixed(1)} stars`} · ${product.selected_review_count} sampled reviews</div>
          <div class="score-row"><span class="score-total">${ranking.score.total}</span><span class="score-bar"><span class="score-fill" style="width:${ranking.score.total}%"></span></span></div>
          ${evidence}
        </div>
      </article>`;
  }).join("");
}

function preferenceEntries(state) {
  const entries = [];
  if (state?.category) entries.push(["category", state.category]);
  Object.entries(state?.hard_constraints || {}).forEach(([key, value]) => entries.push([`hard.${key}`, value]));
  Object.entries(state?.soft_constraints || {}).forEach(([key, value]) => entries.push([`soft.${key}`, value]));
  Object.entries(state?.subjective_needs || {}).forEach(([key, value]) => value && entries.push([`facet.${key}`, value]));
  return entries;
}

function renderState() {
  const entries = preferenceEntries(latestState);
  const actions = [
    ["inspected", latestState?.inspected_items || []],
    ["rejected", (latestState?.rejected_items || []).map((item) => item.product_id)],
    ["purchased", latestState?.purchased_items || []],
    ["trade-offs", (latestState?.tradeoffs || []).map((item) => item.value_text)],
  ];
  ui.inspector.innerHTML = `
    <section class="state-group"><h3>Preferences & provenance</h3>
      ${entries.length ? entries.map(([path, value]) => `
        <div class="state-item"><div class="state-path">${escapeHtml(path)} · ${escapeHtml(value.canonical_id)}</div>
        <div class="state-value">${escapeHtml(value.value_text)}</div>
        <span class="origin-badge ${escapeHtml(value.origin)} ${value.status === "superseded" ? "superseded" : ""}">${escapeHtml(value.origin)} · ${escapeHtml(value.status)}</span></div>`).join("") : '<div class="empty-state compact"><span>No preferences have been recorded yet.</span></div>'}
    </section>
    <section class="state-group"><h3>Behavior & trade-offs</h3>
      ${actions.map(([label, values]) => `<div class="state-item"><div class="state-path">${label}</div><div class="state-value">${values.length ? values.map(escapeHtml).join(", ") : "—"}</div></div>`).join("")}
    </section>`;
}

function renderInspector() {
  if (!latestState) return;
  if (activeTab === "state") return renderState();
  if (activeTab === "diff") {
    const diff = latestTurn?.state_diff;
    ui.inspector.innerHTML = `<ul class="diff-list">${(diff?.summary || ["No State Diff yet."]).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`;
    return;
  }
  if (activeTab === "policy") {
    const policy = latestTurn?.policy;
    ui.inspector.innerHTML = policy ? `<div class="policy-card"><div class="policy-score">${policy.snapshot.vagueness.total}<small> / 100</small></div><p><strong>${escapeHtml(policy.lane)}</strong></p><pre>${escapeHtml(JSON.stringify(policy.snapshot.vagueness, null, 2))}</pre></div>` : "";
    return;
  }
  ui.inspector.innerHTML = `<pre>${escapeHtml(JSON.stringify(latestTurn || latestState, null, 2))}</pre>`;
}

function renderTurn(turn) {
  latestTurn = turn;
  latestState = turn.dialogue_state;
  addMessage("assistant", turn.final_response.message, `${turn.policy.lane} · ${turn.turn_id}`);
  renderTrace(turn);
  renderCards(turn);
  renderInspector();
  ui.turnCount.textContent = `${latestState.preference_history.length} turns`;
  ui.disclosure.textContent = turn.final_response.data_disclosure;
}

async function sendUtterance(utterance) {
  if (busy || !conversationId || !utterance.trim()) return;
  addMessage("user", utterance.trim(), `turn ${(latestState?.preference_history?.length || 0) + 1}`);
  setBusy(true);
  let failed = false;
  try {
    const turn = await request(`/api/actual/conversations/${conversationId}/turns`, {
      method: "POST",
      body: JSON.stringify({ utterance: utterance.trim() }),
    });
    renderTurn(turn);
  } catch (error) {
    failed = true;
    setStatus("Workflow error", "error");
    addMessage("assistant", `The workflow could not complete: ${error.message}`, "error");
  } finally {
    setBusy(false);
    if (failed) setStatus("Workflow error", "error");
    ui.input.focus();
  }
}

async function boot() {
  setStatus("Creating local session", "busy");
  try {
    const created = await request("/api/actual/conversations", { method: "POST", body: "{}" });
    conversationId = created.conversation_id;
    latestState = created.dialogue_state;
    latestTurn = null;
    ui.catalogSummary.textContent = `${created.catalog.product_count} products · ${created.catalog.review_count.toLocaleString("en-US")} real reviews`;
    ui.modelSummary.textContent = `Understanding + response: ${created.llm_provider}`;
    ui.messages.innerHTML = '<div class="empty-state"><strong>Describe the tablet you need in your own words.</strong><span>The system records constraints, evidence, feedback, and trade-offs across turns.</span></div>';
    ui.trace.innerHTML = '<div class="empty-state compact"><strong>No nodes have run yet.</strong><span>Your first message will execute PLAN → MEMORY → DECIDE.</span></div>';
    ui.cards.innerHTML = '<div class="empty-state compact"><span>Ranked real products and exact review evidence will appear here.</span></div>';
    ui.disclosure.textContent = created.catalog.disclosure;
    ui.lane.textContent = "Waiting";
    ui.lane.className = "lane-badge muted";
    ui.turnCount.textContent = "0 turns";
    ui.resultMeta.textContent = "No result";
    renderInspector();
    setStatus("Local demo ready", "ready");
  } catch (error) {
    setStatus("Startup failed", "error");
    ui.messages.innerHTML = `<div class="empty-state"><strong>The actual-data demo could not start.</strong><span>${escapeHtml(error.message)}</span></div>`;
  }
}

ui.form.addEventListener("submit", (event) => {
  event.preventDefault();
  const utterance = ui.input.value;
  ui.input.value = "";
  sendUtterance(utterance);
});
ui.input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    ui.form.requestSubmit();
  }
});
ui.reset.addEventListener("click", async () => {
  if (conversationId) await request(`/api/actual/conversations/${conversationId}`, { method: "DELETE" }).catch(() => null);
  await boot();
});
document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => {
  document.querySelectorAll(".tab").forEach((item) => {
    item.classList.toggle("active", item === tab);
    item.setAttribute("aria-selected", item === tab ? "true" : "false");
  });
  activeTab = tab.dataset.tab;
  renderInspector();
}));

boot();
