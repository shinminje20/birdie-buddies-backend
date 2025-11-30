import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator, StringConstraints
from typing import Optional, List, Literal, Annotated

class RegisterIn(BaseModel):
    seats: Annotated[int, Field(ge=1, le=3)]
    guest_names: List[str] = Field(default_factory=list, description="0..2 guest names")

    @field_validator("guest_names")
    @classmethod
    def max_two(cls, v):
        if len(v) > 2:
            raise ValueError("guest_names can have at most 2 items")
        return v

    @model_validator(mode="after")
    def seats_vs_guests(self):
        if self.seats != 1 + len(self.guest_names or []):
            raise ValueError("seats must equal 1 (host) + len(guest_names)")
        return self

class CancelOut(BaseModel):
    refund_cents: int
    penalty_cents: int
    state: str  # canceled | too_late | not_found | session_closed

class RegisterEnqueuedOut(BaseModel):
    request_id: str
    state: str = "queued"   # constant for Step 5


class RequestStatusOut(BaseModel):
    state: str                     # queued | confirmed | waitlisted | rejected (Step 6 will update)
    session_id: uuid.UUID
    user_id: uuid.UUID
    seats: int
    guest_names: List[str]
    created_at: datetime
    registration_id: Optional[uuid.UUID] = None     # will be set by worker in Step 6 (if any)
    waitlist_pos: Optional[int] = None              # same

class RegRowOut(BaseModel):
    registration_id: uuid.UUID
    host_user_id: uuid.UUID
    host_name: str
    seats: int
    guest_names: list[str] | None = None
    waitlist_pos: int | None = None
    state: str
    group_key: uuid.UUID | None = None
    is_host: bool = False

class MyRegistrationOut(BaseModel):
    """Registration with session details for My Games page"""
    registration_id: uuid.UUID
    session_id: uuid.UUID
    session_title: str | None = None
    starts_at_utc: datetime
    timezone: str
    session_status: str  # scheduled | closed | canceled
    seats: int
    guest_names: list[str] | None = None
    waitlist_pos: int | None = None
    state: str  # confirmed | waitlisted | canceled
    group_key: uuid.UUID | None = None
    is_host: bool = False

class GuestsUpdateIn(BaseModel):
    guest_names: list[str]

    @field_validator("guest_names")
    def max_two(cls, v):
        if len(v) > 2:
            raise ValueError("guest_names can have at most 2 items")
        for name in v:
            if len(name) > 50:
                raise ValueError("guest name too long (max 50)")
        return v


class GuestsUpdateOut(BaseModel):
    registration_id: uuid.UUID
    old_seats: int
    new_seats: int
    refund_cents: int
    penalty_cents: int
    state: str
    
class AdminPreregItemIn(BaseModel):
    user_id: uuid.UUID
    seats: Annotated[int, Field(ge=1)]
    guest_names: List[str] = Field(default_factory=list, description="0..2 guest names")
    idempotency_key: Optional[Annotated[str, StringConstraints(strip_whitespace=True, min_length=6, max_length=120)]] = None

    @field_validator("guest_names")
    @classmethod
    def _max_two(cls, v):
        if len(v) > 2:
            raise ValueError("guest_names can have at most 2 items")
        return v

    @model_validator(mode="after")
    def _seats_vs_guests(self):
        if self.seats != 1 + len(self.guest_names or []):
            raise ValueError("seats must equal 1 (host) + len(guest_names)")
        return self

class AdminPreregResultOut(BaseModel):
    user_id: uuid.UUID
    registration_id: Optional[uuid.UUID] = None
    state: Literal["confirmed", "waitlisted", "rejected"]
    waitlist_pos: Optional[int] = None
    error: Optional[str] = None
