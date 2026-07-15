from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.payments.providers.base import PaymentGateway
from app.payments.providers.mock_wechat import MockWeChatPayGateway


def build_payment_gateway(
    settings: Settings,
    session_factory: sessionmaker[Session],
) -> PaymentGateway | None:
    if settings.payment_mode == "disabled":
        return None
    assert settings.payment_mode == "mock"
    assert settings.payment_mock_signing_secret is not None
    return MockWeChatPayGateway(
        app_id=settings.payment_mock_app_id,
        signing_secret=settings.payment_mock_signing_secret.get_secret_value(),
        session_factory=session_factory,
        prepay_ttl_seconds=settings.payment_prepay_ttl_seconds,
    )
