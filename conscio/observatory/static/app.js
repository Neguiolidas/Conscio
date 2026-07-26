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
    _zoom: { scale: 1, x: 0, y: 0 },
    _dragNode: null,
    _panStart: null,
    _hoverNode: null,
    _selectedNode: null,
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
    if (window.innerWidth <= 768) {
      sb.classList.toggle("open", App._sidebarOpen);
      sb.classList.toggle("closed", !App._sidebarOpen);
    }
  };

  App.init = function () {
    App._renderTabs();
    App.loadProjects();
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
      // Fetch consent status
      fetch("/api/consent").then(function (r2) { return r2.json(); }).then(function (consents) {
        var consentMap = {};
        consents.forEach(function (c) { consentMap[c.workspace_id] = c; });
        ps.forEach(function (p) {
          var d = document.createElement("div");
          d.className = "project-item";
          // consent icon
          var ci = p.ws_id && consentMap[p.ws_id] ? (consentMap[p.ws_id].granted ? "\u2705" : "\u26d4") : "";
          d.innerHTML = '<span class="dot"></span><span>' + p.name + "</span>" +
            (ci ? '<span style="margin-left:4px;font-size:10px" title="consent: ' + (consentMap[p.ws_id] ? consentMap[p.ws_id].scope : "unknown") + '">' + ci + '</span>' : "") +
            '<span style="margin-left:auto;color:#666;font-size:11px">' + p.node_count + "n</span>";
          d.addEventListener("click", function () { App.selectProject(p); });
          el.appendChild(d);
        });
      }).catch(function () {
        // fallback without consent
        ps.forEach(function (p) {
          var d = document.createElement("div");
          d.className = "project-item";
          d.innerHTML = '<span class="dot"></span><span>' + p.name + "</span><span style=\"margin-left:auto;color:#666;font-size:11px\">" + p.node_count + "n</span>";
          d.addEventListener("click", function () { App.selectProject(p); });
          el.appendChild(d);
        });
      });
    }).catch(function () {});
  };

  App.selectProject = function (proj) {
    App._activeProject = proj;
    var items = document.querySelectorAll("#project-list .project-item");
    items.forEach(function (i) { i.classList.remove("active"); });
    var idx = App._projects.indexOf(proj);
    if (idx >= 0 && items[idx]) items[idx].classList.add("active");
    document.getElementById("project-badge").textContent = proj.name + " (" + proj.node_count + " nodes)";
    App.switchTab("graphview");
  };

  App.switchTab = function (tab) {
    if (App._sim) { App._sim.stop(); App._sim = null; }
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

  // ── D3.js Canvas Graph Renderer with zoom/pan/drag/detail ──────

  function _screenToWorld(sx, sy, z) {
    return { x: (sx - z.x) / z.scale, y: (sy - z.y) / z.scale };
  }

  function _hitTest(wx, wy, nodes) {
    for (var i = nodes.length - 1; i >= 0; i--) {
      var n = nodes[i], dx = wx - n.x, dy = wy - n.y;
      if (dx * dx + dy * dy < (n.r + 4) * (n.r + 4)) return n;
    }
    return null;
  }

  function _draw(ctx, canvas, nodes, links, z, hover, selected) {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = "#0f0f1a";
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    ctx.save();
    ctx.translate(z.x, z.y);
    ctx.scale(z.scale, z.scale);

    // links
    links.forEach(function (l) {
      if (!l.source || !l.target) return;
      var hi = l.source === hover || l.target === hover ||
               l.source === selected || l.target === selected;
      ctx.strokeStyle = hi ? "rgba(100,170,255,0.7)" : "rgba(100,100,140,0.25)";
      ctx.lineWidth = hi ? 1.5 / z.scale : 0.5 / z.scale;
      ctx.beginPath();
      ctx.moveTo(l.source.x, l.source.y);
      ctx.lineTo(l.target.x, l.target.y);
      ctx.stroke();
    });

    // nodes
    var showLabel = z.scale > 0.4;
    nodes.forEach(function (n) {
      var r = n.r;
      var isHover = n === hover;
      var isSel = n === selected;
      var rDraw = (isHover || isSel) ? r * 1.6 : r;

      ctx.beginPath();
      ctx.arc(n.x, n.y, rDraw, 0, Math.PI * 2);
      // glow for hover/selected
      if (isHover || isSel) {
        var g = ctx.createRadialGradient(n.x, n.y, rDraw * 0.3, n.x, n.y, rDraw * 2.5);
        g.addColorStop(0, "#6affcc");
        g.addColorStop(1, "rgba(100,170,255,0.05)");
        ctx.fillStyle = g;
        ctx.fill();
        ctx.beginPath();
        ctx.arc(n.x, n.y, rDraw, 0, Math.PI * 2);
      }
      ctx.fillStyle = (isHover || isSel) ? "#6af" : n.color;
      ctx.globalAlpha = (isHover || isSel) ? 1 : 0.85;
      ctx.fill();
      ctx.globalAlpha = 1;

      if (showLabel && (rDraw > 4 || isHover || isSel)) {
        ctx.fillStyle = (isHover || isSel) ? "#fff" : "rgba(255,255,255,0.6)";
        ctx.font = ((isHover || isSel) ? 11 : 9) + "px sans-serif";
        ctx.fillText(n.label, n.x + rDraw + 3, n.y + 3);
      }
    });

    ctx.restore();
  }

  App._renderGraph = function (container) {
    if (!App._activeProject) {
      container.innerHTML = "<div style=\"padding:40px;text-align:center;color:#666\">Select a project from the sidebar</div>";
      return;
    }

    // reset zoom
    App._zoom = { scale: 1, x: 0, y: 0 };
    App._dragNode = null;
    App._panStart = null;
    App._hoverNode = null;
    App._selectedNode = null;

    var canvas = document.createElement("canvas");
    canvas.id = "graph-canvas";
    canvas.width = container.clientWidth;
    canvas.height = container.clientHeight || window.innerHeight - 48;
    container.appendChild(canvas);
    var ctx = canvas.getContext("2d");

    // toolbar
    var toolbar = document.createElement("div");
    toolbar.className = "graph-toolbar";
    toolbar.innerHTML = "<button onclick=\"App._zoomIn()\" title=\"Zoom in\">+</button>" +
      "<button onclick=\"App._zoomOut()\" title=\"Zoom out\">\u2212</button>" +
      "<button onclick=\"App._zoomFit()\" title=\"Fit all\">\u2922</button>" +
      "<span id=\"zoom-pct\" style=\"margin-left:6px;font-size:11px;color:#888\">100%</span>";
    container.appendChild(toolbar);

    // freshness card
    var card = document.createElement("div");
    card.className = "freshness-card";
    card.textContent = "Loading\u2026";
    container.appendChild(card);
    fetch("/api/structural/freshness?root=" + encodeURIComponent(App._activeProject ? App._activeProject.path || "" : "")).then(function (r) { return r.json(); }).then(function (f) {
      card.textContent = f.known ? (f.is_stale ? "\u26a0 stale" : "\u2713 fresh") : "\u2014";
      card.style.borderColor = f.is_stale ? "#e55" : "#5a5";
    }).catch(function () { card.textContent = "\u2014"; });

    // detail panel
    var detail = document.createElement("div");
    detail.id = "detail-panel";
    detail.className = "detail-panel";
    detail.style.display = "none";
    container.appendChild(detail);

    // load graph data
    fetch("/api/projects/" + App._activeProject.id + "/graph").then(function (r) { return r.json(); }).then(function (data) {
      var nodes = (data.nodes || []).map(function (n, i) {
        return {id:n.id, label:n.label||n.id, group:n.group||n.community||null,
          x:Math.random()*canvas.width, y:Math.random()*canvas.height,
          r:Math.max(3, Math.sqrt(n.degree||1)*2),
          color:n.color&&n.color.background||"#6af",
          degree:n.degree||0, _i:i, _raw:n};
      });
      var links = (data.links || []).map(function (l) {
        return {source:typeof l.source==="object"?l.source.id:l.source,
                target:typeof l.target==="object"?l.target.id:l.target};
      });

      // node index for connections lookup
      var nodeMap = {};
      nodes.forEach(function (n) { nodeMap[n.id] = n; });

      // FPS monitor
      App._fpsFrames = 0;
      if (App._fpsTimer) clearInterval(App._fpsTimer);
      App._fpsTimer = setInterval(function () {
        App._fps = App._fpsFrames;
        App._fpsFrames = 0;
      }, 1000);

      var sim = d3.forceSimulation(nodes)
        .force("link", d3.forceLink(links).id(function (d) { return d.id; }).distance(40))
        .force("charge", d3.forceManyBody().strength(-80).theta(0.9))
        .force("center", d3.forceCenter(canvas.width / 2, canvas.height / 2))
        .alphaDecay(0.02)
        .on("tick", function () {
          App._fpsFrames++;
          _draw(ctx, canvas, nodes, links, App._zoom, App._hoverNode, App._selectedNode);
        });
      App._sim = sim;
      App._nodes = nodes;
      App._links = links;

      // ── Zoom (wheel) ──
      canvas.addEventListener("wheel", function (e) {
        e.preventDefault();
        var rect = canvas.getBoundingClientRect();
        var mx = e.clientX - rect.left, my = e.clientY - rect.top;
        var factor = e.deltaY < 0 ? 1.12 : 1 / 1.12;
        var nz = App._zoom.scale * factor;
        if (nz < 0.05 || nz > 20) return;
        App._zoom.x = mx - (mx - App._zoom.x) * factor;
        App._zoom.y = my - (my - App._zoom.y) * factor;
        App._zoom.scale = nz;
        document.getElementById("zoom-pct").textContent = Math.round(nz * 100) + "%";
        _draw(ctx, canvas, nodes, links, App._zoom, App._hoverNode, App._selectedNode);
      }, { passive: false });

      // ── Pan (middle click or right click drag) ──
      canvas.addEventListener("mousedown", function (e) {
        var rect = canvas.getBoundingClientRect();
        var sx = e.clientX - rect.left, sy = e.clientY - rect.top;
        var w = _screenToWorld(sx, sy, App._zoom);
        var hit = _hitTest(w.x, w.y, nodes);

        if (e.button === 1 || (e.button === 0 && e.shiftKey)) {
          // pan
          App._panStart = { sx: sx, sy: sy, zx: App._zoom.x, zy: App._zoom.y };
          e.preventDefault();
        } else if (e.button === 0 && hit) {
          // drag node
          App._dragNode = hit;
          hit.fx = hit.x;
          hit.fy = hit.y;
          sim.alphaTarget(0.3).restart();
        } else if (e.button === 0 && !hit) {
          // pan with left click on empty space
          App._panStart = { sx: sx, sy: sy, zx: App._zoom.x, zy: App._zoom.y };
        }
      });

      canvas.addEventListener("mousemove", function (e) {
        var rect = canvas.getBoundingClientRect();
        var sx = e.clientX - rect.left, sy = e.clientY - rect.top;
        var w = _screenToWorld(sx, sy, App._zoom);

        // panning
        if (App._panStart) {
          App._zoom.x = App._panStart.zx + (sx - App._panStart.sx);
          App._zoom.y = App._panStart.zy + (sy - App._panStart.sy);
          _draw(ctx, canvas, nodes, links, App._zoom, App._hoverNode, App._selectedNode);
          return;
        }

        // dragging node
        if (App._dragNode) {
          App._dragNode.fx = w.x;
          App._dragNode.fy = w.y;
          return;
        }

        // hover
        var hit = _hitTest(w.x, w.y, nodes);
        canvas.style.cursor = hit ? "pointer" : (App._panStart ? "grabbing" : "grab");
        if (hit !== App._hoverNode) {
          App._hoverNode = hit;
          _draw(ctx, canvas, nodes, links, App._zoom, App._hoverNode, App._selectedNode);
        }
      });

      canvas.addEventListener("mouseup", function (e) {
        if (App._dragNode) {
          App._dragNode.fx = null;
          App._dragNode.fy = null;
          App._dragNode = null;
          sim.alphaTarget(0);
        }
        App._panStart = null;
      });

      // click to select node and show detail
      canvas.addEventListener("click", function (e) {
        var rect = canvas.getBoundingClientRect();
        var sx = e.clientX - rect.left, sy = e.clientY - rect.top;
        var w = _screenToWorld(sx, sy, App._zoom);
        var hit = _hitTest(w.x, w.y, nodes);

        if (hit) {
          App._selectedNode = hit;
          _showDetail(hit, links, nodeMap);
        } else {
          App._selectedNode = null;
          detail.style.display = "none";
        }
        _draw(ctx, canvas, nodes, links, App._zoom, App._hoverNode, App._selectedNode);
      });

      // double-click to center on node
      canvas.addEventListener("dblclick", function (e) {
        var rect = canvas.getBoundingClientRect();
        var sx = e.clientX - rect.left, sy = e.clientY - rect.top;
        var w = _screenToWorld(sx, sy, App._zoom);
        var hit = _hitTest(w.x, w.y, nodes);
        if (hit) {
          App._zoom.x = canvas.width / 2 - hit.x * App._zoom.scale;
          App._zoom.y = canvas.height / 2 - hit.y * App._zoom.scale;
          _draw(ctx, canvas, nodes, links, App._zoom, App._hoverNode, App._selectedNode);
        }
      });

      // resize
      var onResize = function () {
        canvas.width = container.clientWidth;
        canvas.height = container.clientHeight || window.innerHeight - 48;
        sim.force("center", d3.forceCenter(canvas.width / 2, canvas.height / 2));
        sim.alpha(0.3).restart();
      };
      window.addEventListener("resize", onResize);

      function _showDetail(node, links, nodeMap) {
        // find connected nodes
        var connected = [];
        links.forEach(function (l) {
          if (l.source === node && nodeMap[l.target.id]) connected.push(l.target);
          if (l.target === node && nodeMap[l.source.id]) connected.push(l.source);
        });
        var html = "<div style=\"display:flex;justify-content:space-between;align-items:center;margin-bottom:8px\">" +
          "<strong style=\"color:#6af\">" + node.label + "</strong>" +
          "<button onclick=\"document.getElementById('detail-panel').style.display='none'\" style=\"background:none;border:none;color:#888;cursor:pointer;font-size:16px\">\u00d7</button></div>" +
          "<div style=\"font-size:11px;color:#888;margin-bottom:8px\">ID: " + node.id + "</div>" +
          "<div style=\"font-size:12px;color:#ccc;margin-bottom:4px\">Degree: " + node.degree + "</div>" +
          "<div style=\"font-size:12px;color:#ccc;margin-bottom:4px\">Group: " + (node.group || "none") + "</div>" +
          "<div style=\"font-size:12px;color:#ccc;margin-bottom:8px\">Connections: " + connected.length + "</div>";
        if (connected.length > 0) {
          html += "<div style=\"font-size:11px;color:#888;margin-bottom:4px\">Connected to:</div>";
          connected.slice(0, 20).forEach(function (c) {
            html += "<div style=\"font-size:12px;color:#aaa;padding:2px 0;cursor:pointer\" onclick=\"App._focusNode('" + c.id.replace(/'/g, "\\'") + "')\">" + c.label + "</div>";
          });
          if (connected.length > 20) html += "<div style=\"font-size:11px;color:#666\">...and " + (connected.length - 20) + " more</div>";
        }
        detail.innerHTML = html;
        detail.style.display = "";
      }
    }).catch(function () {
      container.innerHTML = "<div style=\"padding:40px;text-align:center;color:#e55\">Failed to load graph. Run \u201cgraphify update <project>\u201d first.</div>";
    });
  };

  // zoom controls
  App._zoomIn = function () {
    var z = App._zoom;
    z.scale = Math.min(20, z.scale * 1.3);
    z.x -= (canvas_w() / 2 - z.x) * 0.3;
    z.y -= (canvas_h() / 2 - z.y) * 0.3;
    document.getElementById("zoom-pct").textContent = Math.round(z.scale * 100) + "%";
    _redraw();
  };
  App._zoomOut = function () {
    var z = App._zoom;
    z.scale = Math.max(0.05, z.scale / 1.3);
    z.x += (canvas_w() / 2 - z.x) * 0.23;
    z.y += (canvas_h() / 2 - z.y) * 0.23;
    document.getElementById("zoom-pct").textContent = Math.round(z.scale * 100) + "%";
    _redraw();
  };
  App._zoomFit = function () {
    App._zoom = { scale: 1, x: 0, y: 0 };
    document.getElementById("zoom-pct").textContent = "100%";
    _redraw();
  };
  App._focusNode = function (id) {
    if (!App._nodes) return;
    var n = App._nodes.find(function (n) { return n.id === id; });
    if (!n) return;
    App._selectedNode = n;
    App._zoom.x = canvas_w() / 2 - n.x * App._zoom.scale;
    App._zoom.y = canvas_h() / 2 - n.y * App._zoom.scale;
    _redraw();
  };

  function canvas_w() { var c = document.getElementById("graph-canvas"); return c ? c.width : 800; }
  function canvas_h() { var c = document.getElementById("graph-canvas"); return c ? c.height : 600; }
  function _redraw() {
    var c = document.getElementById("graph-canvas");
    if (c && App._nodes && App._links) {
      var ctx = c.getContext("2d");
      _draw(ctx, c, App._nodes, App._links, App._zoom, App._hoverNode, App._selectedNode);
    }
  }

  // ── Init ─────────────────────────────────────────────────────────
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", App.init);
  } else {
    App.init();
  }
})();