"""The single realtime endpoint. Read-only: subscribe/unsubscribe/resume/ping.

All mutations still go through REST — see PLAN-PHASE-0-1.md §3.3 for the wire protocol.
Not mounted under /api/v1 (that router carries the same-origin-only REST dependency);
the origin check here is manual, since the WebSocket handshake needs its own accept/close
lifecycle regardless of the result.
"""

import asyncio
import json
from datetime import timedelta

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from acc.api.deps import SESSION_COOKIE
from acc.config import Settings
from acc.ids import utcnow
from acc.queries import events as event_queries
from acc.queries.auth import get_active_session
from acc.realtime.hub import Hub, topics_for
from acc.security import session_id_for

router = APIRouter(tags=["realtime"])
log = structlog.get_logger("acc.ws")

SESSION_RECHECK_INTERVAL = timedelta(minutes=5)
RECEIVE_TIMEOUT_S = 30.0
RESUME_OVERLAP = 50
# The resync threshold: a reconnect needing more than this many events to catch up gets
# told to resync via REST instead of receiving a giant backlog of individual messages.
# Matches queries.events.MAX_PAGE, which is what actually bounds the query below.
RESUME_GAP_LIMIT = 500

CLOSE_ORIGIN_MISMATCH = 4403
CLOSE_UNAUTHENTICATED = 4401


def _event_dict(view: event_queries.EventView) -> dict[str, object]:
    """Same wire shape as acc.realtime.publisher.as_wire_dict, for a replayed (not live) event."""
    return {
        "id": str(view.id),
        "seq": view.seq,
        "ts": view.ts.isoformat(),
        "type": view.type,
        "severity": view.severity,
        "actor": view.actor,
        "project_id": None,
        "task_id": None,
        "agent_id": None,
        "task_key": view.task_key,
        "agent_slug": view.agent_slug,
        "payload": view.payload,
    }


async def _handle_resume(websocket: WebSocket, after_seq: int) -> None:
    sf = websocket.app.state.session_factory
    overlap_from = max(0, after_seq - RESUME_OVERLAP)
    items = await event_queries.list_events(sf, after_seq=overlap_from, limit=RESUME_GAP_LIMIT)
    if len(items) >= RESUME_GAP_LIMIT:
        await websocket.send_json({"type": "resync"})
        return
    for view in items:
        event = _event_dict(view)
        await websocket.send_json(
            {"type": "event", "topics": sorted(topics_for(event)), "event": event}
        )


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    settings: Settings = websocket.app.state.settings
    await websocket.accept()

    if websocket.headers.get("origin") != settings.public_origin:
        await websocket.close(code=CLOSE_ORIGIN_MISMATCH)
        return

    sf = websocket.app.state.session_factory
    token = websocket.cookies.get(SESSION_COOKIE)
    session_id = (
        session_id_for(token, settings.session_secret.get_secret_value()) if token else None
    )
    active = (
        await get_active_session(sf, session_id=session_id, now=utcnow()) if session_id else None
    )
    if active is None:
        await websocket.close(code=CLOSE_UNAUTHENTICATED)
        return
    assert session_id is not None

    hub: Hub = websocket.app.state.hub
    conn_id = hub.register(websocket)
    last_checked = utcnow()
    try:
        await websocket.send_json(
            {"type": "hello", "server_seq": await event_queries.latest_seq(sf)}
        )
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=RECEIVE_TIMEOUT_S)
            except TimeoutError:
                raw = None

            now = utcnow()
            if now - last_checked >= SESSION_RECHECK_INTERVAL:
                if await get_active_session(sf, session_id=session_id, now=now) is None:
                    await websocket.close(code=CLOSE_UNAUTHENTICATED)
                    return
                last_checked = now

            if raw is None:
                continue
            try:
                message = json.loads(raw)
                op = message["op"]
            except (ValueError, KeyError, TypeError):
                await websocket.send_json({"type": "error", "detail": "malformed message"})
                continue

            if op == "ping":
                await websocket.send_json({"type": "pong"})
            elif op == "subscribe":
                hub.subscribe(conn_id, message.get("topics") or [])
            elif op == "unsubscribe":
                hub.unsubscribe(conn_id, message.get("topics") or [])
            elif op == "resume":
                try:
                    after_seq = int(message["after_seq"])
                except (KeyError, TypeError, ValueError):
                    await websocket.send_json({"type": "error", "detail": "resume needs after_seq"})
                    continue
                await _handle_resume(websocket, after_seq)
            else:
                await websocket.send_json({"type": "error", "detail": f"unknown op {op!r}"})
    except WebSocketDisconnect:
        pass
    finally:
        hub.unregister(conn_id)
