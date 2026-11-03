import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../scripts/clean_stability_reconnect_observer.js', import.meta.url), 'utf8');

function browser() {
  const window = {};
  const indicators = [];
  let observer;
  const document = {
    readyState: 'complete',
    documentElement: {},
    querySelectorAll() { return indicators; },
  };
  class MutationObserver {
    constructor(callback) { observer = callback; }
    observe() {}
  }
  vm.runInNewContext(source, {window, document, MutationObserver});
  return {window, indicators, mutate: () => observer()};
}

function indicator(text, visible) {
  return {textContent: text, checkVisibility: () => visible};
}

test('hidden reconnect markup does not count as a reconnection', () => {
  const page = browser();
  page.indicators.push(indicator('Reconnecting', false));
  page.mutate();
  assert.equal(page.window.__ccReconnects, 0);
});

test('only hidden-to-visible reconnect transitions increment the counter', () => {
  const page = browser();
  const banner = indicator('Reconnecting', false);
  page.indicators.push(banner);
  page.mutate();
  banner.textContent = 'Reconnecting';
  banner.checkVisibility = () => true;
  page.mutate();
  page.mutate();
  assert.equal(page.window.__ccReconnects, 1);
  banner.checkVisibility = () => false;
  page.mutate();
  banner.checkVisibility = () => true;
  page.mutate();
  assert.equal(page.window.__ccReconnects, 2);
});

test('visible unrelated status text is ignored', () => {
  const page = browser();
  page.indicators.push(indicator('Ready', true));
  page.mutate();
  assert.equal(page.window.__ccReconnects, 0);
});
