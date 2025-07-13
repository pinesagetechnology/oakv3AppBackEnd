import asyncio
import logging
import threading
import time
import os
from typing import Optional, Callable, Dict, Any, List
from datetime import datetime, timedelta
from pathlib import Path

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
from utils.helpers import validate_ip_address, is_port_open

logger = logging.getLogger(__name__)


class OakController:
    """Complete controller for Oak Camera v3 operations."""

    def __init__(self):
        self.device: Optional[dai.Device] = None
        self.pipeline: Optional[dai.Pipeline] = None
        self.camera_ip: Optional[str] = None
        self.is_connected = False
        self.is_streaming = False

        # Camera components
        self.camera_node: Optional[dai.node.Camera] = None
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

        # Performance tracking
        self._frame_count = 0
        self._last_fps_time = time.time()
        self._current_fps = 0.0

    async def discover_cameras(self) -> List[Dict[str, Any]]:
        """Discover available Oak cameras on the network."""
        try:
            logger.info("🔍 Discovering Oak cameras...")
            devices = dai.Device.getAllAvailableDevices()

            discovered = []
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
                    discovered.append(camera_data)

                    logger.info(
                        f"📹 Found PoE camera: IP={device_info.name}, "
                        f"MxId={device_info.getMxId()}, State={device_info.state}"
                    )
                else:
                    camera_data["connection_type"] = "USB"
                    logger.info(f"📹 Found USB camera: {device_info.name}")

            logger.info(f"✅ Discovered {len(discovered)} Oak cameras")
            return discovered

        except Exception as e:
            logger.error(f"❌ Camera discovery failed: {e}")
            return []

    async def connect(self, ip_address: str) -> bool:
        """Connect to Oak Camera v3 over PoE with comprehensive error handling."""
        try:
            # Validate IP address
            if not validate_ip_address(ip_address):
                logger.error(f"❌ Invalid IP address: {ip_address}")
                return False

            # Disconnect if already connected
            if self.is_connected:
                await self.disconnect()

            logger.info(f"🔌 Attempting to connect to Oak Camera at {ip_address}")

            # Step 1: Check if device is discoverable
            devices = dai.Device.getAllAvailableDevices()
            target_device_info = None
            device_state = None

            for device_info in devices:
                if (
                    device_info.name == ip_address
                    and device_info.protocol == dai.XLinkProtocol.X_LINK_TCP_IP
                ):
                    target_device_info = device_info
                    device_state = device_info.state
                    logger.info(
                        f"📹 Found target device: {device_info.name}, "
                        f"State: {device_info.state}, MxId: {device_info.getMxId()}"
                    )
                    break

            # Step 2: Create device info if not discovered
            if not target_device_info:
                logger.info(
                    f"🔧 Device not in discovery list, creating manual connection for {ip_address}"
                )
                target_device_info = dai.DeviceInfo(ip_address)
            else:
                # Check device state
                if device_state == dai.XLinkDeviceState.X_LINK_BOOTED:
                    logger.info("✅ Device is booted and ready")
                elif device_state == dai.XLinkDeviceState.X_LINK_UNBOOTED:
                    logger.warning("⚠️ Device is unbooted, attempting to boot...")
                else:
                    logger.warning(f"⚠️ Device state: {device_state}")

            # Step 3: Create device connection with timeout
            logger.info("⚡ Creating device connection...")
            try:
                # Set a longer timeout for PoE connections
                self.device = dai.Device(target_device_info, usb2Mode=False)

                # Wait a moment for device to initialize
                await asyncio.sleep(1.0)

            except Exception as e:
                logger.error(f"❌ Failed to create device: {e}")

                # Try alternative connection method
                logger.info("🔄 Trying alternative connection method...")
                try:
                    # Create a simple DeviceInfo and try again
                    device_info = dai.DeviceInfo()
                    device_info.name = ip_address
                    device_info.protocol = dai.XLinkProtocol.X_LINK_TCP_IP
                    self.device = dai.Device(device_info)
                    await asyncio.sleep(1.0)
                except Exception as e2:
                    logger.error(f"❌ Alternative connection also failed: {e2}")
                    return False

            # Step 4: Verify device connection and get info
            if not self.device:
                raise Exception("Device object is None after creation")

            try:
                device_name = self.device.getDeviceName()
                platform = self.device.getPlatform().name
                logger.info(
                    f"✅ Connected to device: {device_name}, Platform: {platform}"
                )
            except Exception as e:
                logger.warning(
                    f"⚠️ Could not get device info: {e}, but connection seems successful"
                )

            self.camera_ip = ip_address

            # Step 5: Setup and start pipeline
            if not await self._setup_pipeline():
                logger.error("❌ Failed to setup camera pipeline")
                return False

            logger.info("🚀 Starting camera pipeline...")
            self.device.startPipeline(self.pipeline)

            # Wait for pipeline to start
            await asyncio.sleep(2.0)

            # Step 6: Get queue references
            try:
                self.control_queue = self.device.getInputQueue("control")
                self.video_queue = self.device.getOutputQueue(
                    "video", maxSize=4, blocking=False
                )
                logger.info("✅ Queues created successfully")
            except Exception as e:
                logger.error(f"❌ Failed to create queues: {e}")
                return False

            # Step 7: Update status
            self.is_connected = True
            self.status.connected = True
            self.status.ip_address = ip_address
            self.status.device_info = self._get_device_info()

            # Step 8: Start frame processing
            self._start_frame_thread()

            # Step 9: Apply current settings
            await asyncio.sleep(1.0)  # Let the system stabilize
            await self.update_settings(self.current_settings)

            logger.info(f"🎉 Successfully connected to Oak Camera at {ip_address}")
            return True

        except Exception as e:
            logger.error(f"❌ Failed to connect to camera at {ip_address}: {e}")
            await self.disconnect()
            return False

    async def disconnect(self):
        """Disconnect from the Oak Camera with proper cleanup."""
        try:
            logger.info("🔌 Disconnecting from Oak Camera...")

            # Stop recording if active
            if self.status.recording:
                await self.stop_recording()

            # Stop frame thread
            self._stop_frame_thread()

            # Close device
            if self.device:
                try:
                    self.device.close()
                    logger.info("✅ Device closed successfully")
                except Exception as e:
                    logger.warning(f"⚠️ Error closing device: {e}")
                finally:
                    self.device = None

            # Reset state
            self.is_connected = False
            self.is_streaming = False
            self.camera_ip = None
            self.pipeline = None
            self.camera_node = None
            self.video_encoder = None
            self.control_queue = None
            self.video_queue = None

            # Reset performance tracking
            self._frame_count = 0
            self._current_fps = 0.0

            # Update status
            self.status = CameraStatus()

            logger.info("✅ Disconnected from Oak Camera")

        except Exception as e:
            logger.error(f"❌ Error during disconnect: {e}")

    async def _setup_pipeline(self) -> bool:
        """Setup the DepthAI pipeline with robust configuration."""
        try:
            logger.info("🔧 Setting up camera pipeline...")
            self.pipeline = dai.Pipeline()

            # Create camera node
            self.camera_node = self.pipeline.create(dai.node.Camera)
            self.camera_node.setBoardSocket(dai.CameraBoardSocket.CAM_A)

            # Configure camera with error handling
            try:
                cam_out = self.camera_node.requestOutput(
                    size=(
                        self.current_settings.resolution_width,
                        self.current_settings.resolution_height,
                    ),
                    fps=self.current_settings.fps,
                    type=dai.ImgFrame.Type.BGR888p,
                )
                logger.info(
                    f"📹 Camera configured: {self.current_settings.resolution_width}x{self.current_settings.resolution_height}@{self.current_settings.fps}fps"
                )
            except Exception as e:
                logger.warning(
                    f"⚠️ Failed to set preferred resolution, using defaults: {e}"
                )
                cam_out = self.camera_node.requestOutput(
                    size=(1920, 1080),  # Fallback resolution
                    fps=30,
                    type=dai.ImgFrame.Type.BGR888p,
                )

            # Create video encoder
            self.video_encoder = self.pipeline.create(dai.node.VideoEncoder)

            # Configure encoder based on settings
            encoder_fps = min(self.current_settings.fps, 30)  # Limit encoder FPS
            try:
                self.video_encoder.setDefaultProfilePreset(
                    fps=encoder_fps, profile=dai.VideoEncoderProperties.Profile.MJPEG
                )
                logger.info(f"🎬 Video encoder configured: MJPEG @ {encoder_fps}fps")
            except Exception as e:
                logger.warning(f"⚠️ Encoder configuration failed: {e}")
                # Use basic configuration
                self.video_encoder.setDefaultProfilePreset(
                    30, dai.VideoEncoderProperties.Profile.MJPEG
                )

            # Link camera to encoder
            cam_out.link(self.video_encoder.input)

            # Create control input for camera settings
            control_in = self.pipeline.create(dai.node.XLinkIn)
            control_in.setStreamName("control")
            control_in.out.link(self.camera_node.inputControl)

            # Create video output for streaming
            video_out = self.pipeline.create(dai.node.XLinkOut)
            video_out.setStreamName("video")
            self.video_encoder.bitstream.link(video_out.input)

            logger.info("✅ Camera pipeline setup complete")
            return True

        except Exception as e:
            logger.error(f"❌ Failed to setup pipeline: {e}")
            return False

    def _get_device_info(self) -> Dict[str, Any]:
        """Get comprehensive device information."""
        if not self.device:
            return {}

        try:
            info = {}

            # Basic device info
            try:
                info["platform"] = self.device.getPlatform().name
            except:
                info["platform"] = "Unknown"

            try:
                info["device_name"] = self.device.getDeviceName()
            except:
                info["device_name"] = "Unknown"

            try:
                info["product_name"] = self.device.getProductName()
            except:
                info["product_name"] = "Oak Camera v3"

            try:
                info["platform_string"] = self.device.getPlatformAsString()
            except:
                pass

            # Connection info
            info["connection_type"] = "PoE/Ethernet"
            info["ip_address"] = self.camera_ip
            info["connected_at"] = datetime.now().isoformat()

            return info

        except Exception as e:
            logger.debug(f"Could not get complete device info: {e}")
            return {
                "error": str(e),
                "connection_type": "PoE/Ethernet",
                "ip_address": self.camera_ip,
            }

    def _start_frame_thread(self):
        """Start the frame processing thread with error handling."""
        try:
            self._stop_event.clear()
            self._frame_thread = threading.Thread(
                target=self._frame_processing_loop,
                daemon=True,
                name="OakFrameProcessor",
            )
            self._frame_thread.start()
            self.is_streaming = True
            self.status.streaming = True
            self._last_fps_time = time.time()
            logger.info("🎬 Frame processing thread started")
        except Exception as e:
            logger.error(f"❌ Failed to start frame thread: {e}")

    def _stop_frame_thread(self):
        """Stop the frame processing thread safely."""
        try:
            self._stop_event.set()
            if self._frame_thread and self._frame_thread.is_alive():
                self._frame_thread.join(timeout=5.0)
                if self._frame_thread.is_alive():
                    logger.warning("⚠️ Frame thread did not stop gracefully")
            self._frame_thread = None
            self.is_streaming = False
            self.status.streaming = False
            logger.info("🛑 Frame processing thread stopped")
        except Exception as e:
            logger.error(f"❌ Error stopping frame thread: {e}")

    def _frame_processing_loop(self):
        """Main frame processing loop with comprehensive error handling."""
        logger.info("🎬 Started frame processing loop")

        consecutive_errors = 0
        max_consecutive_errors = 10

        while not self._stop_event.is_set():
            try:
                if not self.video_queue:
                    time.sleep(0.1)
                    continue

                # Get encoded frame with timeout
                encoded_frame = self.video_queue.tryGet()
                if encoded_frame is None:
                    time.sleep(0.01)  # Small delay to prevent busy waiting
                    continue

                # Reset error counter on successful frame
                consecutive_errors = 0
                self._frame_count += 1

                # Calculate FPS every second
                current_time = time.time()
                if current_time - self._last_fps_time >= 1.0:
                    self._current_fps = self._frame_count / (
                        current_time - self._last_fps_time
                    )
                    self._frame_count = 0
                    self._last_fps_time = current_time

                    # Log FPS every 10 seconds
                    if int(current_time) % 10 == 0:
                        logger.debug(f"📹 Streaming at {self._current_fps:.1f} FPS")

                # Get frame data
                frame_data = encoded_frame.getData()
                timestamp = encoded_frame.getTimestamp().total_seconds()

                # Send to recording if active
                if self.video_recorder and self.status.recording:
                    try:
                        self.video_recorder.add_frame(frame_data, timestamp)
                    except Exception as e:
                        logger.error(f"❌ Recording error: {e}")

                # Send to callback (for streaming to web clients)
                if self.frame_callback:
                    try:
                        self.frame_callback(frame_data, timestamp)
                    except Exception as e:
                        logger.debug(f"Frame callback error: {e}")

            except Exception as e:
                consecutive_errors += 1
                logger.error(
                    f"❌ Error in frame processing ({consecutive_errors}/{max_consecutive_errors}): {e}"
                )

                if consecutive_errors >= max_consecutive_errors:
                    logger.error(
                        "❌ Too many consecutive errors, stopping frame processing"
                    )
                    break

                time.sleep(0.1)

        logger.info("🛑 Frame processing loop stopped")

    async def update_settings(self, settings: CameraSettings) -> bool:
        """Update camera settings with validation."""
        try:
            if not self.is_connected or not self.control_queue:
                logger.warning("⚠️ Cannot update settings: camera not connected")
                return False

            logger.info("⚙️ Updating camera settings...")

            # Convert settings to DepthAI control
            control = camera_settings_to_dai_control(settings)

            # Send control to camera
            self.control_queue.send(control)

            # Update current settings
            self.current_settings = settings

            logger.info("✅ Camera settings updated successfully")
            return True

        except Exception as e:
            logger.error(f"❌ Failed to update settings: {e}")
            return False

    async def start_recording(self, codec: str = "h264") -> Optional[str]:
        """Start video recording with the specified codec."""
        try:
            if not self.is_connected:
                logger.error("❌ Cannot start recording: camera not connected")
                return None

            if self.status.recording:
                logger.warning("⚠️ Recording already in progress")
                return None

            # Generate filename with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"oak_recording_{timestamp}.mp4"

            # Ensure recordings directory exists
            videos_dir = Path(settings.RECORDINGS_PATH) / "videos"
            videos_dir.mkdir(parents=True, exist_ok=True)
            output_path = videos_dir / filename

            # Create video encoder
            self.video_recorder = VideoEncoder()

            # Start recording
            success = self.video_recorder.start_recording(
                output_path=str(output_path),
                codec=codec,
                width=self.current_settings.resolution_width,
                height=self.current_settings.resolution_height,
                fps=self.current_settings.fps,
            )

            if success:
                self.status.recording = True
                logger.info(f"🎬 Started recording: {filename} ({codec})")
                return filename
            else:
                self.video_recorder = None
                logger.error("❌ Failed to start video recording")
                return None

        except Exception as e:
            logger.error(f"❌ Failed to start recording: {e}")
            self.video_recorder = None
            return None

    async def stop_recording(self) -> Optional[str]:
        """Stop video recording and return the filename."""
        try:
            if not self.status.recording or not self.video_recorder:
                logger.warning("⚠️ No recording in progress")
                return None

            # Stop recording
            output_path = self.video_recorder.stop_recording()
            self.video_recorder = None
            self.status.recording = False

            if output_path:
                filename = Path(output_path).name
                file_size = Path(output_path).stat().st_size
                logger.info(f"✅ Recording stopped: {filename} ({file_size} bytes)")
                return filename
            else:
                logger.error("❌ Failed to stop recording properly")
                return None

        except Exception as e:
            logger.error(f"❌ Failed to stop recording: {e}")
            self.video_recorder = None
            self.status.recording = False
            return None

    async def capture_image(self) -> Optional[str]:
        """Capture a single image from the camera."""
        try:
            if not self.is_connected or not self.video_queue:
                logger.error("❌ Cannot capture image: camera not connected")
                return None

            # Try to get current frame with timeout
            max_attempts = 10
            encoded_frame = None

            for attempt in range(max_attempts):
                encoded_frame = self.video_queue.tryGet()
                if encoded_frame is not None:
                    break
                await asyncio.sleep(0.1)

            if encoded_frame is None:
                logger.warning("⚠️ No frame available for capture")
                return None

            # Generate filename with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[
                :-3
            ]  # Include milliseconds
            filename = f"oak_capture_{timestamp}.jpg"

            # Ensure images directory exists
            images_dir = Path(settings.RECORDINGS_PATH) / "images"
            images_dir.mkdir(parents=True, exist_ok=True)
            output_path = images_dir / filename

            # Save frame data as image
            frame_data = encoded_frame.getData()
            success = ImageCapture.save_frame(frame_data, str(output_path))

            if success:
                logger.info(f"📸 Image captured: {filename}")
                return filename
            else:
                logger.error("❌ Failed to save captured image")
                return None

        except Exception as e:
            logger.error(f"❌ Failed to capture image: {e}")
            return None

    async def trigger_autofocus(self) -> bool:
        """Trigger autofocus on the camera."""
        try:
            if not self.is_connected or not self.control_queue:
                logger.warning("⚠️ Cannot trigger autofocus: camera not connected")
                return False

            control = dai.CameraControl()
            control.setAutoFocusMode(dai.CameraControl.AutoFocusMode.AUTO)
            control.setAutoFocusTrigger()
            self.control_queue.send(control)

            logger.info("🎯 Autofocus triggered")
            return True

        except Exception as e:
            logger.error(f"❌ Failed to trigger autofocus: {e}")
            return False

    async def set_manual_focus(self, position: int) -> bool:
        """Set manual focus position (0-255)."""
        try:
            if not self.is_connected or not self.control_queue:
                logger.warning("⚠️ Cannot set focus: camera not connected")
                return False

            # Clamp position to valid range
            position = max(0, min(255, position))

            control = dai.CameraControl()
            control.setManualFocus(position)
            self.control_queue.send(control)

            # Update settings
            self.current_settings.focus_position = position
            self.current_settings.auto_focus = False

            logger.info(f"🎯 Manual focus set to: {position}")
            return True

        except Exception as e:
            logger.error(f"❌ Failed to set manual focus: {e}")
            return False

    def set_frame_callback(self, callback: Callable[[bytes, float], None]):
        """Set callback function for receiving video frames."""
        self.frame_callback = callback
        logger.info("📹 Frame callback registered")

    def set_status_callback(self, callback: Callable[[CameraStatus], None]):
        """Set callback function for status updates."""
        self.status_callback = callback
        logger.info("📊 Status callback registered")

    def get_status(self) -> CameraStatus:
        """Get current camera status with real-time data."""
        # Update dynamic status information
        if self.is_connected:
            self.status.connected = True
            self.status.streaming = self.is_streaming
            self.status.recording = (
                hasattr(self, "video_recorder") and self.video_recorder is not None
            )

            # Add performance metrics
            if hasattr(self, "_current_fps"):
                self.status.device_info = self.status.device_info or {}
                self.status.device_info["current_fps"] = round(self._current_fps, 1)
                self.status.device_info["frame_count"] = getattr(
                    self, "_frame_count", 0
                )

        return self.status

    def get_settings(self) -> CameraSettings:
        """Get current camera settings."""
        return self.current_settings

    def get_performance_stats(self) -> Dict[str, Any]:
        """Get performance statistics."""
        return {
            "fps": round(self._current_fps, 1),
            "frame_count": self._frame_count,
            "is_streaming": self.is_streaming,
            "is_recording": self.status.recording,
            "uptime": (
                time.time() - self._last_fps_time
                if hasattr(self, "_last_fps_time")
                else 0
            ),
        }

    async def cleanup(self):
        """Clean up all resources and connections."""
        try:
            logger.info("🧹 Starting Oak controller cleanup...")
            await self.disconnect()

            # Clear callbacks
            self.frame_callback = None
            self.status_callback = None

            logger.info("✅ Oak controller cleanup complete")
        except Exception as e:
            logger.error(f"❌ Error during cleanup: {e}")

    def __del__(self):
        """Destructor to ensure cleanup on object deletion."""
        try:
            if self.is_connected and self.device:
                logger.warning(
                    "⚠️ OakController deleted while still connected, forcing cleanup"
                )
                if self.device:
                    self.device.close()
        except Exception:
            pass  # Ignore errors in destructor
