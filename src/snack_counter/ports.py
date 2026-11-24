"""Ports to the outside world. Real adapters (chat SDK, HR REST API) would implement these;
tests and the demo use the in-memory fakes in `fakes.py`."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from snack_counter.money import Money


class HrStatus(StrEnum):
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"
    REIMBURSED = "reimbursed"


@dataclass(frozen=True)
class HrClaimStatus:
    status: HrStatus
    reason: str | None = None


class HrUnavailable(Exception):
    """The HR system could not be reached (the request was never sent). Safe to retry."""


class HrOutcomeUnknown(Exception):
    """The request was sent but no reply came back (for example a read timeout).
    HR may or may not have created the claim, so retrying blindly could pay twice."""


class ChatPort(Protocol):
    def send_dm(self, employee_id: str, text: str) -> None: ...

    def post_channel(self, office_code: str, text: str) -> None: ...


class HrPort(Protocol):
    def create_claim(
        self, employee_id: str, amount: Money, description: str, receipt_ref: str
    ) -> str:
        """Create a reimbursement claim and return HR's claim id. NOT idempotent."""
        ...

    def get_status(self, claim_id: str) -> HrClaimStatus:
        """Current status of a claim. Raises KeyError if HR has no claim with that id."""
        ...


class Clock(Protocol):
    def now(self) -> datetime: ...
