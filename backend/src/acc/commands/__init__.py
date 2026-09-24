"""The only write path into the database.

API routers, the CLI and (from Phase 2) the orchestrator call these functions. Nothing else
mutates persistent state. Each command runs in a single transaction.
"""
