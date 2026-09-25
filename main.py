#!/usr/bin/env python3
"""Image compression tool - converts images to JPEG XL, WebP, and progressive JPEG."""

import argparse
import sys
import subprocess
import shutil
import logging
from logging.handlers import RotatingFileHandler
import signal
import time
import os
import platform
import threading
from pathlib import Path
from typing import List, Optional, Tuple
from queue import Queue, Empty

from format_detector import scan_folder, detect_formats, normalize_extensions
from file_manager import process_image
from config_loader import load_config, Config


def format_bytes(bytes: int) -> str:
    """Format bytes as human-readable string."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes < 1024.0:
            return f"{bytes:.2f} {unit}"
        bytes /= 1024.0
    return f"{bytes:.2f} TB"


def _record_formats(results: dict, format_kept: str) -> None:
    """Increment per-format counters from a formats_written string like 'jxl+webp+pjpg'."""
    if format_kept == 'none' or not format_kept:
        results['none'] += 1
        return
    names = format_kept.split('+')
    for name in names:
        if name in results:
            results[name] += 1


def format_progress_line(done: int, total: int, bar_width: int = 28) -> str:
    """Single-line progress: bar, count, and percentage (for terminal updates)."""
    if total <= 0:
        return "Progress: —"
    pct = 100.0 * done / total
    filled = min(bar_width, round(bar_width * done / total))
    bar = "#" * filled + "-" * (bar_width - filled)
    return f"[{bar}] {done}/{total} ({pct:.1f}%)"


def report_batch_progress(done: int, total: int, *, is_tty: bool, last_bucket: list) -> None:
    """
    Update progress on stdout. TTY: rewrite one line with \\r; non-TTY: at most ~20 lines per batch.
    last_bucket: single-element list holding last printed twentieth bucket (0..20) for non-TTY.
    """
    if total <= 0:
        return
    line = format_progress_line(done, total)
    if is_tty:
        # Pad to clear remnants from a longer previous line
        pad = max(0, 96 - len(line))
        sys.stdout.write("\r" + line + (" " * pad))
        sys.stdout.flush()
        return
    # Non-TTY: print when crossing ~5% steps (or first/last file)
    bucket = (done * 20) // total
    if done == total or done == 1 or bucket > last_bucket[0]:
        last_bucket[0] = bucket
        print(line, flush=True)


def check_terminal_notifier() -> Optional[str]:
    """Check if terminal-notifier is available and return its path (macOS only)."""
    system = platform.system()
    if system != 'Darwin':
        # terminal-notifier is macOS-only
        return None
    
    # Try shutil.which first (cross-platform)
    notifier_path = shutil.which('terminal-notifier')
    if notifier_path and Path(notifier_path).exists():
        return notifier_path
    
    # macOS-specific paths
    paths = [
        '/opt/homebrew/bin/terminal-notifier',
        '/usr/local/bin/terminal-notifier',
    ]
    for path in paths:
        if path and Path(path).exists():
            return path
    return None


def _ps_escape(text: str) -> str:
    """Escape a string for inclusion in a PowerShell double-quoted string."""
    return (
        text.replace('`', '``')
        .replace('"', '`"')
        .replace('$', '`$')
        .replace('\n', '`n')
    )


def send_notification(title: str, message: str, sound: str = 'default', enabled: bool = True) -> bool:
    """
    Send a notification (macOS: terminal-notifier, Windows: toast, Linux: notify-send).
    
    Args:
        title: Notification title
        message: Notification message
        sound: Notification sound (macOS only, default: 'default')
        enabled: Whether notifications are enabled
        
    Returns:
        True if notification was sent, False otherwise
    """
    if not enabled:
        return False
    
    system = platform.system()
    
    if system == 'Darwin':  # macOS
        notifier = check_terminal_notifier()
        if not notifier:
            return False
        try:
            subprocess.run(
                [
                    notifier,
                    '-title', title,
                    '-message', message,
                    '-sound', sound
                ],
                capture_output=True,
                timeout=5
            )
            return True
        except Exception:
            return False
    
    elif system == 'Windows':  # Windows
        try:
            # Use Windows toast notifications via PowerShell
            # Escape message for PowerShell
            escaped_title = _ps_escape(title)
            escaped_message = _ps_escape(message)
            ps_command = f'[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null; $template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02); $text = $template.GetElementsByTagName("text"); $text[0].AppendChild($template.CreateTextNode("{escaped_title}")) > $null; $text[1].AppendChild($template.CreateTextNode("{escaped_message}")) > $null; $toast = [Windows.UI.Notifications.ToastNotification]::new($template); [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Image Squisher").Show($toast)'
            subprocess.run(
                ['powershell', '-NoProfile', '-Command', ps_command],
                capture_output=True,
                timeout=5
            )
            return True
        except Exception:
            # Fallback: try win10toast if available, or just return False
            return False
    
    elif system == 'Linux':  # Linux
        try:
            # Try notify-send (common on Linux)
            subprocess.run(
                ['notify-send', title, message],
                capture_output=True,
                timeout=5
            )
            return True
        except Exception:
            return False
    
    return False


def setup_logging(log_file: Optional[str] = None, log_verbosity: str = 'INFO') -> logging.Logger:
    """
    Set up logging to both file and console.
    
    Args:
        log_file: Path to log file (default: image-squisher.log in current directory)
        log_verbosity: Logging level ('DEBUG', 'INFO', 'WARNING', 'ERROR')
        
    Returns:
        Configured logger
    """
    if log_file is None:
        log_file = 'image-squisher.log'
    
    log_file = Path(log_file)
    
    # Map verbosity string to logging level
    verbosity_map = {
        'DEBUG': logging.DEBUG,
        'INFO': logging.INFO,
        'WARNING': logging.WARNING,
        'ERROR': logging.ERROR
    }
    log_level = verbosity_map.get(log_verbosity.upper(), logging.INFO)
    
    # Create logger
    logger = logging.getLogger('image-squisher')
    logger.setLevel(log_level)
    
    # Remove existing handlers
    logger.handlers.clear()
    
    # File handler (uses configured verbosity) with rotation to reduce unbounded log growth
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=25 * 1024 * 1024,  # 25 MB per file
        backupCount=5,
    )
    file_handler.setLevel(log_level)
    file_formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)
    
    # Console handler (less verbose - always WARNING or higher)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)  # Only show warnings/errors to console
    console_formatter = logging.Formatter('%(levelname)s: %(message)s')
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)
    
    return logger


def main():
    parser = argparse.ArgumentParser(
        description='Convert images to JPEG XL, WebP, and progressive JPEG, keeping all output files alongside the original.'
    )
    parser.add_argument(
        'folder',
        type=str,
        help='Path to folder containing images'
    )
    parser.add_argument(
        '--no-recursive',
        action='store_true',
        help='Only process top-level folder (disable recursive scanning)'
    )
    parser.add_argument(
        '--config',
        type=str,
        help='Path to config file (default: config.json in current directory)'
    )
    parser.add_argument(
        '--workers',
        type=int,
        default=None,
        help='Number of parallel workers (overrides config.threads)'
    )
    parser.add_argument(
        '--source',
        action='append',
        default=None,
        metavar='EXT',
        help='Source file extension to process (e.g. .heic). Repeatable or comma-separated. Default: all supported types except skip_extensions.'
    )
    
    args = parser.parse_args()
    
    folder_path = Path(args.folder)
    
    if not folder_path.exists():
        print(f"Error: Folder '{folder_path}' does not exist.", file=sys.stderr)
        sys.exit(1)
    
    if not folder_path.is_dir():
        print(f"Error: '{folder_path}' is not a directory.", file=sys.stderr)
        sys.exit(1)
    
    # Load configuration
    try:
        config_path = Path(args.config) if args.config else None
        config = load_config(config_path)
    except Exception as e:
        print(f"Warning: Could not load config file: {e}", file=sys.stderr)
        print("Using default configuration.", file=sys.stderr)
        config = Config()
    
    # Set up logging
    logger = setup_logging(config.log_file, config.log_verbosity)
    
    print(f"Scanning folder: {folder_path}")
    # Command-line argument overrides config
    recursive = not args.no_recursive if args.no_recursive else config.recursive
    source_extensions = normalize_extensions(args.source)
    if source_extensions is None:
        source_extensions = config.source_extensions
    if recursive:
        print("Mode: Recursive (processing subdirectories)")
    else:
        print("Mode: Top-level only")
    if source_extensions:
        print(f"Source types: {', '.join(source_extensions)}")
        heic_exts = {'.heic', '.heif', '.heics', '.heifs', '.hif'}
        wants_heic = any(ext in heic_exts for ext in source_extensions)
        from PIL import Image as _PilImage
        heic_registered = '.heic' in _PilImage.registered_extensions()
        if wants_heic and not heic_registered:
            print("Warning: HEIC opener is not registered. Install pillow-heif (macOS: pip install pillow-heif).")
    print()
    
    # Scan for images
    image_files = scan_folder(
        folder_path,
        recursive=recursive,
        skip_extensions=config.skip_extensions,
        source_extensions=source_extensions
    )
    
    if not image_files:
        print("No image files found.")
        sys.exit(0)
    
    # Detect and report formats
    formats = detect_formats(image_files)
    print(f"Found {len(image_files)} image file(s)")
    print(f"Formats detected: {', '.join(sorted(formats))}")
    
    # Check if JPEG XL support is available (via libjxl command-line tool)
    from processor import _check_cjxl_available
    cjxl_path = _check_cjxl_available()
    
    if not cjxl_path:
        system = platform.system()
        print("⚠ Warning: JPEG XL support not available (libjxl not installed)")
        if system == 'Darwin':
            print("  Install via: brew install jpeg-xl")
        elif system == 'Windows':
            print("  Install via: Download from https://github.com/libjxl/libjxl/releases")
            print("  Or use: winget install libjxl (if available)")
        else:
            print("  Install via your package manager (e.g., apt install libjxl-tools)")
        print("  Images will still be converted to WebP and progressive JPEG.")
    else:
        print(f"✓ JPEG XL support available (using {cjxl_path})")
    print()
    
    # Determine number of workers (--workers overrides config.threads)
    if args.workers is not None:
        num_workers = args.workers
    else:
        num_workers = config.threads
    
    if num_workers < 1:
        num_workers = 1
    if num_workers > len(image_files):
        num_workers = len(image_files)
    
    print(f"Using {num_workers} parallel worker(s)")
    print()
    
    # Process images in parallel
    total_original = 0
    total_final = 0
    results = {'jxl': 0, 'webp': 0, 'pjpg': 0, 'none': 0}
    errors = 0
    start_time = time.time()
    last_progress_time = time.time()
    hang_timeout = config.hang_timeout
    
    # Thread-safe counters and locks
    results_lock = threading.Lock()
    completed_count = [0]  # Use list for mutable reference
    
    # Runtime-adjustable concurrency: only slot_state['max_concurrent'] workers run at once.
    # Signal handlers (SIGUSR1/SIGUSR2 on Unix) can change it mid-batch.
    slot_condition = threading.Condition()
    slot_state = {'active': 0, 'max_concurrent': num_workers}
    
    cpu_count = max(1, os.cpu_count() or 1)
    adaptive_state = {'last_mode': None}
    
    def get_load_adaptive_settings():
        """Return conversion settings adapted to current system load."""
        normalized_load = 0.0
        if hasattr(os, 'getloadavg'):
            try:
                load1, _, _ = os.getloadavg()
                normalized_load = load1 / cpu_count
            except OSError:
                normalized_load = 0.0
        
        is_busy = (
            config.adaptive_effort_enabled
            and normalized_load >= config.busy_load_threshold
        )
        
        if is_busy:
            mode = 'busy'
            settings = {
                'jpegxl_quality': config.jpegxl_quality,
                'jpegxl_effort': config.jpegxl_effort_busy,
                'webp_method': config.webp_method_busy,
                'webp_quality': config.webp_quality,
                'webp_lossless': config.webp_lossless,
                'jpeg_quality': config.jpeg_quality,
                'max_animated_frames': config.max_animated_frames,
                'conversion_timeout': config.conversion_timeout,
                'skip_second_threshold': config.skip_second_threshold,
            }
        else:
            mode = 'normal'
            settings = {
                'jpegxl_quality': config.jpegxl_quality,
                'jpegxl_effort': config.jpegxl_effort,
                'webp_method': config.webp_method,
                'webp_quality': config.webp_quality,
                'webp_lossless': config.webp_lossless,
                'jpeg_quality': config.jpeg_quality,
                'max_animated_frames': config.max_animated_frames,
                'conversion_timeout': config.conversion_timeout,
                'skip_second_threshold': config.skip_second_threshold,
            }
        
        if adaptive_state['last_mode'] != mode:
            adaptive_state['last_mode'] = mode
            if mode == 'busy':
                logger.info(
                    f"Adaptive effort: BUSY mode (load={normalized_load:.2f}, "
                    f"jxl_effort={settings['jpegxl_effort']}, webp_method={settings['webp_method']})"
                )
            else:
                logger.info(
                    f"Adaptive effort: NORMAL mode (load={normalized_load:.2f}, "
                    f"jxl_effort={settings['jpegxl_effort']}, webp_method={settings['webp_method']})"
                )
        
        return settings
    
    def process_worker(image_queue: Queue, result_queue: Queue):
        """Worker thread that processes images from the queue."""
        while True:
            item = image_queue.get()
            if item is None:  # Poison pill
                break
            
            index, image_path = item
            # Acquire a concurrency slot (throttled by slot_state['max_concurrent'])
            with slot_condition:
                while slot_state['active'] >= slot_state['max_concurrent']:
                    slot_condition.wait()
                slot_state['active'] += 1
            try:
                conversion_settings = get_load_adaptive_settings()
                success, format_kept, original_size, final_size = process_image(
                    image_path,
                    jpegxl_quality=conversion_settings['jpegxl_quality'],
                    jpegxl_effort=conversion_settings['jpegxl_effort'],
                    webp_method=conversion_settings['webp_method'],
                    webp_quality=conversion_settings['webp_quality'],
                    webp_lossless=conversion_settings['webp_lossless'],
                    jpeg_quality=conversion_settings['jpeg_quality'],
                    max_animated_frames=conversion_settings['max_animated_frames'],
                    conversion_timeout=conversion_settings['conversion_timeout'],
                    skip_second_threshold=conversion_settings['skip_second_threshold']
                )
                result_queue.put((index, image_path, success, format_kept, original_size, final_size, None))
            except Exception as e:
                # Log exception with full traceback here where exception context exists
                error_msg = f"Exception processing {image_path.name}: {str(e)}"
                logger.error(error_msg, exc_info=True)
                logger.error(f"Error in folder: {image_path.parent}")
                result_queue.put((index, image_path, False, 'original', 0, 0, error_msg))
            finally:
                with slot_condition:
                    slot_state['active'] -= 1
                    slot_condition.notify_all()
                image_queue.task_done()
    
    if num_workers > 1:
        progress_tty = sys.stdout.isatty()
        progress_bucket = [0]
        # Use threading for parallel processing
        image_queue = Queue()
        result_queue = Queue()
        
        # Optional: throttle/restore workers mid-batch via signals (Unix/macOS)
        if hasattr(signal, 'SIGUSR1') and hasattr(signal, 'SIGUSR2'):
            def _throttle_handler(signum, frame):
                with slot_condition:
                    slot_state['max_concurrent'] = 1
                    slot_condition.notify_all()
                print("\n  → Throttled to 1 worker (send SIGUSR2 to restore)", flush=True)
            def _restore_handler(signum, frame):
                with slot_condition:
                    slot_state['max_concurrent'] = num_workers
                    slot_condition.notify_all()
                print(f"\n  → Restored to {num_workers} worker(s)", flush=True)
            signal.signal(signal.SIGUSR1, _throttle_handler)
            signal.signal(signal.SIGUSR2, _restore_handler)
            print("  (Tip: SIGUSR1 = throttle to 1 worker, SIGUSR2 = restore — e.g. kill -USR1 <pid>)")
        
        # Start worker threads
        workers = []
        for _ in range(num_workers):
            worker = threading.Thread(target=process_worker, args=(image_queue, result_queue))
            worker.daemon = True
            worker.start()
            workers.append(worker)
        
        # Add all images to queue
        for i, image_path in enumerate(image_files):
            image_queue.put((i, image_path))
        
        # Collect results as they complete with hang detection
        results_dict = {}
        hang_check_interval = 60  # Check for hangs every 60 seconds
        last_hang_check = time.time()
        
        while len(results_dict) < len(image_files):
            try:
                # Use timeout to periodically check for hangs
                timeout = min(hang_timeout, hang_check_interval)
                index, image_path, success, format_kept, original_size, final_size, error_msg = result_queue.get(timeout=timeout)
                results_dict[index] = (image_path, success, format_kept, original_size, final_size, error_msg)
                completed_count[0] += 1
                report_batch_progress(
                    len(results_dict),
                    len(image_files),
                    is_tty=progress_tty,
                    last_bucket=progress_bucket,
                )
                last_progress_time = time.time()
                last_hang_check = time.time()
            except Empty:
                # Timeout occurred - check for potential hang
                current_time = time.time()
                time_since_progress = current_time - last_progress_time
                
                if time_since_progress > hang_timeout:
                    # Potential hang detected
                    # Find which images are still being processed
                    processed_indices = set(results_dict.keys())
                    remaining_indices = [i for i in range(len(image_files)) if i not in processed_indices]
                    
                    if remaining_indices:
                        # Report the first remaining image as potentially stuck
                        stuck_index = remaining_indices[0]
                        stuck_image = image_files[stuck_index]
                        error_msg = f"Potential hang detected! No progress for {time_since_progress:.0f} seconds"
                        logger.error(error_msg)
                        logger.error(f"Current folder: {stuck_image.parent}")
                        logger.error(f"Stuck on file: {stuck_image.name}")
                        logger.error(f"Remaining images: {len(remaining_indices)}")
                        send_notification(
                            'Image Squisher - Hang Detected',
                            f"Script may be hung processing:\n{stuck_image.parent}\n\nFile: {stuck_image.name}\n\nNo progress for {time_since_progress:.0f}s",
                            'Basso',
                            config.enable_notifications
                        )
                        print(f"\n⚠ WARNING: Potential hang detected! Check log file for details.")
                        print(f"   Current folder: {stuck_image.parent}")
                        print(f"   Stuck on file: {stuck_image.name}")
                        print(f"   No progress for {time_since_progress:.0f} seconds")
                        # Reset last_progress_time to avoid spamming notifications
                        last_progress_time = current_time
                
                # Continue waiting for results
                continue
        
        if progress_tty:
            print()
        
        # Stop workers
        for _ in range(num_workers):
            image_queue.put(None)
        for worker in workers:
            worker.join()
        
        # Process results in order
        for i in range(len(image_files)):
            image_path, success, format_kept, original_size, final_size, error_msg = results_dict[i]
            
            print(f"[{i+1}/{len(image_files)}] Processing: {image_path.name}", end=' ... ', flush=True)
            
            if success:
                total_original += original_size
                total_final += final_size
                _record_formats(results, format_kept)
                
                print(f"wrote {format_kept.upper()} "
                      f"(orig {format_bytes(original_size)}, converted {format_bytes(final_size)})")
            else:
                errors += 1
                if error_msg:
                    # Exception was already logged with traceback in worker thread
                    # Just log the error message here (no exc_info since we're outside exception context)
                    logger.error(error_msg)
                    send_notification(
                        'Image Squisher - Error',
                        f"Error processing:\n{image_path.name}\n\nFolder: {image_path.parent}",
                        'Basso',
                        config.enable_notifications
                    )
                else:
                    logger.warning(f"Failed to process {image_path.name}, kept original")
                    send_notification(
                        'Image Squisher - Error',
                        f"Error processing:\n{image_path.name}\n\nFolder: {image_path.parent}",
                        'Basso',
                        config.enable_notifications
                    )
                print(f"ERROR (kept original)")
    else:
        # Single-threaded processing
        current_file = {'path': None}
        progress_box = {'t': time.time()}
        stop_watchdog = threading.Event()
        
        def _single_thread_watchdog():
            while not stop_watchdog.wait(60):
                stuck = current_file['path']
                elapsed = time.time() - progress_box['t']
                still_running = stuck is not None and elapsed > hang_timeout
                if not still_running:
                    continue
                error_msg = f"Potential hang detected! No progress for {elapsed:.0f} seconds"
                logger.error(error_msg)
                logger.error(f"Current folder: {stuck.parent}")
                logger.error(f"Stuck on file: {stuck.name}")
                send_notification(
                    'Image Squisher - Hang Detected',
                    f"Script may be hung processing:\n{stuck.parent}\n\nFile: {stuck.name}",
                    'Basso',
                    config.enable_notifications
                )
                print(f"\n⚠ WARNING: Potential hang detected! Check log file for details.")
                print(f"   Current folder: {stuck.parent}")
                print(f"   Stuck on file: {stuck.name}")
                progress_box['t'] = time.time()
        
        watcher = threading.Thread(target=_single_thread_watchdog, daemon=True)
        watcher.start()
        
        try:
            for i, image_path in enumerate(image_files, 1):
                current_file['path'] = image_path
                progress_box['t'] = time.time()
                
                print(f"[{i}/{len(image_files)}] Processing: {image_path.name}", end=' ... ', flush=True)
                
                try:
                    conversion_settings = get_load_adaptive_settings()
                    success, format_kept, original_size, final_size = process_image(
                        image_path,
                        jpegxl_quality=conversion_settings['jpegxl_quality'],
                        jpegxl_effort=conversion_settings['jpegxl_effort'],
                        webp_method=conversion_settings['webp_method'],
                        webp_quality=conversion_settings['webp_quality'],
                        webp_lossless=conversion_settings['webp_lossless'],
                        jpeg_quality=conversion_settings['jpeg_quality'],
                        max_animated_frames=conversion_settings['max_animated_frames'],
                        conversion_timeout=conversion_settings['conversion_timeout'],
                        skip_second_threshold=conversion_settings['skip_second_threshold']
                    )
                    progress_box['t'] = time.time()
                
                    if success:
                        total_original += original_size
                        total_final += final_size
                        _record_formats(results, format_kept)
                        
                        print(f"wrote {format_kept.upper()} "
                              f"(orig {format_bytes(original_size)}, converted {format_bytes(final_size)})")
                    else:
                        errors += 1
                        logger.warning(f"Failed to process {image_path.name}, kept original")
                        print(f"ERROR (kept original)")
                        send_notification(
                            'Image Squisher - Error',
                            f"Error processing:\n{image_path.name}\n\nFolder: {image_path.parent}",
                            'Basso',
                            config.enable_notifications
                        )
                except Exception as e:
                    errors += 1
                    error_msg = f"Exception processing {image_path.name}: {str(e)}"
                    logger.error(error_msg, exc_info=True)
                    logger.error(f"Error in folder: {image_path.parent}")
                    print(f"ERROR (kept original)")
                    
                    send_notification(
                        'Image Squisher - Error',
                        f"Error processing:\n{image_path.name}\n\nFolder: {image_path.parent}",
                        'Basso',
                        config.enable_notifications
                    )
        finally:
            current_file['path'] = None
            stop_watchdog.set()
    
    total_duration = time.time() - start_time
    
    print()
    print("=" * 60)
    print("Summary:")
    print(f"  Total images processed: {len(image_files)}")
    print(f"  Successful: {len(image_files) - errors}")
    print(f"  Errors: {errors}")
    print()
    print("  Files written:")
    print(f"    Originals kept: {len(image_files)}")
    print(f"    JPEG XL: {results['jxl']}")
    print(f"    WebP: {results['webp']}")
    print(f"    Progressive JPEG: {results['pjpg']}")
    print(f"    No conversion: {results['none']}")
    print()
    print("  Sizes:")
    print(f"    Original total: {format_bytes(total_original)}")
    print(f"    Converted total: {format_bytes(total_final)}")
    print(f"    Duration: {total_duration:.1f} seconds")
    
    # Log summary
    logger.info("=" * 60)
    logger.info(f"Summary: {len(image_files)} processed, {errors} errors, {total_duration:.1f}s")
    logger.info(
        f"Wrote {results['jxl']} JXL, {results['webp']} WebP, {results['pjpg']} progressive JPEG"
    )
    
    # Send completion notification
    if config.enable_notifications:
        if errors > 0:
            send_notification(
                'Image Squisher - Completed with Errors',
                f"Processed {len(image_files)} images\n{errors} errors\nJXL: {results['jxl']}, WebP: {results['webp']}, progressive JPEG: {results['pjpg']}",
                'Glass',
                config.enable_notifications
            )
        else:
            send_notification(
                'Image Squisher - Completed',
                f"Processed {len(image_files)} images\nJXL: {results['jxl']}, WebP: {results['webp']}, progressive JPEG: {results['pjpg']}",
                'Ping',
                config.enable_notifications
            )
    
    print(f"\nLog file: {config.log_file}")


if __name__ == '__main__':
    main()

