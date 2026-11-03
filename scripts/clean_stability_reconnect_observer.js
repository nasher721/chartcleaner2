(() => {
  let wasVisible = false;
  window.__ccReconnects = 0;

  function check() {
    const indicators = document.querySelectorAll(
      '#popup, [data-cc-reconnecting], [class*="reconnect" i]');
    const visible = Array.from(indicators).some(element =>
      /reconnecting/i.test(element.textContent || '') && element.checkVisibility());
    if (visible && !wasVisible) window.__ccReconnects += 1;
    wasVisible = visible;
  }

  function start() {
    check();
    new MutationObserver(check).observe(document.documentElement, {
      subtree: true,
      childList: true,
      attributes: true,
      characterData: true,
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, {once: true});
  } else {
    start();
  }
})();
