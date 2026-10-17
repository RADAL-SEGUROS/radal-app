"""Mail (v12, docs/v12/04-mail.md).

:mod:`.transport` (protocol + configs; ``SmtpImapTransport`` for broker
mailboxes and inbound IMAP, ``SesTransport`` for the Radal system sender,
``get_transport`` / ``get_system_transport``), :mod:`.sender` (the one door
out), :mod:`.fake` (``FakeTransport`` for tests), :mod:`.ops`
(``notify_ops``), :mod:`.inbound` (the ``mail.poll`` job), classification,
evidence and inquiries.
"""
