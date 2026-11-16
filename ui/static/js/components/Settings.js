/* Gear button with a small popover. */
(function () {
  window.UIComponents.Settings = {
    props: { ask: Boolean, askAvailable: { type: Boolean, default: true },
             compare: Boolean, compareAvailable: { type: Boolean, default: false } },
    emits: ["update:ask", "update:compare"],
    data() { return { open: false }; },
    mounted() {
      this._doc = (e) => { if (this.open && !this.$el.contains(e.target)) this.open = false; };
      this._key = (e) => { if (e.key === "Escape") this.open = false; };
      document.addEventListener("mousedown", this._doc);
      document.addEventListener("keydown", this._key);
    },
    beforeUnmount() {
      document.removeEventListener("mousedown", this._doc);
      document.removeEventListener("keydown", this._key);
    },
    template: '<div style="position:relative">' +
      '<button type="button" class="icon-btn" aria-label="Settings" :aria-expanded="open" @click="open = !open">' +
      '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg></button>' +
      '<div v-if="open" class="popover" role="dialog" aria-label="Settings">' +
      '<h3>Settings</h3>' +
      '<label class="check"><input type="checkbox" :checked="ask" :disabled="!askAvailable" @change="$emit(\'update:ask\', $event.target.checked)">' +
      '<span><strong>Ask me when my question is unclear</strong><br><span class="muted small">The assistant may ask one short question after its first search to narrow things down.</span></span></label>' +
      '<p v-if="!askAvailable" class="small muted" style="margin-top:8px">Clarifying questions are not available right now.</p>' +
      '<label v-if="compareAvailable" class="check" style="margin-top:12px"><input type="checkbox" :checked="compare" @change="$emit(\'update:compare\', $event.target.checked)">' +
      '<span><strong>Compare with simpler search</strong><br><span class="muted small">After each search, show the results next to a plain keyword search and a single search round, and mark what the assistant changed.</span></span></label>' +
      '</div></div>'
  };
})();
