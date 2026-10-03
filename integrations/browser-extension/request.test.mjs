import { test } from "node:test";
import assert from "node:assert/strict";
import { buildRequest, describeResponse } from "./request.js";

test("builds a loopback request with the token", () => {
  const { url, init } = buildRequest("chart-cleaner-abbreviate", "Hypertension", { port: 8799, token: "t0k" });
  assert.equal(url, "http://127.0.0.1:8799/api/v1/abbreviate");
  assert.equal(init.headers.Authorization, "Bearer t0k");
  assert.deepEqual(JSON.parse(init.body), { text: "Hypertension" });
});

test("clean asks for unwrapped text", () => {
  const { init } = buildRequest("chart-cleaner-clean", "x", { token: "t" });
  assert.deepEqual(JSON.parse(init.body), { text: "x", wrap: false });
});

test("rejects bad ports, missing tokens and unknown actions", () => {
  assert.throws(() => buildRequest("chart-cleaner-clean", "x", { port: "evil.com", token: "t" }));
  assert.throws(() => buildRequest("chart-cleaner-clean", "x", { token: "" }), /token/);
  assert.throws(() => buildRequest("nope", "x", { token: "t" }));
});

test("describes API answers", () => {
  assert.deepEqual(describeResponse(200, { text: "HTN", summary: "ok" }), { ok: true, text: "HTN", message: "ok" });
  assert.equal(describeResponse(401, { error: "Missing or wrong token" }).message, "Missing or wrong token");
  assert.equal(describeResponse(500, null).ok, false);
});
