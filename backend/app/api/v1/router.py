from fastapi import APIRouter

from app.api.v1.artisans import router as artisans_router

router = APIRouter(prefix="/api/v1")
router.include_router(artisans_router)
