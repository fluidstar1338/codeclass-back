from fastapi import APIRouter

from app.api.v1 import auth, classrooms, health, organizations, users

api_router = APIRouter()

api_router.include_router(health.router)
api_router.include_router(auth.router, prefix="/auth", tags=["Auth"])
api_router.include_router(organizations.router, prefix="/orgs", tags=["Organizations"])
api_router.include_router(users.router, prefix="/users", tags=["Users"])
api_router.include_router(classrooms.router, prefix="/classrooms", tags=["Classrooms"])
