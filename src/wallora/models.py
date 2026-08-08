"""Data models for Wallora."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from wallora.utils import is_animated_media, is_video_file


@dataclass
class WallpaperItem:
    path: Path
    name: str = ""
    size_bytes: int = 0
    is_favorite: bool = False
    is_animated: bool = False

    def __post_init__(self):
        if not self.name:
            self.name = self.path.name
        try:
            self.size_bytes = self.path.stat().st_size
        except Exception:
            pass
        # Detect animated media if not explicitly set
        if not self.is_animated:
            try:
                self.is_animated = is_animated_media(self.path) or is_video_file(self.path)
            except Exception:
                self.is_animated = False

    @property
    def media_kind(self) -> str:
        return "video" if self.is_animated else "image"


@dataclass
class AdjustmentPreset:
    name: str
    brightness: float = 1.0
    contrast: float = 1.0
    saturation: float = 1.0
    sharpness: float = 1.0
    temperature: int = 0
    blur: float = 0.0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "brightness": self.brightness,
            "contrast": self.contrast,
            "saturation": self.saturation,
            "sharpness": self.sharpness,
            "temperature": self.temperature,
            "blur": self.blur,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AdjustmentPreset":
        return cls(
            name=data.get("name", "Preset"),
            brightness=data.get("brightness", 1.0),
            contrast=data.get("contrast", 1.0),
            saturation=data.get("saturation", 1.0),
            sharpness=data.get("sharpness", 1.0),
            temperature=data.get("temperature", 0),
            blur=data.get("blur", 0.0),
        )
