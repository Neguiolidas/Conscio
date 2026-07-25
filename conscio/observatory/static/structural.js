/* structural.js — Drift timeline + freshness card + graph (v3.4) */
(function () {
  "use strict";

  function render(container) {
    container.innerHTML = "";

    // freshness card
    var card = document.createElement("div");
    card.id = "freshness-card";
    card.style.cssText = "padding:8px;border:1px solid #444;border-radius:6px;margin-bottom:12px";
    card.textContent = "Loading freshness\u2026";
    container.appendChild(card);

    fetch("/api/structural/freshness").then(function (r) { return r.json(); })
      .then(function (f) {
        card.textContent = f.known
          ? "Graph: " + f.graph_commit.slice(0, 8) + " | Head: " + (f.head_commit || "none").slice(0, 8) + (f.is_stale ? " \u26a0 STALE" : " \u2713 fresh")
          : "No structural data";
        card.style.borderColor = f.is_stale ? "#e55" : "#5a5";
      })
      .catch(function () { card.textContent = "Freshness unavailable"; });

    // drift timeline
    var h2 = document.createElement("h3");
    h2.textContent = "Drift Timeline";
    container.appendChild(h2);

    var list = document.createElement("ul");
    list.id = "drift-list";
    container.appendChild(list);

    fetch("/api/structural/drift?limit=20").then(function (r) { return r.json(); })
      .then(function (entries) {
        if (!entries.length) { list.innerHTML = "<li>No drift entries</li>"; return; }
        entries.forEach(function (e) {
          var li = document.createElement("li");
          li.textContent = e.workspace_id + " \u2014 " + e.commit.slice(0, 8) + " nodes:" + e.node_count + " links:" + e.link_count + " @ " + e.seen_at;
          list.appendChild(li);
        });
      })
      .catch(function () { list.innerHTML = "<li>Error loading drift</li>"; });

    // graph viewer (D3)
    var h3 = document.createElement("h3");
    h3.textContent = "Graph";
    container.appendChild(h3);

    var gbox = document.createElement("div");
    gbox.id = "graph-box";
    gbox.style.cssText = "width:100%;height:300px;border:1px solid #444;overflow:auto";
    container.appendChild(gbox);

    fetch("/api/structural/graph").then(function (r) { return r.json(); })
      .then(function (g) {
        if (!g.available) { gbox.textContent = g.reason; return; }
        var data = g.data;
        var nodes = (data.nodes || []).map(function (n) { return {id: n.id, label: n.label || n.id}; });
        var links = (data.links || []).map(function (l) { return {source: l.source, target: l.target}; });
        if (typeof d3 === "undefined") { gbox.textContent = "D3 not loaded"; return; }
        var svg = d3.select(gbox).append("svg").attr("width", "100%").attr("height", 300);
        var sim = d3.forceSimulation(nodes)
          .force("link", d3.forceLink(links).id(function (d) { return d.id; }).distance(60))
          .force("charge", d3.forceManyBody().strength(-120))
          .force("center", d3.forceCenter(300, 150));
        var link = svg.append("g").selectAll("line").data(links).join("line")
          .attr("stroke", "#666").attr("stroke-width", 1);
        var node = svg.append("g").selectAll("circle").data(nodes).join("circle")
          .attr("r", 6).attr("fill", "#6af").attr("title", function (d) { return d.label; });
        var label = svg.append("g").selectAll("text").data(nodes).join("text")
          .attr("font-size", 10).attr("dx", 8).text(function (d) { return d.label; });
        sim.on("tick", function () {
          link.attr("x1", function (d) { return d.source.x; }).attr("y1", function (d) { return d.source.y; })
              .attr("x2", function (d) { return d.target.x; }).attr("y2", function (d) { return d.target.y; });
          node.attr("cx", function (d) { return d.x; }).attr("cy", function (d) { return d.y; });
          label.attr("x", function (d) { return d.x; }).attr("y", function (d) { return d.y; });
        });
      })
      .catch(function () { gbox.textContent = "Error loading graph"; });
  }

  window.TAB_RENDERERS = window.TAB_RENDERERS || {};
  window.TAB_RENDERERS["structural"] = render;
})();
