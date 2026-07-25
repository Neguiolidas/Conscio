/* graphview.js — serve graphify-out/graph.html via iframe (v3.5) */
(function () {
  "use strict";

  function render(container) {
    container.innerHTML = "";
    var frame = document.createElement("iframe");
    frame.src = "/graph";
    frame.style.cssText = "width:100%;height:600px;border:1px solid #444;border-radius:6px";
    frame.title = "Graphify graph view";
    container.appendChild(frame);

    var note = document.createElement("p");
    note.className = "note";
    note.style.cssText = "margin-top:8px;color:#888;font-size:12px";
    note.textContent = "Graph view serves graphify-out/graph.html from the workspace root. Run \u201cgraphify update <root>\u201d to generate.";
    container.appendChild(note);
  }

  window.TAB_RENDERERS = window.TAB_RENDERERS || {};
  window.TAB_RENDERERS["graphview"] = render;
})();
