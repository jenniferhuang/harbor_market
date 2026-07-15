from __future__ import annotations

import pytest

from app.payments.domain import (
    PaymentStatus,
    ProviderTradeState,
    TransitionAction,
    decide_transition,
    status_for_provider_state,
)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (PaymentStatus.CREATED, PaymentStatus.PENDING),
        (PaymentStatus.CREATED, PaymentStatus.CLOSED),
        (PaymentStatus.CREATED, PaymentStatus.FAILED),
        (PaymentStatus.PENDING, PaymentStatus.CLOSED),
        (PaymentStatus.PENDING, PaymentStatus.FAILED),
    ],
)
def test_normal_payment_transitions_are_allowed(
    current: PaymentStatus,
    target: PaymentStatus,
) -> None:
    decision = decide_transition(current, target, provider_authoritative=False)
    assert decision.action == TransitionAction.APPLY


@pytest.mark.parametrize("current", [PaymentStatus.CREATED, PaymentStatus.PENDING])
def test_success_always_requires_provider_authority(current: PaymentStatus) -> None:
    local = decide_transition(
        current,
        PaymentStatus.SUCCEEDED,
        provider_authoritative=False,
    )
    provider = decide_transition(
        current,
        PaymentStatus.SUCCEEDED,
        provider_authoritative=True,
    )

    assert local.action == TransitionAction.IGNORE
    assert local.reason == "success_requires_provider_authority"
    assert provider.action == TransitionAction.APPLY


@pytest.mark.parametrize("current", [PaymentStatus.CLOSED, PaymentStatus.FAILED])
def test_only_authoritative_success_can_correct_an_unpaid_terminal_state(
    current: PaymentStatus,
) -> None:
    local = decide_transition(
        current,
        PaymentStatus.SUCCEEDED,
        provider_authoritative=False,
    )
    provider = decide_transition(
        current,
        PaymentStatus.SUCCEEDED,
        provider_authoritative=True,
    )

    assert local.action == TransitionAction.IGNORE
    assert provider.action == TransitionAction.APPLY
    assert provider.reason == "late_provider_success"


@pytest.mark.parametrize(
    "target",
    [
        PaymentStatus.CREATED,
        PaymentStatus.PENDING,
        PaymentStatus.CLOSED,
        PaymentStatus.FAILED,
    ],
)
def test_success_never_regresses(target: PaymentStatus) -> None:
    decision = decide_transition(
        PaymentStatus.SUCCEEDED,
        target,
        provider_authoritative=True,
    )
    assert decision.action == TransitionAction.IGNORE
    assert decision.reason == "success_never_regresses"


def test_same_state_is_an_idempotent_noop() -> None:
    decision = decide_transition(
        PaymentStatus.PENDING,
        PaymentStatus.PENDING,
        provider_authoritative=True,
    )
    assert decision.action == TransitionAction.NOOP


def test_wechat_refund_state_does_not_claim_refund_completion() -> None:
    assert status_for_provider_state(ProviderTradeState.REFUND) == PaymentStatus.SUCCEEDED
