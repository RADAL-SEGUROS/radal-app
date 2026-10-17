"""The RPA error taxonomy — one exception per ``PortalErrorKind`` (06-rpa §1).

A connector (or the step interpreter) raises one of these; the engine maps it
1:1 to ``portal_search_run.error_kind``, which is also the frontend's message
key (``user_message_key``). Anything that is NOT a :class:`PortalError` is an
``unexpected`` failure.

``detail`` is TECHNICAL and internal (``error_detail``, the ops mail): it must
never carry a credential or a page's HTML. The interpreter only ever passes
selectors, step names and HTTP statuses into it, and the engine scrubs it again
before it is stored.
"""
from __future__ import annotations

from app.models.enums import PortalErrorKind


class PortalError(Exception):
    """Base: a typed, expected portal failure."""

    kind: PortalErrorKind = PortalErrorKind.UNEXPECTED

    def __init__(self, detail: str = "", *, step: str | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.step = step


class CredentialsMissing(PortalError):
    """The broker has no (usable) credentials for this portal. No browser is launched."""

    kind = PortalErrorKind.CREDENTIALS_MISSING


class LoginFailed(PortalError):
    """The portal rejected the user or the password."""

    kind = PortalErrorKind.LOGIN_FAILED


class LayoutChanged(PortalError):
    """A selector was not found, or the portal showed an unexpected page."""

    kind = PortalErrorKind.LAYOUT_CHANGED


class PortalTimeout(PortalError):
    """The portal did not answer in time (navigation or a slow result)."""

    kind = PortalErrorKind.TIMEOUT


class CaptchaDetected(PortalError):
    """The portal asked for human verification."""

    kind = PortalErrorKind.CAPTCHA


class NoResults(PortalError):
    """The portal answered, but with no plans for these data."""

    kind = PortalErrorKind.NO_RESULTS


class PortalUnavailable(PortalError):
    """The portal is down (connection refused, 5xx)."""

    kind = PortalErrorKind.PORTAL_UNAVAILABLE


#: Error kinds the BROKER can act on (wrong data, wrong password, missing
#: credentials, nothing to quote). They are not a connector defect, so they do
#: not page the RADAL team and do not count towards the maintenance streak.
USER_ACTIONABLE_KINDS: frozenset[PortalErrorKind] = frozenset(
    {
        PortalErrorKind.CREDENTIALS_MISSING,
        PortalErrorKind.LOGIN_FAILED,
        PortalErrorKind.NO_RESULTS,
    }
)


def kind_of(exc: BaseException) -> PortalErrorKind:
    """The ``error_kind`` for any exception (non-portal errors are ``unexpected``)."""
    if isinstance(exc, PortalError):
        return exc.kind
    return PortalErrorKind.UNEXPECTED


__all__ = [
    "CaptchaDetected",
    "CredentialsMissing",
    "LayoutChanged",
    "LoginFailed",
    "NoResults",
    "PortalError",
    "PortalTimeout",
    "PortalUnavailable",
    "USER_ACTIONABLE_KINDS",
    "kind_of",
]
