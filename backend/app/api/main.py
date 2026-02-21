from fastapi import APIRouter

from app.api.routes import builds, game, login, optimize, private, saves, users, utils
from app.core.config import settings

api_router = APIRouter()
api_router.include_router(login.router)
api_router.include_router(users.router)
api_router.include_router(utils.router)
api_router.include_router(game.router)
api_router.include_router(saves.router)
api_router.include_router(builds.router)
api_router.include_router(optimize.router)


if settings.ENVIRONMENT == "local":
    api_router.include_router(private.router)
