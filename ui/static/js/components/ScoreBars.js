(function () {
  window.UIComponents.ScoreBars = {
    props: { top: Array },
    computed: {
      hasCombined() { return this.top.some(t => t.combined_score !== null && t.combined_score !== undefined); },
      maxRel() { return Math.max.apply(null, this.top.map(t => t.relevance_score || 0).concat([0.0001])); },
      maxComb() { return Math.max.apply(null, this.top.map(t => t.combined_score || 0).concat([0.0001])); }
    },
    methods: {
      w(v, m) { return Math.max(0, Math.min(100, (v / m) * 100)).toFixed(1) + "%"; },
      fmt(v, d) { return v === null || v === undefined ? "" : Number(v).toFixed(d); },
      // Scores from a cross-encoder can be tiny (a short follow-up scored
      // against ten datasets). 0.42 -> "0.42"; 0.00421 -> "0.00421";
      // below 0.0001 scientific notation.
      show(v) {
        if (v === null || v === undefined) return "";
        v = Number(v);
        if (v === 0) return "0";
        var a = Math.abs(v);
        if (a >= 0.01) return v.toFixed(2);
        if (a >= 0.0001) return v.toPrecision(3);
        return v.toExponential(2).replace("e-0", "e-");
      }
    },
    template: '<div>' +
      '<div v-for="t in top" :key="t.id" class="bar-row">' +
      '<span class="rank-sm">{{ t.rank_after }}</span>' +
      '<span class="t" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap" :title="t.title">{{ t.title }}</span>' +
      '<span><span class="bar" role="img" :title="\'Raw value \' + t.relevance_score" :aria-label="\'Relevance \' + show(t.relevance_score)"><i :style="{width: w(t.relevance_score || 0, maxRel)}"></i></span>' +
      '<span v-if="hasCombined && t.combined_score !== null && t.combined_score !== undefined" class="bar alt" style="display:block;margin-top:3px" :aria-label="\'Combined \' + fmt(t.combined_score, 4)"><i :style="{width: w(t.combined_score, maxComb)}"></i></span></span>' +
      '<span class="rank-sm num" :title="\'Raw value \' + t.relevance_score">{{ show(t.relevance_score) }}</span></div>' +
      '<div class="legend"><span><i class="sw" style="background:#4F46E5"></i>Relevance score</span>' +
      '<span>Bars are relative to the top result.</span>' +
      '<span v-if="hasCombined"><i class="sw" style="background:#A5B4FC"></i>Combined with knowledge graph relations</span></div></div>'
  };
})();
