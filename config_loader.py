"""Configuration file loader and validator."""

import json
from pathlib import Path
from typing import Dict, Any, List, Optional


class Config:
    """Configuration class with defaults and validation."""
    
    def __init__(self, config_dict: Optional[Dict[str, Any]] = None):
        """Initialize config with defaults or provided values."""
        if config_dict is None:
            config_dict = {}
        
        # Processing settings
        self.threads: int = config_dict.get('threads', 1)
        self.min_improvement_pct: float = config_dict.get('min_improvement_pct', 5.0)
        self.hang_timeout: int = config_dict.get('hang_timeout', 300)  # seconds
        self.recursive: bool = config_dict.get('recursive', True)
        
        # File filtering
        skip = config_dict.get('skip_extensions', ['.webp', '.jxl'])
        if skip is None:
            skip = ['.webp', '.jxl']
        if isinstance(skip, str):
            skip = [part.strip() for part in skip.split(',') if part.strip()]
        self.skip_extensions: List[str] = skip
        
        source = config_dict.get('source_extensions')
        if isinstance(source, str):
            source = [part.strip() for part in source.split(',') if part.strip()]
        if not source:
            self.source_extensions: Optional[List[str]] = None
        else:
            self.source_extensions = list(source)
        
        # Conversion settings
        self.jpegxl_quality: int = config_dict.get('jpegxl_quality', 90)
        self.jpegxl_effort: int = config_dict.get('jpegxl_effort', 9)
        self.webp_method: int = config_dict.get('webp_method', 6)
        self.webp_quality: int = config_dict.get('webp_quality', 90)
        self.webp_lossless: bool = config_dict.get('webp_lossless', False)
        self.conversion_timeout: int = config_dict.get('conversion_timeout', 300)  # seconds
        self.max_animated_frames: int = config_dict.get('max_animated_frames', 1000)
        self.skip_second_threshold: float = config_dict.get('skip_second_threshold', 0.70)
        
        # Adaptive conversion settings (load-aware effort/method tuning)
        self.adaptive_effort_enabled: bool = config_dict.get('adaptive_effort_enabled', True)
        self.busy_load_threshold: float = config_dict.get('busy_load_threshold', 0.7)
        self.jpegxl_effort_busy: int = config_dict.get('jpegxl_effort_busy', 6)
        self.webp_method_busy: int = config_dict.get('webp_method_busy', 4)
        
        # Logging and notifications
        self.log_file: str = config_dict.get('log_file', 'image-squisher.log')
        self.log_verbosity: str = config_dict.get('log_verbosity', 'INFO').upper()
        self.enable_notifications: bool = config_dict.get('enable_notifications', False)
        
        # Validate values
        self._validate()
    
    def _validate(self) -> None:
        """Validate configuration values."""
        if self.threads < 1:
            raise ValueError("threads must be >= 1")
        if not (0 <= self.min_improvement_pct <= 100):
            raise ValueError("min_improvement_pct must be between 0 and 100")
        if self.hang_timeout < 1:
            raise ValueError("hang_timeout must be >= 1")
        if not (1 <= self.jpegxl_quality <= 100):
            raise ValueError("jpegxl_quality must be between 1 and 100")
        if not (0 <= self.jpegxl_effort <= 9):
            raise ValueError("jpegxl_effort must be between 0 and 9")
        if not (0 <= self.webp_method <= 6):
            raise ValueError("webp_method must be between 0 and 6")
        if not (1 <= self.webp_quality <= 100):
            raise ValueError("webp_quality must be between 1 and 100")
        if self.conversion_timeout < 1:
            raise ValueError("conversion_timeout must be >= 1")
        if self.max_animated_frames < 1:
            raise ValueError("max_animated_frames must be >= 1")
        if not (0.0 <= self.skip_second_threshold <= 1.0):
            raise ValueError("skip_second_threshold must be between 0.0 and 1.0")
        if self.busy_load_threshold < 0:
            raise ValueError("busy_load_threshold must be >= 0")
        if not (0 <= self.jpegxl_effort_busy <= 9):
            raise ValueError("jpegxl_effort_busy must be between 0 and 9")
        if not (0 <= self.webp_method_busy <= 6):
            raise ValueError("webp_method_busy must be between 0 and 6")
        if self.log_verbosity not in ('DEBUG', 'INFO', 'WARNING', 'ERROR'):
            raise ValueError("log_verbosity must be one of: DEBUG, INFO, WARNING, ERROR")
        
        # Normalize skip_extensions to lowercase with dots
        normalized = []
        for ext in self.skip_extensions:
            ext = ext.lower()
            if not ext.startswith('.'):
                ext = '.' + ext
            normalized.append(ext)
        self.skip_extensions = normalized
        
        if self.source_extensions is not None:
            source_normalized = []
            for ext in self.source_extensions:
                ext = ext.lower()
                if not ext.startswith('.'):
                    ext = '.' + ext
                source_normalized.append(ext)
            self.source_extensions = source_normalized


def load_config(config_path: Optional[Path] = None) -> Config:
    """
    Load configuration from a JSON file.
    
    Args:
        config_path: Path to config file. If None, looks for config.json in current directory.
        
    Returns:
        Config object with loaded settings
    """
    if config_path is None:
        config_path = Path('config.json')
        create_if_missing = True
    else:
        create_if_missing = False
    
    # If config file doesn't exist, optionally write defaults then load them
    if not config_path.exists():
        if create_if_missing:
            try:
                create_default_config(config_path)
            except OSError:
                return Config()
        if not config_path.exists():
            return Config()
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config_dict = json.load(f)
        # Remove _comments field if present (used for documentation only)
        config_dict.pop('_comments', None)
        return Config(config_dict)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in config file: {e}")
    except Exception as e:
        raise ValueError(f"Error loading config file: {e}")


def create_default_config(config_path: Path) -> None:
    """
    Create a default configuration file.
    
    Args:
        config_path: Path where to create the config file
    """
    default_config = {
        "threads": 1,
        "min_improvement_pct": 5.0,
        "hang_timeout": 300,
        "recursive": True,
        "skip_extensions": [".webp", ".jxl"],
        "source_extensions": [],
        "jpegxl_quality": 90,
        "jpegxl_effort": 9,
        "webp_method": 6,
        "webp_quality": 90,
        "webp_lossless": False,
        "conversion_timeout": 300,
        "max_animated_frames": 1000,
        "skip_second_threshold": 0.70,
        "adaptive_effort_enabled": True,
        "busy_load_threshold": 0.7,
        "jpegxl_effort_busy": 6,
        "webp_method_busy": 4,
        "log_file": "image-squisher.log",
        "log_verbosity": "INFO",
        "enable_notifications": False
    }
    
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(default_config, f, indent=2, ensure_ascii=False)

