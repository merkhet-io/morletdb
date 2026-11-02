"""Exceptions raised by the SDK. Database errors keep their original exception as `__cause__`."""


class MorletError(Exception):
    """Base class for every error raised by this package."""


class InvalidInputError(MorletError, ValueError):
    """The arguments cannot be stored or answered as given (wrong shape, type, interval or timing)."""


class NotFoundError(MorletError, LookupError):
    """The collection, recording or interval does not exist or is not visible to this role."""


class ConflictError(MorletError):
    """Samples differ from those already stored at the same offsets (a conflicting replay)."""


class UnsupportedError(MorletError):
    """The connected server's pg_morlet version does not provide the operation."""


class DatabaseError(MorletError):
    """Any other database error, with the server's message."""


def translate(exc):
    """Map a psycopg error from pg_morlet to the SDK's exceptions (by the server's documented messages)."""
    msg = getattr(getattr(exc, "diag", None), "message_primary", None) or str(exc)
    low = msg.lower()
    if "conflicting replay" in low:
        return ConflictError(msg)
    if ("does not exist or is not visible" in low or "incomplete or not fully visible" in low
            or "exceeds recording length" in low or "does not exist" in low):
        return NotFoundError(msg)
    if ("must" in low and ("interval" in low or "window" in low or "chunk" in low or "sample" in low)) \
            or "invalid" in low or "query interval must match" in low or "nonnegative" in low:
        return InvalidInputError(msg)
    return DatabaseError(msg)
