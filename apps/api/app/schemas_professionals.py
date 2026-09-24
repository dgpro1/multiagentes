"""Professionals: what the agency's client page and the client portal send and read."""

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MAX_RANGES_PER_DAY = 4
MIN_SLOT_MINUTES = 5
MAX_SLOT_MINUTES = 240

_CLOCK = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def _minutes(value: object) -> int:
    if not isinstance(value, str) or not _CLOCK.match(value):
        raise ValueError("Times must be written as HH:MM, 24 hours, from 00:00 to 23:59")
    return int(value[:2]) * 60 + int(value[3:])


def check_weekly_hours(value: object) -> dict[str, list[list[str]]]:
    """The week as the API stores it: every day present, ranges sorted.

    Unknown days, malformed times, an end that is not after its start,
    overlapping ranges and more than four ranges in a day are refused.
    """
    if not isinstance(value, dict):
        raise ValueError("weekly_hours must be an object with a list of ranges per day")
    unknown = sorted(str(key) for key in value if key not in DAYS)
    if unknown:
        raise ValueError(f"Unknown day in weekly_hours: {', '.join(unknown)}")
    week: dict[str, list[list[str]]] = {}
    for day in DAYS:
        ranges = value.get(day) or []
        if not isinstance(ranges, list):
            raise ValueError(f"The hours of {day} must be a list of [start, end] ranges")
        if len(ranges) > MAX_RANGES_PER_DAY:
            raise ValueError(f"A day can have up to {MAX_RANGES_PER_DAY} ranges ({day})")
        parsed: list[tuple[int, int]] = []
        for item in ranges:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                raise ValueError(f"Each range of {day} must be a [start, end] pair")
            start, end = _minutes(item[0]), _minutes(item[1])
            if start >= end:
                raise ValueError(f"A range of {day} must end after it starts")
            parsed.append((start, end))
        parsed.sort()
        for (_, previous_end), (next_start, _) in zip(parsed, parsed[1:]):
            if next_start < previous_end:
                raise ValueError(f"The ranges of {day} overlap")
        week[day] = [[f"{start // 60:02d}:{start % 60:02d}", f"{end // 60:02d}:{end % 60:02d}"] for start, end in parsed]
    return week


def full_week(value: object) -> dict[str, list[list[str]]]:
    """A stored week with every day present, for responses (never raises)."""
    stored = value if isinstance(value, dict) else {}
    return {day: [list(pair) for pair in (stored.get(day) or [])] for day in DAYS}


class ProfessionalCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    role: str = Field(default="", max_length=120)
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    is_active: bool = True
    slot_minutes: int = Field(default=30, ge=MIN_SLOT_MINUTES, le=MAX_SLOT_MINUTES)
    weekly_hours: dict = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _named(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("A professional needs a name")
        return value

    @field_validator("weekly_hours")
    @classmethod
    def _week(cls, value: dict) -> dict:
        return check_weekly_hours(value)


class ProfessionalUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    role: str | None = Field(default=None, max_length=120)
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    is_active: bool | None = None
    slot_minutes: int | None = Field(default=None, ge=MIN_SLOT_MINUTES, le=MAX_SLOT_MINUTES)
    weekly_hours: dict | None = None

    @field_validator("name")
    @classmethod
    def _named(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("A professional needs a name")
        return value

    @field_validator("weekly_hours")
    @classmethod
    def _week(cls, value: dict | None) -> dict | None:
        return None if value is None else check_weekly_hours(value)


class ProfessionalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    client_id: uuid.UUID
    name: str
    role: str
    color: str
    is_active: bool
    slot_minutes: int
    weekly_hours: dict[str, list[list[str]]]
    created_at: datetime
    updated_at: datetime

    @field_validator("weekly_hours", mode="before")
    @classmethod
    def _all_seven_days(cls, value):
        return full_week(value)
