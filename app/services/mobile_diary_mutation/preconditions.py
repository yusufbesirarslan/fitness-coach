"""Parser for the one strong ``If-Match`` accepted by diary mutations."""
import re


_OPAQUE_REVISION = re.compile(r"[A-Za-z0-9_-]{16,128}")


class MissingPrecondition(ValueError):
    pass


class InvalidPrecondition(ValueError):
    pass


def parse_if_match(value):
    if value is None:
        raise MissingPrecondition
    if not isinstance(value, str) or len(value) < 2:
        raise InvalidPrecondition
    if value[0] != '"' or value[-1] != '"':
        raise InvalidPrecondition
    token = value[1:-1]
    if not _OPAQUE_REVISION.fullmatch(token):
        raise InvalidPrecondition
    return token
