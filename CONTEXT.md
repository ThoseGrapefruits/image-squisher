# Image Squisher

Lossless-capable image conversion tool. Scans a folder, converts each image to JPEG XL and WebP, and keeps the original plus every successful conversion beside it. Defaults are visually lossless (quality 90), which is appropriate for camera HEIC/JPEG. Set `jpegxl_quality` to 100 and `webp_lossless` to true for mathematically lossless output.

## Layout

```
main.py                      CLI, worker pool, progress, summary, notifications
format_detector.py           Folder scan, optional HEIC registration, format detection
processor.py                 JPEG XL (cjxl) and WebP (Pillow) conversion
file_manager.py              Save converted files next to the original
config_loader.py             config.json load/validate
config.json                  Runtime settings
image-squisher-automator.js  macOS Automator folder-action wrapper
setup.sh / setup.bat         Venv + dependency setup
requirements.txt             Pillow
```

## Pipeline

1. `main.py` loads `config.json` (writes a default file in cwd if missing), sets up rotating file logs, and scans the target folder.
2. `format_detector.scan_folder` finds files. `--source` / `source_extensions` limits by extension; otherwise all known image types minus `skip_extensions` (default `.webp`, `.jxl`). Headers are checked with Pillow; pixels are not decoded at scan time.
3. Worker threads (or one thread) call `file_manager.process_image` per file.
4. `processor.convert_image` writes `{stem}.tmp.jxl` and `{stem}.tmp.webp` in the image directory, attempting both codecs except when the source is already that format.
5. `file_manager.save_converted_file` atomically moves temps to `{stem}.jxl` and `{stem}.webp`. Same-stem inputs in one folder share those output names.
6. Failed conversions are discarded; the original stays.

Temp names use `.tmp.*` so a crash cannot overwrite a finished output. `os.replace` is used for the final rename.

## Conversion

| Codec | How | Notes |
| --- | --- | --- |
| JPEG XL | `cjxl` CLI (`libjxl`) | Skipped if `cjxl` is missing or the source is animated. Quality 100 = lossless. |
| WebP | Pillow `Image.save(format='WEBP')` | Default quality 90, lossy. `webp_lossless` enables lossless. Static images apply EXIF orientation. Animated GIF/APNG frames use Pillow's compositor. |

PNG/GIF/BMP try WebP first; other types try JXL first. Both still run. HEIC/HEIF uses `pillow-heif` (`register_heif_opener` in `format_detector.py`).

Supported scan extensions: png, jpg/jpeg, tiff, bmp, gif, webp, heic/heif/hif, avif, jxl, jp2, ico, icns, tga, dds. `--source` can name any of these, including types normally skipped.

## CLI

```
python main.py /path/to/images
python main.py /path/to/images --source .heic
python main.py /path/to/images --source .heic --source .jpg
python main.py /path/to/images --source heic,jpg
python main.py /path/to/images --no-recursive
python main.py /path/to/images --config /path/to/config.json
python main.py /path/to/images --workers 4
```

`--source` overrides `config.source_extensions`. When set, `skip_extensions` is ignored.

On Unix/macOS, `SIGUSR1` throttles to 1 worker; `SIGUSR2` restores the configured worker count.

## Config (`config.json`)

| Key | Default | Role |
| --- | --- | --- |
| `threads` | 1 | Parallel workers unless `--workers` is set. `1` means one worker. |
| `recursive` | true | Overridden by `--no-recursive`. |
| `skip_extensions` | `.webp`, `.jxl` | Not processed unless `--source` / `source_extensions` is set. |
| `source_extensions` | empty | If non-empty, only these extensions are sources. Overridden by `--source`. |
| `jpegxl_quality` | 90 | 1–100. 90 is visually lossless. 100 is mathematically lossless. |
| `jpegxl_effort` | 9 | 1–10. |
| `webp_method` | 6 | 0–6. Encoder effort, not quality. |
| `webp_quality` | 90 | 1–100 when `webp_lossless` is false. |
| `webp_lossless` | false | True encodes lossless WebP (often much larger for photos). |
| `conversion_timeout` | 300 | Per-image `cjxl` timeout and animated WebP deadline (seconds). |
| `max_animated_frames` | 1000 | Animation frame cap. |
| `hang_timeout` | 300 | No-progress warning (seconds). Parallel: queue timeout. Single-thread: watchdog. |
| `adaptive_effort_enabled` | true | Lower effort when 1-minute load / CPU count >= `busy_load_threshold`. |
| `jpegxl_effort_busy` / `webp_method_busy` | 6 / 4 (code), 5 / 4 (shipped config) | Used in busy mode. |
| `log_file` / `log_verbosity` | `image-squisher.log` / INFO | Rotating 25 MB x 5. Console shows WARNING+. |
| `enable_notifications` | false | macOS `terminal-notifier`, Windows toast, Linux `notify-send`. |
| `min_improvement_pct` | 5.0 | Unused. Previously gated replacing the original. |
| `skip_second_threshold` | 0.70 | Unused. Previously skipped the second codec when the first was already small. |

Missing default `config.json` is created in the current directory. `--config` pointing at a missing file uses in-memory defaults without writing.

## Concurrency

`main.py` uses a thread pool plus a slot condition so `max_concurrent` can change mid-run. Adaptive effort reads `os.getloadavg()` when available.

## Outputs

For `photo.jpg`:

- `photo.jpg` (unchanged)
- `photo.jxl` (if `cjxl` succeeded)
- `photo.webp` (if Pillow WebP succeeded)

`photo.jpg` and `photo.png` both write `photo.jxl` / `photo.webp`; the later file wins. Re-runs overwrite those outputs.

Animated sources typically produce only WebP.

## Automator

`image-squisher-automator.js` requires `IMAGE_SQUISHER_HOME` set to the repo path. Folder paths are shell-quoted.

## Logging

Logger name: `image-squisher`. File: `config.log_file`. Hang and error paths can also send OS notifications.
