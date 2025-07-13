import os
import shutil
import asyncio
import aiofiles
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional
import logging

from config import settings


logger = logging.getLogger(__name__)


def ensure_directories():
    """Ensure all required directories exist."""
    directories = [
        settings.RECORDINGS_PATH,
        os.path.join(settings.RECORDINGS_PATH, "videos"),
        os.path.join(settings.RECORDINGS_PATH, "images"),
        "logs",
        "tmp",
    ]

    for directory in directories:
        os.makedirs(directory, exist_ok=True)
        logger.debug(f"📁 Ensured directory exists: {directory}")


def get_file_size(file_path: str) -> int:
    """Get file size in bytes."""
    try:
        return os.path.getsize(file_path)
    except OSError:
        return 0


def get_available_space(path: str) -> int:
    """Get available disk space in bytes."""
    try:
        statvfs = os.statvfs(path)
        return statvfs.f_frsize * statvfs.f_bavail
    except OSError:
        return 0


def generate_filename(extension: str, prefix: str = "oak") -> str:
    """Generate a unique filename with timestamp."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{timestamp}.{extension}"


def get_recordings_list() -> Dict[str, List[Dict]]:
    """Get list of all recordings with metadata."""
    recordings = {"videos": [], "images": []}

    try:
        # Get videos
        videos_path = os.path.join(settings.RECORDINGS_PATH, "videos")
        if os.path.exists(videos_path):
            for file in os.listdir(videos_path):
                if file.lower().endswith((".mp4", ".avi", ".mov", ".mkv")):
                    file_path = os.path.join(videos_path, file)
                    stat = os.stat(file_path)
                    recordings["videos"].append(
                        {
                            "filename": file,
                            "size": stat.st_size,
                            "created": datetime.fromtimestamp(
                                stat.st_ctime
                            ).isoformat(),
                            "modified": datetime.fromtimestamp(
                                stat.st_mtime
                            ).isoformat(),
                            "path": f"/recordings/videos/{file}",
                        }
                    )

        # Get images
        images_path = os.path.join(settings.RECORDINGS_PATH, "images")
        if os.path.exists(images_path):
            for file in os.listdir(images_path):
                if file.lower().endswith((".jpg", ".jpeg", ".png", ".bmp")):
                    file_path = os.path.join(images_path, file)
                    stat = os.stat(file_path)
                    recordings["images"].append(
                        {
                            "filename": file,
                            "size": stat.st_size,
                            "created": datetime.fromtimestamp(
                                stat.st_ctime
                            ).isoformat(),
                            "modified": datetime.fromtimestamp(
                                stat.st_mtime
                            ).isoformat(),
                            "path": f"/recordings/images/{file}",
                        }
                    )

        # Sort by creation time (newest first)
        recordings["videos"].sort(key=lambda x: x["created"], reverse=True)
        recordings["images"].sort(key=lambda x: x["created"], reverse=True)

    except Exception as e:
        logger.error(f"❌ Error getting recordings list: {e}")

    return recordings


def cleanup_old_files():
    """Clean up old recordings based on AUTO_CLEANUP_DAYS setting."""
    if settings.AUTO_CLEANUP_DAYS <= 0:
        return

    cutoff_date = datetime.now() - timedelta(days=settings.AUTO_CLEANUP_DAYS)
    deleted_count = 0

    try:
        for root, dirs, files in os.walk(settings.RECORDINGS_PATH):
            for file in files:
                file_path = os.path.join(root, file)
                file_stat = os.stat(file_path)
                file_date = datetime.fromtimestamp(file_stat.st_mtime)

                if file_date < cutoff_date:
                    os.remove(file_path)
                    deleted_count += 1
                    logger.info(f"🗑️ Deleted old file: {file}")

        if deleted_count > 0:
            logger.info(f"🧹 Cleaned up {deleted_count} old files")

    except Exception as e:
        logger.error(f"❌ Error during cleanup: {e}")


def delete_file(file_path: str) -> bool:
    """Delete a specific file."""
    try:
        full_path = os.path.join(settings.RECORDINGS_PATH, file_path.lstrip("/"))
        if os.path.exists(full_path):
            os.remove(full_path)
            logger.info(f"🗑️ Deleted file: {file_path}")
            return True
    except Exception as e:
        logger.error(f"❌ Error deleting file {file_path}: {e}")
    return False


async def save_uploaded_file(file_data: bytes, filename: str, file_type: str) -> str:
    """Save uploaded file data to disk."""
    try:
        # Determine subdirectory based on file type
        if file_type.startswith("video/"):
            subdir = "videos"
        elif file_type.startswith("image/"):
            subdir = "images"
        else:
            subdir = "others"

        # Create full path
        file_dir = os.path.join(settings.RECORDINGS_PATH, subdir)
        os.makedirs(file_dir, exist_ok=True)
        file_path = os.path.join(file_dir, filename)

        # Save file asynchronously
        async with aiofiles.open(file_path, "wb") as f:
            await f.write(file_data)

        logger.info(f"💾 Saved file: {file_path}")
        return f"/{subdir}/{filename}"

    except Exception as e:
        logger.error(f"❌ Error saving file {filename}: {e}")
        raise


def get_disk_usage() -> Dict[str, int]:
    """Get disk usage statistics."""
    try:
        total, used, free = shutil.disk_usage(settings.RECORDINGS_PATH)
        return {
            "total": total,
            "used": used,
            "free": free,
            "percent_used": round((used / total) * 100, 2),
        }
    except Exception as e:
        logger.error(f"❌ Error getting disk usage: {e}")
        return {"total": 0, "used": 0, "free": 0, "percent_used": 0}
