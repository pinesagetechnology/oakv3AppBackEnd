from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime

from camera.camera_settings import CameraSettings, CameraStatus


class ConnectionRequest(BaseModel):
    """Request model for camera connection."""

    ip_address: str = Field(..., description="Camera IP address")
    timeout: int = Field(default=10, description="Connection timeout in seconds")


class ConnectionResponse(BaseModel):
    """Response model for camera connection."""

    success: bool
    message: str
    camera_status: Optional[CameraStatus] = None


class SettingsUpdateRequest(BaseModel):
    """Request model for updating camera settings."""

    settings: CameraSettings


class RecordingRequest(BaseModel):
    """Request model for starting recording."""

    codec: str = Field(default="h264", description="Video codec (h264, h265, mjpeg)")
    filename: Optional[str] = Field(
        default=None, description="Optional custom filename"
    )


class RecordingResponse(BaseModel):
    """Response model for recording operations."""

    success: bool
    message: str
    filename: Optional[str] = None
    file_path: Optional[str] = None


class FileInfo(BaseModel):
    """Model for file information."""

    filename: str
    size: int
    created: datetime
    modified: datetime
    path: str
    type: str  # 'video' or 'image'


class FilesListResponse(BaseModel):
    """Response model for files list."""

    videos: List[FileInfo]
    images: List[FileInfo]
    total_count: int
    total_size: int


class SystemStatusResponse(BaseModel):
    """Response model for system status."""

    camera_connected: bool
    camera_status: CameraStatus
    system_info: Dict[str, Any]
    disk_usage: Dict[str, Any]
    uptime: int


class ErrorResponse(BaseModel):
    """Error response model."""

    error: str
    details: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.now)
