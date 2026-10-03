import { DEFAULT_PORT } from "./request.js";

const port = document.getElementById("port");
const token = document.getElementById("token");
const status = document.getElementById("status");

chrome.storage.local.get(["port", "token"]).then((saved) => {
  port.value = saved.port || DEFAULT_PORT;
  token.value = saved.token || "";
});

document.getElementById("save").addEventListener("click", async () => {
  await chrome.storage.local.set({ port: Number(port.value) || DEFAULT_PORT, token: token.value.trim() });
  status.textContent = "Saved.";
});
