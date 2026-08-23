from dataclasses import dataclass


@dataclass(frozen=True)
class BoundCaller:
    """Never a private key."""

    identity_id: str
    recipient: str


@dataclass(frozen=True)
class Identity:
    """Never a private key."""

    id: str
    recipient: str
    store_binding: str
    wallet_material_names: tuple[str, ...] = ()

    def public_view(self) -> dict[str, str]:
        return {"id": self.id, "recipient": self.recipient}
