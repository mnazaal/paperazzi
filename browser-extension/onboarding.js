// From `background/config.js`, not `background.js`: importing the service
// worker registers its context-menu and webRequest listeners a second time,
// so an open onboarding tab doubled every right-click capture.
import { fetchBibs, getEndpoint, getAuthHeaders, isLoopbackEndpoint } from "./background/config.js";

const endpointInput = document.getElementById("endpoint");
const tokenInput = document.getElementById("token");
const bibSelect = document.getElementById("bib");
const saveBtn = document.getElementById("save");
const testBtn = document.getElementById("test");
const statusEl = document.getElementById("status");

const DEFAULT_ENDPOINT = "http://127.0.0.1:8765/capture";

function getStorage() {
  // `chrome.storage.session` is cleared when the browser closes, so first-run
  // setup — the endpoint and the API token the user just pasted — evaporated at
  // the next restart and the extension came back unpaired. Onboarding settings
  // are configuration, not session state.
  return chrome.storage.local;
}

async function loadSettings() {
  const stored = await getStorage().get(["endpoint", "authToken"]);
  if (stored.endpoint) endpointInput.value = stored.endpoint;
  if (stored.authToken) tokenInput.value = stored.authToken;
}

async function populateBibs() {
  // `fetchBibs` throws now, so this needs the same catch the popup has. Without
  // it a first run against a stopped server showed an empty dropdown, an empty
  // status box, and an unhandled rejection in the console — on the one page
  // whose whole job is to tell a new user what is wrong.
  let bibs;
  try {
    bibs = await fetchBibs();
  } catch (error) {
    setStatus(error.message, false);
    return;
  }
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

  // Checked with the same predicate `getEndpoint` applies, because that is what
  // decides whether the setting has any effect. A non-loopback endpoint used to
  // be stored, reported as saved, and then silently replaced by the default at
  // every read — and "Test connection" resolves through `getEndpoint`, so it
  // tested the default, passed, and confirmed the illusion.
  if (!isLoopbackEndpoint(endpoint)) {
    setStatus(
      `✗ Not saved: the endpoint must be on loopback (127.0.0.1 or localhost). `
      + `pzi runs on your own machine, and the extension refuses to send captures anywhere else.`,
      false,
    );
    saveBtn.textContent = "Save settings";
    saveBtn.disabled = false;
    return;
  }

  // An empty box means "I did not type one", never "clear the saved token" —
  // the rule `popup.js` already applies. Writing it unconditionally meant
  // editing only the endpoint unpaired the extension, and every request 401'd
  // until the user retyped a token they had never intended to change.
  const settings = token ? { endpoint, authToken: token } : { endpoint };
  await getStorage().set(settings);
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
