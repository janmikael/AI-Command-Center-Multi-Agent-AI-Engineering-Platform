"""Live push: a Redis-backed EventSink fans out committed events to every API process,
each of which relays them to its own locally-connected WebSockets via a Hub.

Redis holds no durable state here (Postgres already committed the event before this ever
runs); a missed publish only means live push is briefly behind, not lost data. REST and
the WebSocket `resume` op both replay from Postgres.
"""
