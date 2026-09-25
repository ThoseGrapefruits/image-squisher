"""Image conversion to JPEG XL, WebP, and progressive JPEG."""

import io
import logging
import subprocess
import shutil
import platform
import tempfile
import time
from pathlib import Path
from typing import Optional, Tuple, List
from PIL import Image, ImageOps

from format_detector import is_progressive_jpeg_output, register_optional_formats

register_optional_formats()

# Formats cjxl can usually read without a Pillow decode step.
CJXL_NATIVE_EXTS = {
    '.jpg', '.jpeg', '.jpe', '.jfif', '.png', '.gif',
    '.ppm', '.pfm', '.pgx', '.exr', '.jxl',
}


def is_animated_gif(image_path: Path) -> bool:
    """
    Check if a GIF file is animated (has multiple frames).
    
    Args:
        image_path: Path to the image file
        
    Returns:
        True if the file is an animated GIF, False otherwise
    """
    if image_path.suffix.lower() != '.gif':
        return False
    return is_animated_image(image_path)


def is_animated_image(image_path: Path) -> bool:
    """Return True if Pillow reports multiple frames."""
    try:
        with Image.open(image_path) as img:
            if getattr(img, 'is_animated', False):
                return True
            return getattr(img, 'n_frames', 1) > 1
    except Exception:
        return False


def _check_cjxl_available() -> Optional[str]:
    """Check if cjxl command is available and return its path."""
    # First try shutil.which (cross-platform)
    cjxl_path = shutil.which('cjxl')
    if cjxl_path and Path(cjxl_path).exists():
        return cjxl_path
    
    # Platform-specific paths
    system = platform.system()
    if system == 'Darwin':  # macOS
        cjxl_paths = [
            '/opt/homebrew/bin/cjxl',
            '/usr/local/bin/cjxl',
        ]
    elif system == 'Windows':
        # Windows: check common installation locations
        cjxl_paths = [
            Path.home() / 'AppData' / 'Local' / 'Programs' / 'cjxl.exe',
            Path('C:/Program Files/libjxl/bin/cjxl.exe'),
            Path('C:/Program Files (x86)/libjxl/bin/cjxl.exe'),
        ]
    else:  # Linux
        cjxl_paths = [
            '/usr/local/bin/cjxl',
            '/usr/bin/cjxl',
        ]
    
    for path in cjxl_paths:
        if isinstance(path, str):
            path = Path(path)
        if path.exists():
            return str(path)
    
    return None


def _prepare_pillow_image(img: Image.Image) -> Image.Image:
    """Apply EXIF orientation and a mode cjxl/WebP/JPEG can encode."""
    try:
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass
    if img.mode in ('P', 'LA', 'PA'):
        return img.convert('RGBA')
    if img.mode in ('RGB', 'RGBA', 'L'):
        return img
    return img.convert('RGB')


def _decode_for_cjxl(image_path: Path) -> Optional[Path]:
    """Decode via Pillow to a temp PNG that cjxl can read (HEIC, TIFF, etc.)."""
    logger = logging.getLogger('image-squisher')
    try:
        with Image.open(image_path) as img:
            img = _prepare_pillow_image(img)
            tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
            tmp_path = Path(tmp.name)
            tmp.close()
            save_kwargs = {'format': 'PNG'}
            icc = img.info.get('icc_profile')
            if icc:
                save_kwargs['icc_profile'] = icc
            img.save(tmp_path, **save_kwargs)
            return tmp_path
    except Exception as e:
        logger.debug(f"Decode for cjxl failed for {image_path.name}: {e}")
        return None


def _run_cjxl(
    cjxl: str,
    source_path: Path,
    output_path: Path,
    quality: int,
    effort: int,
    timeout: int
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            cjxl,
            str(source_path),
            str(output_path),
            '-q', str(quality),
            '-e', str(effort),
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def convert_to_jpegxl(image_path: Path, output_path: Path, quality: Optional[int] = None, effort: Optional[int] = None, timeout: Optional[int] = None) -> Optional[int]:
    """
    Convert an image to JPEG XL format (lossless, highest compression).
    Uses libjxl's cjxl command-line tool instead of Pillow.
    
    Note: JPEG XL doesn't support animation, so animated GIFs are skipped.
    
    Args:
        image_path: Path to the source image
        output_path: Path where the JPEG XL file should be saved
        quality: JPEG XL quality (1-100, 100 = lossless). If None, uses config value.
        effort: JPEG XL effort (0-9, 9 = highest compression). If None, uses config value.
        timeout: Conversion timeout in seconds. If None, uses config value.
        
    Returns:
        File size in bytes if successful, None if conversion failed
    """
    # Get settings from config if not provided
    if quality is None or effort is None or timeout is None:
        try:
            from config_loader import load_config
            config = load_config()
            if quality is None:
                quality = config.jpegxl_quality
            if effort is None:
                effort = config.jpegxl_effort
            if timeout is None:
                timeout = config.conversion_timeout
        except Exception:
            # Fallback to defaults
            if quality is None:
                quality = 100
            if effort is None:
                effort = 9
            if timeout is None:
                timeout = 300
    
    # Skip animated images - JPEG XL doesn't support animation
    if is_animated_image(image_path):
        return None
    
    # Check if cjxl is available
    cjxl = _check_cjxl_available()
    if not cjxl:
        return None
    
    logger = logging.getLogger('image-squisher')
    decoded_path = None
    cjxl_input = image_path
    suffix = image_path.suffix.lower()
    if suffix not in CJXL_NATIVE_EXTS:
        decoded_path = _decode_for_cjxl(image_path)
        if decoded_path is None:
            return None
        cjxl_input = decoded_path
    
    try:
        result = _run_cjxl(cjxl, cjxl_input, output_path, quality, effort, timeout)
        native_failed = result.returncode != 0 or not output_path.exists()
        should_retry = native_failed and decoded_path is None
        if should_retry:
            decoded_path = _decode_for_cjxl(image_path)
            if decoded_path is not None:
                cjxl_input = decoded_path
                result = _run_cjxl(cjxl, cjxl_input, output_path, quality, effort, timeout)
        
        if result.returncode == 0 and output_path.exists():
            return output_path.stat().st_size
        
        if result.stderr:
            error_msg = result.stderr.strip()
            if error_msg and not error_msg.startswith('Warn'):
                logger.debug(f"JXL conversion failed for {image_path.name}: {error_msg}")
        if output_path.exists():
            output_path.unlink()
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        if output_path.exists():
            output_path.unlink()
        return None
    finally:
        if decoded_path is not None and decoded_path.exists():
            decoded_path.unlink()


def _extract_animation_frames(
    img: Image.Image,
    max_frames: int,
    deadline: Optional[float]
) -> Optional[Tuple[List[Image.Image], List[int], int]]:
    """
    Extract composited RGBA frames from an animated image.
    Relies on Pillow's seek/load compositor (GIF disposal, APNG).
    Returns None if the deadline is hit or no frames are found.
    """
    frames: List[Image.Image] = []
    durations: List[int] = []
    loop = img.info.get('loop', 0)
    frame_count = 0
    
    while True:
        timed_out = deadline is not None and time.monotonic() > deadline
        if timed_out:
            logger = logging.getLogger('image-squisher')
            logger.warning("Animated conversion hit timeout")
            return None
        
        duration = img.info.get('duration', 100) or 100
        frames.append(img.convert('RGBA'))
        durations.append(duration)
        
        frame_count += 1
        if frame_count >= max_frames:
            break
        try:
            img.seek(img.tell() + 1)
        except EOFError:
            break
    
    if not frames:
        return None
    return frames, durations, loop


def _image_for_jpeg(img: Image.Image) -> Image.Image:
    """Return an RGB or L image. Alpha is composited onto white."""
    img = _prepare_pillow_image(img)
    if img.mode == 'RGBA':
        background = Image.new('RGB', img.size, (255, 255, 255))
        background.paste(img, mask=img.getchannel('A'))
        return background
    if img.mode == 'L':
        return img
    if img.mode != 'RGB':
        return img.convert('RGB')
    return img


def convert_to_progressive_jpeg(
    image_path: Path,
    output_path: Path,
    quality: Optional[int] = None
) -> Optional[int]:
    """
    Re-encode an image as a progressive JPEG via Pillow.
    Animated sources and existing {stem}.p.jpg outputs are skipped.
    """
    if is_progressive_jpeg_output(image_path):
        return None
    if is_animated_image(image_path):
        return None

    if quality is None:
        try:
            from config_loader import load_config
            quality = load_config().jpeg_quality
        except Exception:
            quality = 90

    try:
        with Image.open(image_path) as img:
            icc = img.info.get('icc_profile')
            prepared = _image_for_jpeg(img)
            save_kwargs = {
                'format': 'JPEG',
                'quality': quality,
                'progressive': True,
                'optimize': True,
            }
            if icc:
                save_kwargs['icc_profile'] = icc
            prepared.save(output_path, **save_kwargs)
            return output_path.stat().st_size
    except Exception as e:
        logger = logging.getLogger('image-squisher')
        logger.debug(f"Progressive JPEG conversion failed for {image_path.name}: {e}")
        if output_path.exists():
            try:
                output_path.unlink()
            except Exception:
                pass
        return None


def convert_to_webp(
    image_path: Path,
    output_path: Path,
    method: Optional[int] = None,
    max_frames: Optional[int] = None,
    timeout: Optional[int] = None,
    quality: Optional[int] = None,
    lossless: Optional[bool] = None
) -> Optional[int]:
    """
    Convert an image to WebP. Default is high-quality lossy (quality 90).
    Supports static images and animated formats Pillow can seek (GIF, APNG).
    """
    if method is None or max_frames is None or quality is None or lossless is None:
        try:
            from config_loader import load_config
            config = load_config()
            if method is None:
                method = config.webp_method
            if max_frames is None:
                max_frames = config.max_animated_frames
            if quality is None:
                quality = config.webp_quality
            if lossless is None:
                lossless = config.webp_lossless
        except Exception:
            if method is None:
                method = 6
            if max_frames is None:
                max_frames = 1000
            if quality is None:
                quality = 90
            if lossless is None:
                lossless = False
    
    deadline = None
    if timeout is not None and timeout > 0:
        deadline = time.monotonic() + timeout
    
    save_kwargs = {
        'format': 'WEBP',
        'method': method,
        'lossless': lossless,
    }
    if not lossless:
        save_kwargs['quality'] = quality
    
    try:
        with Image.open(image_path) as img:
            animated = getattr(img, 'is_animated', False) or getattr(img, 'n_frames', 1) > 1
            if animated:
                extracted = _extract_animation_frames(img, max_frames, deadline)
                if extracted is None:
                    return None
                frames, durations, loop = extracted
                
                frames[0].save(
                    output_path,
                    save_all=True,
                    append_images=frames[1:],
                    duration=durations,
                    loop=loop,
                    **save_kwargs,
                )
                return output_path.stat().st_size
            
            try:
                img = ImageOps.exif_transpose(img)
            except Exception:
                pass
            if img.mode in ('P', 'LA', 'PA'):
                img = img.convert('RGBA')
            elif img.mode == 'L':
                pass
            elif img.mode not in ('RGB', 'RGBA'):
                img = img.convert('RGB')
            
            img.save(output_path, **save_kwargs)
            return output_path.stat().st_size
    except Exception as e:
        logger = logging.getLogger('image-squisher')
        logger.debug(f"WebP conversion failed for {image_path.name}: {e}")
        if output_path.exists():
            try:
                output_path.unlink()
            except Exception:
                pass
        return None


def convert_image(
    image_path: Path,
    temp_dir: Path,
    original_size: Optional[int] = None,
    jpegxl_quality: Optional[int] = None,
    jpegxl_effort: Optional[int] = None,
    webp_method: Optional[int] = None,
    webp_quality: Optional[int] = None,
    webp_lossless: Optional[bool] = None,
    jpeg_quality: Optional[int] = None,
    max_animated_frames: Optional[int] = None,
    conversion_timeout: Optional[int] = None,
    skip_second_threshold: Optional[float] = None
) -> Tuple[Optional[Path], Optional[Path], Optional[int], Optional[int]]:
    """
    Convert an image to JPEG XL, WebP, and progressive JPEG.
    
    Args:
        image_path: Path to the source image
        temp_dir: Directory where temporary converted files should be saved
        original_size: Original file size in bytes
        
    Returns:
        Tuple of (jxl_path, webp_path, pjpg_path, jxl_size, webp_size, pjpg_size)
        Paths and sizes will be None if conversion failed
    """
    logger = logging.getLogger('image-squisher')

    base_name = image_path.stem

    jxl_path = temp_dir / f"{base_name}.tmp.jxl"
    webp_path = temp_dir / f"{base_name}.tmp.webp"
    pjpg_path = temp_dir / f"{base_name}.tmp.p.jpg"
    
    missing_settings = (
        jpegxl_quality is None
        or jpegxl_effort is None
        or webp_method is None
        or webp_quality is None
        or webp_lossless is None
        or jpeg_quality is None
        or max_animated_frames is None
        or conversion_timeout is None
        or skip_second_threshold is None
    )
    if missing_settings:
        try:
            from config_loader import load_config
            config = load_config()
            if jpegxl_quality is None:
                jpegxl_quality = config.jpegxl_quality
            if jpegxl_effort is None:
                jpegxl_effort = config.jpegxl_effort
            if webp_method is None:
                webp_method = config.webp_method
            if webp_quality is None:
                webp_quality = config.webp_quality
            if webp_lossless is None:
                webp_lossless = config.webp_lossless
            if jpeg_quality is None:
                jpeg_quality = config.jpeg_quality
            if max_animated_frames is None:
                max_animated_frames = config.max_animated_frames
            if conversion_timeout is None:
                conversion_timeout = config.conversion_timeout
            if skip_second_threshold is None:
                skip_second_threshold = config.skip_second_threshold
        except Exception:
            if jpegxl_quality is None:
                jpegxl_quality = 90
            if jpegxl_effort is None:
                jpegxl_effort = 9
            if webp_method is None:
                webp_method = 6
            if webp_quality is None:
                webp_quality = 90
            if webp_lossless is None:
                webp_lossless = False
            if jpeg_quality is None:
                jpeg_quality = 90
            if max_animated_frames is None:
                max_animated_frames = 1000
            if conversion_timeout is None:
                conversion_timeout = 300
            if skip_second_threshold is None:
                skip_second_threshold = 0.70
    
    jxl_size: Optional[int] = None
    webp_size: Optional[int] = None
    pjpg_size: Optional[int] = None
    jxl_error: Optional[str] = None
    webp_error: Optional[str] = None
    pjpg_error: Optional[str] = None

    # PNG/GIF/BMP often compress well as WebP; JPEG/TIFF often as JXL. Order is cosmetic.
    prefer_webp_exts = {'.png', '.gif', '.bmp'}
    suffix = image_path.suffix.lower()
    run_order: List[str] = ['webp', 'jxl'] if suffix in prefer_webp_exts else ['jxl', 'webp']
    if suffix == '.jxl':
        run_order = [codec for codec in run_order if codec != 'jxl']
    elif suffix == '.webp':
        run_order = [codec for codec in run_order if codec != 'webp']
    if not is_progressive_jpeg_output(image_path):
        run_order.append('pjpg')

    for codec in run_order:
        if codec == 'jxl':
            try:
                jxl_size = convert_to_jpegxl(
                    image_path,
                    jxl_path,
                    quality=jpegxl_quality,
                    effort=jpegxl_effort,
                    timeout=conversion_timeout
                )
                if jxl_size is None:
                    logger.debug(f"JXL conversion failed for {image_path.name}")
                else:
                    logger.debug(f"JXL conversion succeeded for {image_path.name}: {jxl_size} bytes")
            except Exception as e:
                jxl_error = str(e)
                logger.warning(f"JXL conversion exception for {image_path.name}: {e}", exc_info=True)
        elif codec == 'pjpg':
            try:
                pjpg_size = convert_to_progressive_jpeg(
                    image_path,
                    pjpg_path,
                    quality=jpeg_quality
                )
                if pjpg_size is None:
                    logger.debug(f"Progressive JPEG conversion failed for {image_path.name}")
                else:
                    logger.debug(
                        f"Progressive JPEG conversion succeeded for {image_path.name}: {pjpg_size} bytes"
                    )
            except Exception as e:
                pjpg_error = str(e)
                logger.warning(
                    f"Progressive JPEG conversion exception for {image_path.name}: {e}",
                    exc_info=True
                )
        else:
            try:
                webp_size = convert_to_webp(
                    image_path,
                    webp_path,
                    method=webp_method,
                    max_frames=max_animated_frames,
                    timeout=conversion_timeout,
                    quality=webp_quality,
                    lossless=webp_lossless
                )
                if webp_size is None:
                    logger.debug(f"WebP conversion failed for {image_path.name}")
                else:
                    logger.debug(f"WebP conversion succeeded for {image_path.name}: {webp_size} bytes")
            except Exception as e:
                webp_error = str(e)
                logger.warning(f"WebP conversion exception for {image_path.name}: {e}", exc_info=True)
    
    # Log results for debugging
    if jxl_size is None and webp_size is None and pjpg_size is None:
        logger.warning(f"All conversions failed for {image_path.name}")
        if jxl_error:
            logger.warning(f"JXL error: {jxl_error}")
        if webp_error:
            logger.warning(f"WebP error: {webp_error}")
        if pjpg_error:
            logger.warning(f"Progressive JPEG error: {pjpg_error}")
    elif jxl_size is None and webp_size is not None:
        logger.debug(f"JXL conversion failed, WebP succeeded ({webp_size} bytes) for {image_path.name}")
    elif webp_size is None and jxl_size is not None:
        logger.debug(f"WebP conversion failed, JXL succeeded ({jxl_size} bytes) for {image_path.name}")
    elif jxl_size is not None and webp_size is not None:
        logger.debug(f"Both conversions succeeded for {image_path.name}: JXL={jxl_size} bytes, WebP={webp_size} bytes")
    
    # Clean up if conversion failed
    if jxl_size is None and jxl_path.exists():
        jxl_path.unlink()
        jxl_path = None
    
    if webp_size is None and webp_path.exists():
        webp_path.unlink()
        webp_path = None

    if pjpg_size is None and pjpg_path.exists():
        pjpg_path.unlink()
        pjpg_path = None
    
    return jxl_path, webp_path, pjpg_path, jxl_size, webp_size, pjpg_size

