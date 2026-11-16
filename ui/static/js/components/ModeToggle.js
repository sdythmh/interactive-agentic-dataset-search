(function () {
  window.UIComponents.ModeToggle = {
    props: { mode: String },
    emits: ["change"],
    template: '<div class="segmented" role="group" aria-label="Display mode">' +
      '<button type="button" :aria-pressed="mode === \'standard\'" @click="$emit(\'change\', \'standard\')">Standard</button>' +
      '<button type="button" :aria-pressed="mode === \'advanced\'" @click="$emit(\'change\', \'advanced\')">Advanced</button></div>'
  };
})();
