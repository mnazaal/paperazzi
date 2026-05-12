import { captureCurrentTab, fetchBibs } from "./background.js";

const out = document.getElementById("out");
const button = document.getElementById("go");
const bibSelect = document.getElementById("bib");
const tokenInput = document.getElementById("token");

chrome.storage.local.get("authToken").then((stored) => {
  tokenInput.value = stored.authToken || "";
});

async function populateBibs() {
  const bibs = await fetchBibs();
  for (const bib of bibs) {
    const option = document.createElement("option");
    option.value = bib.name;
    option.textContent = bib.name + (bib.default ? " (default)" : "");
    bibSelect.appendChild(option);
  }
}

populateBibs();

button.addEventListener("click", async () => {
  const tags = document
    .getElementById("tags")
    .value.split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  const bib = bibSelect.value || null;
  const dryRun = document.getElementById("dry").checked;
  await chrome.storage.local.set({ authToken: tokenInput.value.trim() });

  out.textContent = "capturing...";
  try {
    const result = await captureCurrentTab({ tags, bib, dryRun });
    out.textContent = JSON.stringify(result, null, 2);
  } catch (err) {
    out.textContent = "error: " + (err && err.message ? err.message : String(err));
  }
});
