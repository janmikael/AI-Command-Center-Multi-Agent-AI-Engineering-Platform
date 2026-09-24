"""Per-project knowledge base: what the AI knows about a given codebase, as distinct
from Global AI Rules (what it may do — see acc.domain.governance). See
ARCHITECTURE.md §10.1.
"""

from enum import StrEnum


class ContextEntryKind(StrEnum):
    ARCHITECTURE = "architecture"
    CONVENTION = "convention"
    INTEGRATION = "integration"
    GOTCHA = "gotcha"
    GLOSSARY = "glossary"
