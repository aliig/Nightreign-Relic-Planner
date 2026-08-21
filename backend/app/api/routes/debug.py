"""Local-only debug state export.

Dumps a superuser's builds, parsed inventory, optimization snapshots, current
on-screen scenario, and version metadata into one self-contained JSON bundle —
so weird behavior can be handed to a developer (or Claude Code) and replayed
deterministically through the optimizer.

Double-gated: the router is only mounted when ``ENVIRONMENT == "local"`` (see
api/main.py) AND every endpoint requires a superuser.  The bundle uses the
optimizer's canonical schemas (``BuildDefinition`` / ``OwnedRelic``), so it is a
drop-in for the inline ``/optimize`` endpoint and ``nrplanner.debug_replay``.

The bundle is written to a gitignored ``debug-exports/`` dir at the repo root and
also returned in the response for a browser download.  Nothing here is reachable
outside local dev.
"""
import json
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

import nrplanner
from fastapi import APIRouter, Depends
from nrplanner.models import OwnedRelic
from nrplanner.optimizer import OPTIMIZER_VERSION
from pydantic import BaseModel
from sqlmodel import select

from app.api.deps import SessionDep, get_current_active_superuser
from app.core.build_def import build_def_from_db
from app.core.config import settings
from app.core.game_data import game_data_version
from app.models import Build, OptimizationSnapshot, Profile, Relic, User

router = APIRouter(prefix="/debug", tags=["debug"])


def get_debug_export_dir() -> Path:
    """Gitignored ``debug-exports/`` at the repo root (derived from the package
    location, so it is correct regardless of the process cwd)."""
    repo_root = Path(nrplanner.__file__).resolve().parent.parent
    export_dir = repo_root / "debug-exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    return export_dir


def _git_sha() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=2,
            cwd=Path(nrplanner.__file__).resolve().parent.parent,
        )
        return out.stdout.strip() or None
    except Exception:
        return None


def _relics_to_owned(relics: list[Relic]) -> list[OwnedRelic]:
    return [
        OwnedRelic(
            ga_handle=r.ga_handle,
            item_id=r.item_id,
            real_id=r.real_id,
            color=r.color,
            effects=[r.effect_1, r.effect_2, r.effect_3],
            curses=[r.curse_1, r.curse_2, r.curse_3],
            is_deep=r.is_deep,
            name=r.name,
            tier=r.tier,
        )
        for r in relics
    ]


class DebugExportRequest(BaseModel):
    # "view" narrows to one build/profile (the screen you were on); "full" dumps
    # everything the caller owns.
    mode: Literal["view", "full"] = "full"
    build_id: uuid.UUID | None = None
    profile_id: uuid.UUID | None = None
    # The VesselResult[] currently rendered (raw JSON), for diffing against a replay.
    results: list[dict[str, Any]] | None = None
    note: str | None = None


@router.post("/export")
def export_debug_state(
    req: DebugExportRequest,
    session: SessionDep,
    current_user: Annotated[User, Depends(get_current_active_superuser)],
) -> dict[str, Any]:
    """Bundle the caller's builds, inventory, snapshots, and current scenario to
    a gitignored JSON file (and return it for download).  Local + superuser only."""
    owner_id = current_user.id

    build_q = select(Build).where(Build.owner_id == owner_id)
    if req.mode == "view" and req.build_id is not None:
        build_q = build_q.where(Build.id == req.build_id)
    builds = [
        {
            "raw": b.model_dump(mode="json"),
            "build_def": build_def_from_db(b).model_dump(mode="json"),
        }
        for b in session.exec(build_q).all()
    ]

    prof_q = select(Profile).where(Profile.owner_id == owner_id)
    if req.mode == "view" and req.profile_id is not None:
        prof_q = prof_q.where(Profile.id == req.profile_id)
    profiles = []
    for p in session.exec(prof_q).all():
        relics = list(session.exec(select(Relic).where(Relic.profile_id == p.id)).all())
        profiles.append({
            "id": str(p.id),
            "slot_index": p.slot_index,
            "name": p.name,
            "relic_count": len(relics),
            "relics": [r.model_dump(mode="json") for r in _relics_to_owned(relics)],
        })

    snap_q = select(OptimizationSnapshot).where(OptimizationSnapshot.owner_id == owner_id)
    if req.mode == "view" and req.build_id is not None:
        snap_q = snap_q.where(OptimizationSnapshot.build_id == req.build_id)
    snapshots = [s.model_dump(mode="json") for s in session.exec(snap_q).all()]

    bundle: dict[str, Any] = {
        "meta": {
            "exported_at": datetime.now(UTC).isoformat(),
            "environment": settings.ENVIRONMENT,
            "game_data_version": game_data_version(),
            "optimizer_version": OPTIMIZER_VERSION,
            "git_sha": _git_sha(),
            "mode": req.mode,
            "note": req.note,
        },
        "user": {
            "id": str(current_user.id),
            "email": current_user.email,
            "is_superuser": current_user.is_superuser,
        },
        "scenario": {
            "build_id": str(req.build_id) if req.build_id else None,
            "profile_id": str(req.profile_id) if req.profile_id else None,
            "results": req.results,
        },
        "builds": builds,
        "profiles": profiles,
        "snapshots": snapshots,
    }

    export_dir = get_debug_export_dir()
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    filename = f"{stamp}-{req.mode}.json"
    payload = json.dumps(bundle, indent=2, default=str)
    (export_dir / filename).write_text(payload, encoding="utf-8")
    (export_dir / "latest.json").write_text(payload, encoding="utf-8")

    return {
        "written_path": str(export_dir / "latest.json"),
        "filename": filename,
        "bundle": bundle,
    }
