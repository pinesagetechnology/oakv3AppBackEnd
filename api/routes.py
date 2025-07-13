import asyncio
import os
from typing import List
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
import logging

from .models import *
from camera.camera_settings import CameraSettings
from utils.file_manager import (
    get_recordings_list,
    delete_file,
    get_disk_usage,
    cleanup_old_files,
)
from utils.helpers import create_response, get_system_info, discover_cameras
from config import settings

logger = logging.getLogger(__name__)
router = APIRouter()


def get_oak_controller():
    """Dependency to get Oak controller from app state."""
    from main import app

    return getattr(app.state, "oak_controller", None)


@router.get("/status", response_model=SystemStatusResponse)
async def get_system_status(oak_controller=Depends(get_oak_controller)):
    """Get system and camera status."""
    try:
        # Get camera status
        camera_status = (
            oak_controller.get_status() if oak_controller else CameraStatus()
        )

        # Get system info
        system_info = get_system_info()

        # Get disk usage
        disk_usage = get_disk_usage()

        # Calculate uptime (simplified)
        uptime = int(asyncio.get_event_loop().time())

        return SystemStatusResponse(
            camera_connected=camera_status.connected,
            camera_status=camera_status,
            system_info=system_info,
            disk_usage=disk_usage,
            uptime=uptime,
        )

    except Exception as e:
        logger.error(f"❌ Error getting system status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/camera/connect", response_model=ConnectionResponse)
async def connect_camera(
    request: ConnectionRequest, oak_controller=Depends(get_oak_controller)
):
    """Connect to Oak Camera."""
    try:
        if not oak_controller:
            raise HTTPException(
                status_code=503, detail="Camera controller not available"
            )

        # Attempt connection
        success = await oak_controller.connect(request.ip_address)

        if success:
            camera_status = oak_controller.get_status()
            return ConnectionResponse(
                success=True,
                message=f"Successfully connected to camera at {request.ip_address}",
                camera_status=camera_status,
            )
        else:
            return ConnectionResponse(
                success=False,
                message=f"Failed to connect to camera at {request.ip_address}",
                camera_status=None,
            )

    except Exception as e:
        logger.error(f"❌ Error connecting to camera: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/camera/disconnect")
async def disconnect_camera(oak_controller=Depends(get_oak_controller)):
    """Disconnect from Oak Camera."""
    try:
        if not oak_controller:
            raise HTTPException(
                status_code=503, detail="Camera controller not available"
            )

        await oak_controller.disconnect()

        return create_response(success=True, message="Camera disconnected successfully")

    except Exception as e:
        logger.error(f"❌ Error disconnecting camera: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/camera/discover")
async def discover_cameras_endpoint():
    """Discover Oak cameras on the network."""
    try:
        cameras = discover_cameras()
        return create_response(
            success=True,
            message=f"Found {len(cameras)} cameras",
            data={"cameras": cameras},
        )

    except Exception as e:
        logger.error(f"❌ Error discovering cameras: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/camera/settings", response_model=CameraSettings)
async def get_camera_settings(oak_controller=Depends(get_oak_controller)):
    """Get current camera settings."""
    try:
        if not oak_controller:
            raise HTTPException(
                status_code=503, detail="Camera controller not available"
            )

        return oak_controller.get_settings()

    except Exception as e:
        logger.error(f"❌ Error getting camera settings: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/camera/settings")
async def update_camera_settings(
    request: SettingsUpdateRequest, oak_controller=Depends(get_oak_controller)
):
    """Update camera settings."""
    try:
        if not oak_controller:
            raise HTTPException(
                status_code=503, detail="Camera controller not available"
            )

        success = await oak_controller.update_settings(request.settings)

        if success:
            return create_response(
                success=True,
                message="Camera settings updated successfully",
                data=request.settings.dict(),
            )
        else:
            raise HTTPException(
                status_code=400, detail="Failed to update camera settings"
            )

    except Exception as e:
        logger.error(f"❌ Error updating camera settings: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/camera/focus/trigger")
async def trigger_autofocus(oak_controller=Depends(get_oak_controller)):
    """Trigger autofocus."""
    try:
        if not oak_controller:
            raise HTTPException(
                status_code=503, detail="Camera controller not available"
            )

        success = await oak_controller.trigger_autofocus()

        return create_response(
            success=success,
            message="Autofocus triggered" if success else "Failed to trigger autofocus",
        )

    except Exception as e:
        logger.error(f"❌ Error triggering autofocus: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/camera/focus/manual/{position}")
async def set_manual_focus(position: int, oak_controller=Depends(get_oak_controller)):
    """Set manual focus position."""
    try:
        if not oak_controller:
            raise HTTPException(
                status_code=503, detail="Camera controller not available"
            )

        if not (0 <= position <= 255):
            raise HTTPException(
                status_code=400, detail="Focus position must be between 0 and 255"
            )

        success = await oak_controller.set_manual_focus(position)

        return create_response(
            success=success,
            message=(
                f"Manual focus set to {position}"
                if success
                else "Failed to set manual focus"
            ),
            data={"position": position},
        )

    except Exception as e:
        logger.error(f"❌ Error setting manual focus: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/recording/start", response_model=RecordingResponse)
async def start_recording(
    request: RecordingRequest, oak_controller=Depends(get_oak_controller)
):
    """Start video recording."""
    try:
        if not oak_controller:
            raise HTTPException(
                status_code=503, detail="Camera controller not available"
            )

        filename = await oak_controller.start_recording(codec=request.codec)

        if filename:
            return RecordingResponse(
                success=True,
                message="Recording started successfully",
                filename=filename,
                file_path=f"/recordings/videos/{filename}",
            )
        else:
            return RecordingResponse(success=False, message="Failed to start recording")

    except Exception as e:
        logger.error(f"❌ Error starting recording: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/recording/stop", response_model=RecordingResponse)
async def stop_recording(oak_controller=Depends(get_oak_controller)):
    """Stop video recording."""
    try:
        if not oak_controller:
            raise HTTPException(
                status_code=503, detail="Camera controller not available"
            )

        filename = await oak_controller.stop_recording()

        if filename:
            return RecordingResponse(
                success=True,
                message="Recording stopped successfully",
                filename=filename,
                file_path=f"/recordings/videos/{filename}",
            )
        else:
            return RecordingResponse(success=False, message="No recording in progress")

    except Exception as e:
        logger.error(f"❌ Error stopping recording: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/capture", response_model=RecordingResponse)
async def capture_image(oak_controller=Depends(get_oak_controller)):
    """Capture a single image."""
    try:
        if not oak_controller:
            raise HTTPException(
                status_code=503, detail="Camera controller not available"
            )

        filename = await oak_controller.capture_image()

        if filename:
            return RecordingResponse(
                success=True,
                message="Image captured successfully",
                filename=filename,
                file_path=f"/recordings/images/{filename}",
            )
        else:
            return RecordingResponse(success=False, message="Failed to capture image")

    except Exception as e:
        logger.error(f"❌ Error capturing image: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/files", response_model=FilesListResponse)
async def get_files_list():
    """Get list of recorded files."""
    try:
        recordings = get_recordings_list()

        # Convert to FileInfo models
        videos = [
            FileInfo(
                filename=item["filename"],
                size=item["size"],
                created=datetime.fromisoformat(item["created"]),
                modified=datetime.fromisoformat(item["modified"]),
                path=item["path"],
                type="video",
            )
            for item in recordings["videos"]
        ]

        images = [
            FileInfo(
                filename=item["filename"],
                size=item["size"],
                created=datetime.fromisoformat(item["created"]),
                modified=datetime.fromisoformat(item["modified"]),
                path=item["path"],
                type="image",
            )
            for item in recordings["images"]
        ]

        total_count = len(videos) + len(images)
        total_size = sum(item.size for item in videos + images)

        return FilesListResponse(
            videos=videos, images=images, total_count=total_count, total_size=total_size
        )

    except Exception as e:
        logger.error(f"❌ Error getting files list: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/files/{file_type}/{filename}")
async def delete_file_endpoint(file_type: str, filename: str):
    """Delete a specific file."""
    try:
        if file_type not in ["videos", "images"]:
            raise HTTPException(status_code=400, detail="Invalid file type")

        file_path = f"{file_type}/{filename}"
        success = delete_file(file_path)

        return create_response(
            success=success,
            message=(
                f"File deleted: {filename}"
                if success
                else f"Failed to delete file: {filename}"
            ),
        )

    except Exception as e:
        logger.error(f"❌ Error deleting file: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/files/cleanup")
async def cleanup_files(background_tasks: BackgroundTasks):
    """Clean up old files."""
    try:
        background_tasks.add_task(cleanup_old_files)

        return create_response(
            success=True, message="File cleanup started in background"
        )

    except Exception as e:
        logger.error(f"❌ Error starting cleanup: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/download/{file_type}/{filename}")
async def download_file(file_type: str, filename: str):
    """Download a specific file."""
    try:
        if file_type not in ["videos", "images"]:
            raise HTTPException(status_code=400, detail="Invalid file type")

        file_path = os.path.join(settings.RECORDINGS_PATH, file_type, filename)

        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="File not found")

        return FileResponse(
            path=file_path, filename=filename, media_type="application/octet-stream"
        )

    except Exception as e:
        logger.error(f"❌ Error downloading file: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/camera/discover")
async def discover_cameras_endpoint():
    """Discover Oak cameras on the network with detailed info."""
    try:
        import depthai as dai

        logger.info("🔍 Starting camera discovery...")
        devices = dai.Device.getAllAvailableDevices()

        discovered_cameras = []

        for device_info in devices:
            camera_data = {
                "name": device_info.name,
                "mxid": device_info.getMxId(),
                "state": str(device_info.state),
                "protocol": str(device_info.protocol),
            }

            # For PoE cameras, name is the IP address
            if device_info.protocol == dai.XLinkProtocol.X_LINK_TCP_IP:
                camera_data["ip_address"] = device_info.name
                camera_data["connection_type"] = "PoE/Ethernet"
                discovered_cameras.append(camera_data["ip_address"])
            else:
                camera_data["connection_type"] = "USB"

            logger.info(f"📹 Found device: {camera_data}")

        return create_response(
            success=True,
            message=f"Found {len(discovered_cameras)} PoE cameras",
            data={
                "cameras": discovered_cameras,
                "all_devices": [
                    {
                        "name": d.name,
                        "mxid": d.getMxId(),
                        "state": str(d.state),
                        "protocol": str(d.protocol),
                    }
                    for d in devices
                ],
            },
        )

    except Exception as e:
        logger.error(f"❌ Error discovering cameras: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/camera/debug/{ip_address}")
async def debug_camera_connection(ip_address: str):
    """Debug camera connection with detailed diagnostics."""
    import socket
    import subprocess
    import platform
    import depthai as dai

    debug_info = {
        "ip_address": ip_address,
        "timestamp": datetime.now().isoformat(),
        "tests": {},
    }

    # Test 1: Basic ping
    try:
        if platform.system().lower() == "windows":
            cmd = ["ping", "-n", "1", "-w", "3000", ip_address]
        else:
            cmd = ["ping", "-c", "1", "-W", "3", ip_address]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        debug_info["tests"]["ping"] = {
            "success": result.returncode == 0,
            "response_time": "Available in output",
            "output": (
                result.stdout[:200] if result.returncode == 0 else result.stderr[:200]
            ),
        }
    except Exception as e:
        debug_info["tests"]["ping"] = {"success": False, "error": str(e)}

    # Test 2: Port connectivity
    try:
        sock = socket.create_connection((ip_address, 9876), timeout=10)
        sock.close()
        debug_info["tests"]["port_9876"] = {
            "success": True,
            "message": "Port 9876 is open and accepting connections",
        }
    except Exception as e:
        debug_info["tests"]["port_9876"] = {"success": False, "error": str(e)}

    # Test 3: DepthAI device discovery
    try:
        devices = dai.Device.getAllAvailableDevices()
        target_device = None

        for device_info in devices:
            if device_info.name == ip_address:
                target_device = {
                    "found": True,
                    "name": device_info.name,
                    "mxid": device_info.getMxId(),
                    "state": str(device_info.state),
                    "protocol": str(device_info.protocol),
                }
                break

        if target_device:
            debug_info["tests"]["device_discovery"] = target_device
        else:
            debug_info["tests"]["device_discovery"] = {
                "found": False,
                "available_devices": [d.name for d in devices],
            }

    except Exception as e:
        debug_info["tests"]["device_discovery"] = {"success": False, "error": str(e)}

    # Test 4: Try to connect with DepthAI
    try:
        device_info = dai.DeviceInfo(ip_address)
        device = dai.Device(device_info)

        debug_info["tests"]["depthai_connection"] = {
            "success": True,
            "device_name": device.getDeviceName(),
            "platform": device.getPlatform().name,
            "message": "Successfully connected and got device info",
        }
        device.close()

    except Exception as e:
        debug_info["tests"]["depthai_connection"] = {"success": False, "error": str(e)}

    return debug_info
