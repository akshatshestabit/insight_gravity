"""
Slack messaging tool for the InsightForge MCP server.

Stub implementation — replace the HTTP calls with the real Slack Web API
(https://api.slack.com/web) using the `slack_sdk` package in production.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

# Stub channel registry — in production fetch from Slack API
_CHANNELS: dict[str, dict] = {
    "#general": {"id": "C001GENERAL", "name": "general", "purpose": "Company-wide updates"},
    "#engineering": {"id": "C002ENG", "name": "engineering", "purpose": "Engineering team"},
    "#sales": {"id": "C003SALES", "name": "sales", "purpose": "Sales & revenue"},
    "#alerts": {"id": "C004ALERTS", "name": "alerts", "purpose": "System alerts and on-call"},
    "#insightforge": {"id": "C005IF", "name": "insightforge", "purpose": "InsightForge platform"},
}

# Message store for stub
_MESSAGES: list[dict] = []


def post_to_slack(
    channel: str,
    message: str,
    username: str = "InsightForge Bot",
    icon_emoji: str = ":robot_face:",
    thread_ts: str | None = None,
) -> dict:
    """
    Post a message to a Slack channel.

    Args:
        channel:    Channel name (e.g. "#engineering") or channel ID.
        message:    Message text (supports Slack markdown).
        username:   Display name for the bot post.
        icon_emoji: Emoji icon for the post.
        thread_ts:  If set, posts as a reply in an existing thread.

    Returns:
        Posted message metadata including timestamp and channel.
    """
    # Normalise channel name
    if not channel.startswith("#"):
        channel = f"#{channel}"

    channel_info = _CHANNELS.get(channel)
    if not channel_info:
        return {"error": f"Channel '{channel}' not found or bot not a member."}

    msg_ts = f"{datetime.now(timezone.utc).timestamp():.6f}"
    record = {
        "ok": True,
        "ts": msg_ts,
        "message_id": str(uuid.uuid4()),
        "channel": channel,
        "channel_id": channel_info["id"],
        "text": message,
        "username": username,
        "icon_emoji": icon_emoji,
        "thread_ts": thread_ts,
        "posted_at": datetime.now(timezone.utc).isoformat(),
        "permalink": f"https://your-workspace.slack.com/archives/{channel_info['id']}/p{msg_ts.replace('.', '')}",
    }
    _MESSAGES.append(record)
    return record


def list_slack_channels(include_private: bool = False) -> list[dict]:
    """
    List available Slack channels the bot can post to.

    Args:
        include_private: Whether to include private channels.

    Returns:
        List of channel metadata dicts.
    """
    return [
        {"name": info["name"], "id": info["id"], "purpose": info["purpose"]}
        for info in _CHANNELS.values()
    ]


def get_slack_messages(channel: str, limit: int = 10) -> list[dict]:
    """Retrieve recent stub messages from a channel (for testing)."""
    channel = f"#{channel}" if not channel.startswith("#") else channel
    return [m for m in _MESSAGES if m["channel"] == channel][-limit:]
