"""Test doubles. Never send real email from tests."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SentEmail:
    to: str
    guest_name: str
    ticket_id: int
    qr_png: bytes


@dataclass
class FakeMailer:
    """Records what would have been sent, or fails on demand."""

    sent: list[SentEmail] = field(default_factory=list)
    fail: bool = False
    error: Exception = field(default_factory=lambda: RuntimeError("mail transport down"))

    def send_ticket(
        self,
        *,
        to: str,
        guest_name: str,
        ticket_id: int,
        qr_png: bytes,
        ticket_type: str = "full",
        amount_paid: int = 0,
        total_price: int = 0,
        balance_paid: bool = False,
    ) -> None:
        if self.fail:
            raise self.error
        self.sent.append(
            SentEmail(to=to, guest_name=guest_name, ticket_id=ticket_id, qr_png=qr_png)
        )
