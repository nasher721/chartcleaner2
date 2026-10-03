// Pure helpers shared by the service worker and the tests (node --test).

export const ACTIONS = {
  "chart-cleaner-clean": { title: "Clean with Chart Cleaner", endpoint: "clean" },
  "chart-cleaner-abbreviate": { title: "Abbreviate with Chart Cleaner", endpoint: "abbreviate" },
  "chart-cleaner-expand": { title: "Expand abbreviations with Chart Cleaner", endpoint: "expand" },
};

export const DEFAULT_PORT = 8765;

// Build the fetch() arguments for one action. Only loopback is ever contacted.
export function buildRequest(actionId, text, { port = DEFAULT_PORT, token = "" } = {}) {
  const action = ACTIONS[actionId];
  if (!action) throw new Error(`Unknown action: ${actionId}`);
  const portNumber = Number(port);
  if (!Number.isInteger(portNumber) || portNumber < 1 || portNumber > 65535) {
    throw new Error("Port must be a number between 1 and 65535.");
  }
  if (!token) throw new Error("Paste the token from Chart Cleaner → Settings → Local API first.");
  const body = actionId === "chart-cleaner-clean" ? { text, wrap: false } : { text };
  return {
    url: `http://127.0.0.1:${portNumber}/api/v1/${action.endpoint}`,
    init: {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify(body),
    },
  };
}

// Turn the API's JSON (or error) into the text to copy and a short message.
export function describeResponse(status, data) {
  if (status === 200 && data && typeof data.text === "string") {
    return { ok: true, text: data.text, message: data.summary || "Done — result copied." };
  }
  const reason = (data && data.error) || `Chart Cleaner answered ${status}.`;
  return { ok: false, text: "", message: reason };
}
