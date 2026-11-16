/* Thin client for the gateway. No state; every call returns a promise.
 * run() and replay() parse the Server-Sent Events stream with fetch and a
 * ReadableStream (EventSource cannot POST). */
(function (root) {
  "use strict";

  function getJson(url) {
    return fetch(url, { headers: { Accept: "application/json" } }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (body) {
        if (!res.ok) {
          var err = new Error((body && body.detail) || ("Request failed (" + res.status + ")"));
          err.status = res.status;
          throw err;
        }
        return body;
      });
    });
  }

  function postJson(url, body) {
    return fetch(url, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {})
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (!res.ok) {
          var err = new Error((data && data.detail) || ("Request failed (" + res.status + ")"));
          err.status = res.status;
          throw err;
        }
        return data;
      });
    });
  }

  // Parses "id: / event: / data:" blocks separated by a blank line.
  function readEvents(res, onEvent, signal) {
    var reader = res.body.getReader();
    var decoder = new TextDecoder("utf-8");
    var buffer = "";
    function handleBlock(block) {
      var data = [];
      block.split("\n").forEach(function (line) {
        if (line.indexOf("data:") === 0) data.push(line.slice(5).replace(/^ /, ""));
      });
      if (!data.length) return;          // comment / keep-alive
      try { onEvent(JSON.parse(data.join("\n"))); } catch (e) { /* skip a bad block */ }
    }
    function pump() {
      return reader.read().then(function (r) {
        if (r.done) {
          if (buffer.trim()) handleBlock(buffer);
          return;
        }
        buffer += decoder.decode(r.value, { stream: true }).replace(/\r\n/g, "\n");
        var i;
        while ((i = buffer.indexOf("\n\n")) >= 0) {
          var block = buffer.slice(0, i);
          buffer = buffer.slice(i + 2);
          handleBlock(block);
        }
        if (signal && signal.aborted) return;
        return pump();
      });
    }
    return pump();
  }

  function stream(url, options, onEvent, signal) {
    options = options || {};
    options.signal = signal;
    return fetch(url, options).then(function (res) {
      if (!res.ok) {
        return res.json().catch(function () { return {}; }).then(function (b) {
          var err = new Error((b && b.detail) || ("Request failed (" + res.status + ")"));
          err.status = res.status;
          throw err;
        });
      }
      return readEvents(res, onEvent, signal);
    });
  }

  root.GeoApi = {
    health: function () { return getJson("/api/health"); },
    examples: function (corpus) { return getJson("/api/" + corpus + "/examples"); },
    record: function (corpus, id) {
      return getJson("/api/" + corpus + "/record/" + encodeURIComponent(id));
    },
    neighbours: function (corpus, id) {
      return getJson("/api/" + corpus + "/neighbours/" + encodeURIComponent(id));
    },
    reply: function (corpus, jobId, body) {
      return postJson("/api/" + corpus + "/reply/" + encodeURIComponent(jobId), body);
    },
    cancel: function (corpus, jobId) {
      return postJson("/api/" + corpus + "/cancel/" + encodeURIComponent(jobId), {});
    },
    run: function (corpus, body, onEvent, signal) {
      return stream("/api/" + corpus + "/run", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      }, onEvent, signal);
    },
    replay: function (name, speed, onEvent, signal) {
      return stream("/api/replay/" + encodeURIComponent(name) +
                    "?speed=" + encodeURIComponent(speed), {}, onEvent, signal);
    }
  };
})(window);
