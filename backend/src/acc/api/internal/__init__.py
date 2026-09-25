"""The runner-facing API surface. Never routed through Caddy (see infra/caddy/Caddyfile
and PLAN-PHASE-2-M2.md §2.6); authenticated by a per-run token (acc.security run tokens),
never the operator session cookie. Callers are sandboxed runner containers, not browsers —
no CSRF dependency here, and no acc.db.models import from the runner side of that
boundary (the runner process itself never has this package on its Python path at all;
it only ever speaks HTTP to it).
"""
