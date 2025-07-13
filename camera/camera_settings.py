from enum import Enum
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field
import depthai as dai


class AutoFocusMode(str, Enum):
    """Auto focus modes."""

    AUTO = "AUTO"
    CONTINUOUS_VIDEO = "CONTINUOUS_VIDEO"
    CONTINUOUS_PICTURE = "CONTINUOUS_PICTURE"
    EDOF = "EDOF"


class AutoWhiteBalanceMode(str, Enum):
    """Auto white balance modes."""

    OFF = "OFF"
    AUTO = "AUTO"
    INCANDESCENT = "INCANDESCENT"
    FLUORESCENT = "FLUORESCENT"
    WARM_FLUORESCENT = "WARM_FLUORESCENT"
    DAYLIGHT = "DAYLIGHT"
    CLOUDY_DAYLIGHT = "CLOUDY_DAYLIGHT"
    TWILIGHT = "TWILIGHT"
    SHADE = "SHADE"


class AntiBandingMode(str, Enum):
    """Anti-banding modes."""

    OFF = "OFF"
    MODE_50HZ = "MODE_50HZ"
    MODE_60HZ = "MODE_60HZ"
    MODE_AUTO = "MODE_AUTO"


class EffectMode(str, Enum):
    """Effect modes."""

    OFF = "OFF"
    MONO = "MONO"
    NEGATIVE = "NEGATIVE"
    SOLARIZE = "SOLARIZE"
    SEPIA = "SEPIA"
    POSTERIZE = "POSTERIZE"
    WHITEBOARD = "WHITEBOARD"
    BLACKBOARD = "BLACKBOARD"
    AQUA = "AQUA"


class CameraSettings(BaseModel):
    """Camera settings model."""

    # Auto controls
    auto_exposure: bool = Field(default=True, description="Enable auto exposure")
    auto_focus: bool = Field(default=True, description="Enable auto focus")
    auto_white_balance: bool = Field(
        default=True, description="Enable auto white balance"
    )
    auto_exposure_lock: bool = Field(default=False, description="Lock auto exposure")
    auto_white_balance_lock: bool = Field(
        default=False, description="Lock auto white balance"
    )

    # Manual exposure settings
    exposure_time: int = Field(
        default=20000, ge=1, le=500000, description="Exposure time in microseconds"
    )
    iso_sensitivity: int = Field(
        default=800, ge=100, le=1600, description="ISO sensitivity"
    )

    # Manual focus settings
    focus_position: int = Field(
        default=130, ge=0, le=255, description="Manual focus position"
    )
    auto_focus_mode: AutoFocusMode = Field(
        default=AutoFocusMode.CONTINUOUS_VIDEO, description="Auto focus mode"
    )

    # White balance settings
    white_balance_mode: AutoWhiteBalanceMode = Field(
        default=AutoWhiteBalanceMode.AUTO, description="White balance mode"
    )
    white_balance_temperature: int = Field(
        default=4000,
        ge=1000,
        le=12000,
        description="White balance temperature in Kelvin",
    )

    # Image enhancement
    brightness: int = Field(
        default=0, ge=-10, le=10, description="Brightness adjustment"
    )
    contrast: int = Field(default=0, ge=-10, le=10, description="Contrast adjustment")
    saturation: int = Field(
        default=0, ge=-10, le=10, description="Saturation adjustment"
    )
    sharpness: int = Field(default=1, ge=0, le=4, description="Sharpness level")

    # Noise reduction
    luma_denoise: int = Field(default=1, ge=0, le=4, description="Luma denoise level")
    chroma_denoise: int = Field(
        default=1, ge=0, le=4, description="Chroma denoise level"
    )

    # Other settings
    anti_banding_mode: AntiBandingMode = Field(
        default=AntiBandingMode.MODE_50HZ, description="Anti-banding mode"
    )
    effect_mode: EffectMode = Field(default=EffectMode.OFF, description="Effect mode")
    auto_exposure_compensation: int = Field(
        default=0, ge=-9, le=9, description="Auto exposure compensation"
    )

    # Stream settings
    fps: int = Field(default=30, ge=5, le=60, description="Frames per second")
    resolution_width: int = Field(default=1920, description="Resolution width")
    resolution_height: int = Field(default=1440, description="Resolution height")


class CameraStatus(BaseModel):
    """Camera status information."""

    connected: bool = Field(default=False, description="Camera connection status")
    ip_address: Optional[str] = Field(default=None, description="Camera IP address")
    device_info: Optional[Dict[str, Any]] = Field(
        default=None, description="Device information"
    )
    streaming: bool = Field(default=False, description="Streaming status")
    recording: bool = Field(default=False, description="Recording status")
    temperature: Optional[float] = Field(default=None, description="Device temperature")
    uptime: Optional[int] = Field(default=None, description="Device uptime in seconds")


def camera_settings_to_dai_control(settings: CameraSettings) -> dai.CameraControl:
    """Convert camera settings to DepthAI camera control."""
    ctrl = dai.CameraControl()

    # Auto exposure
    if settings.auto_exposure:
        ctrl.setAutoExposureEnable()
    else:
        ctrl.setManualExposure(settings.exposure_time, settings.iso_sensitivity)

    # Auto exposure lock
    ctrl.setAutoExposureLock(settings.auto_exposure_lock)

    # Auto focus
    if settings.auto_focus:
        af_mode_map = {
            AutoFocusMode.AUTO: dai.CameraControl.AutoFocusMode.AUTO,
            AutoFocusMode.CONTINUOUS_VIDEO: dai.CameraControl.AutoFocusMode.CONTINUOUS_VIDEO,
            AutoFocusMode.CONTINUOUS_PICTURE: dai.CameraControl.AutoFocusMode.CONTINUOUS_PICTURE,
            AutoFocusMode.EDOF: dai.CameraControl.AutoFocusMode.EDOF,
        }
        ctrl.setAutoFocusMode(af_mode_map[settings.auto_focus_mode])
    else:
        ctrl.setManualFocus(settings.focus_position)

    # White balance
    if settings.auto_white_balance:
        awb_mode_map = {
            AutoWhiteBalanceMode.OFF: dai.CameraControl.AutoWhiteBalanceMode.OFF,
            AutoWhiteBalanceMode.AUTO: dai.CameraControl.AutoWhiteBalanceMode.AUTO,
            AutoWhiteBalanceMode.INCANDESCENT: dai.CameraControl.AutoWhiteBalanceMode.INCANDESCENT,
            AutoWhiteBalanceMode.FLUORESCENT: dai.CameraControl.AutoWhiteBalanceMode.FLUORESCENT,
            AutoWhiteBalanceMode.WARM_FLUORESCENT: dai.CameraControl.AutoWhiteBalanceMode.WARM_FLUORESCENT,
            AutoWhiteBalanceMode.DAYLIGHT: dai.CameraControl.AutoWhiteBalanceMode.DAYLIGHT,
            AutoWhiteBalanceMode.CLOUDY_DAYLIGHT: dai.CameraControl.AutoWhiteBalanceMode.CLOUDY_DAYLIGHT,
            AutoWhiteBalanceMode.TWILIGHT: dai.CameraControl.AutoWhiteBalanceMode.TWILIGHT,
            AutoWhiteBalanceMode.SHADE: dai.CameraControl.AutoWhiteBalanceMode.SHADE,
        }
        ctrl.setAutoWhiteBalanceMode(awb_mode_map[settings.white_balance_mode])
    else:
        ctrl.setManualWhiteBalance(settings.white_balance_temperature)

    # White balance lock
    ctrl.setAutoWhiteBalanceLock(settings.auto_white_balance_lock)

    # Image enhancement
    ctrl.setBrightness(settings.brightness)
    ctrl.setContrast(settings.contrast)
    ctrl.setSaturation(settings.saturation)
    ctrl.setSharpness(settings.sharpness)

    # Noise reduction
    ctrl.setLumaDenoise(settings.luma_denoise)
    ctrl.setChromaDenoise(settings.chroma_denoise)

    # Anti-banding
    anti_banding_map = {
        AntiBandingMode.OFF: dai.CameraControl.AntiBandingMode.OFF,
        AntiBandingMode.MODE_50HZ: dai.CameraControl.AntiBandingMode.MAINS_50_HZ,
        AntiBandingMode.MODE_60HZ: dai.CameraControl.AntiBandingMode.MAINS_60_HZ,
        AntiBandingMode.MODE_AUTO: dai.CameraControl.AntiBandingMode.AUTO,
    }
    ctrl.setAntiBandingMode(anti_banding_map[settings.anti_banding_mode])

    # Effect mode
    effect_map = {
        EffectMode.OFF: dai.CameraControl.EffectMode.OFF,
        EffectMode.MONO: dai.CameraControl.EffectMode.MONO,
        EffectMode.NEGATIVE: dai.CameraControl.EffectMode.NEGATIVE,
        EffectMode.SOLARIZE: dai.CameraControl.EffectMode.SOLARIZE,
        EffectMode.SEPIA: dai.CameraControl.EffectMode.SEPIA,
        EffectMode.POSTERIZE: dai.CameraControl.EffectMode.POSTERIZE,
        EffectMode.WHITEBOARD: dai.CameraControl.EffectMode.WHITEBOARD,
        EffectMode.BLACKBOARD: dai.CameraControl.EffectMode.BLACKBOARD,
        EffectMode.AQUA: dai.CameraControl.EffectMode.AQUA,
    }
    ctrl.setEffectMode(effect_map[settings.effect_mode])

    # Auto exposure compensation
    ctrl.setAutoExposureCompensation(settings.auto_exposure_compensation)

    return ctrl
