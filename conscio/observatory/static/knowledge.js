/* knowledge.js — Entities + relationships + timeline (v3.4) */
(function () {
  "use strict";

  function render(container) {
    container.innerHTML = "";

    // entities
    var h1 = document.createElement("h3");
    h1.textContent = "Entities";
    container.appendChild(h1);
    var elist = document.createElement("ul");
    elist.id = "entity-list";
    container.appendChild(elist);

    fetch("/api/knowledge/entities?limit=50").then(function (r) { return r.json(); })
      .then(function (ents) {
        if (!ents.length) { elist.innerHTML = "<li>No entities</li>"; return; }
        ents.forEach(function (e) {
          var li = document.createElement("li");
          li.textContent = e.name + " [" + e.type + "]" + (e.created_at ? " \u2014 " + e.created_at : "");
          elist.appendChild(li);
        });
      })
      .catch(function () { elist.innerHTML = "<li>Error loading entities</li>"; });

    // relationships
    var h2 = document.createElement("h3");
    h2.textContent = "Relationships";
    container.appendChild(h2);
    var rlist = document.createElement("ul");
    rlist.id = "rel-list";
    container.appendChild(rlist);

    fetch("/api/knowledge/relationships?limit=50").then(function (r) { return r.json(); })
      .then(function (rels) {
        if (!rels.length) { rlist.innerHTML = "<li>No relationships</li>"; return; }
        rels.forEach(function (r) {
          var li = document.createElement("li");
          li.textContent = r.subject + " \u2192 " + r.predicate + " \u2192 " + r.object;
          rlist.appendChild(li);
        });
      })
      .catch(function () { rlist.innerHTML = "<li>Error loading relationships</li>"; });
  }

  window.TAB_RENDERERS = window.TAB_RENDERERS || {};
  window.TAB_RENDERERS["knowledge"] = render;
})();
