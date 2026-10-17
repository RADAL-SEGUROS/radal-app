Local fake of the HDI broker portal for the RPA tests (tests/test_v12_rpa_browser.py).

* `fake_hdi/` — login -> quote form -> results table. Test login: `corredor@alfa.cl` / `clave-hdi-123`.
  `results.html` carries `__RESULTS_DELAY_MS__`, substituted by the test server (slow-portal case).
  Plate `ZZZZ99` renders "no results"; plate `CAPT01` renders a captcha challenge.
* `fake_hdi_layout_changed/` — overlay: only `quote.html`, whose plate field was renamed (`#patente`),
  so the connector's `#plate` selector is missing (layout_changed).
