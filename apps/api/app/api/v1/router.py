from fastapi import APIRouter

from .auth import router as auth_router
from .categories import router as categories_router
from .products import router as products_router
from .cart import router as cart_router
from .uploads import router as uploads_router
from .delivery import router as delivery_router
from .promo_codes import router as promo_codes_router
from .orders import router as orders_router
from .addresses import router as addresses_router
from .payments import router as payments_router
from .analytics import router as analytics_router
from .users import router as users_router
from .modifiers import router as modifiers_router
from .import_data import router as import_router

api_router = APIRouter()

api_router.include_router(auth_router, prefix="/auth", tags=["Authentication"])
api_router.include_router(categories_router, prefix="/categories", tags=["Categories"])
api_router.include_router(products_router, prefix="/products", tags=["Products"])
api_router.include_router(modifiers_router, prefix="/modifiers", tags=["Modifiers"])
api_router.include_router(import_router, prefix="/import", tags=["Import"])
api_router.include_router(cart_router, prefix="/cart", tags=["Cart"])
api_router.include_router(uploads_router, prefix="/uploads", tags=["Uploads"])
api_router.include_router(delivery_router, prefix="/delivery", tags=["Delivery"])
api_router.include_router(
    promo_codes_router, prefix="/promo-codes", tags=["Promo Codes"]
)
api_router.include_router(orders_router, prefix="/orders", tags=["Orders"])
api_router.include_router(addresses_router, prefix="/addresses", tags=["Addresses"])
api_router.include_router(payments_router, prefix="/payments", tags=["Payments"])
api_router.include_router(analytics_router, prefix="/analytics", tags=["Analytics"])
api_router.include_router(users_router, prefix="/users", tags=["Users"])
