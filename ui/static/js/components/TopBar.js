(function () {
  var C = window.UIComponents;
  C.TopBar = {
    props: { corpora: Array, corpus: String, health: Object, mode: String, ask: Boolean,
             askAvailable: Boolean, replay: Boolean, busy: Boolean,
             compare: Boolean, compareAvailable: Boolean },
    emits: ["select-corpus", "change-mode", "update:ask", "update:compare", "new-chat"],
    components: { CorpusSwitch: C.CorpusSwitch, ModeToggle: C.ModeToggle, Settings: C.Settings },
    template: '<header class="topbar">' +
      '<div class="brand"><span class="brand-mark" aria-hidden="true">A</span><span class="brand-text">Agentic Dataset Search</span>' +
      '<span v-if="replay" class="replay-badge">Replay</span></div>' +
      '<div class="spacer"></div>' +
      '<CorpusSwitch :corpora="corpora" :current="corpus" :health="health" :replay="replay" @select="$emit(\'select-corpus\', $event)" />' +
      '<ModeToggle :mode="mode" @change="$emit(\'change-mode\', $event)" />' +
      '<Settings :ask="ask" :ask-available="askAvailable" :compare="compare" :compare-available="compareAvailable" ' +
      '@update:ask="$emit(\'update:ask\', $event)" @update:compare="$emit(\'update:compare\', $event)" />' +
      '<button type="button" class="btn" @click="$emit(\'new-chat\')">' +
      '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>New chat</button>' +
      '</header>'
  };
})();
