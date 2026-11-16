/* Leaflet map: the record's footprint as a rectangle (or its own shape), a
 * marker at its centre, and, when given, the query area as a dashed outline.
 * Tiles come from OpenStreetMap; offline, a note is shown and the shapes and
 * coordinates still draw. */
(function () {
  var TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
  window.UIComponents.MapView = {
    props: {
      shape: Object,        // GeoJSON geometry of the record
      center: Array,        // [lon, lat]
      outline: Object,      // GeoJSON polygon of the query area
      outlineLabel: String,
      small: Boolean,
      shapeLabel: String
    },
    data() { return { offline: false, loaded: false }; },
    mounted() { this.draw(); },
    watch: { shape() { this.draw(); }, outline() { this.draw(); } },
    beforeUnmount() { this.destroy(); },
    methods: {
      destroy() {
        if (this._timer) clearTimeout(this._timer);
        if (this._map) { this._map.remove(); this._map = null; }
      },
      draw() {
        var vm = this;
        this.destroy();
        if (!window.L || !this.$refs.box) return;
        var L = window.L;
        var map = L.map(this.$refs.box, { zoomControl: !this.small, attributionControl: true, scrollWheelZoom: false });
        this._map = map;
        this.offline = false; this.loaded = false;
        var tiles = L.tileLayer(TILE_URL, {
          maxZoom: 18, attribution: "&copy; OpenStreetMap contributors"
        });
        tiles.on("tileload", function () { vm.loaded = true; vm.offline = false; });
        tiles.on("tileerror", function () { if (!vm.loaded) vm.offline = true; });
        tiles.addTo(map);
        this._timer = setTimeout(function () { if (!vm.loaded) vm.offline = true; }, 4000);
        if (typeof navigator !== "undefined" && navigator.onLine === false) this.offline = true;

        var group = L.featureGroup().addTo(map);
        if (this.shape) {
          L.geoJSON(this.shape, { style: { color: "#4F46E5", weight: 2, fillColor: "#4F46E5", fillOpacity: .12 } }).addTo(group);
        }
        if (this.center && this.center.length === 2) {
          L.marker([this.center[1], this.center[0]]).addTo(group);
        }
        if (this.outline) {
          L.geoJSON(this.outline, { style: { color: "#059669", weight: 2, dashArray: "6 6", fill: false } }).addTo(group);
        }
        var bounds = group.getBounds();
        if (bounds.isValid()) {
          if (bounds.getSouthWest().equals(bounds.getNorthEast())) map.setView(bounds.getCenter(), 8);
          else map.fitBounds(bounds, { padding: [24, 24] });
        } else {
          map.setView([0, 0], 1);
        }
        setTimeout(function () { map.invalidateSize(); }, 50);
      }
    },
    template: '<div><div class="map-wrap" :class="{small: small}"><div class="map-box" ref="box" role="img" aria-label="Map"></div>' +
      '<div v-if="offline" class="map-note">Map tiles unavailable offline</div></div>' +
      '<div class="legend"><span><i class="sw" style="background:#4F46E5"></i>{{ shapeLabel || "Area covered by this dataset" }}</span>' +
      '<span v-if="outline"><i class="sw" style="border:2px dashed #059669;height:10px;background:transparent"></i>{{ outlineLabel || "Area searched" }}</span></div></div>'
  };
})();
