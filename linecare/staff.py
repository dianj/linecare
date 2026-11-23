"""Staff sessions for the helpdesk agent and the operator console (ARCHITECTURE.md §7).

Demo authentication: a token from `config.STAFF_TOKENS` selects the role. There is no identity gate for staff, but
every look at a customer's data is written to the audit log with the role and a reason.
"""
from dataclasses import dataclass, field

from linecare import config

OPERATOR, TECHNICIAN = "operator", "technician"
ROLES = (OPERATOR, TECHNICIAN)

# Internal documents a role may read (public documents are open to every role). Operators work with the policies,
# technicians with the technical notes.
ROLE_INTERNAL_GROUPS = {OPERATOR: frozenset({"policy"}), TECHNICIAN: frozenset({"technical"})}


@dataclass
class StaffSession:
    conv_id: str
    staff_id: str
    role: str
    handover_id: str | None = None   # the handover this conversation is about (operator console)
    turn: int = 0
    history: list = field(default_factory=list)
    kb_sources: list = field(default_factory=list)

    def state_block(self) -> str:
        from mocks import clock

        lines = [f"today: {clock.now():%A %Y-%m-%d %H:%M}", f"your user's role: {self.role}"]
        if self.handover_id:
            lines.append(f"working on handover {self.handover_id}")
        return "\n".join(f"- {line}" for line in lines)


def role_for_token(token: str | None) -> str | None:
    return config.STAFF_TOKENS.get((token or "").strip())
