import asyncio
import os
from typing import Optional, Callable
from pathlib import Path
import logging
import av
from fractions import Fraction

logger = logging.getLogger(__name__)


class VideoEncoder:
    """Handles video encoding for Oak Camera recordings."""

    def __init__(self):
        self.output_container: Optional[av.container.OutputContainer] = None
        self.stream: Optional[av.video.stream.VideoStream] = None
        self.start_time: Optional[float] = None
        self.is_recording = False
        self.frame_count = 0

    def start_recording(
        self,
        output_path: str,
        codec: str = "h264",
        width: int = 1920,
        height: int = 1440,
        fps: int = 30,
    ) -> bool:
        """Start video recording with specified parameters."""
        try:
            # Ensure output directory exists
            os.makedirs(os.path.dirname(output_path), exist_ok=True)

            # Open output container
            self.output_container = av.open(output_path, "w")

            # Add video stream
            if codec == "h265":
                self.stream = self.output_container.add_stream("hevc", rate=fps)
            else:
                self.stream = self.output_container.add_stream(codec, rate=fps)

            # Configure stream
            self.stream.width = width
            self.stream.height = height
            self.stream.time_base = Fraction(1, 1_000_000)  # Microseconds

            if codec == "mjpeg":
                self.stream.pix_fmt = "yuvj420p"

            self.start_time = None
            self.frame_count = 0
            self.is_recording = True

            logger.info(
                f"🎬 Started recording: {output_path} ({codec}, {width}x{height}@{fps}fps)"
            )
            return True

        except Exception as e:
            logger.error(f"❌ Failed to start recording: {e}")
            self.cleanup()
            return False

    def add_frame(self, frame_data: bytes, timestamp: Optional[float] = None) -> bool:
        """Add an encoded frame to the recording."""
        if not self.is_recording or not self.output_container or not self.stream:
            return False

        try:
            # Create packet from encoded data
            packet = av.Packet(frame_data)

            # Set timestamp
            if timestamp is not None:
                if self.start_time is None:
                    self.start_time = timestamp

                pts = int((timestamp - self.start_time) * 1_000_000)
                packet.pts = pts
                packet.dts = pts
            else:
                # Use frame count if no timestamp
                packet.pts = self.frame_count * (1_000_000 // 30)  # Assume 30fps
                packet.dts = packet.pts

            packet.time_base = self.stream.time_base
            packet.stream = self.stream

            # Mux packet
            self.output_container.mux(packet)
            self.frame_count += 1

            return True

        except Exception as e:
            logger.error(f"❌ Failed to add frame: {e}")
            return False

    def stop_recording(self) -> Optional[str]:
        """Stop recording and return the output path."""
        if not self.is_recording:
            return None

        try:
            output_path = self.output_container.name if self.output_container else None
            self.cleanup()

            if output_path and os.path.exists(output_path):
                file_size = os.path.getsize(output_path)
                logger.info(
                    f"✅ Recording saved: {output_path} ({file_size} bytes, {self.frame_count} frames)"
                )
                return output_path

        except Exception as e:
            logger.error(f"❌ Failed to stop recording: {e}")

        return None

    def cleanup(self):
        """Clean up recording resources."""
        self.is_recording = False

        if self.output_container:
            try:
                self.output_container.close()
            except:
                pass
            self.output_container = None

        self.stream = None
        self.start_time = None
        self.frame_count = 0


class ImageCapture:
    """Handles image capture from Oak Camera."""

    @staticmethod
    def save_frame(frame_data: bytes, output_path: str, format: str = "JPEG") -> bool:
        """Save a frame as an image file."""
        try:
            # Ensure output directory exists
            os.makedirs(os.path.dirname(output_path), exist_ok=True)

            # Save image data
            with open(output_path, "wb") as f:
                f.write(frame_data)

            file_size = os.path.getsize(output_path)
            logger.info(f"📸 Image captured: {output_path} ({file_size} bytes)")
            return True

        except Exception as e:
            logger.error(f"❌ Failed to save image: {e}")
            return False
