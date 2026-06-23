"use strict";
const out = document.getElementById("out");
const tokenEl = document.getElementById("token");
const ENDPOINTS = {
  events: "/api/events",
  goals: "/api/goals",
  actions: "/api/actions",
  skills: "/api/skills",
  state: "/api/state",
};
async function load(tab) {
  out.textContent = "loading…";
  const headers = {};
  if (tokenEl.value) headers["Authorization"] = "Bearer " + tokenEl.value;
  try {
    const r = await fetch(ENDPOINTS[tab], { headers });
    const data = await r.json();
    out.textContent = JSON.stringify(data, null, 2);
  } catch (e) {
    out.textContent = "error: " + e;
  }
}
for (const b of document.querySelectorAll("nav button")) {
  b.addEventListener("click", () => load(b.dataset.tab));
}
load("events");
