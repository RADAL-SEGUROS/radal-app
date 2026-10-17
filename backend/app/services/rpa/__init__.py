"""Portal-search RPA (v12, docs/v12/06-rpa.md).

* :mod:`.base`        — the connector Protocol, ``RunContext``, ``PortalResult``;
* :mod:`.errors`      — the typed taxonomy (1:1 with ``PortalErrorKind``);
* :mod:`.interpreter` — runs a portal's JSON step map (``selectors/*.json``);
* :mod:`.engine`      — executes a ``portal_search_run`` end to end;
* :mod:`.registry`    — ``CONNECTORS`` / ``get_connector``;
* :mod:`.connectors`  — one module per portal (HDI first).
"""
