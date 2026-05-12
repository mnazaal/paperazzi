# pzi browser extension

Minimal Manifest V3 extension that sends the current tab URL to the local
`pzi serve` HTTP API.

## Build

Browser-specific manifests are generated from `manifest.base.json`:

```sh
python tools/build_extension.py
```

Outputs:
- `dist/firefox/` — unpacked extension for Firefox
- `dist/chrome/` — unpacked extension for Chrome
- `dist/pzi-capture-firefox.zip` — packaged for Firefox store
- `dist/pzi-capture-chrome.zip` — packaged for Chrome store

## Install (Firefox)

1. Build: `python tools/build_extension.py`
2. Start the service: `pzi serve`
3. Go to `about:debugging` → This Firefox → Load Temporary Add-on
4. Select `dist/firefox/manifest.json`
5. Click the extension action, optionally set tags/bib/dry-run, and press **capture**

## Install (Chrome)

1. Build: `python tools/build_extension.py`
2. Start the service: `pzi serve`
3. Open `chrome://extensions`, enable developer mode, click "Load unpacked"
4. Select `dist/chrome/`
5. Click the extension action and press **capture**

## Configuration

- The capture endpoint defaults to `http://127.0.0.1:8765/capture`.
- The popup fetches available bibs from `GET /bibs` and populates the bib dropdown automatically.
- To change the endpoint, open the extension popup devtools and set a value:
  `chrome.storage.local.set({ endpoint: "http://127.0.0.1:9000/capture" })`.
