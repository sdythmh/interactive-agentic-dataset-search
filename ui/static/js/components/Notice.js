/* A gentle information bar with the original log line behind "Details". */
(function () {
  window.UIComponents.Notice = {
    props: { notice: Object, memo: Object },
    data() { return { local: false }; },
    template: '<div class="notice fade-in" role="status">' +
      '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" style="flex:none;margin-top:2px" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 8h.01M11 12h1v5h1"/></svg>' +
      '<div style="min-width:0"><div>{{ notice.text }}<span v-if="notice.count > 1" class="num" style="margin-left:6px;font-weight:600">\u00d7{{ notice.count }}</span></div>' +
      '<template v-if="lines.length"><button type="button" @click="open = !open" :aria-expanded="open">{{ open ? "Hide details" : "Details" }}</button>' +
      '<pre v-if="open">{{ joined }}</pre></template></div></div>',
    computed: {
      open: {
        get() { return this.memo ? !!this.memo.notices[this.notice.text] : this.local; },
        set(v) { if (this.memo) this.memo.notices[this.notice.text] = v; else this.local = v; }
      },
      joined() { return this.lines.join(String.fromCharCode(10)); },
      lines() {
        var n = this.notice;
        if (n.details && n.details.length) return n.details;
        return n.detail ? [n.detail] : [];
      }
    }
  };
})();
