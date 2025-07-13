import asyncio
import logging
import threading
import time
from typing import Optional, Callable, Dict, Any
from datetime import datetime, timedelta

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
    """Main controller for Oak Camera v3 operations."""

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

    async def connect(self, ip_address: str) -> bool:
        """Connect to Oak Camera v3 over PoE."""
        try:
            # Validate IP address
            if not validate_ip_address(ip_address):
                logger.error(f"❌ Invalid IP address: {ip_address}")
                return False

            # Check if camera is reachable
            if not is_port_open(ip_address, 9876, timeout=5.0):
                logger.error(f"❌ Camera not reachable at {ip_address}:9876")
                return False

            # Disconnect if already connected
            if self.is_connected:
                await self.disconnect()

            # Create device connection
            device_info = dai.DeviceInfo(ip_address)
            self.device = dai.Device(device_info)
            self.camera_ip = ip_address

            # Setup pipeline
            if not await self._setup_pipeline():
                logger.error("❌ Failed to setup camera pipeline")
                return False

            # Start pipeline
            self.device.startPipeline(self.pipeline)

            # Get queue references
            self.control_queue = self.device.getInputQueue("control")
            self.video_queue = self.device.getOutputQueue(
                "video", maxSize=4, blocking=False
            )

            # Update status
            self.is_connected = True
            self.status.connected = True
            self.status.ip_address = ip_address
            self.status.device_info = self._get_device_info()

            # Start frame processing thread
            self._start_frame_thread()

            logger.info(f"✅ Connected to Oak Camera at {ip_address}")

            # Apply current settings
            await self.update_settings(self.current_settings)

            return True

        except Exception as e:
            logger.error(f"❌ Failed to connect to camera: {e}")
            await self.disconnect()
            return False

    async def disconnect(self):
        """Disconnect from the Oak Camera."""
        try:
            # Stop recording if active
            if self.status.recording:
                await self.stop_recording()

            # Stop frame thread
            self._stop_frame_thread()

            # Close device
            if self.device:
                self.device.close()
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

            # Update status
            self.status = CameraStatus()

            logger.info("🔌 Disconnected from Oak Camera")

        except Exception as e:
            logger.error(f"❌ Error during disconnect: {e}")

    async def _setup_pipeline(self) -> bool:
        """Setup the DepthAI pipeline."""
        try:
            self.pipeline = dai.Pipeline()

            # Create camera node
            self.camera_node = self.pipeline.create(dai.node.Camera)
            self.camera_node.setBoardSocket(dai.CameraBoardSocket.CAM_A)

            # Request camera output
            cam_out = self.camera_node.requestOutput(
                size=(
                    self.current_settings.resolution_width,
                    self.current_settings.resolution_height,
                ),
                fps=self.current_settings.fps,
            )

            # Create video encoder
            self.video_encoder = self.pipeline.create(dai.node.VideoEncoder)
            self.video_encoder.setDefaultProfilePreset(
                fps=self.current_settings.fps,
                profile=dai.VideoEncoderProperties.Profile.MJPEG,
            )

            # Link camera to encoder
            cam_out.link(self.video_encoder.input)

            # Create control input
            control_in = self.pipeline.create(dai.node.XLinkIn)
            control_in.setStreamName("control")
            control_in.out.link(self.camera_node.inputControl)

            # Create video output
            video_out = self.pipeline.create(dai.node.XLinkOut)
            video_out.setStreamName("video")
            self.video_encoder.bitstream.link(video_out.input)

            logger.info("📹 Camera pipeline setup complete")
            return True

        except Exception as e:
            logger.error(f"❌ Failed to setup pipeline: {e}")
            return False

    def _get_device_info(self) -> Dict[str, Any]:
        """Get device information."""
        if not self.device:
            return {}

        try:
            return {
                "platform": self.device.getPlatform().name,
                "device_name": self.device.getDeviceName(),
                "product_name": self.device.getProductName(),
                "platform_name": self.device.getPlatformAsString(),
            }
        except Exception as e:
            logger.debug(f"Could not get device info: {e}")
            return {}

    def _start_frame_thread(self):
        """Start the frame processing thread."""
        self._stop_event.clear()
        self._frame_thread = threading.Thread(target=self._frame_processing_loop)
        self._frame_thread.start()
        self.is_streaming = True
        self.status.streaming = True

    def _stop_frame_thread(self):
        """Stop the frame processing thread."""
        self._stop_event.set()
        if self._frame_thread:
            self._frame_thread.join(timeout=5.0)
            self._frame_thread = None
        self.is_streaming = False
        self.status.streaming = False

    def _frame_processing_loop(self):
        """Main frame processing loop running in separate thread."""
        logger.info("🎬 Started frame processing loop")

        while not self._stop_event.is_set() and self.video_queue:
            try:
                # Get encoded frame with timeout
                encoded_frame = self.video_queue.tryGet()
                if encoded_frame is None:
                    time.sleep(0.01)  # Small delay to prevent busy waiting
                    continue

                # Get frame data
                frame_data = encoded_frame.getData()
                timestamp = encoded_frame.getTimestamp().total_seconds()

                # Send to recording if active
                if self.video_recorder and self.status.recording:
                    self.video_recorder.add_frame(frame_data, timestamp)

                # Send to callback (for streaming to web clients)
                if self.frame_callback:
                    try:
                        self.frame_callback(frame_data, timestamp)
                    except Exception as e:
                        logger.debug(f"Frame callback error: {e}")

            except Exception as e:
                logger.error(f"❌ Error in frame processing: {e}")
                time.sleep(0.1)

        logger.info("🛑 Frame processing loop stopped")

    async def update_settings(self, settings: CameraSettings) -> bool:
        """Update camera settings."""
        try:
            if not self.is_connected or not self.control_queue:
                logger.warning("⚠️ Cannot update settings: camera not connected")
                return False

            # Convert settings to DepthAI control
            control = camera_settings_to_dai_control(settings)

            # Send control to camera
            self.control_queue.send(control)

            # Update current settings
            self.current_settings = settings

            logger.info(f"⚙️ Camera settings updated")
            return True

        except Exception as e:
            logger.error(f"❌ Failed to update settings: {e}")
            return False

    async def start_recording(self, codec: str = "h264") -> Optional[str]:
        """Start video recording."""
        try:
            if not self.is_connected:
                logger.error("❌ Cannot start recording: camera not connected")
                return None

            if self.status.recording:
                logger.warning("⚠️ Recording already in progress")
                return None

            # Generate filename
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"oak_recording_{timestamp}.mp4"
            output_path = f"{settings.RECORDINGS_PATH}/videos/{filename}"

            # Create video encoder
            self.video_recorder = VideoEncoder()

            # Start recording
            if self.video_recorder.start_recording(
                output_path=output_path,
                codec=codec,
                width=self.current_settings.resolution_width,
                height=self.current_settings.resolution_height,
                fps=self.current_settings.fps,
            ):
                self.status.recording = True
                logger.info(f"🎬 Started recording: {filename}")
                return filename
            else:
                self.video_recorder = None
                return None

        except Exception as e:
            logger.error(f"❌ Failed to start recording: {e}")
            return None

    async def stop_recording(self) -> Optional[str]:
        """Stop video recording."""
        try:
            if not self.status.recording or not self.video_recorder:
                logger.warning("⚠️ No recording in progress")
                return None

            # Stop recording
            output_path = self.video_recorder.stop_recording()
            self.video_recorder = None
            self.status.recording = False

            if output_path:
                filename = output_path.split("/")[-1]
                logger.info(f"✅ Recording stopped: {filename}")
                return filename

            return None

        except Exception as e:
            logger.error(f"❌ Failed to stop recording: {e}")
            return None

    async def capture_image(self) -> Optional[str]:
        """Capture a single image."""
        try:
            if not self.is_connected or not self.video_queue:
                logger.error("❌ Cannot capture image: camera not connected")
                return None

            # Get current frame
            encoded_frame = self.video_queue.tryGet()
            if encoded_frame is None:
                logger.warning("⚠️ No frame available for capture")
                return None

            # Generate filename
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"oak_capture_{timestamp}.jpg"
            output_path = f"{settings.RECORDINGS_PATH}/images/{filename}"

            # Save frame data
            frame_data = encoded_frame.getData()
            if ImageCapture.save_frame(frame_data, output_path):
                logger.info(f"📸 Image captured: {filename}")
                return filename

            return None

        except Exception as e:
            logger.error(f"❌ Failed to capture image: {e}")
            return None

    def set_frame_callback(self, callback: Callable):
        """Set callback function for receiving frames."""
        self.frame_callback = callback

    def set_status_callback(self, callback: Callable):
        """Set callback function for status updates."""
        self.status_callback = callback

    def get_status(self) -> CameraStatus:
        """Get current camera status."""
        if self.is_connected and self.device:
            try:
                # Update temperature if available
                # Note: Temperature monitoring may not be available on all devices
                pass
            except Exception:
                pass

        return self.status

    def get_settings(self) -> CameraSettings:
        """Get current camera settings."""
        return self.current_settings

    async def trigger_autofocus(self) -> bool:
        """Trigger autofocus."""
        try:
            if not self.is_connected or not self.control_queue:
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
        """Set manual focus position."""
        try:
            if not self.is_connected or not self.control_queue:
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

    async def cleanup(self):
        """Clean up resources."""
        try:
            await self.disconnect()
            logger.info("🧹 Oak controller cleanup complete")
        except Exception as e:
            logger.error(f"❌ Error during cleanup: {e}")

    def __del__(self):
        """Destructor to ensure cleanup."""
        try:
            if self.is_connected:
                # Note: Can't use async in __del__, so just do basic cleanup
                if self.device:
                    self.device.close()
        except Exception:
            pass
