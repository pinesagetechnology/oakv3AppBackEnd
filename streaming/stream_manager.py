import asyncio
import logging
from typing import Dict, Set, Optional, Callable
from dataclasses import dataclass, field
import time

logger = logging.getLogger(__name__)


@dataclass
class StreamClient:
    """Represents a streaming client."""

    id: str
    websocket: Optional[object] = None
    last_ping: float = field(default_factory=time.time)
    frame_count: int = 0
    bytes_sent: int = 0
    connected_at: float = field(default_factory=time.time)


class StreamManager:
    """Manages video streaming to multiple clients."""

    def __init__(self):
        self.clients: Dict[str, StreamClient] = {}
        self.is_streaming = False
        self.frame_callback: Optional[Callable] = None
        self.stats = {
            "total_frames": 0,
            "total_bytes": 0,
            "active_clients": 0,
            "uptime": time.time(),
        }

    def add_client(self, client_id: str, websocket=None) -> StreamClient:
        """Add a new streaming client."""
        client = StreamClient(id=client_id, websocket=websocket)
        self.clients[client_id] = client
        self.stats["active_clients"] = len(self.clients)

        logger.info(f"📹 Stream client added: {client_id}")
        return client

    def remove_client(self, client_id: str):
        """Remove a streaming client."""
        if client_id in self.clients:
            del self.clients[client_id]
            self.stats["active_clients"] = len(self.clients)
            logger.info(f"📹 Stream client removed: {client_id}")

    def get_client(self, client_id: str) -> Optional[StreamClient]:
        """Get a streaming client by ID."""
        return self.clients.get(client_id)

    def update_client_ping(self, client_id: str):
        """Update client last ping time."""
        if client_id in self.clients:
            self.clients[client_id].last_ping = time.time()

    async def broadcast_frame(self, frame_data: bytes, timestamp: float):
        """Broadcast frame to all connected clients."""
        if not self.clients:
            return

        self.stats["total_frames"] += 1
        self.stats["total_bytes"] += len(frame_data)

        # Remove stale clients (no ping for 30 seconds)
        current_time = time.time()
        stale_clients = [
            client_id
            for client_id, client in self.clients.items()
            if current_time - client.last_ping > 30
        ]

        for client_id in stale_clients:
            self.remove_client(client_id)

        # Update client stats
        for client in self.clients.values():
            client.frame_count += 1
            client.bytes_sent += len(frame_data)

        # Call frame callback if set
        if self.frame_callback:
            try:
                await self.frame_callback(frame_data, timestamp)
            except Exception as e:
                logger.debug(f"Frame callback error: {e}")

    def set_frame_callback(self, callback: Callable):
        """Set the frame callback function."""
        self.frame_callback = callback

    def get_stats(self) -> Dict:
        """Get streaming statistics."""
        current_time = time.time()
        uptime = current_time - self.stats["uptime"]

        return {
            "total_frames": self.stats["total_frames"],
            "total_bytes": self.stats["total_bytes"],
            "active_clients": self.stats["active_clients"],
            "uptime_seconds": uptime,
            "frames_per_second": (
                self.stats["total_frames"] / uptime if uptime > 0 else 0
            ),
            "bytes_per_second": self.stats["total_bytes"] / uptime if uptime > 0 else 0,
            "clients": {
                client_id: {
                    "frame_count": client.frame_count,
                    "bytes_sent": client.bytes_sent,
                    "connected_duration": current_time - client.connected_at,
                    "last_ping": current_time - client.last_ping,
                }
                for client_id, client in self.clients.items()
            },
        }

    def reset_stats(self):
        """Reset streaming statistics."""
        self.stats = {
            "total_frames": 0,
            "total_bytes": 0,
            "active_clients": len(self.clients),
            "uptime": time.time(),
        }

        # Reset client stats
        for client in self.clients.values():
            client.frame_count = 0
            client.bytes_sent = 0
            client.connected_at = time.time()


# Global stream manager instance
stream_manager = StreamManager()
