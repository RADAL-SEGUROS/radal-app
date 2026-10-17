"""Payment-provider registry (v12, 03 §4).

``get_provider(name)`` is the ONLY way application code reaches a provider, so
tests can swap every provider for a fake in one place: the ``fake_provider``
fixture sets :data:`_OVERRIDE` (via ``monkeypatch``) and every lookup — however
the caller imported this function — returns the fake.

Implementations register by name. Built-ins are imported LAZILY from
:data:`_BUILTIN` (``"forpay"`` -> ``app.services.payments.providers.forpay:
ForpayProvider``, written by lane B3), so this module never fails to import
while an implementation does not exist yet; asking for it raises
:class:`ProviderUnavailable` instead.
"""
from __future__ import annotations

import importlib
from typing import Callable

from app.services.payments.providers.base import (
    PaymentProvider,
    PaymentProviderError,
    ProviderUnavailable,
)

#: Test seam: when set, every ``get_provider`` call returns it.
_OVERRIDE: PaymentProvider | None = None

#: name -> "module:Class" imported on first use.
_BUILTIN: dict[str, str] = {
    "forpay": "app.services.payments.providers.forpay:ForpayProvider",
}

_FACTORIES: dict[str, Callable[[], PaymentProvider]] = {}
_INSTANCES: dict[str, PaymentProvider] = {}


def register_provider(name: str, factory: Callable[[], PaymentProvider]) -> None:
    """Register (or replace) the factory for ``name``; the instance is cached."""
    _FACTORIES[name] = factory
    _INSTANCES.pop(name, None)


def _load_builtin(name: str) -> Callable[[], PaymentProvider]:
    target = _BUILTIN.get(name)
    if target is None:
        raise ProviderUnavailable(f"unknown payment provider: {name!r}")
    module_name, _, attr = target.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            raise ProviderUnavailable(f"payment provider {name!r} is not installed") from exc
        raise
    factory = getattr(module, attr, None)
    if factory is None:
        raise ProviderUnavailable(f"payment provider {name!r} is not installed")
    return factory


def get_provider(name: str) -> PaymentProvider:
    """The provider implementation for ``name`` (``PaymentProviderName`` value)."""
    if _OVERRIDE is not None:
        return _OVERRIDE
    key = name.value if hasattr(name, "value") else str(name)
    instance = _INSTANCES.get(key)
    if instance is None:
        factory = _FACTORIES.get(key) or _load_builtin(key)
        instance = factory()
        _INSTANCES[key] = instance
    return instance


__all__ = [
    "PaymentProvider",
    "PaymentProviderError",
    "ProviderUnavailable",
    "get_provider",
    "register_provider",
]
