"""Build definition CRUD — per-user, auth required."""
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlmodel import col, func, select

from app.api.deps import CurrentUser, SessionDep
from app.models import (
    Build,
    BuildCreate,
    BuildPublic,
    BuildsPublic,
    BuildUpdate,
    Message,
    _default_tiers,
)

router = APIRouter(prefix="/builds", tags=["builds"])


@router.get("/", response_model=BuildsPublic)
def list_builds(
    session: SessionDep,
    current_user: CurrentUser,
    skip: int = 0,
    limit: int = 100,
) -> Any:
    """List all builds for the current user."""
    count = session.exec(
        select(func.count()).select_from(Build).where(Build.owner_id == current_user.id)
    ).one()
    builds = session.exec(
        select(Build)
        .where(Build.owner_id == current_user.id)
        .order_by(col(Build.updated_at).desc())
        .offset(skip)
        .limit(limit)
    ).all()
    return BuildsPublic(data=builds, count=count)


@router.post("/", response_model=BuildPublic)
def create_build(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    build_in: BuildCreate,
) -> Any:
    """Create a new build (empty tiers)."""
    build = Build(
        owner_id=current_user.id,
        name=build_in.name,
        character=build_in.character,
        tiers=_default_tiers(),
        family_tiers=_default_tiers(),
    )
    session.add(build)
    session.commit()
    session.refresh(build)
    return build


@router.get("/{build_id}", response_model=BuildPublic)
def get_build(
    build_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
) -> Any:
    """Get a single build by ID."""
    build = session.get(Build, build_id)
    if not build:
        raise HTTPException(status_code=404, detail="Build not found")
    if build.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not enough permissions")
    return build


@router.put("/{build_id}", response_model=BuildPublic)
def update_build(
    *,
    build_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
    build_in: BuildUpdate,
) -> Any:
    """Update a build's name, character, tiers, or settings."""
    build = session.get(Build, build_id)
    if not build:
        raise HTTPException(status_code=404, detail="Build not found")
    if build.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not enough permissions")

    update_data = build_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(build, field, value)
    build.updated_at = datetime.now(timezone.utc)

    session.add(build)
    session.commit()
    session.refresh(build)
    return build


@router.delete("/{build_id}", response_model=Message)
def delete_build(
    build_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
) -> Message:
    """Delete a build."""
    build = session.get(Build, build_id)
    if not build:
        raise HTTPException(status_code=404, detail="Build not found")
    if build.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not enough permissions")
    session.delete(build)
    session.commit()
    return Message(message="Build deleted successfully")
