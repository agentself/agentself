class CustodyError(Exception):
    """Visible fail. Message must never contain a secret value."""


class UnboundCaller(CustodyError):
    """Client could not name an identity. No ResourceAccess calls."""


class Refused(CustodyError):
    """Owner check or bind mismatch. Stop before StoreAccess."""


class ProtectedName(Refused):
    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"{name} is protected")


class UnknownIdentity(CustodyError):
    """Identity lookup missed. No StoreAccess."""


class MissingSecret(CustodyError):
    pass


class MissingNote(CustodyError):
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
    """Cannot pay the network fee. Fail closed when the wallet has no gas."""

    def __init__(self, message: str = "need gas", reason: str = "no_gas") -> None:
        self.reason = reason
        super().__init__(message)


class CannotAuthorize(CustodyError):
    def __init__(self) -> None:
        super().__init__("backend cannot authorize")


class CannotSend(CustodyError):
    def __init__(
        self, message: str = "backend cannot send", reason: str = "cannot_send"
    ) -> None:
        self.reason = reason
        super().__init__(message)
