import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import EmailStr
from sqlalchemy import BigInteger, Column, DateTime, JSON, UniqueConstraint
from sqlmodel import Field, Relationship, SQLModel

from nrplanner.models import BuildChange


def get_datetime_utc() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# User models
# ---------------------------------------------------------------------------

class UserBase(SQLModel):
    email: EmailStr = Field(unique=True, index=True, max_length=255)
    is_active: bool = True
    is_superuser: bool = False
    full_name: str | None = Field(default=None, max_length=255)


class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=128)


class UserRegister(SQLModel):
    email: EmailStr = Field(max_length=255)
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)


class UserUpdate(UserBase):
    email: EmailStr | None = Field(default=None, max_length=255)  # type: ignore
    password: str | None = Field(default=None, min_length=8, max_length=128)


class UserUpdateMe(SQLModel):
    full_name: str | None = Field(default=None, max_length=255)
    email: EmailStr | None = Field(default=None, max_length=255)


class UpdatePassword(SQLModel):
    current_password: str = Field(min_length=8, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class User(UserBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    hashed_password: str | None = None
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    save_uploads: list["SaveUpload"] = Relationship(
        back_populates="owner", cascade_delete=True
    )
    builds: list["Build"] = Relationship(
        back_populates="owner", cascade_delete=True
    )


class UserPublic(UserBase):
    id: uuid.UUID
    created_at: datetime | None = None


class UsersPublic(SQLModel):
    data: list[UserPublic]
    count: int


# ---------------------------------------------------------------------------
# Save upload models
# ---------------------------------------------------------------------------

class SaveUpload(SQLModel, table=True):
    __tablename__ = "save_upload"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    owner_id: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, ondelete="CASCADE", index=True
    )
    platform: str = Field(max_length=10)  # "PC" | "PS4"
    uploaded_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    profile_count: int = 0

    owner: Optional["User"] = Relationship(back_populates="save_uploads")
    profiles: list["Profile"] = Relationship(
        back_populates="save_upload", cascade_delete=True
    )


class SaveUploadPublic(SQLModel):
    id: uuid.UUID
    platform: str
    uploaded_at: datetime | None = None
    profile_count: int


# ---------------------------------------------------------------------------
# Profile models (save-file character slots)
# ---------------------------------------------------------------------------

class Profile(SQLModel, table=True):
    __tablename__ = "profile"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    owner_id: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, ondelete="CASCADE", index=True
    )
    save_upload_id: uuid.UUID = Field(
        foreign_key="save_upload.id", nullable=False, ondelete="CASCADE", index=True
    )
    slot_index: int
    name: str = Field(max_length=100)
    relics_hash: str | None = Field(default=None, max_length=64)

    save_upload: Optional["SaveUpload"] = Relationship(back_populates="profiles")
    relics: list["Relic"] = Relationship(
        back_populates="profile", cascade_delete=True
    )


class ProfilePublic(SQLModel):
    id: uuid.UUID
    save_upload_id: uuid.UUID
    slot_index: int
    name: str


class ProfilesPublic(SQLModel):
    data: list[ProfilePublic]
    count: int


# ---------------------------------------------------------------------------
# Relic models
# ---------------------------------------------------------------------------

class Relic(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    owner_id: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, ondelete="CASCADE", index=True
    )
    profile_id: uuid.UUID = Field(
        foreign_key="profile.id", nullable=False, ondelete="CASCADE", index=True
    )
    # BigInteger for values that may exceed int32 (e.g., 0xC000xxxx handles, 0xFFFFFFFF EMPTY)
    ga_handle: int = Field(sa_column=Column(BigInteger(), nullable=False))
    item_id: int = Field(sa_column=Column(BigInteger(), nullable=False))
    real_id: int = Field(sa_column=Column(BigInteger(), nullable=False))
    color: str = Field(max_length=10)
    effect_1: int = Field(sa_column=Column(BigInteger(), nullable=False))
    effect_2: int = Field(sa_column=Column(BigInteger(), nullable=False))
    effect_3: int = Field(sa_column=Column(BigInteger(), nullable=False))
    curse_1: int = Field(sa_column=Column(BigInteger(), nullable=False))
    curse_2: int = Field(sa_column=Column(BigInteger(), nullable=False))
    curse_3: int = Field(sa_column=Column(BigInteger(), nullable=False))
    is_deep: bool
    name: str = Field(max_length=255)
    tier: str = Field(max_length=20)  # "Grand" | "Polished" | "Delicate"

    profile: Optional["Profile"] = Relationship(back_populates="relics")


class RelicPublic(SQLModel):
    id: uuid.UUID
    profile_id: uuid.UUID
    ga_handle: int
    item_id: int
    real_id: int
    color: str
    effect_1: int
    effect_2: int
    effect_3: int
    curse_1: int
    curse_2: int
    curse_3: int
    is_deep: bool
    name: str
    tier: str


class RelicsPublic(SQLModel):
    data: list[RelicPublic]
    count: int


# ---------------------------------------------------------------------------
# Build models
# ---------------------------------------------------------------------------

class Build(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    owner_id: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, ondelete="CASCADE", index=True
    )
    name: str = Field(max_length=255)
    character: str = Field(max_length=50)
    groups: list = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False, server_default="[]"),
    )
    required_effects: list = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False, server_default="[]"),
    )
    required_families: list = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False, server_default="[]"),
    )
    excluded_effects: list = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False, server_default="[]"),
    )
    excluded_families: list = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False, server_default="[]"),
    )
    include_deep: bool = True
    curse_max: int = 1
    default_curse_weight: int = 0
    pinned_relics: list = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False, server_default="[]"),
    )
    excluded_stacking_categories: list = Field(
        default_factory=lambda: [300, 6630000],
        sa_column=Column(JSON, nullable=False, server_default="[300, 6630000]"),
    )
    effect_limits: dict = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False, server_default="{}"),
    )
    family_limits: dict = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False, server_default="{}"),
    )
    is_featured: bool = Field(default=False, index=True)
    build_hash: str | None = Field(default=None, max_length=64)
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    updated_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )

    owner: Optional["User"] = Relationship(back_populates="builds")


class BuildCreate(SQLModel):
    name: str = Field(min_length=1, max_length=255)
    character: str = Field(max_length=50)
    groups: list[dict] | None = None


class BuildUpdate(SQLModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    character: str | None = Field(default=None, max_length=50)
    groups: list[dict] | None = None
    required_effects: list[int] | None = None
    required_families: list[str] | None = None
    excluded_effects: list[int] | None = None
    excluded_families: list[str] | None = None
    include_deep: bool | None = None
    curse_max: int | None = Field(default=None, ge=1)
    default_curse_weight: int | None = None
    pinned_relics: list[int] | None = None
    excluded_stacking_categories: list[int] | None = None
    effect_limits: dict[int, int] | None = None
    family_limits: dict[str, int] | None = None


class BuildPublic(SQLModel):
    id: uuid.UUID
    owner_id: uuid.UUID
    name: str
    character: str
    groups: list[dict] = Field(default_factory=list)
    required_effects: list[int] = Field(default_factory=list)
    required_families: list[str] = Field(default_factory=list)
    excluded_effects: list[int] = Field(default_factory=list)
    excluded_families: list[str] = Field(default_factory=list)
    include_deep: bool
    curse_max: int
    default_curse_weight: int
    pinned_relics: list[int] = Field(default_factory=list)
    excluded_stacking_categories: list[int] = Field(default_factory=list)
    effect_limits: dict[int, int] = Field(default_factory=dict)
    family_limits: dict[str, int] = Field(default_factory=dict)
    is_featured: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None


class BuildsPublic(SQLModel):
    data: list[BuildPublic]
    count: int


class FeaturedBuildPublic(SQLModel):
    id: uuid.UUID
    name: str
    character: str
    groups: list[dict] = Field(default_factory=list)
    required_effects: list[int] = Field(default_factory=list)
    required_families: list[str] = Field(default_factory=list)
    excluded_effects: list[int] = Field(default_factory=list)
    excluded_families: list[str] = Field(default_factory=list)
    include_deep: bool
    curse_max: int
    default_curse_weight: int
    pinned_relics: list[int] = Field(default_factory=list)
    excluded_stacking_categories: list[int] = Field(default_factory=list)
    effect_limits: dict[int, int] = Field(default_factory=dict)
    family_limits: dict[str, int] = Field(default_factory=dict)
    owner_name: str | None = None
    created_at: datetime | None = None


class FeaturedBuildsPublic(SQLModel):
    data: list[FeaturedBuildPublic]
    count: int


# ---------------------------------------------------------------------------
# Optimization snapshots (save-diff change detection)
# ---------------------------------------------------------------------------

class OptimizationSnapshot(SQLModel, table=True):
    """Persisted optimization result + the inputs it was computed from.

    Keyed by (build_id, slot_index) — NOT profile_id — because Profile rows are
    cascade-deleted and recreated on every save re-upload, while slot_index is the
    stable identity of a save's character slot.  This lets the prior result
    survive a re-upload so the new inventory can be diffed against it.

    Staleness is a pure function of recorded provenance: the snapshot is current
    iff relics_hash, build_hash, game_data_version and optimizer_version all match
    the live inputs.
    """
    __tablename__ = "optimization_snapshot"
    __table_args__ = (
        UniqueConstraint(
            "build_id", "slot_index", name="uq_optimization_snapshot_build_slot"
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    owner_id: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, ondelete="CASCADE", index=True
    )
    build_id: uuid.UUID = Field(
        foreign_key="build.id", nullable=False, ondelete="CASCADE"
    )
    slot_index: int

    # --- provenance (stale ⇔ any differs from the live inputs) ---
    relics_hash: str = Field(max_length=64)
    build_hash: str = Field(max_length=64)
    game_data_version: str = Field(max_length=32)
    optimizer_version: int

    # --- result + cached change summary ---
    # Compact, handle-free layouts used as the DIFF BASELINE (diff_results
    # consumes this shape). Not enough to render results.
    top_layouts: list = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False, server_default="[]"),
    )
    # Complete VesselResult dumps served back by GET /optimize/snapshot.
    # Empty for legacy rows -> treated as stale (forces one re-optimize).
    full_results: list = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False, server_default="[]"),
    )
    best_score: int = 0
    any_truncated: bool = False
    last_change: Optional[dict] = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    reviewed: bool = Field(default=False)

    computed_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    updated_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )


# ---------------------------------------------------------------------------
# Save status schema
# ---------------------------------------------------------------------------

class SaveStatusPublic(SQLModel):
    """Lightweight status of the user's most recent save upload."""
    id: uuid.UUID
    platform: str
    uploaded_at: datetime | None = None
    profile_count: int
    profile_names: list[str]


# ---------------------------------------------------------------------------
# Upload response schemas
# ---------------------------------------------------------------------------

class ParsedRelicData(SQLModel):
    """OwnedRelic data as returned in the upload response (before DB persistence)."""
    ga_handle: int
    item_id: int
    real_id: int
    color: str
    effect_1: int
    effect_2: int
    effect_3: int
    curse_1: int
    curse_2: int
    curse_3: int
    is_deep: bool
    name: str
    tier: str


class ParsedProfileData(SQLModel):
    slot_index: int
    name: str
    relic_count: int
    relics: list[ParsedRelicData]
    # Populated for authenticated users after DB persistence
    id: uuid.UUID | None = None


class RelicDelta(SQLModel):
    """How many relics were gained/lost versus the previous save."""
    added: int = 0
    removed: int = 0


class UploadResponse(SQLModel):
    platform: str
    profile_count: int
    profiles: list[ParsedProfileData]
    save_upload_id: uuid.UUID | None = None
    persisted: bool = False
    # Save-diff summary (authenticated uploads only).
    relic_delta: Optional[RelicDelta] = None
    affected_builds: list[BuildChange] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Auth / Generic
# ---------------------------------------------------------------------------

class Token(SQLModel):
    access_token: str
    token_type: str = "bearer"


class TokenPayload(SQLModel):
    sub: str | None = None


class NewPassword(SQLModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)


class Message(SQLModel):
    message: str
