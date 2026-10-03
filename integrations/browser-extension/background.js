import { ACTIONS, DEFAULT_PORT, buildRequest, describeResponse } from "./request.js";

chrome.runtime.onInstalled.addListener(() => {
  for (const [id, action] of Object.entries(ACTIONS)) {
    chrome.contextMenus.create({ id, title: action.title, contexts: ["selection"] });
  }
});

chrome.action.onClicked.addListener(() => chrome.runtime.openOptionsPage());

// Runs inside the page: the selection with its line breaks (selectionText loses them).
function readSelection() {
  return window.getSelection().toString();
}

// Runs inside the page: copy the result and show a small toast.
function showResult(ok, text, message) {
  const done = () => {
    const toast = document.createElement("div");
    toast.textContent = `Chart Cleaner: ${message}`;
    toast.style.cssText = "position:fixed;z-index:2147483647;right:16px;bottom:16px;max-width:420px;" +
      "padding:10px 14px;border-radius:8px;font:13px system-ui;color:#fff;" +
      `background:${ok ? "#1b5e20" : "#b71c1c"};box-shadow:0 2px 8px rgba(0,0,0,.3)`;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 4000);
  };
  if (ok) navigator.clipboard.writeText(text).then(done, () => { message = "could not copy"; ok = false; done(); });
  else done();
}

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (!ACTIONS[info.menuItemId] || !tab?.id) return;
  let text = info.selectionText || "";
  try {
    const [{ result }] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: readSelection });
    if (result && result.trim()) text = result;
  } catch (_) { /* some pages block scripts; fall back to selectionText */ }

  const { port = DEFAULT_PORT, token = "" } = await chrome.storage.local.get(["port", "token"]);
  let outcome;
  try {
    const { url, init } = buildRequest(info.menuItemId, text, { port, token });
    const response = await fetch(url, init);
    outcome = describeResponse(response.status, await response.json().catch(() => null));
  } catch (error) {
    outcome = { ok: false, text: "", message: error.message.includes("fetch")
      ? "Chart Cleaner isn't running on this computer." : error.message };
  }
  await chrome.scripting.executeScript({
    target: { tabId: tab.id }, func: showResult, args: [outcome.ok, outcome.text, outcome.message],
  }).catch(() => {});
});
