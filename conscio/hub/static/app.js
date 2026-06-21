const $ = (id) => document.getElementById(id);
const api = async (m, p, body) => {
  const r = await fetch(p, {
    method: m,
    headers: body ? {"Content-Type": "application/json"} : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  return {status: r.status, data: await r.json()};
};

async function loadAll() {
  const h = await api("GET", "/api/health");
  $("status").textContent = h.data.ok ? "● ready" : "● error";
  const cat = (await api("GET", "/api/providers")).data;
  const names = [...cat.builtin, ...Object.keys(cat.custom || {})];
  $("provider").innerHTML = names.map((n) => `<option>${n}</option>`).join("");
  renderProviders(cat);
  const cfg = (await api("GET", "/api/config")).data;
  if (cfg.model) $("model").value = cfg.model;
  if (cfg.adapter && cfg.adapter.type) $("provider").value = cfg.adapter.type;
  loadModels();
}

function renderProviders(cat) {
  const li = [];
  cat.builtin.forEach((t) => li.push(`<li>● ${t} <em>(builtin)</em></li>`));
  Object.entries(cat.custom || {}).forEach(([n, p]) =>
    li.push(`<li>● ${p.type} <b>${n}</b> ${p.base_url || ""} `
      + `key:${p.api_key_present ? "set" : "unset"}</li>`));
  $("providers").innerHTML = li.join("");
}

async function loadModels() {
  const p = $("provider").value;
  const res = await api("GET", `/api/models?provider=${encodeURIComponent(p)}`);
  const models = (res.data.models || []);
  $("models").innerHTML = models.map((m) => `<option value="${m}">`).join("");
}

$("provider").onchange = loadModels;

$("test").onclick = async () => {
  $("testResult").textContent = "…";
  const res = await api("POST", "/api/model/test",
    {provider: $("provider").value, model: $("model").value});
  const d = res.data;
  $("testResult").textContent = d.ok
    ? `✓ ${d.latency_ms}ms · ${JSON.stringify(d.sample_output)}`
    : `✗ ${d.error}`;
};

$("save").onclick = async () => {
  // send the provider NAME/type; the server resolves base_url + api_key_env.
  const res = await api("PUT", "/api/config",
    {model: $("model").value, provider: $("provider").value});
  $("testResult").textContent = res.status === 200
    ? "saved ↻ next session" : `error: ${JSON.stringify(res.data.detail)}`;
};

$("addProvider").onclick = async () => {
  const res = await api("POST", "/api/providers", {
    name: $("pName").value, type: $("pType").value,
    base_url: $("pUrl").value || undefined,
    api_key_env: $("pEnv").value || undefined,
  });
  $("addResult").textContent = res.status === 200 ? "added" :
    `error: ${JSON.stringify(res.data.detail)}`;
  if (res.status === 200) loadAll();
};

loadAll();
