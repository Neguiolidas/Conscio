"use strict";
var out = document.getElementById("out");
var tokenEl = document.getElementById("token");
var ENDPOINTS = {
  events: "/api/events",
  goals: "/api/goals",
  actions: "/api/actions",
  skills: "/api/skills",
  state: "/api/state",
  daemon: "/api/daemon",
  relay: "/api/relay/inbox",
  identity: "/api/identity",
  society_members: "/api/society/members",
  society_skills: "/api/society/skills",
  society_records: "/api/society/records",
};
async function load(tab) {
  var renderers = window.TAB_RENDERERS || {};
  if (renderers[tab]) {
    out.style.display = "none";
    var main = out.parentElement;
    var container = main.querySelector(".tab-container");
    if (!container) {
      container = document.createElement("div");
      container.className = "tab-container";
      main.appendChild(container);
    }
    renderers[tab](container);
    return;
  }
  out.style.display = "";
  var c = out.parentElement.querySelector(".tab-container");
  if (c) c.innerHTML = "";
  out.textContent = "loading\u2026";
  var headers = {};
  if (tokenEl.value) headers["Authorization"] = "Bearer " + tokenEl.value;
  try {
    var r = await fetch(ENDPOINTS[tab], { headers: headers });
    var data = await r.json();
    out.textContent = JSON.stringify(data, null, 2);
  } catch (e) {
    out.textContent = "error: " + e;
  }
}
for (var _i = 0, _a = document.querySelectorAll("nav button"); _i < _a.length; _i++) {
  var b = _a[_i];
  b.addEventListener("click", function () { load(this.dataset.tab); }.bind(b));
}
load("events");
