# Input Sources

> **See also:** architecture.md · effects.md · scenes.md · shaders.md · optimization.md · troubleshooting.md

## Audio Analysis

### Loading

```python
tmp = tempfile.mktemp(suffix=".wav")
subprocess.run(["ffmpeg", "-y", "-i", input_path, "-ac", "1", "-ar", "22050",
                "-sample_fmt", "s16", tmp], capture_output=True, check=True)
with wave.open(tmp) as wf:
    sr = wf.getframerate()
    raw = wf.readframes(wf.getnframes())
samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
```

### Per-Frame FFT

```python
hop = sr // fps          # samples per frame
win = hop * 2            # analysis window (2x hop for overlap)
window = np.hanning(win)
freqs = rfftfreq(win, 1.0 / sr)

bands = {
    "sub":   (freqs >= 20)  & (freqs < 80),
    "bass":  (freqs >= 80)  & (freqs < 250),
    "lomid": (freqs >= 250) & (freqs < 500),
    "mid":   (freqs >= 500) & (freqs < 2000),
    "himid": (freqs >= 2000)& (freqs < 6000),
    "hi":    (freqs >= 6000),
}
```

For each frame: extract chunk, apply window, FFT, compute band energies.

### Feature Set

| Feature | Formula | Controls |
|---------|---------|----------|
| `rms` | `sqrt(mean(chunk²))` | Overall loudness/energy |
| `sub`..`hi` | `sqrt(mean(band_magnitudes²))` | Per-band energy |
| `centroid` | `sum(freq*mag) / sum(mag)` | Brightness/timbre |
| `flatness` | `geomean(mag) / mean(mag)` | Noise vs tone |
| `flux` | `sum(max(0, mag - prev_mag))` | Transient strength |
| `sub_r`..`hi_r` | `band / sum(all_bands)` | Spectral shape (volume-independent) |
| `cent_d` | `abs(gradient(centroid))` | Timbral change rate |
| `beat` | Flux peak detection | Binary beat onset |
| `bdecay` | Exponential decay from beats | Smooth beat pulse (0→1→0) |

**Band ratios are critical** — they decouple spectral shape from volume, so a quiet bass section and a loud bass section both read as "bassy" rather than just "loud" vs "quiet".

### Smoothing

EMA prevents visual jitter:

```python
def ema(arr, alpha):
    out = np.empty_like(arr); out[0] = arr[0]
    for i in range(1, len(arr)):
        out[i] = alpha * arr[i] + (1 - alpha) * out[i-1]
    return out

# Slow-moving features (alpha=0.12): centroid, flatness, band ratios, cent_d
# Fast-moving features (alpha=0.3): rms, flux, raw bands
```

### Beat Detection

```python
flux_smooth = np.convolve(flux, np.ones(5)/5, mode="same")
peaks, _ = signal.find_peaks(flux_smooth, height=0.15, distance=fps//5, prominence=0.05)

beat = np.zeros(n_frames)
bdecay = np.zeros(n_frames, dtype=np.float32)
for p in peaks:
    beat[p] = 1.0
    for d in range(fps // 2):
        if p + d < n_frames:
            bdecay[p + d] = max(bdecay[p + d], math.exp(-d * 2.5 / (fps // 2)))
```

`bdecay` gives smooth 0→1→0 pulse per beat, decaying over ~0.5s. Use for flash/glitch/mirror triggers.

### Normalization

After computing all frames, normalize each feature to 0-1:

```python
for k in features:
    a = features[k]
    lo, hi = a.min(), a.max()
    features[k] = (a - lo) / (hi - lo + 1e-10)
```

## Video Sampling

### Frame Extraction

```python
# Method 1: ffmpeg pipe (memory efficient)
cmd = ["ffmpeg", "-i", input_video, "-f", "rawvideo", "-pix_fmt", "rgb24",
       "-s", f"{target_w}x{target_h}", "-r", str(fps), "-"]
pipe = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
frame_size = target_w * target_h * 3
for fi in range(n_frames):
    raw = pipe.stdout.read(frame_size)
    if len(raw) < frame_size: break
    frame = np.frombuffer(raw, dtype=np.uint8).reshape(target_h, target_w, 3)
    # process frame...

# Method 2: OpenCV (if available)
cap = cv2.VideoCapture(input_video)
```

### Luminance-to-Character Mapping

Convert video pixels to ASCII characters based on brightness:

```python
def frame_to_ascii(frame_rgb, grid, pal=PAL_DEFAULT):
    """Convert video frame to character + color arrays."""
    rows, cols = grid.rows, grid.cols
    # Resize frame to grid dimensions
    small = np.array(Image.fromarray(frame_rgb).resize((cols, rows), Image.LANCZOS))
    # Luminance
    lum = (0.299 * small[:,:,0] + 0.587 * small[:,:,1] + 0.114 * small[:,:,2]) / 255.0
    # Map to chars
    chars = val2char(lum, lum > 0.02, pal)
    # Colors: use source pixel colors, scaled by luminance for visibility
    colors = np.clip(small * np.clip(lum[:,:,None] * 1.5 + 0.3, 0.3, 1), 0, 255).astype(np.uint8)
    return chars, colors
```

### Edge-Weighted Character Mapping

Use edge detection for more detail in contour regions:

```python
def frame_to_ascii_edges(frame_rgb, grid, pal=PAL_DEFAULT, edge_pal=PAL_BOX):
    gray = np.mean(frame_rgb, axis=2)
    small_gray = resize(gray, (grid.rows, grid.cols))
    lum = small_gray / 255.0

    # Sobel edge detection
    gx = np.abs(small_gray[:, 2:] - small_gray[:, :-2])
    gy = np.abs(small_gray[2:, :] - small_gray[:-2, :])
    edge = np.zeros_like(small_gray)
    edge[:, 1:-1] += gx; edge[1:-1, :] += gy
    edge = np.clip(edge / edge.max(), 0, 1)

    # Edge regions get box drawing chars, flat regions get brightness chars
    is_edge = edge > 0.15
    chars = val2char(lum, lum > 0.02, pal)
    edge_chars = val2char(edge, is_edge, edge_pal)
    chars[is_edge] = edge_chars[is_edge]

    return chars, colors
```

### Motion Detection

Detect pixel changes between frames for motion-reactive effects:

```python
prev_frame = None
def compute_motion(frame):
    global prev_frame
    if prev_frame is None:
        prev_frame = frame.astype(np.float32)
        return np.zeros(frame.shape[:2])
    diff = np.abs(frame.astype(np.float32) - prev_frame).mean(axis=2)
    prev_frame = frame.astype(np.float32) * 0.7 + prev_frame * 0.3  # smoothed
    return np.clip(diff / 30.0, 0, 1)  # normalized motion map
```

Use motion map to drive particle emission, glitch intensity, or character density.

### Video Feature Extraction

Per-frame features analogous to audio features, for driving effects:

```python
def analyze_video_frame(frame_rgb):
    gray = np.mean(frame_rgb, axis=2)
    return {
        "brightness": gray.mean() / 255.0,
        "contrast": gray.std() / 128.0,
        "edge_density": compute_edge_density(gray),
        "motion": compute_motion(frame_rgb).mean(),
        "dominant_hue": compute_dominant_hue(frame_rgb),
        "color_variance": compute_color_variance(frame_rgb),
    }
```

## Image Sequence

### Static Image to ASCII

Same as single video frame conversion. For animated sequences:

```python
import glob
frames = sorted(glob.glob("frames/*.png"))
for fi, path in enumerate(frames):
    img = np.array(Image.open(path).resize((VW, VH)))
    chars, colors = frame_to_ascii(img, grid, pal)
```

### Image as Texture Source

Use an image as a background texture that effects modulate:

```python
def load_texture(path, grid):
    img = np.array(Image.open(path).resize((grid.cols, grid.rows)))
    lum = np.mean(img, axis=2) / 255.0
    return lum, img  # luminance for char mapping, RGB for colors
```

## Text / Lyrics

### SRT Parsing

```python
import re
def parse_srt(path):
    """Returns [(start_sec, end_sec, text), ...]"""
    entries = []
    with open(path) as f:
        content = f.read()
    blocks = content.strip().split("\n\n")
    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) >= 3:
            times = lines[1]
            m = re.match(r"(\d+):(\d+):(\d+),(\d+) --> (\d+):(\d+):(\d+),(\d+)", times)
            if m:
                g = [int(x) for x in m.groups()]
                start = g[0]*3600 + g[1]*60 + g[2] + g[3]/1000
                end = g[4]*3600 + g[5]*60 + g[6] + g[7]/1000
                text = " ".join(lines[2:])
                entries.append((start, end, text))
    return entries
```

### Lyrics Display Modes

- **Typewriter**: characters appear left-to-right over the time window
- **Fade-in**: whole line fades from dark to bright
- **Flash**: appear instantly on beat, fade out
- **Scatter**: characters start at random positions, converge to final position
- **Wave**: text follows a sine wave path

```python
def lyrics_typewriter(ch, co, text, row, col, t, t_start, t_end, color):
    """Reveal characters progressively over time window."""
    progress = np.clip((t - t_start) / (t_end - t_start), 0, 1)
    n_visible = int(len(text) * progress)
    stamp(ch, co, text[:n_visible], row, col, color)
```

## Generative (No Input)

For pure generative ASCII art, the "features" dict is synthesized from time:

```python
def synthetic_features(t, bpm=120):
    """Generate audio-like features from time alone."""
    beat_period = 60.0 / bpm
    beat_phase = (t % beat_period) / beat_period
    return {
        "rms": 0.5 + 0.3 * math.sin(t * 0.5),
        "bass": 0.5 + 0.4 * math.sin(t * 2 * math.pi / beat_period),
        "sub": 0.3 + 0.3 * math.sin(t * 0.8),
        "mid": 0.4 + 0.3 * math.sin(t * 1.3),
        "hi": 0.3 + 0.2 * math.sin(t * 2.1),
        "cent": 0.5 + 0.2 * math.sin(t * 0.3),
        "flat": 0.4,
        "flux": 0.3 + 0.2 * math.sin(t * 3),
        "beat": 1.0 if beat_phase < 0.05 else 0.0,
        "bdecay": max(0, 1.0 - beat_phase * 4),
        # ratios
        "sub_r": 0.2, "bass_r": 0.25, "lomid_r": 0.15,
        "mid_r": 0.2, "himid_r": 0.12, "hi_r": 0.08,
        "cent_d": 0.1,
    }
```

## Text Visual Styles

### Per-Quote Visual Style

Cycle through visual presets per quote for variety. Each preset defines a background effect, color scheme, and text color:

```python
QUOTE_STYLES = [
    {"hue": 0.08, "accent": 0.7, "bg": "spiral",       "text_rgb": (255, 220, 140)},  # warm gold
    {"hue": 0.55, "accent": 0.6, "bg": "rings",         "text_rgb": (180, 220, 255)},  # cool blue
    {"hue": 0.75, "accent": 0.7, "bg": "wave",          "text_rgb": (220, 180, 255)},  # purple
    {"hue": 0.35, "accent": 0.6, "bg": "matrix",        "text_rgb": (140, 255, 180)},  # green
    {"hue": 0.95, "accent": 0.8, "bg": "fire",          "text_rgb": (255, 180, 160)},  # red/coral
    {"hue": 0.12, "accent": 0.5, "bg": "interference",  "text_rgb": (255, 240, 200)},  # amber
    {"hue": 0.60, "accent": 0.7, "bg": "tunnel",        "text_rgb": (160, 210, 255)},  # cyan
    {"hue": 0.45, "accent": 0.6, "bg": "aurora",        "text_rgb": (180, 255, 220)},  # teal
]

style = QUOTE_STYLES[quote_index % len(QUOTE_STYLES)]
```

This guarantees no two adjacent quotes share the same look, even without randomness.

### Typewriter Text Rendering

Display quote text character-by-character synced to speech progress. Recently revealed characters are brighter, creating a "just typed" glow:

```python
def render_typewriter(ch, co, lines, block_start, cols, progress, total_chars, text_rgb, t):
    """Overlay typewriter text onto character/color grids.
    progress: 0.0 (nothing visible) to 1.0 (all text visible)."""
    chars_visible = int(total_chars * min(1.0, progress * 1.2))  # slight overshoot for snappy feel
    tr, tg, tb = text_rgb
    char_count = 0
    for li, line in enumerate(lines):
        row = block_start + li
        col = (cols - len(line)) // 2
        for ci, c in enumerate(line):
            if char_count < chars_visible:
                age = chars_visible - char_count
                bri_factor = min(1.0, 0.5 + 0.5 / (1 + age * 0.015))  # newer = brighter
                hue_shift = math.sin(char_count * 0.3 + t * 2) * 0.05
                stamp(ch, co, c, row, col + ci,
                      (int(min(255, tr * bri_factor * (1.0 + hue_shift))),
                       int(min(255, tg * bri_factor)),
                       int(min(255, tb * bri_factor * (1.0 - hue_shift)))))
            char_count += 1

    # Blinking cursor at insertion point
    if progress < 1.0 and int(t * 3) % 2 == 0:
        # Find cursor position (char_count == chars_visible)
        cc = 0
        for li, line in enumerate(lines):
            for ci, c in enumerate(line):
                if cc == chars_visible:
                    stamp(ch, co, "\u258c", block_start + li,
                          (cols - len(line)) // 2 + ci, (255, 220, 100))
                    return
                cc += 1
```

## Audio-Video Sync Verification

After rendering, verify that visual beat markers align with actual audio beats. Drift accumulates from frame timing errors, ffmpeg concat boundaries, and rounding in `fi / fps`.

### Beat Timestamp Extraction

```python
def extract_beat_timestamps(features, fps, threshold=0.5):
    """Extract timestamps where beat feature exceeds threshold."""
    beat = features["beat"]
    timestamps = []
    for fi in range(len(beat)):
        if beat[fi] > threshold:
            timestamps.append(fi / fps)
    return timestamps

def extract_visual_beat_timestamps(video_path, fps, brightness_jump=30):
    """Detect visual beats by brightness jumps between consecutive frames.
    Returns timestamps where mean brightness increases by more than threshold."""
    import subprocess
    cmd = ["ffmpeg", "-i", video_path, "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    proc = subprocess.run(cmd, capture_output=True)
    frames = np.frombuffer(proc.stdout, dtype=np.uint8)
    # Infer frame dimensions from total byte count
    n_pixels = len(frames)
    # For 1080p: 1920*1080 pixels per frame
    # Auto-detect from video metadata is more robust:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height",
         "-of", "csv=p=0", video_path],
        capture_output=True, text=True)
    w, h = map(int, probe.stdout.strip().split(","))
    ppf = w * h  # pixels per frame
    n_frames = n_pixels // ppf
    frames = frames[:n_frames * ppf].reshape(n_frames, ppf)
    means = frames.mean(axis=1)
    
    timestamps = []
    for i in range(1, len(means)):
        if means[i] - means[i-1] > brightness_jump:
            timestamps.append(i / fps)
    return timestamps
```

### Sync Report

```python
def sync_report(audio_beats, visual_beats, tolerance_ms=50):
    """Compare audio beat timestamps to visual beat timestamps.
    
    Args:
        audio_beats: list of timestamps (seconds) from audio analysis
        visual_beats: list of timestamps (seconds) from video brightness analysis
        tolerance_ms: max acceptable drift in milliseconds
    
    Returns:
        dict with matched/unmatched/drift statistics
    """
    tolerance = tolerance_ms / 1000.0
    matched = []
    unmatched_audio = []
    unmatched_visual = list(visual_beats)
    
    for at in audio_beats:
        best_match = None
        best_delta = float("inf")
        for vt in unmatched_visual:
            delta = abs(at - vt)
            if delta < best_delta:
                best_delta = delta
                best_match = vt
        if best_match is not None and best_delta < tolerance:
            matched.append({"audio": at, "visual": best_match, "drift_ms": best_delta * 1000})
            unmatched_visual.remove(best_match)
        else:
            unmatched_audio.append(at)
    
    drifts = [m["drift_ms"] for m in matched]
    return {
        "matched": len(matched),
        "unmatched_audio": len(unmatched_audio),
        "unmatched_visual": len(unmatched_visual),
        "total_audio_beats": len(audio_beats),
        "total_visual_beats": len(visual_beats),
        "mean_drift_ms": np.mean(drifts) if drifts else 0,
        "max_drift_ms": np.max(drifts) if drifts else 0,
        "p95_drift_ms": np.percentile(drifts, 95) if len(drifts) > 1 else 0,
    }

# Usage:
audio_beats = extract_beat_timestamps(features, fps=24)
visual_beats = extract_visual_beat_timestamps("output.mp4", fps=24)
report = sync_report(audio_beats, visual_beats)
print(f"Matched: {report['matched']}/{report['total_audio_beats']} beats")
print(f"Mean drift: {report['mean_drift_ms']:.1f}ms, Max: {report['max_drift_ms']:.1f}ms")
# Target: mean drift < 20ms, max drift < 42ms (1 frame at 24fps)
```

### Common Sync Issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| Consistent late visual beats | ffmpeg concat adds frames at boundaries | Use `-vsync cfr` flag; pad segments to exact frame count |
| Drift increases over time | Floating-point accumulation in `t = fi / fps` | Use integer frame counter, compute `t` fresh each frame |
| Random missed beats | Beat threshold too high / feature smoothing too aggressive | Lower threshold; reduce EMA alpha for beat feature |
| Beats land on wrong frame | Off-by-one in frame indexing | Verify: frame 0 = t=0, frame 1 = t=1/fps (not t=0) |
