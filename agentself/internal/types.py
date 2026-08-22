from dataclasses import dataclass


@dataclass(frozen=True)
class BoundCaller:
    """Never a private key."""

    principal_id: str
    recipient: str


@dataclass(frozen=True)
class Principal:
    """Never a private key."""

    id: str
    recipient: str
    store_binding: str

    def public_view(self) -> dict[str, str]:
        return {"id": self.id, "recipient": self.recipient}
