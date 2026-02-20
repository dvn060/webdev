# Screen Extractor

Cross-platform Python desktop application for extracting text and tables from on-screen content via screenshot OCR, with automatic page change detection.

## Features

- **Region Selection** — Draw a rectangle to define the capture area (e.g. your PDF viewer)
- **Auto-Capture** — Monitors the screen region and automatically captures when the page changes
- **Dual Page Mode** — Splits side-by-side pages into separate left/right captures
- **Table Detection** — Automatically detects tables via OpenCV; manual F8 override available
- **OCR** — Tesseract OCR for text, img2table for structured table extraction
- **Duplicate Skipping** — Skips captures when page content hasn't meaningfully changed
- **Global Hotkeys** — F9 (capture), F8 (capture as table), F10 (stop and process)
- **Export** — Save combined text as `.txt`, export tables as individual `.csv` files

## Prerequisites

### Tesseract OCR

The app requires Tesseract OCR to be installed on your system.

#### macOS

```bash
brew install tesseract
```

#### Windows

1. Download the installer from: https://github.com/UB-Mannheim/tesseract/wiki
2. Run the installer (default path: `C:\Program Files\Tesseract-OCR`)
3. Add Tesseract to your PATH, or set it in Python:
   ```python
   pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
   ```

### Python 3.9+

Ensure you have Python 3.9 or later installed.

## Installation

```bash
# Clone / navigate to this directory
cd tools/screen-extractor

# Create a virtual environment (recommended)
python -m venv venv
source venv/bin/activate        # macOS/Linux
# venv\Scripts\activate         # Windows

# Install dependencies
pip install -r requirements.txt
```

### Optional dependencies

- **scikit-image** — Better image comparison (SSIM). Falls back to MSE if not installed.
- **opencv-python** — Enables automatic table detection via line/contour analysis.
- **img2table** — Structured table extraction with markdown and CSV output.

If you only need basic OCR without table detection, the core dependencies (Pillow, numpy, pytesseract, pynput) are sufficient.

## Usage

```bash
python screen_extractor.py
```

### Workflow

1. Click **Select Region** and draw a rectangle around your PDF viewer area
2. Choose **Single Page** or **Dual Page** mode
3. Click **Start Auto-Capture** — the app will monitor for page changes
4. Navigate through your PDF; pages are captured automatically
5. Use **Pause/Resume** if you need to interact with the screen without triggering captures
6. Press **F10** or click **Stop & Process** to run OCR on all captured pages
7. Save output as `.txt` or export tables as `.csv`

### Hotkeys

| Key | Action |
|-----|--------|
| F9  | Manually capture current screen (text page) |
| F8  | Manually capture and flag as table |
| F10 | Stop capturing and process all screenshots |

### Sensitivity Slider

Adjust the sensitivity slider to control how much the screen must change before a new capture triggers:
- **Lower values** (1-5) — Very sensitive, triggers on small changes
- **Medium values** (10-20) — Good default, ignores cursor blinks and small UI changes
- **Higher values** (30-50) — Only triggers on major content changes

### Dual Page Mode

When reading side-by-side PDFs, enable **Dual Page** mode. Each capture is split vertically into left and right halves, processed as separate pages with correct numbering (capture 1 = pages 1 & 2, capture 2 = pages 3 & 4, etc.).

## Output Format

```
--- Page 1 ---
Extracted text from page 1...

--- Page 2 (TABLE) ---
| Column A | Column B | Column C |
|----------|----------|----------|
| data     | data     | data     |

--- Page 3 ---
Extracted text from page 3...
```

## Screenshots Directory

All captured screenshots are saved as numbered PNG files in a temporary directory for verification. The path is printed to the console on startup.

## Troubleshooting

### "pytesseract not installed"
Install with `pip install pytesseract` and ensure Tesseract OCR binary is installed (see Prerequisites).

### "pynput not installed"
Install with `pip install pynput`. On macOS, you may need to grant Accessibility permissions in System Preferences > Security & Privacy > Privacy > Accessibility.

### macOS: Screen capture permissions
On macOS, you may need to grant Screen Recording permissions to your terminal or Python in System Preferences > Security & Privacy > Privacy > Screen Recording.

### Windows: Tesseract not found
Ensure the Tesseract install directory is in your system PATH, or set the path explicitly in the script.

### Auto-capture triggers too often / not enough
Adjust the Sensitivity slider. Higher values require more change to trigger, lower values are more sensitive.
