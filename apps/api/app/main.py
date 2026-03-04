from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="Melting Moments API",
    description="Backend API for Melting Moments Cakes ecommerce",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# Routers will be added in Prompt 3+
# app.include_router(auth_router, prefix="/api/v1")
# app.include_router(products_router, prefix="/api/v1")
# etc.


@app.get("/health", tags=["System"])
async def health():
    return {"status": "ok", "service": "mm-api"}
