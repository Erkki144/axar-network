"""Card terminal integration layer (#25). See ``base`` for the contract,
``flow`` for what the POS calls, ``sandbox`` for the test/demo provider.

Real providers are added as new adapters (one module each) only after the
owner chooses a provider, signs a merchant agreement and gets sandbox access;
none is connected today.
"""

from .base import (  # noqa: F401
    Capabilities, CapabilityNotSupported, Health, PaymentRequest, RefundRequest, SettlementLine, Status,
    TerminalAdapter, TerminalError, TerminalResult, TerminalTimeout, TerminalUnavailable,
)
from .sandbox import SandboxTerminal  # noqa: F401

ADAPTERS = {"sandbox": SandboxTerminal}


def get_adapter(name: str, **kw) -> TerminalAdapter:
    try:
        return ADAPTERS[name](**kw)
    except KeyError:
        raise ValueError(f"no terminal adapter {name!r}; available: {sorted(ADAPTERS)}") from None
