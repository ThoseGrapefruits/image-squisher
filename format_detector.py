"""Format detection and image file scanning."""

from pathlib import Path
from typing import List, Set, Optional
from PIL import Image

# Common image extensions
IMAGE_EXTENSIONS = {
    '.png', '.jpg', '.jpeg', '.jpe', '.jfif',
    '.tiff', '.tif', '.bmp', '.gif', '.webp',
    '.heic', '.heif', '.heics', '.heifs', '.hif',
    '.avif', '.jxl', '.jp2',
    '.ico', '.icns', '.tga', '.dds'
}


def register_optional_formats() -> None:
    """Register HEIC/HEIF with Pillow when pillow-heif is installed."""
    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
    except ImportError:
        pass


register_optional_formats()


def normalize_extensions(values: Optional[List[str]]) -> Optional[List[str]]:
    """
    Normalize extension strings to lowercase dotted form.
    Accepts comma-separated entries. Returns None if values is empty/None.
    """
    if not values:
        return None
    exts: List[str] = []
    for value in values:
        if value is None:
            continue
        for part in str(value).split(','):
            part = part.strip().lower()
            if not part:
                continue
            if not part.startswith('.'):
                part = '.' + part
            if part not in exts:
                exts.append(part)
    if not exts:
        return None
    return exts


def is_image_file(filepath: Path) -> bool:
    """Check if a file is a valid image by reading headers, not pixels."""
    try:
        with Image.open(filepath) as img:
            img.size
        return True
    except Exception:
        return False


def scan_folder(
    folder_path: Path,
    recursive: bool = False,
    skip_extensions: Optional[List[str]] = None,
    source_extensions: Optional[List[str]] = None
) -> List[Path]:
    """
    Scan a folder for image files.
    
    Args:
        folder_path: Path to the folder to scan
        recursive: If True, scan subdirectories recursively
        skip_extensions: Extensions to skip when source_extensions is not set.
                        Defaults to config / .webp and .jxl.
        source_extensions: If set, only these extensions are processed and
                          skip_extensions is ignored.
        
    Returns:
        List of paths to valid image files
    """
    image_files = []
    
    source_set = None
    if source_extensions:
        source_set = {ext.lower() if ext.startswith('.') else f'.{ext.lower()}' for ext in source_extensions}
    
    if skip_extensions is None:
        try:
            from config_loader import load_config
            config = load_config()
            skip_extensions_set = set(config.skip_extensions)
        except Exception:
            skip_extensions_set = {'.webp', '.jxl'}
    else:
        skip_extensions_set = {ext.lower() for ext in skip_extensions}
    
    if recursive:
        pattern = '**/*'
    else:
        pattern = '*'
    
    for filepath in folder_path.glob(pattern):
        if not filepath.is_file():
            continue
        suffix = filepath.suffix.lower()
        if source_set is not None:
            if suffix not in source_set:
                continue
        else:
            if suffix not in IMAGE_EXTENSIONS:
                continue
            if suffix in skip_extensions_set:
                continue
        if is_image_file(filepath):
            image_files.append(filepath)
    
    return sorted(image_files)


def detect_formats(image_files: List[Path]) -> Set[str]:
    """
    Detect which image formats are present in the file list.
    
    Args:
        image_files: List of image file paths
        
    Returns:
        Set of file extensions (lowercase, with dot) found
    """
    formats = set()
    for filepath in image_files:
        formats.add(filepath.suffix.lower())
    return formats
