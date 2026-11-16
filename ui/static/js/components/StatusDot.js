/* A small status light. state: ready | starting | busy | error | unknown. */
(function () {
  window.UIComponents = window.UIComponents || {};
  window.UIComponents.StatusDot = {
    props: { state: { type: String, default: "unknown" }, label: { type: String, default: "" } },
    template: '<span class="status-dot" :class="state" role="img" :aria-label="label || state" :title="label"></span>'
  };
})();
