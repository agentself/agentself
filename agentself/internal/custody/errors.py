class CustodyError(Exception):
    """Visible fail. Message must never contain a secret value."""


class UnboundCaller(CustodyError):
    """Gateway could not name a principal. No ResourceAccess calls."""


class Refused(CustodyError):
    """Owner check or bind mismatch. Stop before StoreAccess."""


class ProtectedName(Refused):
    """Named secret is product-protected and cannot be deleted."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"{name} is protected")


class UnknownPrincipal(CustodyError):
    """PrincipalAccess.Find missed. No StoreAccess."""


class MissingHoldName(CustodyError):
    pass


class StoreFailure(CustodyError):
    """Store Resource error. No silent failover to the other implementation."""

    def __init__(self, message: str = "store error", name: str | None = None) -> None:
        self.name = name
        super().__init__(message)


class ChannelFailure(CustodyError):
    def __init__(self, message: str = "channel error", reason: str = "error") -> None:
        self.reason = reason
        super().__init__(message)


class HostToolMissing(CustodyError):
    def __init__(self, tool: str) -> None:
        self.tool = tool
        super().__init__(f"{tool} not on PATH")


class EmailSendNotReady(ChannelFailure):
    def __init__(self) -> None:
        super().__init__(
            "email send needs a domain and send credentials",
            reason="not_ready",
        )


class NoGas(CustodyError):
    """USDC send needs ETH for gas. Fail closed when the EOA has no ETH."""


class CannotSign(CustodyError):
    def __init__(self) -> None:
        super().__init__("backend cannot sign")


class CannotSend(CustodyError):
    def __init__(self, message: str = "backend cannot send") -> None:
        super().__init__(message)
