from fastapi import APIRouter

from app.api.v1.artisan_authorizations import router as artisan_authorizations_router
from app.api.v1.artisans import router as artisans_router
from app.api.v1.certificates import router as certificates_router
from app.api.v1.design_reviews import router as design_reviews_router
from app.api.v1.hero import router as hero_router
from app.api.v1.pieces import router as pieces_router
from app.api.v1.site_images import router as site_images_router

router = APIRouter(prefix="/api/v1")
router.include_router(artisans_router)
router.include_router(pieces_router)
router.include_router(certificates_router)
router.include_router(design_reviews_router)
router.include_router(artisan_authorizations_router)
router.include_router(hero_router)
router.include_router(site_images_router)
