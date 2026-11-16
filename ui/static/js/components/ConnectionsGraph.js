/* The record's neighbourhood in the knowledge graph (Cytoscape, concentric). */
(function () {
  var COLORS = { centre: "#4F46E5", related: "#7C3AED", relatedOff: "#C4B5FD", publisher: "#0F766E", tag: "#94A3B8" };
  window.UIComponents.ConnectionsGraph = {
    props: { graph: Object },
    emits: ["open"],
    data() { return { tip: "", clickable: false }; },
    mounted() { this.draw(); },
    watch: { graph() { this.draw(); } },
    beforeUnmount() { if (this._cy) { this._cy.destroy(); this._cy = null; } },
    methods: {
      trunc(t) { t = t || ""; return t.length > 24 ? t.slice(0, 23) + "…" : t; },
      draw() {
        var vm = this;
        if (this._cy) { this._cy.destroy(); this._cy = null; }
        if (!this.graph || !window.cytoscape || !this.$refs.box) return;
        var nodes = this.graph.nodes.map(function (n) {
          var type = n.center ? "centre" : (n.kind === "dataset" ? (n.in_index ? "related" : "relatedOff") : n.kind);
          return { data: { id: n.id, label: vm.trunc(n.label), full: n.label, type: type,
                           record: n.record_id || null, ring: n.center ? 3 : (n.kind === "dataset" ? 2 : (n.kind === "publisher" ? 1 : 1)) } };
        });
        var edges = this.graph.edges.map(function (e, i) {
          return { data: { id: "e" + i, source: e.source, target: e.target } };
        });
        var cy = window.cytoscape({
          container: this.$refs.box, elements: { nodes: nodes, edges: edges },
          layout: { name: "concentric", padding: 24, minNodeSpacing: 40, avoidOverlap: true,
                    concentric: function (n) { return n.data("ring"); },
                    levelWidth: function () { return 1; } },
          style: [
            { selector: "node", style: { label: "data(label)", "font-size": 9, "text-background-color": "#F9FAFC", "text-background-opacity": 0.85, "text-background-padding": 1, "font-family": "Inter, sans-serif",
              color: "#1F2330", "text-valign": "bottom", "text-margin-y": 4, "text-wrap": "none",
              width: 16, height: 16, "background-color": COLORS.tag, "border-width": 0 } },
            { selector: 'node[type = "centre"]', style: { width: 28, height: 28, "background-color": COLORS.centre, "font-weight": 600 } },
            { selector: 'node[type = "related"]', style: { "background-color": COLORS.related } },
            { selector: 'node[type = "relatedOff"]', style: { "background-color": COLORS.relatedOff } },
            { selector: 'node[type = "publisher"]', style: { "background-color": COLORS.publisher, shape: "round-rectangle", width: 20, height: 20 } },
            { selector: 'node[type = "tag"]', style: { "background-color": COLORS.tag, shape: "ellipse", width: 12, height: 12 } },
            { selector: "edge", style: { width: 1, "line-color": "#D5D9E4", "curve-style": "straight" } },
            { selector: "node.hover", style: { "border-width": 3, "border-color": "#1F2330" } }
          ],
          userZoomingEnabled: false, boxSelectionEnabled: false, autoungrabify: true,
          minZoom: 0.3, maxZoom: 1.3
        });
        cy.on("mouseover", "node", function (e) {
          var n = e.target; n.addClass("hover"); vm.tip = n.data("full");
          var clickable = n.data("type") === "related";
          vm.clickable = clickable;
          if (vm.$refs.box) vm.$refs.box.style.cursor = clickable ? "pointer" : "default";
        });
        cy.on("mouseout", "node", function (e) {
          e.target.removeClass("hover"); vm.tip = "";
          if (vm.$refs.box) vm.$refs.box.style.cursor = "default";
        });
        cy.on("tap", "node", function (e) {
          var d = e.target.data();
          if (d.type === "related" && d.record) vm.$emit("open", d.record);
        });
        this._cy = cy;
      }
    },
    template: '<div><div class="graph-box"><div ref="box" style="position:absolute;inset:0" role="img" aria-label="Knowledge graph neighbourhood"></div>' +
      '<div v-if="tip" class="graph-tip">{{ tip }}</div></div>' +
      '<div class="legend">' +
      '<span><i class="dot" style="background:#4F46E5"></i> This dataset</span>' +
      '<span><i class="dot" style="background:#7C3AED"></i> Related dataset (click to open)</span>' +
      '<span><i class="dot" style="background:#C4B5FD"></i> Related, not in this index</span>' +
      '<span><i class="dot" style="background:#0F766E"></i> Publisher</span>' +
      '<span><i class="dot" style="background:#94A3B8"></i> Tag</span></div></div>'
  };
})();
