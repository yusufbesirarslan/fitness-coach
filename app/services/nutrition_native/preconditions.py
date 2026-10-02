"""HTTP preconditions for NUTR-PR7 native writes.

``If-Match`` reuses the diary mutation parser verbatim (one strong, quoted,
opaque token; weak validators, ``*``, lists, unquoted and blank values are all
refused), so every native Nutrition write speaks one precondition dialect.

The Nutrition Plan is the one resource that can legitimately be ABSENT, so its
create precondition is the standard one for "there must be no current
representation": ``If-None-Match: *`` — and only that exact spelling.
"""
from app.services.mobile_diary_mutation import (
    InvalidPrecondition,
    MissingPrecondition,
    parse_if_match,
)


__all__ = [
    "CREATE",
    "InvalidPrecondition",
    "MissingPrecondition",
    "Precondition",
    "parse_create_or_match",
    "parse_if_match",
]


CREATE = object()


class Precondition:
    """Either ``CREATE`` (``If-None-Match: *``) or one expected revision."""

    __slots__ = ("revision",)

    def __init__(self, revision):
        self.revision = revision

    @property
    def is_create(self):
        return self.revision is CREATE


def parse_create_or_match(if_match, if_none_match):
    """Exactly one of the two headers, each in its one accepted spelling."""
    if if_match is not None and if_none_match is not None:
        raise InvalidPrecondition
    if if_none_match is not None:
        if if_none_match != "*":
            raise InvalidPrecondition
        return Precondition(CREATE)
    if if_match is None:
        raise MissingPrecondition
    return Precondition(parse_if_match(if_match))
