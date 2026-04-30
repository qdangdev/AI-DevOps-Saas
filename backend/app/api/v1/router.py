"""Aggregates v1 routers."""
from fastapi import APIRouter

from app.api.v1 import auth, deployments, repos

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(repos.router)
api_router.include_router(deployments.router)
