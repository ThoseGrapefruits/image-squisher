"""Safe file operations for converted outputs."""

import os
from pathlib import Path
from typing import Optional, Tuple


def get_file_size(filepath: Path) -> int:
    """Get file size in bytes."""
    return filepath.stat().st_size


def verify_file(filepath: Path) -> bool:
    """
    Verify that a file is valid and readable.
    
    Args:
        filepath: Path to the file to verify
        
    Returns:
        True if file is valid, False otherwise
    """
    try:
        if not filepath.exists():
            return False
        # Try to open and read a small portion
        with open(filepath, 'rb') as f:
            f.read(1)
        return True
    except Exception:
        return False


def save_converted_file(original_path: Path, converted_path: Path, target_extension: str) -> Optional[Path]:
    """
    Move a converted temp file next to the original, keeping the original intact.
    
    Args:
        original_path: Path to the original image
        converted_path: Path to the converted temp file
        target_extension: Extension for the output file (e.g., '.webp', '.jxl')
        
    Returns:
        Path to the saved file, or None if the move failed
    """
    try:
        if not verify_file(converted_path):
            return None
        
        target_path = original_path.parent / f"{original_path.stem}{target_extension}"
        if converted_path != target_path:
            os.replace(converted_path, target_path)
        
        if not target_path.exists():
            return None
        
        return target_path
    except Exception:
        return None


def cleanup_temp_files(*filepaths: Optional[Path]) -> None:
    """
    Delete temporary files, ignoring errors.
    
    Args:
        *filepaths: Variable number of file paths to delete (None values are ignored)
    """
    for filepath in filepaths:
        if filepath and filepath.exists():
            try:
                filepath.unlink()
            except Exception:
                pass


def process_image(
    image_path: Path,
    jpegxl_quality: Optional[int] = None,
    jpegxl_effort: Optional[int] = None,
    webp_method: Optional[int] = None,
    webp_quality: Optional[int] = None,
    webp_lossless: Optional[bool] = None,
    max_animated_frames: Optional[int] = None,
    conversion_timeout: Optional[int] = None,
    skip_second_threshold: Optional[float] = None
) -> Tuple[bool, str, int, int]:
    """
    Process a single image: convert to JPEG XL and WebP, keep all successful outputs.
    
    Successful conversions are saved beside the original as {stem}.jxl and/or
    {stem}.webp. Same-stem files in one folder share those output names.
    
    Args:
        image_path: Path to the image to process
        
    Returns:
        Tuple of (success, formats_written, original_size, converted_total_size)
        success: True if processing completed successfully
        formats_written: 'jxl', 'webp', 'jxl+webp', or 'none'
        original_size: Size of original file in bytes
        converted_total_size: Combined size of written converted files in bytes
    """
    from processor import convert_image
    
    original_size = get_file_size(image_path)
    temp_dir = image_path.parent
    
    jxl_path, webp_path, jxl_size, webp_size = convert_image(
        image_path,
        temp_dir,
        original_size,
        jpegxl_quality=jpegxl_quality,
        jpegxl_effort=jpegxl_effort,
        webp_method=webp_method,
        webp_quality=webp_quality,
        webp_lossless=webp_lossless,
        max_animated_frames=max_animated_frames,
        conversion_timeout=conversion_timeout,
        skip_second_threshold=skip_second_threshold
    )
    
    try:
        written = []
        converted_total = 0
        
        if jxl_path and jxl_size is not None:
            saved = save_converted_file(image_path, jxl_path, '.jxl')
            if saved:
                written.append('jxl')
                converted_total += get_file_size(saved)
            else:
                cleanup_temp_files(jxl_path)
        
        if webp_path and webp_size is not None:
            saved = save_converted_file(image_path, webp_path, '.webp')
            if saved:
                written.append('webp')
                converted_total += get_file_size(saved)
            else:
                cleanup_temp_files(webp_path)
        
        format_name = '+'.join(written) if written else 'none'
        return True, format_name, original_size, converted_total
    
    except Exception:
        cleanup_temp_files(jxl_path, webp_path)
        return False, 'none', original_size, 0

