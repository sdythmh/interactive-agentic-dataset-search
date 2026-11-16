/* Segmented control for the three catalogues, each with a status light. */
(function () {
  window.UIComponents.CorpusSwitch = {
    props: { corpora: Array, current: String, health: Object, replay: Boolean },
    emits: ["select"],
    components: { StatusDot: window.UIComponents.StatusDot },
    methods: {
      state(c) {
        if (this.replay) return "unknown";
        var h = this.health && this.health[c];
        return h ? h.state : "unknown";
      },
      title(c) {
        if (this.replay) return "Replay";
        var h = this.health && this.health[c];
        if (!h) return "Checking status";
        var words = { ready: "Ready", starting: "Starting up", busy: "Searching", error: "Unavailable" };
        var t = words[h.state] || "Unknown";
        if (h.state === "ready" && h.index && h.index.documents) {
          t += " · " + h.index.documents.toLocaleString("en-US") + " datasets";
        }
        if (h.state === "error") {
          var why = h.reason || (h.errors && h.errors.length ? "Not reachable: " + h.errors.join(", ") : "");
          if (why) t += " · " + why;
        }
        return t;
      }
    },
    template: '<div class="segmented" role="tablist" aria-label="Catalogue">' +
      '<button v-for="c in corpora" :key="c.id" role="tab" type="button" :aria-selected="c.id === current" ' +
      ':title="title(c.id)" @click="$emit(\'select\', c.id)">' +
      '<StatusDot :state="state(c.id)" :label="title(c.id)" />{{ c.label }}</button></div>'
  };
})();
