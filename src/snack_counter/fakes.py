"""In-memory stand-ins for the chat tool, the HR system and the clock."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, get_args

from snack_counter.money import Money
from snack_counter.ports import HrClaimStatus, HrOutcomeUnknown, HrStatus, HrUnavailable


class FakeClock:
    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("FakeClock needs a timezone-aware datetime")
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, **delta: float) -> None:
        self._now += timedelta(**delta)

    def set(self, moment: datetime) -> None:
        self._now = moment


@dataclass(frozen=True)
class ChatMessage:
    to: str  # employee id, or office code for channel posts
    text: str
    channel: bool = False


class FakeChat:
    def __init__(self) -> None:
        self.messages: list[ChatMessage] = []

    def send_dm(self, employee_id: str, text: str) -> None:
        self.messages.append(ChatMessage(employee_id, text))

    def post_channel(self, office_code: str, text: str) -> None:
        self.messages.append(ChatMessage(office_code, text, channel=True))

    def dms_to(self, employee_id: str) -> list[str]:
        return [m.text for m in self.messages if m.to == employee_id and not m.channel]

    def channel_posts(self, office_code: str) -> list[str]:
        return [m.text for m in self.messages if m.to == office_code and m.channel]


@dataclass
class FakeClaim:
    claim_id: str
    employee_id: str
    amount: Money
    description: str
    receipt_ref: str
    status: HrStatus = HrStatus.SUBMITTED
    reason: str | None = None


FailureMode = Literal["down", "lost_reply_created", "lost_reply_not_created"]


class FakeHr:
    """Scriptable HR system: tests decide when claims are approved, rejected, paid, or when HR is down."""

    def __init__(self) -> None:
        self.claims: dict[str, FakeClaim] = {}
        self._failures_left = 0
        self._failure_mode: FailureMode = "down"

    def fail_next(self, times: int = 1, mode: FailureMode = "down") -> None:
        """The next `times` create_claim calls fail. mode: "down" (request never sent),
        "lost_reply_created" (HR created the claim but the reply was lost: one claim is stored
        per failure), "lost_reply_not_created" (request lost before HR acted on it)."""
        if mode not in get_args(FailureMode):
            raise ValueError(f"Unknown failure mode {mode!r}")
        self._failures_left = times
        self._failure_mode = mode

    def create_claim(
        self, employee_id: str, amount: Money, description: str, receipt_ref: str
    ) -> str:
        if self._failures_left > 0:
            self._failures_left -= 1
            if self._failure_mode == "down":
                raise HrUnavailable("HR system unreachable")
            if self._failure_mode == "lost_reply_created":
                self._store(employee_id, amount, description, receipt_ref)
            raise HrOutcomeUnknown("HR request timed out after sending")
        return self._store(employee_id, amount, description, receipt_ref)

    def _store(self, employee_id: str, amount: Money, description: str, receipt_ref: str) -> str:
        claim_id = f"HR-{len(self.claims) + 1:04d}"
        self.claims[claim_id] = FakeClaim(claim_id, employee_id, amount, description, receipt_ref)
        return claim_id

    def get_status(self, claim_id: str) -> HrClaimStatus:
        claim = self.claims[claim_id]  # unknown id -> KeyError, as the HrPort contract says
        return HrClaimStatus(claim.status, claim.reason)

    # --- test controls ------------------------------------------------------
    def approve(self, claim_id: str) -> None:
        self.claims[claim_id].status = HrStatus.APPROVED

    def reimburse(self, claim_id: str) -> None:
        self.claims[claim_id].status = HrStatus.REIMBURSED

    def reject(self, claim_id: str, reason: str) -> None:
        self.claims[claim_id].status = HrStatus.REJECTED
        self.claims[claim_id].reason = reason
