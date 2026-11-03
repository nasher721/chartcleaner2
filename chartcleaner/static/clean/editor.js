(() => {
  const state = {revision: 0, acknowledged: 0, lastValue: null,
                 timer: null, raf: null, latestEmit: null};
  const selector = '[mark="highlight-source"] textarea';
  const textarea = () => document.querySelector(selector);

  function syncValue(ta) {
    if (ta && ta.value !== state.lastValue) {
      state.lastValue = ta.value;
      state.revision += 1;
    }
  }

  function counts(text) {
    const trimmed = text.trim();
    return {characters: Array.from(text).length,
            words: trimmed ? trimmed.split(/\s+/u).length : 0};
  }

  function drawCounts(ta) {
    if (state.raf) return;
    state.raf = requestAnimationFrame(() => {
      state.raf = null;
      const label = document.querySelector('[mark="chart-counts"]');
      if (!label || !ta) return;
      const value = counts(ta.value);
      label.textContent = `${value.characters.toLocaleString()} characters / ` +
                          `${value.words.toLocaleString()} words`;
    });
  }

  function snapshot() {
    const ta = textarea();
    if (!ta) return null;
    syncValue(ta);
    return {value: ta.value, revision: state.revision};
  }

  function flush() {
    if (!state.latestEmit) return;
    const value = snapshot();
    if (value && value.revision > state.acknowledged) {
      state.latestEmit(value);
    }
  }

  function input(event, emit) {
    const ta = event.target;
    if (!(ta instanceof HTMLTextAreaElement)) return;
    state.latestEmit = emit;
    syncValue(ta);
    drawCounts(ta);
    clearTimeout(state.timer);
    state.timer = setTimeout(flush, 300);
  }

  function ack(revision) {
    if (Number.isInteger(revision)) {
      state.acknowledged = Math.max(state.acknowledged, revision);
    }
  }

  window.ccCleanEditor = {input, snapshot, ack, counts};
  setInterval(flush, 1000);  // re-send unacknowledged edits after socket recovery
})();
