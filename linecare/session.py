"""The customer conversation's state, enforced in code (spec §6.1).

    UNVERIFIED --verify ok--> VERIFIED --handover--> HANDED_OVER
        | 3 failed attempts
        +------> LOCKED (15 min, then UNVERIFIED again)

Side-effect tools (reboot, technician booking) work in two steps: the first call creates a *pending
confirmation*; only a call with its id in a LATER user turn executes it. "The customer said yes" is then a fact
the code checked, not a claim of the model.
"""
import secrets as _secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from linecare import config
from mocks import clock

UNVERIFIED, VERIFIED, LOCKED, HANDED_OVER = "UNVERIFIED", "VERIFIED", "LOCKED", "HANDED_OVER"


@dataclass
class PendingAction:
    confirmation_id: str
    tool: str
    arguments: dict
    summary: str
    created_turn: int


@dataclass
class Session:
    conv_id: str
    state: str = UNVERIFIED
    account_no: str | None = None
    first_name: str | None = None
    language: str | None = None
    area_code: str | None = None
    plan_name: str | None = None
    router_model: str | None = None
    failed_attempts: int = 0
    locked_until: datetime | None = None
    turn: int = 0
    reboots: int = 0
    fixes: list = field(default_factory=list)        # fixing actions that succeeded (for record_outcome, D2)
    pending: dict = field(default_factory=dict)      # confirmation_id -> PendingAction
    secrets: set = field(default_factory=set)        # raw PINs / dates passed to verify_customer
    identity_phase_upto: int = 0                     # history index: user messages before it were sent unverified
    history: list = field(default_factory=list)

    # --- turns ---------------------------------------------------------------------------
    def begin_turn(self) -> None:
        """Called once per user message: counts the turn, ends an expired lock, drops stale confirmations."""
        self.turn += 1
        if self.state == LOCKED and self.locked_until and clock.now() >= self.locked_until:
            self.state, self.failed_attempts, self.locked_until = UNVERIFIED, 0, None
        self.pending = {cid: p for cid, p in self.pending.items()
                        if self.turn - p.created_turn <= config.CONFIRMATION_TTL_TURNS}

    @property
    def verified(self) -> bool:
        return self.state == VERIFIED

    # --- identity ------------------------------------------------------------------------
    def record_failed_attempt(self) -> bool:
        """Count a failed verification; returns True when this attempt locked the conversation."""
        self.failed_attempts += 1
        if self.failed_attempts >= config.MAX_IDENTITY_ATTEMPTS:
            self.state = LOCKED
            self.locked_until = clock.now() + timedelta(minutes=config.IDENTITY_LOCK_MINUTES)
            return True
        return False

    def attempts_left(self) -> int:
        return max(config.MAX_IDENTITY_ATTEMPTS - self.failed_attempts, 0)

    def mark_verified(self, customer: dict) -> None:
        self.state = VERIFIED
        self.failed_attempts = 0
        self.account_no = customer["account_no"]
        self.first_name = customer["first_name"]
        self.language = customer["language"]
        self.area_code = customer["area_code"]
        self.plan_name = customer["plan_name"]
        self.router_model = customer["router_model"]

    # --- confirmations -------------------------------------------------------------------
    def create_pending(self, tool: str, arguments: dict, summary: str) -> PendingAction:
        """A new offer replaces an earlier pending offer for the same tool."""
        self.pending = {cid: p for cid, p in self.pending.items() if p.tool != tool}
        action = PendingAction(f"CF-{_secrets.token_hex(3).upper()}", tool, arguments, summary, self.turn)
        self.pending[action.confirmation_id] = action
        return action

    def take_pending(self, tool: str, confirmation_id: str) -> tuple[PendingAction | None, str | None]:
        """Return (action, None) if it may run now, else (None, reason). A taken action cannot run twice."""
        action = self.pending.get(str(confirmation_id).strip().upper())
        if action is None or action.tool != tool:
            return None, "unknown_or_expired_confirmation"
        if action.created_turn >= self.turn:
            return None, "confirm_in_next_turn"
        del self.pending[action.confirmation_id]
        return action, None

    # --- prompt --------------------------------------------------------------------------
    def state_block(self) -> str:
        """Facts for the system prompt. Never the PIN, date of birth, address, phone or email."""
        lines = [f"today: {clock.now():%A %Y-%m-%d %H:%M}", f"identity: {self.state.lower()}"]
        if self.verified:
            lines += [f"customer first name: {self.first_name}", f"plan: {self.plan_name}",
                      f"router: {self.router_model}", f"remote reboots used: {self.reboots} of "
                      f"{config.MAX_REBOOTS_PER_CONVERSATION}"]
        elif self.state == UNVERIFIED and self.failed_attempts:
            lines.append(f"verification attempts left: {self.attempts_left()}")
        elif self.state == LOCKED:
            lines.append(f"identity checks locked until {self.locked_until:%H:%M}")
        for action in self.pending.values():
            lines.append(f"waiting for the customer's answer to: {action.summary} "
                         f"(confirmation_id {action.confirmation_id})")
        return "\n".join(f"- {line}" for line in lines)
