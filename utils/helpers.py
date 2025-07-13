import asyncio
import socket
import subprocess
import platform
from typing import Dict, Any, Optional, List
import logging

logger = logging.getLogger(__name__)


def is_port_open(host: str, port: int, timeout: float = 3.0) -> bool:
    """Check if a port is open on a given host."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (socket.timeout, socket.error):
        return False


def ping_host(host: str, timeout: int = 3) -> bool:
    """Ping a host to check if it's reachable."""
    try:
        # Use platform-appropriate ping command
        if platform.system().lower() == "windows":
            cmd = ["ping", "-n", "1", "-w", str(timeout * 1000), host]
        else:
            cmd = ["ping", "-c", "1", "-W", str(timeout), host]

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout + 1
        )
        return result.returncode == 0

    except Exception as e:
        logger.debug(f"Ping failed for {host}: {e}")
        return False


def discover_cameras(ip_range: str = "192.168.1", timeout: float = 1.0) -> List[str]:
    """Discover Oak cameras on the network."""
    discovered = []

    async def check_ip(ip: str):
        if is_port_open(ip, 9876, timeout):  # Default Oak camera port
            discovered.append(ip)

    async def scan_range():
        tasks = []
        for i in range(1, 255):
            ip = f"{ip_range}.{i}"
            tasks.append(check_ip(ip))

        await asyncio.gather(*tasks, return_exceptions=True)

    try:
        asyncio.run(scan_range())
    except Exception as e:
        logger.error(f"❌ Error during camera discovery: {e}")

    return discovered


def validate_ip_address(ip: str) -> bool:
    """Validate IP address format."""
    try:
        socket.inet_aton(ip)
        return True
    except socket.error:
        return False


def clamp(value: float, min_val: float, max_val: float) -> float:
    """Clamp a value between min and max."""
    return max(min_val, min(value, max_val))


def format_bytes(bytes_value: int) -> str:
    """Format bytes into human readable format."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if bytes_value < 1024.0:
            return f"{bytes_value:.2f} {unit}"
        bytes_value /= 1024.0
    return f"{bytes_value:.2f} PB"


def format_duration(seconds: int) -> str:
    """Format duration in seconds to HH:MM:SS."""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    seconds = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def create_response(
    success: bool = True,
    message: str = "",
    data: Any = None,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    """Create standardized API response."""
    response = {
        "success": success,
        "message": message,
        "timestamp": asyncio.get_event_loop().time(),
    }

    if data is not None:
        response["data"] = data

    if error:
        response["error"] = error

    return response


async def run_with_timeout(coro, timeout: float):
    """Run coroutine with timeout."""
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning(f"Operation timed out after {timeout} seconds")
        raise


def get_system_info() -> Dict[str, Any]:
    """Get system information."""
    try:
        return {
            "platform": platform.platform(),
            "python_version": platform.python_version(),
            "architecture": platform.architecture()[0],
            "processor": platform.processor(),
            "machine": platform.machine(),
            "node": platform.node(),
        }
    except Exception as e:
        logger.error(f"❌ Error getting system info: {e}")
        return {}
