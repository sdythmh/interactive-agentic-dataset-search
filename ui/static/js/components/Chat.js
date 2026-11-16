(function () {
  var C = window.UIComponents, R = window.Reducer;
  C.Chat = {
    props: { messages: Array, jobs: Object, focusJob: String, examples: Object, mode: String,
             busy: Boolean, disabled: Boolean, corpusLabel: String, loading: Boolean, memoFor: Function },
    emits: ["send", "stop", "open-dataset", "reply", "skip", "focus"],
    components: { Message: C.Message },
    data() { return { text: "" }; },
    watch: {
      messages: { handler() { this.scroll(); }, deep: true },
      jobs: { handler() { this.scroll(); }, deep: true }
    },
    methods: {
      scroll() {
        this.$nextTick(() => {
          var el = this.$refs.scroll;
          if (el) el.scrollTop = el.scrollHeight;
        });
      },
      submit() {
        var t = this.text.trim();
        if (!t || this.busy || this.disabled) return;
        this.$emit("send", t);
        this.text = "";
      },
      pick(t) { if (!this.busy && !this.disabled) this.$emit("send", t); },
      jobOf(m) { return m.jobId ? this.jobs[m.jobId] : null; }
    },
    template: '<section class="chat" aria-label="Chat">' +
      '<div class="chat-scroll" ref="scroll">' +
      '<div v-if="!messages.length" class="chat-empty">' +
      '<h3>What are you looking for?</h3>' +
      '<p class="muted">Ask in your own words, in any language. {{ corpusLabel }} is selected.</p>' +
      '<div v-if="loading" class="chips"><span class="skeleton" style="height:32px;width:70%"></span><span class="skeleton" style="height:32px;width:55%"></span><span class="skeleton" style="height:32px;width:62%"></span></div>' +
      '<template v-else-if="examples && examples.examples && examples.examples.length">' +
      '<div class="examples-label">Try one of these</div>' +
      '<div class="chips"><button v-for="e in examples.examples" :key="e.text" type="button" class="chip-btn" @click="pick(e.text)">{{ e.text }}</button></div>' +
      '<template v-if="examples.clarify">' +
      '<div class="examples-label" style="margin-top:8px">A deliberately vague question, to see a clarifying question</div>' +
      '<div class="chips"><button type="button" class="chip-btn" @click="pick(examples.clarify.text)">{{ examples.clarify.text }}</button></div>' +
      '<div class="small muted" v-if="examples.clarify.suggested_reply">You could answer: “{{ examples.clarify.suggested_reply }}”</div>' +
      '</template></template></div>' +
      '<template v-for="m in messages" :key="m.id">' +
      '<Message :message="m" :job="jobOf(m)" :focused="m.jobId === focusJob" :mode="mode" :memo="memoFor(m.jobId)" ' +
      '@open-dataset="$emit(\'open-dataset\', $event)" @reply="$emit(\'reply\', $event)" @skip="$emit(\'skip\')" @focus="$emit(\'focus\', m.jobId)" />' +
      '</template></div>' +
      '<form class="composer" @submit.prevent="submit">' +
      '<div class="row"><input class="input" type="text" v-model="text" :disabled="disabled" ' +
      ':placeholder="busy ? \'Searching…\' : \'Ask about datasets…\'" aria-label="Your question" autocomplete="off">' +
      '<button v-if="busy" type="button" class="btn" @click="$emit(\'stop\')">Stop</button>' +
      '<button v-else type="submit" class="btn btn-primary" :disabled="!text.trim() || disabled">Send</button></div>' +
      '</form></section>'
  };
})();
