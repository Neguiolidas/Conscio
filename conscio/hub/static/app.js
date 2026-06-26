const $ = (id) => document.getElementById(id);
const TOKEN_KEY = "conscio_hub_token";
const POLL_MS = 3000;

const api = async (m, p, body) => {
  const headers = {};
  if (body) headers["Content-Type"] = "application/json";
  const tok = localStorage.getItem(TOKEN_KEY);
  if (tok) headers["Authorization"] = `Bearer ${tok}`;
  const r = await fetch(p, { method: m, headers, body: body ? JSON.stringify(body) : undefined });
  if (r.status === 401) {
    const t = prompt("Hub token required (CONSCIO_HUB_TOKEN):");
    if (t) { localStorage.setItem(TOKEN_KEY, t); return api(m, p, body); }
  }
  return { status: r.status, data: await r.json() };
};

// ── Friendly labels for built-in types ─────────────────────────────
const TYPE_LABELS = {
  "lmstudio": "LM Studio",
  "ollama": "Ollama",
  "openai": "OpenAI",
  "anthropic": "Anthropic",
  "gemini": "Google Gemini",
  "openai-compat": "Custom Provider",
};

function typeLabel(t) { return TYPE_LABELS[t] || t; }

// ── State ──────────────────────────────────────────────────────────
let _providerList = [];
let _initialLoadDone = false;  // Only set select from config on first load
let _userEditing = false;       // True while user is actively changing fields

// ── Brain / Config ─────────────────────────────────────────────────

function findProviderOf(cfg) {
  const adapter = cfg.adapter || {};
  const custom = cfg.providers || {};
  // Try custom providers first (match type + base_url)
  for (const [name, block] of Object.entries(custom)) {
    if (block.type === adapter.type && block.base_url === adapter.base_url) return name;
  }
  // Builtin type match
  if (adapter.type && _providerList.some(p => p.id === adapter.type && !p.isCustom)) return adapter.type;
  return adapter.type || "";
}

async function loadBrain() {
  const h = await api("GET", "/api/health");
  $("version").textContent = h.data.version ? `v${h.data.version}` : "";

  const [catRes, cfgRes] = await Promise.all([
    api("GET", "/api/providers"),
    api("GET", "/api/config"),
  ]);
  const cat = catRes.data;
  const cfg = cfgRes.data;

  // Build provider list: builtins + plugins + custom
  _providerList = [];
  cat.builtin.forEach(t => _providerList.push({ id: t, label: typeLabel(t), isCustom: false }));
  (cat.plugins || []).forEach(p => _providerList.push({ id: p, label: p, isCustom: false }));
  Object.keys(cat.custom || {}).forEach(n =>
    _providerList.push({ id: n, label: `${n} (${typeLabel((cat.custom[n]||{}).type||"")})`, isCustom: true }));

  // Only repopulate <select> on first load or if list changed
  const sel = $("provider");
  const currentOptions = Array.from(sel.options).map(o => o.value).join(",");
  const newOptions = _providerList.map(p => p.id).join(",");
  if (currentOptions !== newOptions) {
    sel.innerHTML = _providerList.map(p =>
      `<option value="${p.id}" data-custom="${p.isCustom}">${p.label}</option>`
    ).join("");
  }

  renderProviders(cat);

  // Only set select/model/key/url values from config on INITIAL load.
  // Subsequent polls leave the user's edits alone.
  if (!_initialLoadDone) {
    if (cfg.model) $("model").value = cfg.model;
    const adapter = cfg.adapter || {};
    // API key is never echoed by the server; leave the field blank.
    if (adapter.base_url) $("base-url").value = adapter.base_url;
    const currentProvider = findProviderOf(cfg);
    if (currentProvider) sel.value = currentProvider;
    onProviderChange();
    _initialLoadDone = true;
  }
}

function renderProviders(cat) {
  const li = [];
  cat.builtin.forEach(t => li.push(`<li><span class="badge green">builtin</span> ${typeLabel(t)}</li>`));
  (cat.plugins || []).forEach(p => li.push(`<li><span class="badge purple">plugin</span> ${p}</li>`));
  Object.entries(cat.custom || {}).forEach(([n, p]) =>
    li.push(`<li><span class="badge purple">custom</span> ${typeLabel(p.type||"")} <b>${n}</b> ${p.base_url || ""} key:${p.api_key_present ? "✓" : "✗"}</li>`));
  $("providers").innerHTML = li.join("") || `<li class="empty">No custom providers</li>`;
}

function needsBaseUrl(providerId) {
  const opt = $("provider").selectedOptions[0];
  if (!opt) return false;
  return opt.dataset.custom === "true" || providerId === "openai-compat";
}

function onProviderChange() {
  const providerId = $("provider").value;
  const urlRow = $("base-url-row");
  urlRow.style.display = needsBaseUrl(providerId) ? "" : "none";
  loadModels();
}

async function loadModels() {
  const p = $("provider").value;
  const res = await api("GET", `/api/models?provider=${encodeURIComponent(p)}`);
  const data = res.data;
  const models = data.models || [];
  $("models").innerHTML = models.map(m => `<option value="${m}">`).join("");
  const indicator = $("models-indicator");
  if (indicator) {
    indicator.textContent = data.probed
      ? `${models.length} models from API`
      : (models.length ? `${models.length} known models` : "");
  }
}

$("provider").onchange = onProviderChange;

// ── Test ────────────────────────────────────────────────────────────
$("test").onclick = async () => {
  const providerId = $("provider").value;
  const model = $("model").value;
  const apiKey = $("api-key").value.trim();
  const baseUrl = $("base-url").value.trim();
  const opt = $("provider").selectedOptions[0];
  const isCustom = opt && opt.dataset.custom === "true";
  const body = { provider: providerId, model };
  if (apiKey) body.api_key = apiKey;
  if (baseUrl && (isCustom || providerId === "openai-compat")) body.base_url = baseUrl;

  $("testResult").innerHTML = `<span class="spinner"></span> Testing…`;

  const [testRes, modelsRes] = await Promise.all([
    api("POST", "/api/model/test", body),
    api("GET", `/api/models?provider=${encodeURIComponent(providerId)}`),
  ]);

  const d = testRes.data;
  const models = modelsRes.data.models || [];
  const modelList = models.length ? models.join(", ") : "(none detected)";

  if (d.ok) {
    $("testResult").innerHTML =
      `<span style="color:var(--green)">✓ API online</span> · ${d.latency_ms}ms · <span class="hint">response: "${d.sample_output}"</span>` +
      `<br><span class="hint">Models: ${modelList}</span>`;
  } else {
    $("testResult").innerHTML =
      `<span style="color:var(--red)">✗ ${d.error}</span>` +
      `<br><span class="hint">Detected models: ${modelList}</span>`;
  }
};

// ── Save ─────────────────────────────────────────────────────────────
$("save").onclick = async () => {
  const providerId = $("provider").value;
  const model = $("model").value;
  const apiKey = $("api-key").value.trim();
  const baseUrl = $("base-url").value.trim();
  const opt = $("provider").selectedOptions[0];
  const isCustom = opt && opt.dataset.custom === "true";

  const body = { model, provider: providerId };

  // Build adapter override with the UI fields
  const adapter = {};
  if (isCustom) {
    adapter.type = "openai-compat";
    if (baseUrl) adapter.base_url = baseUrl;
  } else {
    adapter.type = providerId;
    if (providerId === "openai-compat" && baseUrl) adapter.base_url = baseUrl;
  }
  // API key: send raw key, backend stores as env var reference internally
  if (apiKey) adapter.api_key = apiKey;
  body.adapter = adapter;

  const res = await api("PUT", "/api/config", body);
  $("testResult").textContent = res.status === 200
    ? "saved ↻" : `error: ${JSON.stringify(res.data.detail)}`;
};

// ── Main loop ───────────────────────────────────────────────────────
async function loadAll() {
  await loadBrain();
}

loadAll();
setInterval(loadAll, POLL_MS);
