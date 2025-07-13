import asyncio
import logging
import threading
import time
import socket
from typing import Optional, Callable, Dict, Any, List
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

import depthai as dai
import cv2
import numpy as np

from .camera_settings import (
    CameraSettings,
    CameraStatus,
    camera_settings_to_dai_control,
)
from .video_encoder import VideoEncoder, ImageCapture
from config import settings
from utils.file_manager import generate_filename
from utils.helpers import validate_ip_address

logger = logging.getLogger(__name__)


class OakController:
    """Main controller for Oak Camera v3 operations."""

    def __init__(self):
        self.device: Optional[dai.Device] = None
        self.pipeline: Optional[dai.Pipeline] = None
        self.camera_ip: Optional[str] = None
        self.is_connected = False
        self.is_streaming = False

        # Camera components
        self.camera_node: Optional[dai.node.ColorCamera] = None
        self.video_encoder: Optional[dai.node.VideoEncoder] = None
        self.control_queue: Optional[dai.DataInputQueue] = None
        self.video_queue: Optional[dai.DataOutputQueue] = None

        # Settings and status
        self.current_settings = CameraSettings()
        self.status = CameraStatus()

        # Recording
        self.video_recorder: Optional[VideoEncoder] = None
        self.recording_thread: Optional[threading.Thread] = None
        self.recording_stop_event = threading.Event()

        # Callbacks
        self.frame_callback: Optional[Callable] = None
        self.status_callback: Optional[Callable] = None

        # Threading
        self._stop_event = threading.Event()
        self._frame_thread: Optional[threading.Thread] = None

        # Thread pool for blocking operations
        self._executor = ThreadPoolExecutor(max_workers=2)

    def _is_port_open(self, host: str, port: int, timeout: float = 3.0) -> bool:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except (socket.timeout, socket.error):
            return False

    async def discover_cameras(self) -> List[str]:
        try:
            logger.info("Discovering Oak cameras...")
            def _discover_devices():
                try:
                    devices = dai.Device.getAllAvailableDevices()
                    discovered = []
                    for device_info in devices:
                        logger.info(f"Found device: {device_info.name}, Protocol: {device_info.protocol}, State: {device_info.state}")
                        if device_info.protocol == dai.XLinkProtocol.X_LINK_TCP_IP:
                            ip = device_info.name
                            state = device_info.state
                            logger.info(f"Found PoE camera: IP={ip}, State={state}")
                            discovered.append(ip)
                    return discovered
                except Exception as e:
                    logger.error(f"DepthAI discovery error: {e}")
                    return []
                    
            discovered_cameras = await asyncio.get_event_loop().run_in_executor(
                self._executor, _discover_devices
            )
            
            # Also try network discovery for cameras that might not be detected by DepthAI
            network_cameras = await self._discover_network_cameras()
            all_cameras = list(set(discovered_cameras + network_cameras))
            
            logger.info(f"Discovered {len(all_cameras)} Oak cameras: {all_cameras}")
            logger.info(f"DepthAI discovery: {discovered_cameras}")
            logger.info(f"Network discovery: {network_cameras}")
            
            return all_cameras
        except Exception as e:
            logger.error(f"Camera discovery failed: {e}")
            return []

    async def _discover_network_cameras(self, ip_range: str = "192.168.1") -> List[str]:
        discovered = []
        # Common IP ranges for OAK cameras
        ip_ranges = ["192.168.1", "192.168.0", "192.168.10", "10.0.0", "192.168.100"]
        
        async def check_ip(ip: str):
            try:
                # Check multiple ports that OAK Camera v3 might use
                ports_to_check = [9876, 14495, 14496, 14497]
                for port in ports_to_check:
                    future = asyncio.get_event_loop().run_in_executor(
                        self._executor, self._is_port_open, ip, port, 1.0
                    )
                    is_open = await asyncio.wait_for(future, timeout=2.0)
                    if is_open:
                        discovered.append(ip)
                        logger.info(f"Network discovery found camera at: {ip} (port {port})")
                        return  # Found one port, no need to check others
            except asyncio.TimeoutError:
                pass
            except Exception as e:
                logger.debug(f"Network check failed for {ip}: {e}")
                
        # Common IPs for OAK cameras
        common_ips = [
            "192.168.1.100", "192.168.1.247", "192.168.1.200",
            "192.168.0.100", "192.168.0.247", "192.168.0.200",
            "192.168.10.100", "192.168.10.247", "192.168.10.200",
            "10.0.0.100", "10.0.0.247", "10.0.0.200",
            "192.168.100.100", "192.168.100.247", "192.168.100.200",
        ]
        
        tasks = [check_ip(ip) for ip in common_ips]
        await asyncio.gather(*tasks, return_exceptions=True)
        return discovered

    async def connect(self, ip_address: str) -> bool:
        try:
            if not validate_ip_address(ip_address):
                logger.error(f"Invalid IP address: {ip_address}")
                return False
            if self.is_connected:
                await self.disconnect()
            
            logger.info(f"Attempting to connect to Oak Camera at {ip_address}")
            
            # First, try to ping the device to ensure basic connectivity
            ping_success = await self._ping_device(ip_address)
            if not ping_success:
                logger.warning(f"Device at {ip_address} is not responding to ping")
                # Continue anyway as some devices might not respond to ping
            
            # Check device state first before attempting connection
            device_state = await self._check_device_state(ip_address)
            if device_state:
                logger.info(f"Device state: {device_state}")
                if "X_LINK_GATE" in str(device_state):
                    logger.warning("Device is in GATE state - may need power cycle or initialization")
            
            # For OAK4-D-PRO-W, try connection even if ports appear closed
            # The device might be in a state where ports aren't immediately accessible
            ports_to_check = [9876, 14495, 14496, 14497]
            port_open = False
            working_port = None
            
            for port in ports_to_check:
                logger.info(f"Checking port {port} on {ip_address}")
                port_open = await asyncio.get_event_loop().run_in_executor(
                    self._executor, self._is_port_open, ip_address, port, 3.0
                )
                if port_open:
                    working_port = port
                    logger.info(f"Port {port} is open on {ip_address}")
                    break
                else:
                    logger.debug(f"Port {port} is not accessible on {ip_address}")
            
            # For OAK4-D-PRO-W, try connection even if ports are closed
            # The device might initialize ports during connection
            if not port_open:
                logger.warning(f"No accessible ports found on {ip_address}, but attempting connection anyway")
                logger.info("This is normal for OAK4-D-PRO-W in certain states")
            
            def _connect_device():
                try:
                    # Try different connection methods for OAK Camera v3
                    device_info = dai.DeviceInfo(ip_address)
                    logger.info(f"Creating device connection with info: {device_info}")
                    
                    # For OAK4-D-PRO-W, try with longer timeout and specific config
                    config = dai.Device.Config()
                    config.setLogLevel(dai.LogLevel.DEBUG)
                    
                    device = dai.Device(device_info, config)
                    
                    if not device:
                        raise Exception("Failed to create device connection")
                    
                    logger.info(f"Connected to device at {ip_address}")
                    return device
                except Exception as e:
                    logger.error(f"DepthAI connection failed: {e}")
                    # Try alternative connection method
                    try:
                        logger.info("Trying alternative connection method...")
                        device = dai.Device(ip_address)
                        if device:
                            logger.info(f"Alternative connection successful to {ip_address}")
                            return device
                    except Exception as e2:
                        logger.error(f"Alternative connection also failed: {e2}")
                    raise e
            
            self.device = await asyncio.get_event_loop().run_in_executor(
                self._executor, _connect_device
            )
            
            self.camera_ip = ip_address
            if not await self._setup_pipeline():
                logger.error("Failed to setup camera pipeline")
                return False
                
            def _start_pipeline():
                try:
                    self.device.start_pipeline(self.pipeline)
                    return True
                except Exception as e:
                    logger.error(f"Failed to start pipeline: {e}")
                    return False
                    
            success = await asyncio.get_event_loop().run_in_executor(
                self._executor, _start_pipeline
            )
            if not success:
                return False
                
            logger.info("Camera pipeline started")
            try:
                self.control_queue = self.device.get_input_queue("control")
                self.video_queue = self.device.get_output_queue("video")
            except Exception as e:
                logger.error(f"Failed to get queues: {e}")
                return False
                
            self.is_connected = True
            self.status.connected = True
            self.status.ip_address = ip_address
            self.status.device_info = self._get_device_info()
            self._start_frame_thread()
            logger.info(f"Successfully connected to Oak Camera at {ip_address}")
            await self.update_settings(self.current_settings)
            return True
            
        except Exception as e:
            logger.error(f"Failed to connect to camera at {ip_address}: {e}")
            await self.disconnect()
            return False

    async def _ping_device(self, ip_address: str) -> bool:
        """Ping device to check basic connectivity."""
        try:
            import subprocess
            import platform
            
            if platform.system().lower() == "windows":
                cmd = ["ping", "-n", "1", "-w", "3000", ip_address]
            else:
                cmd = ["ping", "-c", "1", "-W", "3", ip_address]
                
            result = await asyncio.get_event_loop().run_in_executor(
                self._executor, 
                lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            )
            return result.returncode == 0
        except Exception as e:
            logger.debug(f"Ping failed for {ip_address}: {e}")
            return False

    async def disconnect(self):
        try:
            logger.info("Disconnecting from Oak Camera...")
            if self.status.recording:
                await self.stop_recording()
            self._stop_frame_thread()
            if self.device:
                def _close_device():
                    try:
                        self.device.close()
                    except Exception as e:
                        logger.warning(f"Error closing device: {e}")
                await asyncio.get_event_loop().run_in_executor(
                    self._executor, _close_device
                )
                self.device = None
            self.is_connected = False
            self.is_streaming = False
            self.camera_ip = None
            self.pipeline = None
            self.camera_node = None
            self.video_encoder = None
            self.control_queue = None
            self.video_queue = None
            self.status = CameraStatus()
            logger.info("Disconnected from Oak Camera")
        except Exception as e:
            logger.error(f"Error during disconnect: {e}")

    async def _setup_pipeline(self) -> bool:
        try:
            logger.info("Setting up camera pipeline...")
            self.pipeline = dai.Pipeline()
            self.camera_node = self.pipeline.create(dai.node.ColorCamera)
            try:
                self.camera_node.set_board_socket(dai.CameraBoardSocket.CAM_A)
            except AttributeError:
                self.camera_node.setBoardSocket(dai.CameraBoardSocket.CAM_A)
            self.camera_node.setResolution(dai.ColorCameraProperties.SensorResolution.THE_1080_P)
            self.camera_node.setFps(self.current_settings.fps)
            xout = self.pipeline.create(dai.node.XLinkOut)
            xout.setStreamName("video")
            self.camera_node.video.link(xout.input)
            control_in = self.pipeline.create(dai.node.XLinkIn)
            control_in.setStreamName("control")
            control_in.out.link(self.camera_node.inputControl)
            logger.info("Camera pipeline setup complete")
            return True
        except Exception as e:
            logger.error(f"Failed to setup pipeline: {e}")
            return False

    def _get_device_info(self) -> Dict[str, Any]:
        if not self.device:
            return {}
        try:
            info = {
                "platform": self.device.get_platform().name,
                "device_name": self.device.get_device_name(),
            }
            try:
                info["product_name"] = self.device.get_product_name()
            except:
                pass
            try:
                info["platform_string"] = self.device.get_platform_as_string()
            except:
                pass
            return info
        except Exception as e:
            logger.debug(f"Could not get device info: {e}")
            return {"error": str(e)}

    def _start_frame_thread(self):
        self._stop_event.clear()
        self._frame_thread = threading.Thread(target=self._frame_processing_loop)
        self._frame_thread.daemon = True
        self._frame_thread.start()

    def _stop_frame_thread(self):
        self._stop_event.set()
        if self._frame_thread and self._frame_thread.is_alive():
            self._frame_thread.join(timeout=2.0)

    def _frame_processing_loop(self):
        while not self._stop_event.is_set():
            try:
                if not self.is_connected or not self.video_queue:
                    time.sleep(0.1)
                    continue
                frame_data = self.video_queue.get()
                if frame_data and self.frame_callback:
                    self.frame_callback(frame_data)
            except Exception as e:
                logger.error(f"Frame processing error: {e}")
                time.sleep(0.1)

    async def update_settings(self, settings: CameraSettings) -> bool:
        try:
            if not self.is_connected or not self.control_queue:
                return False
            control = camera_settings_to_dai_control(settings)
            def _send_control():
                try:
                    self.control_queue.send(control)
                    return True
                except Exception as e:
                    logger.error(f"Failed to send control: {e}")
                    return False
            success = await asyncio.get_event_loop().run_in_executor(
                self._executor, _send_control
            )
            if success:
                self.current_settings = settings
                logger.info("Camera settings updated")
            return success
        except Exception as e:
            logger.error(f"Failed to update settings: {e}")
            return False

    async def start_recording(self, codec: str = "h264") -> Optional[str]:
        try:
            if not self.is_connected:
                logger.error("Camera not connected")
                return None
            if self.status.recording:
                logger.warning("Recording already in progress")
                return None
            filename = generate_filename("recording", "mp4")
            filepath = f"{settings.RECORDINGS_PATH}/{filename}"
            self.video_recorder = VideoEncoder(
                filepath, self.current_settings.resolution_width, 
                self.current_settings.resolution_height, self.current_settings.fps
            )
            self.recording_stop_event.clear()
            self.recording_thread = threading.Thread(
                target=self._recording_loop, args=(filepath,)
            )
            self.recording_thread.daemon = True
            self.recording_thread.start()
            self.status.recording = True
            self.status.recording_file = filepath
            logger.info(f"Started recording: {filepath}")
            return filepath
        except Exception as e:
            logger.error(f"Failed to start recording: {e}")
            return None

    async def stop_recording(self) -> Optional[str]:
        try:
            if not self.status.recording:
                logger.warning("No recording in progress")
                return None
            self.recording_stop_event.set()
            if self.recording_thread:
                self.recording_thread.join(timeout=5.0)
            if self.video_recorder:
                self.video_recorder.close()
                self.video_recorder = None
            filepath = self.status.recording_file
            self.status.recording = False
            self.status.recording_file = None
            logger.info(f"Stopped recording: {filepath}")
            return filepath
        except Exception as e:
            logger.error(f"Failed to stop recording: {e}")
            return None

    async def capture_image(self) -> Optional[str]:
        try:
            if not self.is_connected:
                logger.error("Camera not connected")
                return None
            filename = generate_filename("capture", "jpg")
            filepath = f"{settings.RECORDINGS_PATH}/{filename}"
            def _get_frame():
                try:
                    if self.video_queue:
                        frame_data = self.video_queue.get()
                        if frame_data:
                            frame = frame_data.getCvFrame()
                            cv2.imwrite(filepath, frame)
                            return True
                    return False
                except Exception as e:
                    logger.error(f"Failed to capture image: {e}")
                    return False
            success = await asyncio.get_event_loop().run_in_executor(
                self._executor, _get_frame
            )
            if success:
                logger.info(f"Image captured: {filepath}")
                return filepath
            else:
                return None
        except Exception as e:
            logger.error(f"Failed to capture image: {e}")
            return None

    def set_frame_callback(self, callback: Callable):
        self.frame_callback = callback

    def set_status_callback(self, callback: Callable):
        self.status_callback = callback

    def get_status(self) -> CameraStatus:
        return self.status

    def get_settings(self) -> CameraSettings:
        return self.current_settings

    async def trigger_autofocus(self) -> bool:
        try:
            if not self.is_connected:
                return False
            def _trigger_af():
                try:
                    if self.control_queue:
                        control = dai.CameraControl()
                        control.setAutoFocusMode(dai.CameraControl.AutoFocusMode.AUTO)
                        self.control_queue.send(control)
                        return True
                    return False
                except Exception as e:
                    logger.error(f"Failed to trigger autofocus: {e}")
                    return False
            success = await asyncio.get_event_loop().run_in_executor(
                self._executor, _trigger_af
            )
            if success:
                logger.info("Autofocus triggered")
            return success
        except Exception as e:
            logger.error(f"Failed to trigger autofocus: {e}")
            return False

    async def set_manual_focus(self, position: int) -> bool:
        try:
            if not self.is_connected:
                return False
            def _set_focus():
                try:
                    if self.control_queue:
                        control = dai.CameraControl()
                        control.setAutoFocusMode(dai.CameraControl.AutoFocusMode.OFF)
                        control.setManualFocus(position)
                        self.control_queue.send(control)
                        return True
                    return False
                except Exception as e:
                    logger.error(f"Failed to set manual focus: {e}")
                    return False
            success = await asyncio.get_event_loop().run_in_executor(
                self._executor, _set_focus
            )
            if success:
                logger.info(f"Manual focus set to {position}")
            return success
        except Exception as e:
            logger.error(f"Failed to set manual focus: {e}")
            return False

    async def cleanup(self):
        try:
            await self.disconnect()
            self._executor.shutdown(wait=True)
            logger.info("OakController cleanup complete")
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")

    def _recording_loop(self, filepath: str):
        try:
            while not self.recording_stop_event.is_set():
                if self.video_queue and self.video_recorder:
                    frame_data = self.video_queue.get()
                    if frame_data:
                        frame = frame_data.getCvFrame()
                        self.video_recorder.write_frame(frame)
                else:
                    time.sleep(0.01)
        except Exception as e:
            logger.error(f"Recording loop error: {e}")

    async def _check_device_state(self, ip_address: str) -> str:
        """Check the current state of the device."""
        try:
            def _get_device_state():
                try:
                    devices = dai.Device.getAllAvailableDevices()
                    for device_info in devices:
                        if device_info.name == ip_address:
                            return str(device_info.state)
                    return None
                except Exception as e:
                    logger.debug(f"Could not get device state: {e}")
                    return None
            
            return await asyncio.get_event_loop().run_in_executor(
                self._executor, _get_device_state
            )
        except Exception as e:
            logger.debug(f"Device state check failed: {e}")
            return None

    def __del__(self):
        try:
            asyncio.create_task(self.cleanup())
        except:
            pass
