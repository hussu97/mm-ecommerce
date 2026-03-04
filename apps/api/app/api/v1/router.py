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

# Prompt 6: payments
# Prompt 16: analytics

api_router = APIRouter()

api_router.include_router(auth_router, prefix="/auth", tags=["Authentication"])
api_router.include_router(categories_router, prefix="/categories", tags=["Categories"])
api_router.include_router(products_router, prefix="/products", tags=["Products"])
api_router.include_router(cart_router, prefix="/cart", tags=["Cart"])
api_router.include_router(uploads_router, prefix="/uploads", tags=["Uploads"])
api_router.include_router(delivery_router, prefix="/delivery", tags=["Delivery"])
api_router.include_router(promo_codes_router, prefix="/promo-codes", tags=["Promo Codes"])
api_router.include_router(orders_router, prefix="/orders", tags=["Orders"])
api_router.include_router(addresses_router, prefix="/addresses", tags=["Addresses"])
