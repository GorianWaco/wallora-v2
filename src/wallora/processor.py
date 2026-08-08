"""Image processing pipeline for Wallora using Pillow."""
from __future__ import annotations

import io
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

from wallora.utils import get_all_monitors_geometry, get_primary_monitor_geometry


class ImageProcessor:
    """Handles all image adjustments + final wallpaper rendering."""

    def __init__(self):
        self._original: Optional[Image.Image] = None
        self._original_path: Optional[Path] = None

    def load(self, path: Path | str) -> bool:
        """Load original image. Returns True on success."""
        try:
            path = Path(path)
            img = Image.open(path)
            # Convert to RGB to simplify processing (drop alpha for wallpaper)
            if img.mode in ("RGBA", "LA", "P"):
                background = Image.new("RGB", img.size, (0, 0, 0))
                if img.mode == "P":
                    img = img.convert("RGBA")
                if img.mode in ("RGBA", "LA"):
                    background.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None)
                    img = background
                else:
                    img = img.convert("RGB")
            elif img.mode != "RGB":
                img = img.convert("RGB")

            self._original = img
            self._original_path = path
            return True
        except Exception as e:
            print(f"Failed to load image {path}: {e}")
            self._original = None
            return False

    @property
    def has_image(self) -> bool:
        return self._original is not None

    def get_preview(self, adjustments: dict, max_size: Tuple[int, int] = (1600, 900), 
                    scaling: str = "fit", bg_blur: float = 0.5, bg_expand: float = 0.0, bg_fade: float = 0.1) -> Optional[Image.Image]:
        """Fast preview with downscaling + adjustments.
        Larger default to avoid blurry upscaling in the UI preview widget.
        """
        if not self._original:
            return None
        img = self._original.copy()

        # Downscale for preview speed
        img.thumbnail(max_size, Image.Resampling.LANCZOS)

        img = self._apply_adjustments(img, adjustments)

        # Use values from adjustments for simulation if available (for live preview)
        bg_blur = float(adjustments.get("bg_blur", bg_blur))
        bg_expand = float(adjustments.get("bg_expand", bg_expand))
        bg_fade = float(adjustments.get("bg_fade", bg_fade))

        # Simulate blurred background scaling modes in preview too
        if scaling in ("fit_blur", "center_blur"):
            pw, ph = max_size
            img = self._apply_blurred_letterbox(img, pw, ph, scaling == "fit_blur", bg_blur, bg_expand, bg_fade)

        return img

    def process_for_wallpaper(
        self,
        adjustments: dict,
        scaling: str = "fill",
        target_size: Optional[Tuple[int, int]] = None,
        multi_monitor: bool = False,
    ) -> Optional[Image.Image]:
        """
        Produce final image ready to be set as wallpaper.

        scaling: "fill" | "fit" | "stretch" | "center" | "tile" | "span"
        """
        if not self._original:
            return None

        img = self._original.copy()

        # Apply artistic adjustments first (on original quality)
        img = self._apply_adjustments(img, adjustments)

        if target_size is None:
            if multi_monitor:
                # For span: calculate bounding box of all monitors
                geoms = get_all_monitors_geometry()
                min_x = min(g[0] for g in geoms)
                min_y = min(g[1] for g in geoms)
                max_x = max(g[0] + g[2] for g in geoms)
                max_y = max(g[1] + g[3] for g in geoms)
                target_size = (max_x - min_x, max_y - min_y)
            else:
                target_size = get_primary_monitor_geometry()

        w, h = target_size
        if w <= 0 or h <= 0:
            w, h = 1920, 1080

        img = self._apply_scaling(img, scaling, w, h, adjustments)
        return img

    def _apply_adjustments(self, img: Image.Image, adj: dict) -> Image.Image:
        brightness = float(adj.get("brightness", 1.0))
        contrast = float(adj.get("contrast", 1.0))
        saturation = float(adj.get("saturation", 1.0))
        sharpness = float(adj.get("sharpness", 1.0))
        temperature = int(adj.get("temperature", 0))
        blur = float(adj.get("blur", 0.0))
        hue = float(adj.get("hue", 0))
        gamma = float(adj.get("gamma", 1.0))
        vignette = float(adj.get("vignette", 0.0))

        # Basic enhancements
        if brightness != 1.0:
            img = ImageEnhance.Brightness(img).enhance(brightness)
        if contrast != 1.0:
            img = ImageEnhance.Contrast(img).enhance(contrast)
        if saturation != 1.0:
            img = ImageEnhance.Color(img).enhance(saturation)
        if sharpness != 1.0:
            # Use unsharp_mask for nicer results
            if sharpness > 1.0:
                img = img.filter(ImageFilter.UnsharpMask(radius=1, percent=int((sharpness - 1) * 150), threshold=3))
            else:
                # softer
                img = ImageEnhance.Sharpness(img).enhance(sharpness)

        if blur > 0.1:
            img = img.filter(ImageFilter.GaussianBlur(radius=min(blur, 12)))

        # Color temperature
        if temperature != 0:
            img = self._apply_temperature(img, temperature)

        # Hue shift
        if abs(hue) > 0.5:
            img = self._apply_hue(img, hue)

        # Gamma
        if abs(gamma - 1.0) > 0.01:
            img = self._apply_gamma(img, gamma)

        # Vignette
        if vignette > 0.01:
            img = self._apply_vignette(img, vignette)

        # Edge blur + coloring of blurred edges
        edge_blur = float(adj.get("edge_blur", 0.0))
        edge_tint = int(adj.get("edge_tint", 0))
        if edge_blur > 0.5 or edge_tint != 0:
            img = self._apply_edge_blur_and_tint(img, edge_blur, edge_tint)

        return img

    def _apply_temperature(self, img: Image.Image, temp: int) -> Image.Image:
        """
        temp: -100 (very cool/blue) ... +100 (very warm/orange)
        Simple but effective channel shift.
        """
        if temp == 0:
            return img

        # Split channels
        r, g, b = img.split()

        factor = abs(temp) / 120.0  # 0..~0.83

        if temp > 0:
            # Warmer: boost red, slightly reduce blue
            r = ImageEnhance.Brightness(r).enhance(1.0 + factor * 0.35)
            b = ImageEnhance.Brightness(b).enhance(max(0.65, 1.0 - factor * 0.28))
        else:
            # Cooler: boost blue, slightly reduce red
            b = ImageEnhance.Brightness(b).enhance(1.0 + factor * 0.32)
            r = ImageEnhance.Brightness(r).enhance(max(0.68, 1.0 - factor * 0.25))

        # Re-merge, keep green mostly neutral
        return Image.merge("RGB", (r, g, b))

    def _apply_hue(self, img: Image.Image, hue: float) -> Image.Image:
        """Shift hue by degrees (-180 to 180)."""
        hue = hue % 360
        if abs(hue) < 0.1:
            return img
        # Convert to HSV (H 0-255 for 0-360 in Pillow)
        hsv = img.convert("HSV")
        h, s, v = hsv.split()
        shift = int((hue / 360.0) * 255)
        h = h.point(lambda x: (x + shift) % 255)
        hsv = Image.merge("HSV", (h, s, v))
        return hsv.convert("RGB")

    def _apply_gamma(self, img: Image.Image, gamma: float) -> Image.Image:
        """Apply gamma correction."""
        if gamma <= 0:
            gamma = 0.01
        return img.point(lambda x: 255 * pow(x / 255.0, 1.0 / gamma))

    def _apply_vignette(self, img: Image.Image, strength: float) -> Image.Image:
        """Darken edges with a vignette effect."""
        if strength <= 0:
            return img
        w, h = img.size
        # Create a soft radial mask (white in center, black at edges)
        mask = Image.new("L", (w, h), 0)
        draw = ImageDraw.Draw(mask)
        # Larger ellipse in center stays bright
        margin_x = int(w * (0.5 - strength * 0.35))
        margin_y = int(h * (0.5 - strength * 0.35))
        draw.ellipse([margin_x, margin_y, w - margin_x, h - margin_y], fill=255)
        # Blur for soft edges
        mask = mask.filter(ImageFilter.GaussianBlur(radius=min(w, h) * 0.12))
        # Use mask to blend toward black
        black = Image.new("RGB", (w, h), (0, 0, 0))
        # composite black where mask is low
        return Image.composite(black, img, ImageOps.invert(mask).point(lambda x: int(x * strength)))

    def _apply_edge_blur_and_tint(self, img: Image.Image, edge_blur: float, edge_tint: int) -> Image.Image:
        """Apply blur to the edges and optionally tint/color the blurred edge area.
        This creates a soft colored border effect around the image.
        """
        if edge_blur < 0.5 and edge_tint == 0:
            return img

        w, h = img.size

        # Strongly blurred version for edges
        blurred = img.filter(ImageFilter.GaussianBlur(radius=min(30, edge_blur)))

        if edge_tint != 0:
            blurred = self._apply_temperature(blurred, edge_tint)

        # Edge mask: 0 in center, higher on the borders
        mask = Image.new("L", (w, h), 0)
        draw = ImageDraw.Draw(mask)
        margin = int(min(w, h) * 0.12)
        # The outer area will get the blur+tint
        draw.rectangle([margin, margin, w - margin, h - margin], fill=0)
        # invert so edges are bright in mask
        mask = ImageOps.invert(mask)
        mask = mask.filter(ImageFilter.GaussianBlur(radius=max(10, int(min(w, h) * 0.08))))

        # Blend: more blur/tint on edges using mask
        alpha = mask.point(lambda x: int(x * min(1.0, edge_blur / 25.0)))
        return Image.composite(blurred, img, alpha)

    def _apply_scaling(self, img: Image.Image, mode: str, target_w: int, target_h: int, adjustments: dict = None) -> Image.Image:
        """Resize/crop the image according to desired scaling mode.

        Supported modes:
        - stretch, tile, center, fill, fit, span (basic)
        - fit_blur, center_blur : letterbox with blurred image as background (great for ultrawide)
        """
        orig_w, orig_h = img.size
        if orig_w == 0 or orig_h == 0:
            return img

        mode = mode.lower()

        if mode == "stretch":
            return img.resize((target_w, target_h), Image.Resampling.LANCZOS)

        if mode == "tile":
            canvas = Image.new("RGB", (target_w, target_h))
            for y in range(0, target_h, orig_h):
                for x in range(0, target_w, orig_w):
                    canvas.paste(img, (x, y))
            return canvas

        if mode == "center":
            canvas = Image.new("RGB", (target_w, target_h), (0, 0, 0))
            x = (target_w - orig_w) // 2
            y = (target_h - orig_h) // 2
            canvas.paste(img, (x, y))
            return canvas

        # New: blurred letterbox modes (ideal for 3440x1440 when source is 16:9 or narrower)
        if mode in ("fit_blur", "center_blur"):
            adj = adjustments or {}
            bg_blur = float(adj.get("bg_blur", 0.5))
            bg_expand = float(adj.get("bg_expand", 0.0))
            bg_fade = float(adj.get("bg_fade", 0.1))
            return self._apply_blurred_letterbox(img, target_w, target_h, mode == "fit_blur", bg_blur, bg_expand, bg_fade)

        # fill (zoom/crop to cover) and fit (letterbox with black)
        is_fill = mode == "fill"
        ratio = max(target_w / orig_w, target_h / orig_h) if is_fill else min(target_w / orig_w, target_h / orig_h)

        new_w = int(orig_w * ratio)
        new_h = int(orig_h * ratio)

        resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

        if is_fill:
            left = (new_w - target_w) // 2
            top = (new_h - target_h) // 2
            return resized.crop((left, top, left + target_w, top + target_h))
        else:
            # classic fit with black bars
            canvas = Image.new("RGB", (target_w, target_h), (0, 0, 0))
            x = (target_w - new_w) // 2
            y = (target_h - new_h) // 2
            canvas.paste(resized, (x, y))
            return canvas

    def _apply_blurred_letterbox(self, img: Image.Image, target_w: int, target_h: int, is_fit: bool, bg_blur: float = 0.5, bg_expand: float = 0.0, bg_fade: float = 0.1) -> Image.Image:
        """Create blurred sides ONLY (left and right), never top/bottom.

        The blurred background is created by horizontally stretching the image
        (within the sharp image's vertical band) and blurring it.
        Top and bottom bars (if any) are always pure black.
        """
        orig_w, orig_h = img.size

        # Compute sharp size that touches top and bottom (for fit) or uses original
        if is_fit:
            # Scale to full target height → bars only on sides
            sharp_h = target_h
            sharp_w = max(1, int(orig_w * (target_h / orig_h)))
            sharp = img.resize((sharp_w, sharp_h), Image.Resampling.LANCZOS)
        else:
            # Center blur - use original size (no upscaling)
            sharp_w = orig_w
            sharp_h = orig_h
            sharp = img

        x = (target_w - sharp_w) // 2
        y = (target_h - sharp_h) // 2

        # Create the blurred side fill by stretching horizontally (same height as sharp)
        # bg_expand makes us take a slightly cropped+stretched source for more "zoomed" blur
        expand = 1.0 + (bg_expand * 0.6)
        src_w = max(1, int(sharp_w / expand))
        if src_w < sharp_w:
            crop = (sharp_w - src_w) // 2
            src_for_blur = sharp.crop((crop, 0, sharp_w - crop, sharp_h))
        else:
            src_for_blur = sharp

        stretched = src_for_blur.resize((target_w, sharp_h), Image.Resampling.LANCZOS)

        # Blur strength
        base_radius = max(8, int(sharp_h / 12))
        blur_radius = base_radius * (0.2 + bg_blur * 2.5)
        blurred_fill = stretched.filter(ImageFilter.GaussianBlur(radius=blur_radius))

        # Tone down the blurred fill
        blurred_fill = ImageEnhance.Brightness(blurred_fill).enhance(0.65)
        blurred_fill = ImageEnhance.Color(blurred_fill).enhance(0.55)
        blurred_fill = ImageEnhance.Contrast(blurred_fill).enhance(0.9)

        # Create final canvas - black everywhere (top/bottom will stay black)
        canvas = Image.new("RGB", (target_w, target_h), (0, 0, 0))

        # Paste blurred fill only in the vertical band of the sharp content
        canvas.paste(blurred_fill, (0, y))

        # Soft horizontal mask only on left and right of the sharp image
        fade = max(20, min(180, int(sharp_w * (0.04 + bg_fade * 0.32))))
        mask = Image.new('L', (sharp_w, sharp_h), 255)
        draw = ImageDraw.Draw(mask)

        for i in range(fade):
            val = int(255 * (i / fade))
            draw.line([(i, 0), (i, sharp_h-1)], fill=val)
            draw.line([(sharp_w-1-i, 0), (sharp_w-1-i, sharp_h-1)], fill=val)

        mask = mask.filter(ImageFilter.GaussianBlur(radius=2))

        # Blend sharp into the side-blurred band
        region = canvas.crop((x, y, x + sharp_w, y + sharp_h))
        blended = Image.composite(sharp, region, mask)
        canvas.paste(blended, (x, y))

        return canvas

    def save_processed(self, img: Image.Image, dest: Path | None = None) -> Path:
        """Save processed image to cache or provided path. Returns final path."""
        if dest is None:
            from wallora.config import CACHE_DIR

            dest = CACHE_DIR / "current_wallpaper.jpg"
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Use high quality JPEG for wallpaper
        img.save(dest, "JPEG", quality=95, optimize=True)
        return dest

    def get_current_original_path(self) -> Optional[Path]:
        return self._original_path
