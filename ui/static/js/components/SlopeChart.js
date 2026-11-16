/* Before -> after re-ranking for the final top ten. The left axis is the
 * position in the combined ranking and reaches as far as the furthest
 * dataset that moved in (up to 50); the right axis is the final 1-10. */
(function () {
  window.UIComponents.SlopeChart = {
    props: { top: Array },
    emits: ["open"],
    data() { return { hover: null }; },
    computed: {
      maxBefore() {
        var m = 10;
        this.top.forEach(t => { if (t.rank_before > m) m = t.rank_before; });
        return m;
      },
      H() { return Math.max(300, (this.maxBefore - 1) * 12); },
      vbHeight() { return this.H + 50; },
      lines() {
        var M = this.maxBefore, H = this.H, n = this.top.length;
        return this.top.map(t => ({
          id: t.id, title: t.title, before: t.rank_before, after: t.rank_after, moved: t.moved_in,
          y1: 24 + (M > 1 ? (t.rank_before - 1) / (M - 1) * H : 0),
          y2: 24 + (n > 1 ? (t.rank_after - 1) / (n - 1) * H : 0)
        }));
      },
      band() {
        var M = this.maxBefore;
        return { y: 24 - 6, h: (M > 1 ? 9 / (M - 1) * this.H : 0) + 12 };
      },
      hovered() { return this.lines.find(l => l.id === this.hover) || null; }
    },
    methods: {
      cut(t) { t = t || ""; return t.length > 44 ? t.slice(0, 43) + "…" : t; }
    },
    template: '<div>' +
      '<svg class="slope" :viewBox="\'0 0 700 \' + vbHeight" role="img" aria-label="Positions before and after re-ranking">' +
      '<rect class="band" x="0" :y="band.y" width="150" :height="band.h" rx="8"></rect>' +
      '<text x="4" y="14" style="font-weight:600">Combined ranking</text>' +
      '<text x="190" y="14" text-anchor="start" style="font-weight:600">After re-ranking</text>' +
      '<g v-for="l in lines" :key="l.id" @mouseenter="hover = l.id" @mouseleave="hover = null" @click="$emit(\'open\', l.id)" style="cursor:pointer">' +
      '<path class="hit" fill="none" stroke="transparent" stroke-width="12" style="pointer-events:stroke" :d="\'M 60 \' + l.y1 + \' C 120 \' + l.y1 + \' 130 \' + l.y2 + \' 190 \' + l.y2"></path>' +
      '<path class="line" :class="{in: l.moved, hover: hover === l.id}" :d="\'M 60 \' + l.y1 + \' C 120 \' + l.y1 + \' 130 \' + l.y2 + \' 190 \' + l.y2"></path>' +
      '<title>{{ l.title }}</title>' +
      '<circle cx="60" :cy="l.y1" r="3.5" :fill="l.moved ? \'#4F46E5\' : \'#9AA0B4\'"></circle>' +
      '<circle cx="190" :cy="l.y2" r="3.5" :fill="l.moved ? \'#4F46E5\' : \'#9AA0B4\'"></circle>' +
      '<text x="52" :y="l.y1 + 4" text-anchor="end" :style="{fill: l.moved ? \'#4F46E5\' : \'\', fontWeight: l.moved || hover === l.id ? 700 : 400}">{{ l.before }}</text>' +
      '<text x="200" :y="l.y2 + 4" :style="{fontWeight: hover === l.id ? 700 : 400}">{{ l.after }}  {{ cut(l.title) }}</text>' +
      '</g></svg>' +
      '<div class="small" style="min-height:20px">' +
      '<span v-if="hovered"><strong>{{ hovered.title }}</strong> — position {{ hovered.before }} before, {{ hovered.after }} after<span v-if="hovered.moved"> (moved in from outside the top 10)</span></span>' +
      '<span v-else class="muted">Blue lines moved in from outside the top 10. Hover a line for its title.</span></div></div>'
  };
})();
