/* The clarifying question: text box, Send, a prominent Skip; with answer
 * options (grounded rule), one chip per option, which sends its value. */
(function () {
  window.UIComponents.ClarifyCard = {
    props: { clarify: Object, disabled: Boolean, live: { type: Boolean, default: true } },
    emits: ["send", "skip"],
    data() { return { text: "" }; },
    computed: {
      awaiting() { return this.clarify.stage === "awaiting"; },
      closedText() {
        var st = this.clarify.stage;
        if (st === "answered") return "Searching again with your clarification";
        if (st === "skipped") return "Skipped";
        if (st === "timed_out") return "No answer was given in time, so the search went on without one";
        return "";
      }
    },
    methods: {
      send() { var t = this.text.trim(); if (t) this.$emit("send", t); }
    },
    template: '<section class="clarify fade-in" :class="{closed: !awaiting}" aria-label="Clarifying question">' +
      '<div class="kicker">Clarifying question</div>' +
      '<div class="q">{{ clarify.question }}</div>' +
      '<template v-if="awaiting">' +
      '<div v-if="clarify.options && clarify.options.length" class="chips" role="group" aria-label="Suggested answers">' +
      '<button v-for="o in clarify.options" :key="o.value" type="button" class="chip-btn" :disabled="disabled" @click="$emit(\'send\', o.value)">{{ o.label }}</button></div>' +
      '<div class="row"><input class="input" type="text" v-model="text" placeholder="Type your answer" aria-label="Your answer" @keydown.enter="send" :disabled="disabled">' +
      '<button type="button" class="btn btn-primary" :disabled="disabled || !text.trim()" @click="send">Send</button>' +
      '<button type="button" class="btn skip" :disabled="disabled" @click="$emit(\'skip\')">Skip</button></div>' +
      '<div class="small muted">Skip to continue without answering.</div></template>' +
      '<template v-else>' +
      '<div v-if="clarify.reply && clarify.stage === \'answered\'" class="quote">{{ clarify.reply }}</div>' +
      '<div class="pill" :class="clarify.stage === \'answered\' ? \'pill-accent\' : \'\'" style="justify-self:start">' +
      '<span v-if="clarify.stage === \'answered\' && live" class="spinner" aria-hidden="true"></span>{{ closedText }}</div></template>' +
      '</section>'
  };
})();
