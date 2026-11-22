/* global URL, document, location, window */
(function () {
  "use strict";
  function embed(element, options) {
    var server = String(options.server || "").replace(/\/$/, "");
    var serverOrigin = new URL(server).origin;
    var frame = document.createElement("iframe");
    frame.title = options.title || "Dashboard";
    frame.style.border = "0"; frame.style.width = "100%"; frame.style.height = options.height || "600px";
    var hash = "token=" + encodeURIComponent(options.token) + "&origin=" + encodeURIComponent(location.origin);
    frame.src = server + "/embed/" + encodeURIComponent(options.dashboard) + "#" + hash;
    function onMessage(event) {
      if (event.origin !== serverOrigin || event.source !== frame.contentWindow || !event.data || event.data.source !== "tabfree") return;
      if (event.data.type === "state" && options.onState) options.onState(event.data.state, event.data.selections);
      if (event.data.type === "ready" && options.onReady) options.onReady();
      if (event.data.type === "error" && options.onError) options.onError(event.data.message);
    }
    window.addEventListener("message", onMessage);
    element.appendChild(frame);
    return {
      setState: function (state) {
        frame.contentWindow.postMessage({ target: "tabfree", type: "setState", filters: state.filters || {}, parameters: state.parameters || {} }, serverOrigin);
      },
      destroy: function () { window.removeEventListener("message", onMessage); frame.remove(); }
    };
  }
  window.TabFree = { embed: embed };
})();
