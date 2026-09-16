"""Validated YAML settings. Paths are relative to the selected YAML file."""
from dataclasses import asdict, dataclass, fields
import math
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Config:
    wavelength_nm: float = 515.0
    pixel_pitch_um: float = 2.74
    # Signed optical propagation cam0 -> cam1; must be measured, never inferred
    # from camera numbering. Null deliberately disables phase recovery.
    plane_separation_mm: float | None = None
    scan_min_mm: float = 30.0
    scan_max_mm: float = 90.0
    scan_step_mm: float = 1.0
    gs_iterations: int = 20
    serial0: str = "26259157"
    serial1: str = "26259158"
    pair_tolerance_ms: float = 8.0
    max_clock_uncertainty_ms: float = 3.0
    output_dir: str = "captures"
    calibration_file: str | None = None
    camera_library: str | None = None
    cache_megabytes: int = 192
    slider_debounce_ms: int = 160
    calibration_window_px: int = 96
    calibration_step_px: int = 96
    calibration_search_px: int = 24
    calibration_min_matches: int = 15
    calibration_max_rms_px: float = 1.0
    calibration_max_holdout_px: float = 1.5

    def validate(self):
        for name in ("wavelength_nm", "pixel_pitch_um", "scan_min_mm", "scan_max_mm",
                     "scan_step_mm", "pair_tolerance_ms", "max_clock_uncertainty_ms",
                     "calibration_max_rms_px", "calibration_max_holdout_px"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{name} must be a finite number")
        for name in ("wavelength_nm", "pixel_pitch_um", "scan_step_mm", "pair_tolerance_ms",
                     "max_clock_uncertainty_ms", "calibration_max_rms_px", "calibration_max_holdout_px"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.plane_separation_mm is not None:
            v = self.plane_separation_mm
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v == 0:
                raise ValueError("plane_separation_mm must be null or a nonzero signed distance")
        if self.scan_max_mm < self.scan_min_mm:
            raise ValueError("scan_max_mm must be >= scan_min_mm")
        if (self.scan_max_mm - self.scan_min_mm) / self.scan_step_mm > 10000:
            raise ValueError("At most 10001 depths per scan")
        for name, lo, hi in (("gs_iterations", 1, 10000), ("cache_megabytes", 1, 4096),
                             ("slider_debounce_ms", 0, 2000), ("calibration_window_px", 16, 512),
                             ("calibration_step_px", 8, 512), ("calibration_search_px", 2, 512),
                             ("calibration_min_matches", 12, 10000)):
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
                raise ValueError(f"{name} must be an integer in [{lo}, {hi}]")
        if not self.serial0 or not self.serial1 or self.serial0 == self.serial1:
            raise ValueError("Two distinct camera serials are required")
        if any(not isinstance(s, str) or not s.replace("-", "").isalnum()
               for s in (self.serial0, self.serial1)):
            raise ValueError("Camera serials must contain letters, digits or hyphens")
        if self.pixel_pitch_um * 1000 <= self.wavelength_nm / math.sqrt(2):
            raise ValueError("Sampling contains non-propagating frequencies")
        return self


def load_config(path: Path) -> Config:
    path = path.resolve()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("Config must be a YAML mapping")
    unknown = set(data) - {f.name for f in fields(Config)}
    if unknown:
        raise ValueError(f"Unknown config keys: {', '.join(sorted(unknown))}")
    for key in ("output_dir", "calibration_file", "camera_library"):
        if data.get(key):
            p = Path(data[key]).expanduser()
            data[key] = str(p if p.is_absolute() else path.parent / p)
    data.setdefault("output_dir", str(path.parent / "captures"))
    return Config(**data).validate()


def save_config(config: Config, path: Path):
    path.write_text(yaml.safe_dump(asdict(config), sort_keys=False, allow_unicode=True), encoding="utf-8")
