/* Application shell: one conversation per catalogue, one workspace. */
(function () {
  "use strict";
  var V = Vue, R = window.Reducer, C = window.UIComponents, Api = window.GeoApi;

  var CORPORA = [
    { id: "au", label: "Australia" },
    { id: "uk", label: "United Kingdom" },
    { id: "us", label: "United States" }
  ];
  var INTRO = {
    au: { title: "Australia", portal: "data.gov.au", text: "Open data from federal, state and local government agencies across Australia." },
    uk: { title: "United Kingdom", portal: "data.gov.uk", text: "Datasets published by UK central government, councils, agencies and public bodies." },
    us: { title: "United States", portal: "catalog.data.gov", text: "The federal open-data catalogue, with datasets from agencies, states and cities." }
  };

  // Preferences: localStorage can throw (private windows, blocked storage).
  var PREF_PREFIX = "ads.";
  function loadPref(key, fallback) {
    try {
      var v = window.localStorage.getItem(PREF_PREFIX + key);
      return v === null ? fallback : JSON.parse(v);
    } catch (e) { return fallback; }
  }
  function savePref(key, value) {
    try { window.localStorage.setItem(PREF_PREFIX + key, JSON.stringify(value)); } catch (e) { /* ignore */ }
  }

  var params = new URLSearchParams(window.location.search);
  var replayName = params.get("replay");
  var replaySpeed = params.get("speed") || "1";
  var liveDetail = params.get("live") === "1";
  var initialMode = params.get("mode");
  var counter = 0;

  var store = V.reactive({
    corpus: params.get("corpus") || "au",
    mode: initialMode === "advanced" || initialMode === "standard" ? initialMode : loadPref("mode", "standard"),
    ask: loadPref("ask", true),
    compare: loadPref("compare", false),
    health: {},
    examples: null, examplesLoading: false,
    messages: [], jobs: {}, history: [],
    focusJob: null, view: "main", detail: null,
    running: null, confirm: null, announce: "",
    // What the person opened or expanded, per job (see memoOf).
    memo: {}, scroll: {}
  });
  if (["standard", "advanced"].indexOf(store.mode) < 0) store.mode = "standard";
  if (typeof store.ask !== "boolean") store.ask = true;
  if (typeof store.compare !== "boolean") store.compare = false;

  var scrollEl = null;
  var restoreToken = 0;      // a newer restore, or the person scrolling, cancels older ones
  var healthTimer = null;

  // Expand/collapse and tab state of one job's cards, kept at application
  // level so that it survives a component being unmounted (mode switch, the
  // detail page). Cleared by New chat and by switching catalogue.
  function memoOf(key) {
    if (!key) return null;
    if (!store.memo[key]) {
      store.memo[key] = { cards: {}, groups: {}, tabs: {}, all: {}, drafts: {},
                          notices: {}, steps: false, err: false };
    }
    return store.memo[key];
  }

  // ---- jobs ---------------------------------------------------------------
  function apply(key, ev) {
    var job = store.jobs[key];
    if (!job) return;
    store.jobs[key] = R.reduce(job, ev);
    var j = store.jobs[key];
    if ((ev.type === "job_started" || ev.type === "queued") && replayName) {
      if (ev.corpus && ev.corpus !== store.corpus) store.corpus = ev.corpus;
      var um = store.messages.find(function (m) { return m.jobId === key && m.role === "user"; });
      if (um && !um.text && ev.question) um.text = ev.question;
    }
    if (ev.type === "progress" || ev.type === "step_start") store.announce = ev.label;
    if (ev.type === "clarify_question") store.announce = "The assistant has a question for you";
    if (ev.type === "answer") store.announce = "Answer ready";
    if (ev.type === "results") store.announce = "Results ready";
    if (ev.type === "error") store.announce = j.error ? j.error.message : "Error";
  }

  function finishJob(key, question, completed) {
    var job = store.jobs[key];
    if (job && R.isLive(job)) store.jobs[key] = R.interrupted(job);
    job = store.jobs[key];
    if (job && job.status === "done" && !replayName) {
      store.history = R.historyAfter(store.history, question,
                                     job.answer ? job.answer.text : null);
    }
    if (store.running && store.running.key === key) store.running = null;
  }

  function newJob(question) {
    var n = ++counter;
    var key = "j" + n;
    store.messages.push({ id: "u" + n, role: "user", text: question, jobId: key });
    store.messages.push({ id: "a" + n, role: "assistant", jobId: key });
    store.jobs[key] = R.initial(question, { ask: store.ask, corpus: store.corpus });
    memoOf(key);
    store.focusJob = key;
    store.view = "main";
    store.detail = null;
    return key;
  }

  function send(question) {
    question = (question || "").trim();
    if (!question || store.running || replayName) return;
    var key = newJob(question);
    var ctrl = new AbortController();
    var corpus = store.corpus;
    store.running = { key: key, controller: ctrl, corpus: corpus };
    var body = { question: question, history: store.history.slice(), ask: !!store.ask && askAvailable() };
    // Only sent when on, so the request is the same as before otherwise.
    if (store.compare && compareAvailable()) body.compare = true;
    Api.run(corpus, body, function (ev) { apply(key, ev); }, ctrl.signal)
      .catch(function (err) {
        if (ctrl.signal.aborted) return;
        var msg = err && err.status === 503
          ? "This catalogue is not available right now."
          : "The search could not be completed.";
        apply(key, { type: "error", message: msg, detail: String(err && err.message || err) });
        apply(key, { type: "done", ok: false, cancelled: false, steps: 0, duration_s: store.jobs[key].lastT || 0 });
      })
      .then(function () { finishJob(key, question, true); });
  }

  function startReplay() {
    var key = newJob("");
    var ctrl = new AbortController();
    store.running = { key: key, controller: ctrl, corpus: store.corpus, replay: true };
    Api.replay(replayName, replaySpeed, function (ev) { apply(key, ev); }, ctrl.signal)
      .catch(function (err) {
        apply(key, { type: "error", message: "This recording could not be played.", detail: String(err && err.message || err) });
        apply(key, { type: "done", ok: false, cancelled: false, steps: 0, duration_s: 0 });
      })
      .then(function () { finishJob(key, "", true); });
  }

  function stop() {
    var run = store.running;
    if (!run) return;
    var job = store.jobs[run.key];
    if (job && job.id && !run.replay) Api.cancel(run.corpus, job.id).catch(function () {});
    run.controller.abort();
    apply(run.key, { type: "done", ok: false, cancelled: true, steps: job ? job.steps.filter(function (s) { return s.status === "done"; }).length : 0, duration_s: job ? job.lastT : 0 });
    store.running = null;
  }

  function reply(text) {
    var run = store.running;
    if (!run) return;
    var job = store.jobs[run.key];
    if (!job || !job.clarify || !job.clarify.job_id) return;
    Api.reply(run.corpus, job.clarify.job_id, { reply: text }).catch(function (e) {
      apply(run.key, { type: "notice", level: "warning", text: "Your answer could not be sent. Try again or press Skip.", detail: String(e.message || e) });
    });
  }
  function skip() {
    var run = store.running;
    if (!run) return;
    var job = store.jobs[run.key];
    if (!job || !job.clarify || !job.clarify.job_id) return;
    Api.reply(run.corpus, job.clarify.job_id, { skip: true }).catch(function (e) {
      apply(run.key, { type: "notice", level: "warning", text: "Skipping could not be sent. Try again.", detail: String(e.message || e) });
    });
  }

  // ---- conversation -------------------------------------------------------
  function resetConversation() {
    if (store.running) stop();
    store.messages = []; store.jobs = {}; store.history = [];
    store.memo = {}; store.scroll = {};
    store.focusJob = null; store.view = "main"; store.detail = null;
    store.announce = "";
  }
  function askAvailable() {
    var h = store.health[store.corpus];
    return !h || h.ask_available !== false;
  }
  // The comparison view is offered only by a worker that says so.
  function compareAvailable() {
    var h = store.health[store.corpus];
    return !!(h && h.compare_available === true);
  }
  function loadExamples() {
    if (replayName) return;
    store.examples = null; store.examplesLoading = true;
    var corpus = store.corpus;
    Api.examples(corpus).then(function (e) {
      if (corpus === store.corpus) { store.examples = e; store.examplesLoading = false; }
    }).catch(function () {
      if (corpus === store.corpus) { store.examples = { examples: [], clarify: null }; store.examplesLoading = false; }
    });
  }
  function doSwitch(corpus) {
    resetConversation();
    store.corpus = corpus;
    loadExamples();
  }
  function requestSwitch(corpus) {
    if (corpus === store.corpus || replayName) return;
    if (store.running) {
      store.confirm = {
        title: "Switch catalogue?",
        body: "A search is running. Switching starts a new conversation and stops it.",
        ok: "Switch", run: function () { doSwitch(corpus); }
      };
    } else if (store.messages.length) {
      store.confirm = {
        title: "Switch catalogue?",
        body: "Switching starts a new conversation in the other catalogue.",
        ok: "Switch", run: function () { doSwitch(corpus); }
      };
    } else {
      doSwitch(corpus);
    }
  }
  function newChat() {
    if (replayName) { resetConversation(); return; }
    if (store.running) {
      store.confirm = { title: "Start a new chat?", body: "A search is running and will be stopped.",
                        ok: "New chat", run: resetConversation };
    } else {
      resetConversation();
    }
  }

  // ---- navigation ---------------------------------------------------------
  function openDataset(id) {
    store.view = "detail"; store.detail = { id: id };
  }
  function back() {
    store.view = "main"; store.detail = null;
  }

  // ---- health -------------------------------------------------------------
  function pollHealth() {
    Api.health().then(function (h) { store.health = h.corpora || {}; })
      .catch(function () {
        var down = {};
        CORPORA.forEach(function (c) { down[c.id] = { state: "error", reason: "The interface cannot reach its server." }; });
        store.health = down;
      });
  }

  var App = {
    components: {
      TopBar: C.TopBar, Chat: C.Chat, ResultList: C.ResultList,
      DatasetDetail: C.DatasetDetail, ProcessTimeline: C.ProcessTimeline,
      CompareSummary: C.CompareSummary
    },
    setup: function () {
      var focus = V.computed(function () { return store.focusJob ? store.jobs[store.focusJob] : null; });
      var corpusLabel = V.computed(function () {
        return CORPORA.find(function (c) { return c.id === store.corpus; }).label;
      });
      var detailItem = V.computed(function () {
        var f = focus.value;
        if (!f || !f.results || !store.detail) return null;
        return f.results.find(function (r) { return r.id === store.detail.id; }) || null;
      });
      var cited = V.computed(function () { return focus.value ? R.citedSet(focus.value) : {}; });
      var busy = V.computed(function () { return !!store.running && !store.running.replay; });
      var live = V.computed(function () { return focus.value ? R.isLive(focus.value) : false; });
      var jobError = V.computed(function () { return focus.value && focus.value.error; });
      var viewKey = V.computed(function () {
        var k = store.focusJob || "-";
        if (store.view === "detail" && store.detail) return k + ":detail:" + store.detail.id;
        return k + ":" + (store.focusJob ? store.mode : "empty");
      });

      V.onMounted(function () {
        scrollEl = document.getElementById("workspace-scroll");
        ["wheel", "touchmove", "keydown", "mousedown"].forEach(function (name) {
          scrollEl.addEventListener(name, function () { restoreToken += 1; }, { passive: true });
        });
        if (replayName) { startReplay(); }
        else {
          pollHealth();
          healthTimer = setInterval(pollHealth, 4000);
          loadExamples();
        }
      });
      V.onBeforeUnmount(function () { if (healthTimer) clearInterval(healthTimer); });
      V.watch(function () { return store.mode; }, function (m) { savePref("mode", m); });
      // Scroll memory per view. The watcher runs before the DOM changes, so the
      // old view's scrollTop is still readable; the new view's is restored once
      // it is rendered.
      V.watch(viewKey, function (next, prev) {
        if (scrollEl && prev) store.scroll[prev] = scrollEl.scrollTop;
        // Restore after render, and again over the next frames: charts and
        // fonts can still be settling, which shortens the scrollable height
        // for a moment and would clamp the position.
        var target = store.scroll[next] || 0;
        var token = ++restoreToken;
        function restore() {
          if (scrollEl && token === restoreToken) scrollEl.scrollTop = target;
        }
        V.nextTick(function () {
          restore();
          requestAnimationFrame(function () {
            restore();
            requestAnimationFrame(restore);
          });
          setTimeout(restore, 120);
        });
      }, { flush: "pre" });
      V.watch(function () { return store.ask; }, function (a) { savePref("ask", a); });
      V.watch(function () { return store.compare; }, function (a) { savePref("compare", a); });

      function confirmOk() {
        var c = store.confirm; store.confirm = null;
        if (c && c.run) c.run();
      }
      return {
        store: store, corpora: CORPORA, intro: INTRO, replay: !!replayName, liveDetail: liveDetail,
        focus: focus, corpusLabel: corpusLabel, detailItem: detailItem, cited: cited,
        busy: busy, live: live, jobError: jobError, memoOf: memoOf, askAvailable: V.computed(askAvailable),
        compareAvailable: V.computed(compareAvailable),
        send: send, stop: stop, reply: reply, skip: skip, requestSwitch: requestSwitch,
        newChat: newChat, openDataset: openDataset, back: back, confirmOk: confirmOk,
        setMode: function (m) { store.mode = m; },
        setAsk: function (a) { store.ask = a; },
        setCompare: function (a) { store.compare = a; },
        focusOn: function (k) { store.focusJob = k; store.view = "main"; store.detail = null; },
        health: V.computed(function () { return store.health; })
      };
    },
    template:
      '<div class="app">' +
      '<TopBar :corpora="corpora" :corpus="store.corpus" :health="health" :mode="store.mode" :ask="store.ask" ' +
      ':ask-available="askAvailable" :replay="replay" :busy="busy" ' +
      ':compare="store.compare" :compare-available="compareAvailable" ' +
      '@select-corpus="requestSwitch" @change-mode="setMode" @update:ask="setAsk" @update:compare="setCompare" @new-chat="newChat" />' +
      '<main class="main">' +
      '<section class="workspace" aria-label="Workspace">' +
      '<div class="workspace-head">' +
      '<template v-if="store.view === \'detail\'">' +
      '<button type="button" class="btn btn-quiet" @click="back">← {{ store.mode === "advanced" ? "Back to search process" : "Back to results" }}</button>' +
      '<div class="grow"></div></template>' +
      '<template v-else>' +
      '<h2>{{ !focus ? "Find datasets" : (store.mode === "advanced" ? "Search process" : "Results") }}</h2>' +
      '<span v-if="focus && focus.question" class="muted grow" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">“{{ focus.question }}”</span>' +
      '<div v-else class="grow"></div></template>' +
      '</div>' +
      '<div class="workspace-scroll" id="workspace-scroll">' +
      '<div v-if="store.view === \'detail\' && store.detail">' +
      '<DatasetDetail :key="store.detail.id" :corpus="store.corpus" :id="store.detail.id" :item="detailItem" ' +
      ':plan-area="focus ? focus.planArea : null" :cited="!!cited[store.detail.id]" :replay="replay && !liveDetail" @open="openDataset" /></div>' +
      '<div v-else-if="!focus" class="empty fade-in">' +
      '<h1>Find datasets by asking</h1>' +
      '<p>Search three open-data catalogues in plain language. You can watch what the assistant does at each step, and switch to Advanced to see the whole process.</p>' +
      '<div class="intro-grid">' +
      '<button v-for="c in corpora" :key="c.id" type="button" class="intro-card" :class="{current: c.id === store.corpus}" @click="requestSwitch(c.id)">' +
      '<h3>{{ intro[c.id].title }}</h3><p>{{ intro[c.id].text }}</p><p class="small" style="margin-top:8px">{{ intro[c.id].portal }}</p></button></div></div>' +
      '<div v-else-if="store.mode === \'advanced\'"><ProcessTimeline :job="focus" :memo="memoOf(store.focusJob)" @open="openDataset" /></div>' +
      '<div v-else>' +
      '<div v-if="jobError && !(focus.results && focus.results.length)" class="error-panel" role="alert" style="margin-bottom:16px">The search stopped before results were ready. {{ focus.error.message }}</div>' +
      '<p v-if="focus.scope && !(jobError && !(focus.results && focus.results.length))" class="small muted scope-line">Also searching within: {{ focus.scope }} (the publisher you asked about)</p>' +
      '<CompareSummary v-if="focus.comparison && !(jobError && !(focus.results && focus.results.length))" :cmp="focus.comparison" :memo="memoOf(store.focusJob)" @open="openDataset" />' +
      '<ResultList v-if="!(jobError && !(focus.results && focus.results.length))" :items="focus.results" :cited="cited" :live="live" @open="openDataset" /></div>' +
      '</div></section>' +
      '<Chat :messages="store.messages" :jobs="store.jobs" :focus-job="store.focusJob" :examples="store.examples" ' +
      ':mode="store.mode" :busy="busy" :disabled="replay" :corpus-label="corpusLabel" :loading="store.examplesLoading" :memo-for="memoOf" ' +
      '@send="send" @stop="stop" @open-dataset="openDataset" @reply="reply" @skip="skip" @focus="focusOn" />' +
      '</main>' +
      '<div class="sr-only" aria-live="polite" aria-atomic="true">{{ store.announce }}</div>' +
      '<div v-if="store.confirm" class="scrim" @click.self="store.confirm = null">' +
      '<div class="dialog" role="alertdialog" aria-modal="true" :aria-label="store.confirm.title">' +
      '<h2>{{ store.confirm.title }}</h2><p class="muted">{{ store.confirm.body }}</p>' +
      '<div class="row"><button type="button" class="btn" @click="store.confirm = null">Cancel</button>' +
      '<button type="button" class="btn btn-primary" @click="confirmOk">{{ store.confirm.ok }}</button></div></div></div>' +
      '</div>'
  };

  // Test hook: the page exposes the pure reducer and its store for the
  // reducer in tests; it is not a user interface.
  window.__ads = { store: store };
  V.createApp(App).mount("#app");
})();
