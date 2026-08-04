# Manual Indeed HTML ingest (recommended when blocked)

Indeed often returns **Permission Denied / 403 / CAPTCHA** to automated HTTP clients.
For academic research, the most reliable workflow is:

1. Open Indeed in your normal browser.
2. Search for a HIM keyword + inclusion location (example: `Health Information Management`, `Louisiana`).
3. Save the results page:
   - Chrome/Edge: **Ctrl+S** / **Cmd+S** → Webpage, Complete (or HTML Only)
4. Put the `.html` file in this folder.
5. Optional naming convention for provenance:
   - `louisiana__health_information_management.html`
   - `remote__rhia.html`
6. Run:

```bash
python main.py --fetch-mode offline --html-dir data/raw/manual_indeed
```

The pipeline will parse these pages with the same filters/normalization as a live crawl.
