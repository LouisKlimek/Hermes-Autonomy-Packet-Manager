"""Agent half of the Hermes Autonomy Packet Manager (HAPM).

This is the *agent* half of HAPM. The dashboard half (``dashboard/``) ships the
Autonomy Packet Manager tab, its FastAPI backend, and the reversibility engine
under ``dashboard/hapm/``. This root module is what makes HAPM a **full** Hermes
plugin — with a root ``plugin.yaml`` + ``register(ctx)`` the agent plugin loader
recognizes and can enable it, structurally analogous to the Tasklist plugin.

Scope (v1)
----------
v1 is deliberately a **structural no-op skeleton**. ``register()`` registers no
hooks and performs no work: HAPM becomes a discoverable, enable-able agent
plugin *without any behavioral side effects*. In particular this half does NOT
apply presets/addons, does NOT mutate any profile, and does NOT touch the
dashboard backend or the ``dashboard/hapm/`` engine.

Import-safety
-------------
Importing this module must stay cheap and side-effect-free: it pulls in only the
standard library and never imports the dashboard/``hapm`` engine, so merely
loading the plugin cannot trigger DB writes, profile mutation, or any preset/
addon application.

Extension point
---------------
Agent-tools and auto-kanban-hooks that would apply presets/addons to profiles
are a separate, human-gate-required product decision. When (and only when) that
is approved, wire them in at the marked extension point inside ``register()``.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def register(ctx: Any) -> None:
    """Register the HAPM agent half with the Hermes plugin host.

    v1: intentional no-op. HAPM is recognized and enable-able as a full agent
    plugin, but registers no hooks and applies no profile mutation.
    """
    # v1.x extension point: hook up Kanban lifecycle hooks / agent-tools here
    # (e.g. ctx.register_hook("kanban_task_claimed", ...)) once the profile-
    # mutating behavior is approved via the required human gate. Until then this
    # register() stays a clean no-op with no side effects.
    logger.debug("hapm agent half: register() no-op skeleton (v1, no hooks)")
