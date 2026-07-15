from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PaymentStatus(StrEnum):
    CREATED = "created"
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    CLOSED = "closed"
    FAILED = "failed"


class ProviderTradeState(StrEnum):
    NOTPAY = "NOTPAY"
    SUCCESS = "SUCCESS"
    CLOSED = "CLOSED"
    PAYERROR = "PAYERROR"
    REFUND = "REFUND"


class TransitionAction(StrEnum):
    APPLY = "apply"
    NOOP = "noop"
    IGNORE = "ignore"


@dataclass(frozen=True, slots=True)
class TransitionDecision:
    action: TransitionAction
    reason: str


_ALLOWED_TRANSITIONS: dict[PaymentStatus, frozenset[PaymentStatus]] = {
    PaymentStatus.CREATED: frozenset(
        {
            PaymentStatus.PENDING,
            PaymentStatus.SUCCEEDED,
            PaymentStatus.CLOSED,
            PaymentStatus.FAILED,
        }
    ),
    PaymentStatus.PENDING: frozenset(
        {
            PaymentStatus.SUCCEEDED,
            PaymentStatus.CLOSED,
            PaymentStatus.FAILED,
        }
    ),
    # An authenticated SUCCESS observation is financially authoritative. It may
    # correct a stale local close/failure caused by a close-vs-pay race.
    PaymentStatus.CLOSED: frozenset({PaymentStatus.SUCCEEDED}),
    PaymentStatus.FAILED: frozenset({PaymentStatus.SUCCEEDED}),
    PaymentStatus.SUCCEEDED: frozenset(),
}


def decide_transition(
    current: PaymentStatus,
    target: PaymentStatus,
    *,
    provider_authoritative: bool,
) -> TransitionDecision:
    if current == target:
        return TransitionDecision(TransitionAction.NOOP, "same_state")
    if target == PaymentStatus.SUCCEEDED and not provider_authoritative:
        return TransitionDecision(TransitionAction.IGNORE, "success_requires_provider_authority")
    if target in _ALLOWED_TRANSITIONS[current]:
        if current in {PaymentStatus.CLOSED, PaymentStatus.FAILED}:
            if provider_authoritative and target == PaymentStatus.SUCCEEDED:
                return TransitionDecision(TransitionAction.APPLY, "late_provider_success")
            return TransitionDecision(TransitionAction.IGNORE, "terminal_state")
        return TransitionDecision(TransitionAction.APPLY, "allowed")
    if current == PaymentStatus.SUCCEEDED:
        return TransitionDecision(TransitionAction.IGNORE, "success_never_regresses")
    if current in {PaymentStatus.CLOSED, PaymentStatus.FAILED}:
        return TransitionDecision(TransitionAction.IGNORE, "terminal_state")
    return TransitionDecision(TransitionAction.IGNORE, "stale_or_invalid_provider_state")


def status_for_provider_state(state: ProviderTradeState) -> PaymentStatus:
    return {
        ProviderTradeState.NOTPAY: PaymentStatus.PENDING,
        ProviderTradeState.SUCCESS: PaymentStatus.SUCCEEDED,
        ProviderTradeState.CLOSED: PaymentStatus.CLOSED,
        ProviderTradeState.PAYERROR: PaymentStatus.FAILED,
        # WeChat's REFUND query state proves that payment previously succeeded;
        # refund completion must be modeled by separate refund rows later.
        ProviderTradeState.REFUND: PaymentStatus.SUCCEEDED,
    }[state]
