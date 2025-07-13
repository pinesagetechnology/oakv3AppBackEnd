import asyncio
import json
import base64
import logging
from typing import Dict, Set
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from fastapi.websockets import WebSocketState

logger = logging.getLogger(__name__)
router = APIRouter()


class ConnectionManager:
    """Manages WebSocket connections."""

    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self.stream_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        """Connect a new WebSocket client."""
        await websocket.accept()
        self.active_connections.add(websocket)
        logger.info(
            f"📱 WebSocket client connected. Total: {len(self.active_connections)}"
        )

    def disconnect(self, websocket: WebSocket):
        """Disconnect a WebSocket client."""
        self.active_connections.discard(websocket)
        self.stream_connections.discard(websocket)
        logger.info(
            f"📱 WebSocket client disconnected. Total: {len(self.active_connections)}"
        )

    async def send_personal_message(self, websocket: WebSocket, message: dict):
        """Send a message to a specific client."""
        try:
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.send_text(json.dumps(message))
        except Exception as e:
            logger.debug(f"Failed to send personal message: {e}")
            self.disconnect(websocket)

    async def broadcast(self, message: dict, exclude: WebSocket = None):
        """Broadcast a message to all connected clients."""
        disconnected = set()

        for connection in self.active_connections:
            if connection != exclude:
                try:
                    if connection.client_state == WebSocketState.CONNECTED:
                        await connection.send_text(json.dumps(message))
                except Exception as e:
                    logger.debug(f"Failed to broadcast to client: {e}")
                    disconnected.add(connection)

        # Clean up disconnected clients
        for connection in disconnected:
            self.disconnect(connection)

    async def broadcast_frame(self, frame_data: bytes, timestamp: float):
        """Broadcast video frame to streaming clients."""
        if not self.stream_connections:
            return

        # Encode frame as base64
        frame_b64 = base64.b64encode(frame_data).decode("utf-8")

        message = {
            "type": "frame",
            "data": frame_b64,
            "timestamp": timestamp,
            "size": len(frame_data),
        }

        disconnected = set()

        for connection in self.stream_connections:
            try:
                if connection.client_state == WebSocketState.CONNECTED:
                    await connection.send_text(json.dumps(message))
            except Exception as e:
                logger.debug(f"Failed to send frame to client: {e}")
                disconnected.add(connection)

        # Clean up disconnected clients
        for connection in disconnected:
            self.disconnect(connection)

    def add_stream_connection(self, websocket: WebSocket):
        """Add a client to the streaming list."""
        self.stream_connections.add(websocket)
        logger.info(
            f"📹 Stream client added. Total streaming: {len(self.stream_connections)}"
        )

    def remove_stream_connection(self, websocket: WebSocket):
        """Remove a client from the streaming list."""
        self.stream_connections.discard(websocket)
        logger.info(
            f"📹 Stream client removed. Total streaming: {len(self.stream_connections)}"
        )


# Global connection manager
manager = ConnectionManager()


def get_oak_controller():
    """Dependency to get Oak controller from app state."""
    from main import app

    return getattr(app.state, "oak_controller", None)


async def setup_frame_callback(oak_controller):
    """Setup frame callback for streaming."""
    if oak_controller:
        oak_controller.set_frame_callback(manager.broadcast_frame)


@router.websocket("/control")
async def websocket_control_endpoint(
    websocket: WebSocket, oak_controller=Depends(get_oak_controller)
):
    """WebSocket endpoint for camera control and status updates."""
    await manager.connect(websocket)

    # Setup frame callback
    await setup_frame_callback(oak_controller)

    try:
        # Send initial status
        if oak_controller:
            status = oak_controller.get_status()
            settings = oak_controller.get_settings()

            await manager.send_personal_message(
                websocket,
                {
                    "type": "status",
                    "data": {
                        "camera_status": status.dict(),
                        "camera_settings": settings.dict(),
                    },
                },
            )

        while True:
            # Receive message from client
            data = await websocket.receive_text()
            message = json.loads(data)

            await handle_control_message(websocket, message, oak_controller)

    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"❌ WebSocket control error: {e}")
        manager.disconnect(websocket)


@router.websocket("/stream")
async def websocket_stream_endpoint(
    websocket: WebSocket, oak_controller=Depends(get_oak_controller)
):
    """WebSocket endpoint for video streaming."""
    await manager.connect(websocket)
    manager.add_stream_connection(websocket)

    # Setup frame callback
    await setup_frame_callback(oak_controller)

    try:
        # Send stream start message
        await manager.send_personal_message(
            websocket, {"type": "stream_start", "message": "Video stream connected"}
        )

        # Keep connection alive and handle client messages
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)

            # Handle stream control messages
            if message.get("type") == "ping":
                await manager.send_personal_message(
                    websocket, {"type": "pong", "timestamp": message.get("timestamp")}
                )

    except WebSocketDisconnect:
        manager.remove_stream_connection(websocket)
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"❌ WebSocket stream error: {e}")
        manager.remove_stream_connection(websocket)
        manager.disconnect(websocket)


async def handle_control_message(websocket: WebSocket, message: dict, oak_controller):
    """Handle control messages from WebSocket clients."""
    try:
        msg_type = message.get("type")

        if not oak_controller:
            await manager.send_personal_message(
                websocket,
                {"type": "error", "message": "Camera controller not available"},
            )
            return

        if msg_type == "connect":
            # Connect to camera
            ip_address = message.get("ip_address")
            if not ip_address:
                await manager.send_personal_message(
                    websocket, {"type": "error", "message": "IP address required"}
                )
                return

            success = await oak_controller.connect(ip_address)

            response = {
                "type": "connection_result",
                "success": success,
                "message": f"{'Connected to' if success else 'Failed to connect to'} camera at {ip_address}",
            }

            if success:
                response["data"] = {
                    "camera_status": oak_controller.get_status().dict(),
                    "camera_settings": oak_controller.get_settings().dict(),
                }

            await manager.send_personal_message(websocket, response)

            # Broadcast connection status to all clients
            await manager.broadcast(
                {
                    "type": "camera_status_update",
                    "connected": success,
                    "ip_address": ip_address if success else None,
                },
                exclude=websocket,
            )

        elif msg_type == "disconnect":
            # Disconnect from camera
            await oak_controller.disconnect()

            await manager.send_personal_message(
                websocket,
                {
                    "type": "connection_result",
                    "success": True,
                    "message": "Disconnected from camera",
                },
            )

            # Broadcast disconnection to all clients
            await manager.broadcast(
                {
                    "type": "camera_status_update",
                    "connected": False,
                    "ip_address": None,
                },
                exclude=websocket,
            )

        elif msg_type == "update_settings":
            # Update camera settings
            settings_data = message.get("settings")
            if not settings_data:
                await manager.send_personal_message(
                    websocket, {"type": "error", "message": "Settings data required"}
                )
                return

            try:
                from camera.camera_settings import CameraSettings

                settings = CameraSettings(**settings_data)
                success = await oak_controller.update_settings(settings)

                response = {
                    "type": "settings_update_result",
                    "success": success,
                    "message": (
                        "Settings updated" if success else "Failed to update settings"
                    ),
                }

                if success:
                    response["data"] = settings.dict()

                    # Broadcast settings update to other clients
                    await manager.broadcast(
                        {"type": "settings_updated", "settings": settings.dict()},
                        exclude=websocket,
                    )

                await manager.send_personal_message(websocket, response)

            except Exception as e:
                await manager.send_personal_message(
                    websocket,
                    {"type": "error", "message": f"Invalid settings data: {str(e)}"},
                )

        elif msg_type == "start_recording":
            # Start recording
            codec = message.get("codec", "h264")
            filename = await oak_controller.start_recording(codec=codec)

            success = filename is not None
            response = {
                "type": "recording_result",
                "action": "start",
                "success": success,
                "message": f"Recording {'started' if success else 'failed'}",
                "filename": filename,
            }

            await manager.send_personal_message(websocket, response)

            # Broadcast recording status
            await manager.broadcast(
                {
                    "type": "recording_status_update",
                    "recording": success,
                    "filename": filename,
                },
                exclude=websocket,
            )

        elif msg_type == "stop_recording":
            # Stop recording
            filename = await oak_controller.stop_recording()

            success = filename is not None
            response = {
                "type": "recording_result",
                "action": "stop",
                "success": success,
                "message": f"Recording {'stopped' if success else 'not active'}",
                "filename": filename,
            }

            await manager.send_personal_message(websocket, response)

            # Broadcast recording status
            await manager.broadcast(
                {
                    "type": "recording_status_update",
                    "recording": False,
                    "filename": filename,
                },
                exclude=websocket,
            )

        elif msg_type == "capture_image":
            # Capture image
            filename = await oak_controller.capture_image()

            success = filename is not None
            response = {
                "type": "capture_result",
                "success": success,
                "message": f"Image {'captured' if success else 'capture failed'}",
                "filename": filename,
            }

            await manager.send_personal_message(websocket, response)

        elif msg_type == "trigger_autofocus":
            # Trigger autofocus
            success = await oak_controller.trigger_autofocus()

            await manager.send_personal_message(
                websocket,
                {
                    "type": "autofocus_result",
                    "success": success,
                    "message": "Autofocus triggered" if success else "Autofocus failed",
                },
            )

        elif msg_type == "set_manual_focus":
            # Set manual focus
            position = message.get("position")
            if position is None:
                await manager.send_personal_message(
                    websocket, {"type": "error", "message": "Focus position required"}
                )
                return

            success = await oak_controller.set_manual_focus(position)

            response = {
                "type": "focus_result",
                "success": success,
                "message": (
                    f"Focus set to {position}" if success else "Failed to set focus"
                ),
                "position": position,
            }

            await manager.send_personal_message(websocket, response)

            # Broadcast focus change
            if success:
                await manager.broadcast(
                    {"type": "focus_updated", "position": position}, exclude=websocket
                )

        elif msg_type == "get_status":
            # Get current status
            status = oak_controller.get_status()
            settings = oak_controller.get_settings()

            await manager.send_personal_message(
                websocket,
                {
                    "type": "status",
                    "data": {
                        "camera_status": status.dict(),
                        "camera_settings": settings.dict(),
                    },
                },
            )

        elif msg_type == "ping":
            # Ping/pong for connection keepalive
            await manager.send_personal_message(
                websocket, {"type": "pong", "timestamp": message.get("timestamp")}
            )

        else:
            await manager.send_personal_message(
                websocket,
                {"type": "error", "message": f"Unknown message type: {msg_type}"},
            )

    except Exception as e:
        logger.error(f"❌ Error handling control message: {e}")
        await manager.send_personal_message(
            websocket, {"type": "error", "message": f"Internal error: {str(e)}"}
        )


# Periodic status updates
async def periodic_status_updates(oak_controller):
    """Send periodic status updates to all connected clients."""
    while True:
        try:
            if oak_controller and manager.active_connections:
                status = oak_controller.get_status()

                await manager.broadcast(
                    {
                        "type": "status_update",
                        "data": {
                            "camera_status": status.dict(),
                            "timestamp": asyncio.get_event_loop().time(),
                        },
                    }
                )

            await asyncio.sleep(5)  # Update every 5 seconds

        except Exception as e:
            logger.error(f"❌ Error in periodic status updates: {e}")
            await asyncio.sleep(10)


# Start periodic updates task
@router.on_event("startup")
async def start_periodic_updates():
    """Start the periodic status updates task."""
    from main import app

    oak_controller = getattr(app.state, "oak_controller", None)

    if oak_controller:
        asyncio.create_task(periodic_status_updates(oak_controller))
