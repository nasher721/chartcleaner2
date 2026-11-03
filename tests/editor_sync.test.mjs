import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../chartcleaner/static/clean/editor.js', import.meta.url), 'utf8');

function browser() {
  class HTMLTextAreaElement {
    value = '';
  }
  const textarea = new HTMLTextAreaElement();
  const counts = {textContent: ''};
  const timeouts = new Map();
  const intervals = new Map();
  let nextTimer = 1;
  const window = {};
  const document = {
    querySelector(selector) {
      return selector === '[data-cc-marker="highlight-source"]' ? textarea : counts;
    },
  };
  const context = {
    window,
    document,
    HTMLTextAreaElement,
    requestAnimationFrame() { return 1; },
    setTimeout(callback, delay) {
      const id = nextTimer++;
      timeouts.set(id, {callback, delay});
      return id;
    },
    clearTimeout(id) { timeouts.delete(id); },
    setInterval(callback, delay) {
      const id = nextTimer++;
      intervals.set(id, {callback, delay});
      return id;
    },
  };
  vm.runInNewContext(source, context);
  return {
    editor: window.ccCleanEditor,
    textarea,
    fireDebounce() {
      const pending = [...timeouts.values()];
      assert.equal(pending.length, 1);
      assert.equal(pending[0].delay, 300);
      timeouts.clear();
      pending[0].callback();
    },
    fireRetry() {
      const retry = [...intervals.values()].find(timer => timer.delay === 1000);
      assert.ok(retry);
      retry.callback();
    },
  };
}

test('rapid input emits only the latest snapshot after 300 ms', () => {
  const {editor, textarea, fireDebounce} = browser();
  const emitted = [];
  textarea.value = 'first';
  editor.input({target: textarea}, value => emitted.push(value));
  textarea.value = 'second';
  editor.input({target: textarea}, value => emitted.push(value));
  assert.equal(emitted.length, 0);
  fireDebounce();
  assert.equal(emitted.length, 1);
  assert.equal(emitted[0].value, 'second');
  assert.equal(emitted[0].revision, 2);
});

test('unacknowledged snapshots retry and acknowledgement stops retries', () => {
  const {editor, textarea, fireDebounce, fireRetry} = browser();
  const emitted = [];
  const emit = value => emitted.push(value);
  textarea.value = 'chart';
  editor.input({target: textarea}, emit);
  fireDebounce();
  fireRetry();
  assert.equal(emitted.length, 2);
  assert.equal(emitted[0].revision, 1);
  assert.equal(emitted[1].revision, 1);
  editor.ack(1);
  fireRetry();
  assert.equal(emitted.length, 2);
});

test('an older acknowledgement cannot reopen retry of newer text', () => {
  const {editor, textarea, fireDebounce, fireRetry} = browser();
  const emitted = [];
  const emit = value => emitted.push(value);
  textarea.value = 'one';
  editor.input({target: textarea}, emit);
  fireDebounce();
  textarea.value = 'two';
  editor.input({target: textarea}, emit);
  fireDebounce();
  editor.ack(2);
  editor.ack(1);
  fireRetry();
  assert.equal(emitted.length, 2);
});

test('character counts use Unicode code points', () => {
  const {editor} = browser();
  const value = editor.counts('A😀𐐷 B');
  assert.equal(value.characters, 5);
  assert.equal(value.words, 2);
});
