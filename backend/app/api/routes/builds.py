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
    FeaturedBuildPublic,
    FeaturedBuildsPublic,
    Message,
    User,
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


# NOTE: /featured must be declared before /{build_id} to avoid FastAPI
# interpreting the literal string "featured" as a UUID path parameter.

@router.get("/featured", response_model=FeaturedBuildsPublic)
def list_featured_builds(
    session: SessionDep,
    skip: int = 0,
    limit: int = 50,
) -> Any:
    """List all featured/suggested builds. Public endpoint — no auth required."""
    count = session.exec(
        select(func.count()).select_from(Build).where(Build.is_featured == True)  # noqa: E712
    ).one()
    rows = session.exec(
        select(Build, User.full_name)
        .join(User, Build.owner_id == User.id)
        .where(Build.is_featured == True)  # noqa: E712
        .order_by(col(Build.created_at).desc())
        .offset(skip)
        .limit(limit)
    ).all()
    data = [
        FeaturedBuildPublic(
            id=build.id,
            name=build.name,
            character=build.character,
            tiers=build.tiers,
            family_tiers=build.family_tiers,
            include_deep=build.include_deep,
            curse_max=build.curse_max,
            owner_name=full_name,
            created_at=build.created_at,
        )
        for build, full_name in rows
    ]
    return FeaturedBuildsPublic(data=data, count=count)


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


@router.patch("/{build_id}/featured", response_model=BuildPublic)
def toggle_featured(
    build_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
) -> Any:
    """Toggle a build's featured status. Superuser only, and only for own builds."""
    if not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Superuser required")
    build = session.get(Build, build_id)
    if not build:
        raise HTTPException(status_code=404, detail="Build not found")
    if build.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Can only feature your own builds")
    build.is_featured = not build.is_featured
    session.add(build)
    session.commit()
    session.refresh(build)
    return build


@router.post("/{build_id}/clone", response_model=BuildPublic)
def clone_build(
    build_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
) -> Any:
    """Clone a build. Allowed if the source is the user's own build OR is featured."""
    source = session.get(Build, build_id)
    if not source:
        raise HTTPException(status_code=404, detail="Build not found")
    if source.owner_id != current_user.id and not source.is_featured:
        raise HTTPException(status_code=403, detail="Not enough permissions")
    clone = Build(
        owner_id=current_user.id,
        name=f"{source.name} (Copy)",
        character=source.character,
        tiers=dict(source.tiers),
        family_tiers=dict(source.family_tiers),
        include_deep=source.include_deep,
        curse_max=source.curse_max,
        is_featured=False,
    )
    session.add(clone)
    session.commit()
    session.refresh(clone)
    return clone


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
