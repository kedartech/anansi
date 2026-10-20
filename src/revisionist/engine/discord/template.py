"""The fixed layout of a campaign's Discord server (Layer 9b).

Every name is a constant that encodes nothing: not the world, a seat, an
alliance, or who is human. There are no per-player channels, threads or
roles. Briefings travel by DM, because the server owner, who also plays, can
read every channel and private thread. The host creates the one channel and
gives the bot View Channel and Send Messages there; the bot never needs
Manage Channels, Manage Threads or Manage Roles.
"""

PUBLIC_CHANNEL = "chronicle"
"""Player statements and the press. Set with ``revisionist discord channel public <id>``."""

CHANNELS = (PUBLIC_CHANNEL,)
ROLES: tuple[str, ...] = ()
"""Nothing beyond @everyone."""

__all__ = ["CHANNELS", "PUBLIC_CHANNEL", "ROLES"]
