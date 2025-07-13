import asyncio
import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import uvicorn

from config import settings
from api.routes import router as api_router
from api.websocket_routes import router as ws_router
from utils.logger import setup_logging
from utils.file_manager import ensure_directories


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events for the FastAPI application."""

    # Startup
    logger = logging.getLogger(__name__)
    logger.info("Starting Oak Camera Interface Backend...")

    # Ensure required directories exist
    ensure_directories()

    # Initialize camera system (if available)
    try:
        from camera.oak_controller import OakController

        app.state.oak_controller = OakController()
        logger.info("Oak Camera controller initialized")
    except Exception as e:
        logger.warning(f"Camera not available: {e}")
        app.state.oak_controller = None

    yield

    # Shutdown
    logger.info("Shutting down Oak Camera Interface Backend...")
    if hasattr(app.state, "oak_controller") and app.state.oak_controller:
        await app.state.oak_controller.cleanup()


# Create FastAPI application
app = FastAPI(
    title="Oak Camera Interface API",
    description="REST API and WebSocket interface for Oak Camera v3 control",
    version="1.0.0",
    lifespan=lifespan,
)

# Setup CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(api_router, prefix="/api")
app.include_router(ws_router, prefix="/ws")

# Serve static files (recordings)
if os.path.exists(settings.RECORDINGS_PATH):
    app.mount(
        "/recordings",
        StaticFiles(directory=settings.RECORDINGS_PATH),
        name="recordings",
    )


# Health check endpoint
@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "camera_available": hasattr(app.state, "oak_controller")
        and app.state.oak_controller is not None,
    }


# Root endpoint
@app.get("/")
async def root():
    """Root endpoint with API information."""
    return {
        "message": "Oak Camera Interface API",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health",
    }


if __name__ == "__main__":
    # Setup logging
    setup_logging()

    # Run the server
    uvicorn.run(
        "main:app",
        host=settings.BACKEND_HOST,
        port=settings.BACKEND_PORT,
        reload=settings.DEBUG,
        log_level=settings.LOG_LEVEL.lower(),
    )
