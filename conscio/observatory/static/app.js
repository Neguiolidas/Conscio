/* Conscio Observatory — App (v3.4.2) */
(function () {
  "use strict";

  var App = window.App = {
    _sidebarOpen: true,
    _projects: [],
    _activeProject: null,
    _sim: null,
    _fpsTimer: null,
    _fpsFrames: 0,
    _fps: 60,
  };

  var LABELS = {
    events:"Logs",goals:"Goals",actions:"Actions",skills:"Skills",
    state:"State",daemon:"Daemon",relay:"Relay",identity:"Identity",
    society_members:"Society",society_skills:"Soc.Skills",society_records:"Soc.Records",
    structural:"Debug",knowledge:"KG",graphview:"Graph View"
  };
  var TAB_ORDER = ["events","goals","actions","skills","state","daemon","relay",
    "identity","society_members","society_skills","society_records","structural","knowledge","graphview"];

  App.toggleSidebar = function () {
    App._sidebarOpen = !App._sidebarOpen;
    var sb = document.getElementById("sidebar");
    sb.classList.toggle("closed", !App._sidebarOpen);
    // mobile: use 'open' class instead
    if (window.innerWidth <= 768) {
      sb.classList.toggle("open", App._sidebarOpen);
      sb.classList.toggle("closed", !App._sidebarOpen);
    }
  };

  App.init = function () {
    App._renderTabs();
    App.loadProjects();
    // click outside sidebar on mobile closes it
    document.getElementById("main").addEventListener("click", function () {
      if (window.innerWidth <= 768 && App._sidebarOpen) App.toggleSidebar();
    });
  };

  App._renderTabs = function () {
    var nav = document.getElementById("tabs");
    nav.innerHTML = "";
    TAB_ORDER.forEach(function (id) {
      var b = document.createElement("button");
      b.textContent = LABELS[id] || id;
      b.dataset.tab = id;
      b.addEventListener("click", function () { App.switchTab(id); });
      nav.appendChild(b);
    });
  };

  App.loadProjects = function () {
    fetch("/api/projects").then(function (r) { return r.json(); }).then(function (ps) {
      App._projects = ps;
      var el = document.getElementById("project-list");
      el.innerHTML = "";
      if (!ps.length) { el.style.display = "none"; return; }
      el.style.display = "";
      ps.forEach(function (p) {
        var d = document.createElement("div");
        d.className = "project-item";
        d.innerHTML = '<span class="dot"></span><span>' + p.name + "</span><span style=\"margin-left:auto;color:#666;font-size:11px\">" + p.node_count + "n</span>";
        d.addEventListener("click", function () { App.selectProject(p); });
        el.appendChild(d);
      });
    }).catch(function () {});
  };

  App.selectProject = function (proj) {
    App._activeProject = proj;
    // highlight
    var items = document.querySelectorAll("#project-list .project-item");
    items.forEach(function (i) { i.classList.remove("active"); });
    var idx = App._projects.indexOf(proj);
    if (idx >= 0 && items[idx]) items[idx].classList.add("active");
    document.getElementById("project-badge").textContent = proj.name + " (" + proj.node_count + " nodes)";
    // switch to graphview tab
    App.switchTab("graphview");
  };

  App.switchTab = function (tab) {
    // stop previous simulation
    if (App._sim) { App._sim.stop(); App._sim = null; }

    // highlight nav
    document.querySelectorAll("#tabs button").forEach(function (b) {
      b.classList.toggle("active", b.dataset.tab === tab);
    });

    var content = document.getElementById("content");
    content.className = "";
    content.innerHTML = "";
    var title = document.getElementById("title");

    if (tab === "graphview") {
      title.textContent = App._activeProject ? "Graph: " + App._activeProject.name : "Graph View";
      content.className = "graph-mode";
      App._renderGraph(content);
      return;
    }

    title.textContent = LABELS[tab] || tab;
    content.innerHTML = "<pre>loading\u2026</pre>";

    var endpoints = {
      events:"/api/events",goals:"/api/goals",actions:"/api/actions",
      skills:"/api/skills",state:"/api/state",daemon:"/api/daemon",
      relay:"/api/relay/inbox",identity:"/api/identity",
      society_members:"/api/society/members",society_skills:"/api/society/skills",
      society_records:"/api/society/records",structural:"/api/structural/drift",
      knowledge:"/api/knowledge/entities",
    };
    var url = endpoints[tab];
    if (!url) { content.querySelector("pre").textContent = "no endpoint for " + tab; return; }

    fetch(url).then(function (r) { return r.json(); }).then(function (data) {
      content.innerHTML = "<pre>" + JSON.stringify(data, null, 2) + "</pre>";
    }).catch(function (e) {
      content.innerHTML = "<pre>error: " + e + "</pre>";
    });
  };

  // ── D3.js Canvas Graph Renderer ──────────────────────────────────

  App._renderGraph = function (container) {
    if (!App._activeProject) {
      container.innerHTML = "<div style=\"padding:40px;text-align:center;color:#666\">Select a project from the sidebar</div>";
      return;
    }
    var canvas = document.createElement("canvas");
    canvas.id = "graph-canvas";
    canvas.width = container.clientWidth;
    canvas.height = container.clientHeight || window.innerHeight - 48;
    container.appendChild(canvas);
    var ctx = canvas.getContext("2d");

    // freshness card
    var card = document.createElement("div");
    card.className = "freshness-card";
    card.textContent = "Loading\u2026";
    container.appendChild(card);
    fetch("/api/structural/freshness").then(function (r) { return r.json(); }).then(function (f) {
      card.textContent = f.known ? (f.is_stale ? "\u26a0 stale" : "\u2713 fresh") : "\u2014";
      card.style.borderColor = f.is_stale ? "#e55" : "#5a5";
    }).catch(function () { card.textContent = "\u2014"; });

    // load graph data
    fetch("/api/projects/" + App._activeProject.id + "/graph").then(function (r) { return r.json(); }).then(function (data) {
      var nodes = (data.nodes || []).map(function (n, i) {
        return {id:n.id, label:n.label||n.id, x:Math.random()*canvas.width, y:Math.random()*canvas.height, r:Math.max(3, Math.sqrt(n.degree||1)*2), color:n.color&&n.color.background||"#6af", _i:i};
      });
      var links = (data.links || []).map(function (l) {
        return {source:typeof l.source==="object"?l.source.id:l.source, target:typeof l.target==="object"?l.target.id:l.target};
      });

      // FPS monitor
      App._fpsFrames = 0;
      if (App._fpsTimer) clearInterval(App._fpsTimer);
      App._fpsTimer = setInterval(function () {
        App._fps = App._fpsFrames;
        App._fpsFrames = 0;
        if (App._fps < 15 && nodes.length > 500) {
          // reduce detail
          nodes.forEach(function (n) { n.r = Math.max(2, n.r - 0.5); });
        }
      }, 1000);

      var sim = d3.forceSimulation(nodes)
        .force("link", d3.forceLink(links).id(function (d) { return d.id; }).distance(40))
        .force("charge", d3.forceManyBody().strength(-80).theta(0.9))
        .force("center", d3.forceCenter(canvas.width / 2, canvas.height / 2))
        .alphaDecay(0.02)
        .on("tick", function () {
          App._fpsFrames++;
          ctx.clearRect(0, 0, canvas.width, canvas.height);
          ctx.fillStyle = "#0f0f1a";
          ctx.fillRect(0, 0, canvas.width, canvas.height);

          // links
          ctx.strokeStyle = "rgba(100,100,140,0.4)";
          ctx.lineWidth = 0.5;
          links.forEach(function (l) {
            if (l.source && l.target) {
              ctx.beginPath();
              ctx.moveTo(l.source.x, l.source.y);
              ctx.lineTo(l.target.x, l.target.y);
              ctx.stroke();
            }
          });

          // nodes
          nodes.forEach(function (n) {
            var r = n.r;
            ctx.beginPath();
            ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
            // subtle glow
            var g = ctx.createRadialGradient(n.x - r * 0.3, n.y - r * 0.3, 0, n.x, n.y, r * 2);
            g.addColorStop(0, n.color + "cc");
            g.addColorStop(1, n.color + "22");
            ctx.fillStyle = g;
            ctx.fill();
            // label only for big nodes
            if (r > 6) {
              ctx.fillStyle = "#fff";
              ctx.font = "9px sans-serif";
              ctx.fillText(n.label, n.x + r + 2, n.y + 3);
            }
          });
        });
      App._sim = sim;

      // resize handler
      var onResize = function () {
        canvas.width = container.clientWidth;
        canvas.height = container.clientHeight || window.innerHeight - 48;
        sim.force("center", d3.forceCenter(canvas.width / 2, canvas.height / 2));
        sim.alpha(0.3).restart();
      };
      window.addEventListener("resize", onResize);

      // hover (mousemove)
      canvas.addEventListener("mousemove", function (e) {
        var rect = canvas.getBoundingClientRect();
        var mx = e.clientX - rect.left, my = e.clientY - rect.top;
        var found = null;
        for (var i = nodes.length - 1; i >= 0; i--) {
          var n = nodes[i];
          var dx = mx - n.x, dy = my - n.y;
          if (dx * dx + dy * dy < (n.r + 4) * (n.r + 4)) { found = n; break; }
        }
        canvas.style.cursor = found ? "pointer" : "default";
        // highlight
        if (found) {
          App._sim.alphaTarget(0.01).restart();
          ctx.clearRect(0, 0, canvas.width, canvas.height);
          ctx.fillStyle = "#0f0f1a";
          ctx.fillRect(0, 0, canvas.width, canvas.height);
          links.forEach(function (l) {
            if (l.source && l.target) {
              var h = l.source===found||l.target===found;
              ctx.strokeStyle = h ? "rgba(100,170,255,0.7)" : "rgba(100,100,140,0.15)";
              ctx.lineWidth = h ? 1.5 : 0.5;
              ctx.beginPath();
              ctx.moveTo(l.source.x, l.source.y);
              ctx.lineTo(l.target.x, l.target.y);
              ctx.stroke();
            }
          });
          nodes.forEach(function (n) {
            var h = n===found;
            var r = h ? n.r * 1.8 : n.r;
            ctx.beginPath();
            ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
            ctx.fillStyle = h ? "#6af" : (n.color + "88");
            ctx.fill();
            if (h || r > 6) {
              ctx.fillStyle = h ? "#fff" : "rgba(255,255,255,0.5)";
              ctx.font = h ? "11px sans-serif" : "9px sans-serif";
              ctx.fillText(n.label, n.x + r + 2, n.y + 3);
            }
          });
        }
      });
      canvas.addEventListener("mouseleave", function () {
        App._sim.alphaTarget(0);
      });
    }).catch(function () {
      container.innerHTML = "<div style=\"padding:40px;text-align:center;color:#e55\">Failed to load graph. Run \u201cgraphify update <project>\u201d first.</div>";
    });
  };

  // ── Init ─────────────────────────────────────────────────────────
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", App.init);
  } else {
    App.init();
  }
})();