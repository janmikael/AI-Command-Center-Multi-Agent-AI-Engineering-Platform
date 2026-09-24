"""Global AI Governance rules: one set, applies to every project identically (Guiding
Principle 8 in ARCHITECTURE.md). This module is the vocabulary only — enforcement (a
future MCP gateway reading `governance_rules.match`/`effect`) is out of scope for
Phase 1.5. See ARCHITECTURE.md §10.3 for how these fields are meant to be consumed later.
"""

from enum import StrEnum


class RuleCategory(StrEnum):
    SECURITY = "security"
    TOOLS = "tools"
    CODE = "code"
    INFRASTRUCTURE = "infrastructure"


class RuleEffect(StrEnum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"
    # Not tool-call-shaped (e.g. "review changes before merge"): a future compiler
    # routes these to a workflow exit_check instead of a policies row.
    ADVISORY = "advisory"
