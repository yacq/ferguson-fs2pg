"""Constants for the Ferguson FS2PG integration."""

DOMAIN = "ferguson_fs2pg"

CONF_DEVICES = "devices"  # entry.data: list of device IDs seen so far
CONF_NAMES = "names"  # entry.data: device ID -> name reported by the plug
CONF_ALLOWED_SOURCES = "allowed_sources"  # entry.options: list of IPs/networks
DEFAULT_PORT = 23778

SIGNAL_NEW_PLUG = f"{DOMAIN}_new_plug"
