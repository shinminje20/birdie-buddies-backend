from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .config import get_settings
from .api.routers import health as health_router
from .api.routers import auth as auth_router
from .api.routers import wallet as wallet_router
from .api.routers import admin as admin_router
from .api.routers import sessions as sessions_router
from .api.routers import registrations as registrations_router
from .api.routers import events as events_router 
from .observability.logging import setup_logging
from .middleware.request_context import RequestContextMiddleware
from .observability.metrics import MetricsHTTPMiddleware
from .api.routers import metrics as metrics_router
from .api.routers import admin_users as admin_users_router
import uvicorn

settings = get_settings()
setup_logging()
ALLOWED_ORIGINS = [
    "https://birdie-buddies-a32af.web.app",
    "https://birdie-buddies-a32af.firebaseapp.com",  # 필요 시
    "http://localhost:5173",                         # 로컬 개발용 (원하면)
]
def create_app() -> FastAPI:
    app = FastAPI(title=settings.APP_NAME, debug=settings.DEBUG)

    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(MetricsHTTPMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router.router)
    app.include_router(auth_router.router)
    app.include_router(wallet_router.router)
    app.include_router(admin_router.router)
    app.include_router(sessions_router.router)
    app.include_router(registrations_router.router)
    app.include_router(events_router.router)
    app.include_router(metrics_router.router)
    app.include_router(admin_users_router.router)
    
    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)