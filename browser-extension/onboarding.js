import { fetchBibs, getEndpoint, getAuthHeaders } from "./background.js";

const endpointInput = document.getElementById("endpoint");
const tokenInput = document.getElementById("token");
const bibSelect = document.getElementById("bib");
const saveBtn = document.getElementById("save");
const testBtn = document.getElementById("test");
const statusEl = document.getElementById("status");

const DEFAULT_ENDPOINT = "http://127.0.0.1:8765/capture";

function getStorage() {
  return chrome.storage.session || chrome.storage.local;
}

async function loadSettings() {
  const stored = await getStorage().get(["endpoint", "authToken"]);
  if (stored.endpoint) endpointInput.value = stored.endpoint;
  if (stored.authToken) tokenInput.value = stored.authToken;
}

async function populateBibs() {
  const bibs = await fetchBibs();
  bibSelect.innerHTML = '<option value="">default</option>';
  for (const bib of bibs) {
    const opt = document.createElement("option");
    opt.value = bib.name;
    opt.textContent = bib.name + (bib.default ? " (default)" : "");
    bibSelect.appendChild(opt);
  }
}

function setStatus(text, ok) {
  statusEl.textContent = text;
  statusEl.style.background = ok ? "#e8f5e9" : "#ffebee";
}

saveBtn.addEventListener("click", async () => {
  saveBtn.disabled = true;
  saveBtn.textContent = "Saving…";

  const endpoint = endpointInput.value.trim() || DEFAULT_ENDPOINT;
  const token = tokenInput.value.trim();

  await getStorage().set({ endpoint, authToken: token });
  setStatus("Settings saved.", true);

  saveBtn.textContent = "Save settings";
  saveBtn.disabled = false;
});

testBtn.addEventListener("click", async () => {
  testBtn.disabled = true;
  testBtn.textContent = "Testing…";
  setStatus("Checking server…", null);

  try {
    const endpoint = await getEndpoint();
    const authHeaders = await getAuthHeaders();
    const healthUrl = endpoint.replace(/\/capture\/?$/, "/health");

    const resp = await fetch(healthUrl, { headers: authHeaders });
    if (resp.ok) {
      setStatus("✓ pzi server is running and reachable.", true);
    } else {
      setStatus(`✗ Server returned HTTP ${resp.status}. Check token or server logs.`, false);
    }
  } catch (err) {
    setStatus(`✗ Cannot reach server. Is pzi running? (${err.message})`, false);
  }

  testBtn.textContent = "Test connection";
  testBtn.disabled = false;
});

loadSettings();
populateBibs();
