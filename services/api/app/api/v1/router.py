from fastapi import APIRouter

from app.modules.accounts.router import router as accounts_router
from app.modules.health.router import router as health_router
from app.modules.payments.router import router as payments_router

api_v1_router = APIRouter()
api_v1_router.include_router(health_router)
api_v1_router.include_router(accounts_router)
api_v1_router.include_router(payments_router)
