import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import EmailStr
from sqlalchemy import BigInteger, Column, DateTime, JSON
from sqlmodel import Field, Relationship, SQLModel


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
    hashed_password: str
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
        foreign_key="user.id", nullable=False, ondelete="CASCADE"
    )
    platform: str = Field(max_length=10)  # "PC" | "PS4"
    uploaded_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    character_count: int = 0

    owner: Optional["User"] = Relationship(back_populates="save_uploads")
    characters: list["CharacterSlot"] = Relationship(
        back_populates="save_upload", cascade_delete=True
    )


class SaveUploadPublic(SQLModel):
    id: uuid.UUID
    platform: str
    uploaded_at: datetime | None = None
    character_count: int


# ---------------------------------------------------------------------------
# Character models
# ---------------------------------------------------------------------------

class CharacterSlot(SQLModel, table=True):
    __tablename__ = "character_slot"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    owner_id: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, ondelete="CASCADE"
    )
    save_upload_id: uuid.UUID = Field(
        foreign_key="save_upload.id", nullable=False, ondelete="CASCADE"
    )
    slot_index: int
    name: str = Field(max_length=100)

    save_upload: Optional["SaveUpload"] = Relationship(back_populates="characters")
    relics: list["Relic"] = Relationship(
        back_populates="character_slot", cascade_delete=True
    )


class CharacterPublic(SQLModel):
    id: uuid.UUID
    save_upload_id: uuid.UUID
    slot_index: int
    name: str


class CharactersPublic(SQLModel):
    data: list[CharacterPublic]
    count: int


# ---------------------------------------------------------------------------
# Relic models
# ---------------------------------------------------------------------------

class Relic(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    owner_id: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, ondelete="CASCADE"
    )
    character_id: uuid.UUID = Field(
        foreign_key="character_slot.id", nullable=False, ondelete="CASCADE"
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

    character_slot: Optional["CharacterSlot"] = Relationship(back_populates="relics")


class RelicPublic(SQLModel):
    id: uuid.UUID
    character_id: uuid.UUID
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

_DEFAULT_TIER_KEYS = ["required", "preferred", "nice_to_have", "avoid", "blacklist"]


def _default_tiers() -> dict:
    return {k: [] for k in _DEFAULT_TIER_KEYS}


class Build(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    owner_id: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, ondelete="CASCADE"
    )
    name: str = Field(max_length=255)
    character: str = Field(max_length=50)
    tiers: dict = Field(
        default_factory=_default_tiers,
        sa_column=Column(JSON, nullable=False),
    )
    family_tiers: dict = Field(
        default_factory=_default_tiers,
        sa_column=Column(JSON, nullable=False),
    )
    include_deep: bool = True
    curse_max: int = 1
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


class BuildUpdate(SQLModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    character: str | None = Field(default=None, max_length=50)
    tiers: dict[str, list[int]] | None = None
    family_tiers: dict[str, list[str]] | None = None
    include_deep: bool | None = None
    curse_max: int | None = Field(default=None, ge=0)


class BuildPublic(SQLModel):
    id: uuid.UUID
    owner_id: uuid.UUID
    name: str
    character: str
    tiers: dict[str, list[int]]
    family_tiers: dict[str, list[str]]
    include_deep: bool
    curse_max: int
    created_at: datetime | None = None
    updated_at: datetime | None = None


class BuildsPublic(SQLModel):
    data: list[BuildPublic]
    count: int


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


class ParsedCharacterData(SQLModel):
    slot_index: int
    name: str
    relic_count: int
    relics: list[ParsedRelicData]
    # Populated for authenticated users after DB persistence
    id: uuid.UUID | None = None


class UploadResponse(SQLModel):
    platform: str
    character_count: int
    characters: list[ParsedCharacterData]
    save_upload_id: uuid.UUID | None = None
    persisted: bool = False


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
