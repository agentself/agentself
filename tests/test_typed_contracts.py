"""Focused checks for provider-neutral typed contract boundaries."""

from __future__ import annotations

from typing import get_type_hints

from agentself.backends.email.contract import mailbox_view, setup_failed, setup_needed
from agentself.internal.setup import (
    SETUP_ACTION_REQUIRED,
    SETUP_FAILED,
    SETUP_PENDING,
    SetupOption,
    SetupResult,
    public_setup_option,
    setup_option,
    setup_status_of,
)
from agentself.internal.types import (
    EmailConnectView,
    IdentityView,
    MailboxMessage,
    MailboxView,
    WalletAuthorization,
    WalletBalance,
    WalletView,
)

from tests.synthetic_email import SyntheticEmailAccess
from tests.synthetic_wallet import SyntheticWalletAccess


def test_setup_contract_has_typed_states_and_public_option_shape() -> None:
    option: SetupOption = setup_option(
        name="credential",
        type="secret",
        required=True,
        sensitive=True,
        persist=True,
    )
    pending = setup_needed(
        option,
        status=SETUP_PENDING,
        continuation={"phase": "credential"},
    )

    assert pending["status"] == SETUP_PENDING
    assert pending["option"]["name"] == "credential"
    assert public_setup_option(pending["option"]) == {
        "name": "credential",
        "type": "secret",
        "required": True,
        "sensitive": True,
        "default": None,
        "choices": [],
        "source": "",
        "help": "",
        "action": None,
    }
    assert setup_status_of(mailbox_view("agent@example.test", owned_address=True)) == (
        "connected"
    )
    assert setup_status_of(setup_failed("bad credential")) == SETUP_FAILED
    assert (
        setup_status_of(setup_needed(option, status=SETUP_ACTION_REQUIRED))
        == SETUP_ACTION_REQUIRED
    )


def test_contract_typeddicts_cover_stable_boundary_fields() -> None:
    assert {"status", "option", "continuation"} <= set(get_type_hints(SetupResult))
    assert {"id", "from", "subject", "body", "status", "acted"} <= set(
        get_type_hints(MailboxMessage)
    )
    assert {"address", "owned_address", "needs_domain"} <= set(
        get_type_hints(MailboxView)
    )
    assert {"address", "chain", "asset", "scheme"} <= set(get_type_hints(WalletView))
    assert {"asset", "amount", "address"} <= set(get_type_hints(WalletBalance))
    assert {"valid", "address", "scheme"} <= set(get_type_hints(WalletAuthorization))
    assert {"id", "recipient", "email", "wallet"} <= set(get_type_hints(IdentityView))
    assert {"status", "state", "continue"} <= set(get_type_hints(EmailConnectView))


def test_provider_substitution_returns_contract_shapes() -> None:
    SyntheticEmailAccess.reset()
    mailbox = SyntheticEmailAccess()
    setup = mailbox.connect("agent")
    assert setup["status"] == "action_required"
    assert setup["human_action_required"] is True

    wallet = SyntheticWalletAccess()
    wallet.bind_material(wallet.create_material())
    assert wallet.describe("agent")["scheme"] == "ed25519"
    assert wallet.balance("agent")["asset"] == "NOTE"
    assert wallet.verify("agent", "hello", "ed25519:hello")["valid"] is True
