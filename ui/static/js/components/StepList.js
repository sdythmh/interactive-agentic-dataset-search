/* Live step with a spinner, then "Completed N steps - Xs" that expands. */
(function () {
  function secs(s) { return (Math.round(s * 10) / 10).toFixed(1) + "s"; }
  window.UIComponents.StepList = {
    props: { job: Object, memo: Object },
    data() { return { local: false }; },
    computed: {
      open: {
        get() { return this.memo ? !!this.memo.steps : this.local; },
        set(v) { if (this.memo) this.memo.steps = v; else this.local = v; }
      },
      live() { return this.job.status === "starting" || this.job.status === "running"; },
      finished() { return this.job.steps.filter(s => s.status === "done"); },
      total() {
        if (this.job.done) return this.job.done.duration_s;
        return this.job.lastT || 0;
      },
      count() { return this.job.done ? this.job.done.steps : this.finished.length; },
      stopped() { return this.job.status === "cancelled" || this.job.status === "interrupted"; }
    },
    methods: {
      secs,
      text(step) { return step.label.replace(/…$/, "").replace(/\.\.\.$/, ""); }
    },
    template: '<div>' +
      '<div v-if="live && job.progress" class="steps-live"><span class="spinner" aria-hidden="true"></span><span>{{ job.progress }}</span></div>' +
      '<div v-else-if="live" class="steps-live"><span class="spinner" aria-hidden="true"></span><span>Starting…</span></div>' +
      '<div v-if="count > 0 || stopped">' +
      '<button type="button" class="steps-toggle" :aria-expanded="open" @click="open = !open">' +
      '<span class="chev" :class="{open: open}" aria-hidden="true">▸</span>' +
      '<span class="num">{{ live ? "Done so far" : (stopped ? "Stopped after" : "Completed") }} {{ count }} step{{ count === 1 ? "" : "s" }} · {{ secs(total) }}</span></button>' +
      '<ul v-if="open" class="steps-list">' +
      '<li v-for="s in finished" :key="s.id"><span class="tick" aria-hidden="true">✓</span>' +
      '<span>{{ text(s) }}<span v-if="s.summary" class="muted"> — {{ s.summary }}</span></span>' +
      '<span class="dur">{{ secs(s.duration_ms / 1000) }}</span></li></ul></div></div>'
  };
})();
