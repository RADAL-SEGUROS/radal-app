"""Mail (v12, docs/v12/04-mail.md).

Phase 1 ships the seam only: :mod:`.transport` (protocol + configs +
``get_transport``; the real SMTP/IMAP transport is a stub), :mod:`.fake`
(``FakeTransport`` for tests) and :mod:`.ops` (``notify_ops``). Lane B4 owns
the rest of this package: sending with sender selection, template rendering,
inbound classification and the ``mail.poll`` job.
"""
