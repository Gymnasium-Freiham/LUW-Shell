import os
import datetime
import sys

# runtime flag controlling debug message suppression (only for debug messages)
_DEBUG_SUPPRESSED = False

def set_debug_suppressed(v: bool):
	"""Suppress debug-level logs when True (consumer decides what is debug)."""
	global _DEBUG_SUPPRESSED
	_DEBUG_SUPPRESSED = bool(v)

def is_debug_suppressed() -> bool:
	"""Query whether debug-level logs are suppressed."""
	return _DEBUG_SUPPRESSED

def _now():
	return datetime.datetime.now().isoformat()

def _timestamp_enabled():
	# read dynamically from env
	return os.environ.get("TIMESTAMP", "0").strip().lower() in ("1", "true", "yes", "on")

def log(msg: str, end: str = "\n", file=None, flush: bool = True):
	"""
	Print a log message. Timestamping is controlled by TIMESTAMP env var.
	This function always writes output; debug suppression is handled by callers.
	"""
	out = file if file is not None else sys.stdout
	s = str(msg)
	if _timestamp_enabled():
		s = f"{_now()} {s}"
	print(s, file=out, end=end, flush=flush)
