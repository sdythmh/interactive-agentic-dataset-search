/* One chat message. User: a bubble. Assistant: live steps, notices, the
 * clarifying question, the answer with citation chips, or an error. */
(function () {
  var C = window.UIComponents, R = window.Reducer;
  C.Message = {
    props: { message: Object, job: Object, focused: Boolean, mode: String, memo: Object },
    emits: ["open-dataset", "reply", "skip", "focus"],
    components: { StepList: C.StepList, Notice: C.Notice, ClarifyCard: C.ClarifyCard },
    data() { return { localErr: false }; },
    computed: {
      showErr: {
        get() { return this.memo ? !!this.memo.err : this.localErr; },
        set(v) { if (this.memo) this.memo.err = v; else this.localErr = v; }
      },
      live() { return R.isLive(this.job); },
      html() {
        return this.job.answer ? R.renderAnswer(this.job.answer, R.rankMap(this.job)) : "";
      },
      refused() { return this.job.answer && this.job.answer.refused; },
      noCitation() {
        var a = this.job.answer;
        return !!(a && (a.refused || !(a.cited && a.cited.length)) && this.hasResults);
      },
      hasResults() { return !!(this.job.results && this.job.results.length); },
      stoppedNote() {
        if (this.job.status === "cancelled") return "This search was stopped.";
        if (this.job.status === "interrupted") return "The connection was lost before the search finished.";
        return "";
      }
    },
    methods: {
      onClick(e) {
        var b = e.target.closest && e.target.closest("button.cite");
        if (b && b.dataset.id) this.$emit("open-dataset", b.dataset.id);
      }
    },
    template: '<div v-if="message.role === \'user\'" class="msg-user">{{ message.text }}</div>' +
      '<div v-else class="msg-assistant fade-in">' +
      '<div v-if="job.status === \'queued\'" class="queued"><span class="spinner" aria-hidden="true"></span>' +
      '<span>{{ (job.queued && job.queued.text) || "Waiting for the current search to finish" }}</span></div>' +
      '<StepList v-else :job="job" :memo="memo" />' +
      '<Notice v-for="(n, i) in job.notices" :key="i" :notice="n" :memo="memo" />' +
      '<ClarifyCard v-if="job.clarify" :clarify="job.clarify" :live="live" :disabled="!live" @send="$emit(\'reply\', $event)" @skip="$emit(\'skip\')" />' +
      '<div v-if="job.answer" class="bubble answer" @click="onClick">' +
      '<div v-html="html"></div></div>' +
      '<div v-if="noCitation" class="small muted">The datasets the search found are listed on the left.</div>' +
      '<div v-if="stoppedNote" class="small muted">{{ stoppedNote }}</div>' +
      '<div v-if="job.error" class="err" role="alert"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" style="flex:none;margin-top:2px" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7v6M12 16.5h.01"/></svg>' +
      '<div style="min-width:0"><div>{{ job.error.message }}</div>' +
      '<button type="button" @click="showErr = !showErr" :aria-expanded="showErr">{{ showErr ? "Hide details" : "Details" }}</button>' +
      '<pre v-if="showErr" style="white-space:pre-wrap;margin:6px 0 0;font:12px/1.4 ui-monospace,monospace;overflow-wrap:anywhere">{{ job.error.detail }}</pre></div></div>' +
      '<div v-if="hasResults && !focused" class="small"><button type="button" class="link-btn" @click="$emit(\'focus\')">Show these results</button></div>' +
      '</div>'
  };
})();
