#!/usr/bin/env python3
"""
Screen Extractor - Cross-platform desktop app for extracting text and tables
from on-screen content via screenshot OCR, with automatic page change detection.
Works on macOS and Windows.
"""

import csv
import io
import os
import platform
import sys
import tempfile
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import numpy as np
from PIL import Image, ImageGrab

# ---------------------------------------------------------------------------
# Optional imports with graceful fallback
# ---------------------------------------------------------------------------

try:
    import pytesseract
except ImportError:
    pytesseract = None

try:
    from pynput import keyboard as pynput_keyboard
except ImportError:
    pynput_keyboard = None

try:
    from skimage.metrics import structural_similarity as ssim
    HAS_SKIMAGE = True
except ImportError:
    HAS_SKIMAGE = False

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

try:
    from img2table.document import Image as Img2TableImage
    from img2table.ocr import TesseractOCR
    HAS_IMG2TABLE = True
except ImportError:
    HAS_IMG2TABLE = False

# ---------------------------------------------------------------------------
# Platform helpers
# ---------------------------------------------------------------------------

IS_MAC = platform.system() == "Darwin"
IS_WIN = platform.system() == "Windows"


def _play_beep():
    """Play a short confirmation sound, cross-platform."""
    try:
        if IS_WIN:
            import winsound
            winsound.Beep(1000, 150)
        elif IS_MAC:
            os.system("afplay /System/Library/Sounds/Tink.aiff &")
        else:
            print("\a", end="", flush=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Region selector overlay
# ---------------------------------------------------------------------------

class RegionSelector:
    """Full-screen transparent overlay that lets the user draw a rectangle."""

    def __init__(self, callback):
        self._callback = callback
        self._start_x = 0
        self._start_y = 0
        self._rect_id = None

        self.root = tk.Toplevel()
        self.root.attributes("-fullscreen", True)
        self.root.attributes("-topmost", True)
        self.root.configure(cursor="cross")
        self.root.attributes("-alpha", 0.3)

        self.canvas = tk.Canvas(self.root, bg="gray", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.canvas.create_text(
            self.root.winfo_screenwidth() // 2, 40,
            text="Click and drag to select the capture region. Release to confirm. Esc to cancel.",
            font=("Helvetica", 18, "bold"), fill="white",
        )

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.root.bind("<Escape>", lambda e: self._cancel())

    def _on_press(self, event):
        self._start_x = event.x
        self._start_y = event.y
        if self._rect_id:
            self.canvas.delete(self._rect_id)
        self._rect_id = self.canvas.create_rectangle(
            event.x, event.y, event.x, event.y, outline="red", width=2
        )

    def _on_drag(self, event):
        if self._rect_id:
            self.canvas.coords(
                self._rect_id, self._start_x, self._start_y, event.x, event.y
            )

    def _on_release(self, event):
        x1, y1 = self._start_x, self._start_y
        x2, y2 = event.x, event.y
        region = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
        if (region[2] - region[0]) < 20 or (region[3] - region[1]) < 20:
            return
        self.root.destroy()
        self._callback(region)

    def _cancel(self):
        self.root.destroy()
        self._callback(None)


# ---------------------------------------------------------------------------
# Image comparison
# ---------------------------------------------------------------------------

def _compute_difference(img_a, img_b):
    """Return a 0-100 difference score (0=identical, 100=completely different)."""
    try:
        a = np.array(img_a.convert("L").resize((320, 240)))
        b = np.array(img_b.convert("L").resize((320, 240)))
    except Exception:
        return 100.0

    if HAS_SKIMAGE:
        try:
            score = ssim(a, b)
            return (1.0 - score) * 100.0
        except Exception:
            pass

    mse = np.mean((a.astype(float) - b.astype(float)) ** 2)
    max_mse = 255.0 * 255.0
    return (mse / max_mse) * 100.0


# ---------------------------------------------------------------------------
# Table detection helpers
# ---------------------------------------------------------------------------

def _detect_table_opencv(img_np):
    """Use OpenCV line/contour detection to guess if the image contains a table."""
    if not HAS_CV2:
        return False
    try:
        gray = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY)
        thresh = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV, 15, 4
        )
        h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (40, 1))
        h_lines = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, h_kernel)
        v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 40))
        v_lines = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, v_kernel)
        combined = cv2.add(h_lines, v_lines)
        contours, _ = cv2.findContours(combined, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        rect_count = 0
        for cnt in contours:
            approx = cv2.approxPolyDP(cnt, 0.02 * cv2.arcLength(cnt, True), True)
            if len(approx) == 4:
                rect_count += 1
        return rect_count >= 4
    except Exception:
        return False


def _extract_table_img2table(pil_img):
    """Use img2table to extract tables. Returns (markdown_str, csv_str) or (None, None)."""
    if not HAS_IMG2TABLE:
        return None, None
    try:
        ocr = TesseractOCR(lang="eng")
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        pil_img.save(tmp.name)
        tmp.close()
        doc = Img2TableImage(src=tmp.name)
        tables = doc.extract_tables(ocr=ocr)
        os.unlink(tmp.name)
        if not tables:
            return None, None
        md_parts, csv_parts = [], []
        for table in tables:
            df = table.df
            if df is None or df.empty:
                continue
            md_parts.append(df.to_markdown(index=False))
            buf = io.StringIO()
            df.to_csv(buf, index=False)
            csv_parts.append(buf.getvalue())
        if md_parts:
            return "\n\n".join(md_parts), "\n---\n".join(csv_parts)
        return None, None
    except Exception:
        return None, None


def _extract_table_fallback(pil_img):
    """Fallback: pytesseract with --psm 6 to preserve column alignment."""
    if pytesseract is None:
        return "Error: pytesseract not installed."
    try:
        processed = _preprocess_for_ocr(pil_img)
        return pytesseract.image_to_string(processed, config="--psm 6")
    except Exception as exc:
        return f"Table OCR fallback error: {exc}"


def _preprocess_for_ocr(pil_img):
    """Preprocess a PIL image to improve Tesseract OCR accuracy."""
    from PIL import ImageFilter

    img = pil_img.convert("L")  # grayscale

    # Upscale small images so Tesseract can read the glyphs
    w, h = img.size
    if w < 1000:
        scale = max(2, 1500 // w)
        img = img.resize((w * scale, h * scale), Image.LANCZOS)

    # Sharpen to recover edges lost during scaling
    img = img.filter(ImageFilter.SHARPEN)

    # Binarize with Otsu-style threshold (simple but effective)
    arr = np.array(img)
    threshold = int(np.mean(arr))
    arr = ((arr > threshold) * 255).astype(np.uint8)
    return Image.fromarray(arr)


def _ocr_image(pil_img):
    """Standard OCR on a PIL image."""
    if pytesseract is None:
        return "Error: pytesseract is not installed."
    try:
        processed = _preprocess_for_ocr(pil_img)
        return pytesseract.image_to_string(processed, config="--psm 6")
    except Exception as exc:
        return f"OCR error: {exc}"


# ---------------------------------------------------------------------------
# Main Application
# ---------------------------------------------------------------------------

class ScreenExtractorApp:
    DUPLICATE_THRESHOLD = 3.0  # percent difference below which captures are skipped

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Screen Extractor")
        self.root.attributes("-topmost", True)
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # State
        self.region = None
        self.captures = []
        self.page_counter = 0
        self.dual_page = tk.BooleanVar(value=False)
        self.auto_table_detect = tk.BooleanVar(value=True)
        self.sensitivity = tk.DoubleVar(value=15.0)
        self._monitoring = False
        self._paused = False
        self._monitor_thread = None
        self._last_frame = None
        self._last_captured_image = None
        self._hotkey_listener = None
        self._temp_dir = tempfile.mkdtemp(prefix="screen_extractor_")
        self._csv_tables = []

        self._build_gui()
        self._start_hotkey_listener()

    # ------------------------------------------------------------------ GUI

    def _build_gui(self):
        pad = dict(padx=6, pady=3)

        # Region selection
        frm_top = ttk.Frame(self.root)
        frm_top.pack(fill=tk.X, **pad)
        self.btn_select = ttk.Button(frm_top, text="Select Region", command=self._select_region)
        self.btn_select.pack(side=tk.LEFT, **pad)
        self.lbl_region = ttk.Label(frm_top, text="No region selected")
        self.lbl_region.pack(side=tk.LEFT, **pad)

        # Mode toggles
        frm_mode = ttk.Frame(self.root)
        frm_mode.pack(fill=tk.X, **pad)
        ttk.Radiobutton(frm_mode, text="Single Page", variable=self.dual_page, value=False).pack(side=tk.LEFT, **pad)
        ttk.Radiobutton(frm_mode, text="Dual Page (side by side)", variable=self.dual_page, value=True).pack(side=tk.LEFT, **pad)
        ttk.Checkbutton(frm_mode, text="Auto table detect", variable=self.auto_table_detect).pack(side=tk.LEFT, **pad)

        # Sensitivity slider
        frm_sens = ttk.Frame(self.root)
        frm_sens.pack(fill=tk.X, **pad)
        ttk.Label(frm_sens, text="Sensitivity:").pack(side=tk.LEFT, **pad)
        self.slider = ttk.Scale(frm_sens, from_=1, to=50, variable=self.sensitivity, orient=tk.HORIZONTAL, length=200)
        self.slider.pack(side=tk.LEFT, **pad)
        self.lbl_sens_val = ttk.Label(frm_sens, text="15")
        self.lbl_sens_val.pack(side=tk.LEFT, **pad)
        self.sensitivity.trace_add("write", self._update_sens_label)

        # Auto capture controls
        frm_auto = ttk.Frame(self.root)
        frm_auto.pack(fill=tk.X, **pad)
        self.btn_start = ttk.Button(frm_auto, text="Start Auto-Capture", command=self._start_monitoring)
        self.btn_start.pack(side=tk.LEFT, **pad)
        self.btn_pause = ttk.Button(frm_auto, text="Pause", command=self._toggle_pause, state=tk.DISABLED)
        self.btn_pause.pack(side=tk.LEFT, **pad)
        self.btn_stop = ttk.Button(frm_auto, text="Stop & Process", command=self._stop_and_process, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT, **pad)

        # Page counter and status
        frm_status = ttk.Frame(self.root)
        frm_status.pack(fill=tk.X, **pad)
        self.lbl_pages = ttk.Label(frm_status, text="Pages captured: 0", font=("Helvetica", 12, "bold"))
        self.lbl_pages.pack(side=tk.LEFT, **pad)
        self.lbl_status = ttk.Label(frm_status, text="Idle", foreground="gray")
        self.lbl_status.pack(side=tk.LEFT, **pad)
        self.lbl_flash = tk.Label(frm_status, text="  ", width=3, bg=self.root.cget("bg"))
        self.lbl_flash.pack(side=tk.LEFT, **pad)

        # Manual capture buttons
        frm_manual = ttk.Frame(self.root)
        frm_manual.pack(fill=tk.X, **pad)
        ttk.Button(frm_manual, text="Capture (F9)", command=self._manual_capture).pack(side=tk.LEFT, **pad)
        ttk.Button(frm_manual, text="Capture Table (F8)", command=self._manual_capture_table).pack(side=tk.LEFT, **pad)
        ttk.Button(frm_manual, text="Stop & Process (F10)", command=self._stop_and_process).pack(side=tk.LEFT, **pad)

        # Output controls
        frm_out = ttk.Frame(self.root)
        frm_out.pack(fill=tk.X, **pad)
        ttk.Button(frm_out, text="Save as .txt", command=self._save_txt).pack(side=tk.LEFT, **pad)
        ttk.Button(frm_out, text="Export tables to CSV", command=self._export_csv).pack(side=tk.LEFT, **pad)
        ttk.Button(frm_out, text="Clear All", command=self._clear_all).pack(side=tk.LEFT, **pad)

        # Text output area
        frm_text = ttk.Frame(self.root)
        frm_text.pack(fill=tk.BOTH, expand=True, **pad)
        self.txt_output = tk.Text(frm_text, height=16, width=80, wrap=tk.WORD)
        scroll = ttk.Scrollbar(frm_text, orient=tk.VERTICAL, command=self.txt_output.yview)
        self.txt_output.configure(yscrollcommand=scroll.set)
        self.txt_output.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Hotkey reminder
        ttk.Label(
            self.root,
            text="Hotkeys: F9=Capture | F8=Capture Table | F10=Stop & Process",
            foreground="gray",
        ).pack(**pad)

    # ------------------------------------------------------ helpers

    def _update_sens_label(self, *_args):
        try:
            self.lbl_sens_val.config(text=f"{self.sensitivity.get():.0f}")
        except Exception:
            pass

    def _set_status(self, msg):
        try:
            self.lbl_status.config(text=msg)
        except Exception:
            pass

    def _update_page_label(self):
        try:
            self.lbl_pages.config(text=f"Pages captured: {self.page_counter}")
        except Exception:
            pass

    def _flash_green(self):
        try:
            self.lbl_flash.config(bg="green")
            self.root.after(400, lambda: self.lbl_flash.config(bg=self.root.cget("bg")))
        except Exception:
            pass

    # ---------------------------------------------------- region selection

    def _select_region(self):
        self.root.withdraw()
        time.sleep(0.3)

        def _on_region(region):
            self.root.deiconify()
            if region:
                self.region = region
                self.lbl_region.config(
                    text=f"Region: ({region[0]},{region[1]}) to ({region[2]},{region[3]})"
                )
            else:
                self.lbl_region.config(text="Selection cancelled")

        RegionSelector(_on_region)

    # ---------------------------------------------------- screen grab

    def _grab_region(self):
        if not self.region:
            return None
        try:
            return ImageGrab.grab(bbox=self.region)
        except Exception as exc:
            self._set_status(f"Grab error: {exc}")
            return None

    # ---------------------------------------------------- auto capture

    def _start_monitoring(self):
        if not self.region:
            messagebox.showwarning("No region", "Please select a capture region first.")
            return
        if self._monitoring:
            return

        self._monitoring = True
        self._paused = False
        self._last_frame = self._grab_region()
        self.btn_start.config(state=tk.DISABLED)
        self.btn_pause.config(state=tk.NORMAL, text="Pause")
        self.btn_stop.config(state=tk.NORMAL)
        self._set_status("Monitoring...")

        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()

    def _monitor_loop(self):
        while self._monitoring:
            time.sleep(0.5)
            if self._paused or not self._monitoring:
                continue

            current = self._grab_region()
            if current is None or self._last_frame is None:
                self._last_frame = current
                continue

            diff = _compute_difference(self._last_frame, current)
            threshold = self.sensitivity.get()

            if diff > threshold:
                # Significant change — wait for stabilisation
                time.sleep(0.7)
                if not self._monitoring:
                    break
                stable = self._grab_region()
                if stable is None:
                    continue

                # Verify screen has settled
                time.sleep(0.3)
                verify = self._grab_region()
                if verify is not None:
                    settle_diff = _compute_difference(stable, verify)
                    if settle_diff > threshold * 0.5:
                        self._last_frame = verify
                        continue
                    stable = verify

                # Duplicate check against last captured content
                if self._last_captured_image is not None:
                    dup_diff = _compute_difference(self._last_captured_image, stable)
                    if dup_diff < self.DUPLICATE_THRESHOLD:
                        self._last_frame = stable
                        continue

                self._do_capture(stable, is_table=False)
                self._last_frame = stable
            else:
                self._last_frame = current

    def _toggle_pause(self):
        if self._paused:
            self._paused = False
            self.btn_pause.config(text="Pause")
            self._set_status("Monitoring...")
        else:
            self._paused = True
            self.btn_pause.config(text="Resume")
            self._set_status("Paused")

    def _stop_monitoring(self):
        self._monitoring = False
        self._paused = False
        self.btn_start.config(state=tk.NORMAL)
        self.btn_pause.config(state=tk.DISABLED, text="Pause")
        self.btn_stop.config(state=tk.DISABLED)
        self._set_status("Stopped")

    # ---------------------------------------------------- capture logic

    def _do_capture(self, img, is_table):
        """Process a captured image. In dual-page mode, splits into left/right halves."""
        is_dual = self.dual_page.get()

        if is_dual:
            w, h = img.size
            left = img.crop((0, 0, w // 2, h))
            right = img.crop((w // 2, 0, w, h))
            sub_images = [left, right]
        else:
            sub_images = [img]

        for sub_img in sub_images:
            self.page_counter += 1
            page_num = self.page_counter

            # Auto table detection
            detected_table = is_table
            if not detected_table and self.auto_table_detect.get():
                np_img = np.array(sub_img)
                if _detect_table_opencv(np_img):
                    detected_table = True

            # Save screenshot
            ss_path = os.path.join(self._temp_dir, f"page_{page_num:04d}.png")
            sub_img.save(ss_path)

            self.captures.append({
                "image": sub_img.copy(),
                "is_table": detected_table,
                "page_num": page_num,
                "ss_path": ss_path,
            })

        self._last_captured_image = img.copy()

        # UI feedback
        self.root.after(0, self._update_page_label)
        self.root.after(0, self._flash_green)
        threading.Thread(target=_play_beep, daemon=True).start()

    def _manual_capture(self):
        img = self._grab_region()
        if img is None:
            messagebox.showwarning("No region", "Please select a capture region first.")
            return
        if self._last_captured_image is not None:
            dup_diff = _compute_difference(self._last_captured_image, img)
            if dup_diff < self.DUPLICATE_THRESHOLD:
                self._set_status("Skipped (duplicate)")
                return
        self._do_capture(img, is_table=False)

    def _manual_capture_table(self):
        img = self._grab_region()
        if img is None:
            messagebox.showwarning("No region", "Please select a capture region first.")
            return
        if self._last_captured_image is not None:
            dup_diff = _compute_difference(self._last_captured_image, img)
            if dup_diff < self.DUPLICATE_THRESHOLD:
                self._set_status("Skipped (duplicate)")
                return
        self._do_capture(img, is_table=True)

    # ---------------------------------------------------- process & output

    def _stop_and_process(self):
        self._stop_monitoring()
        if not self.captures:
            messagebox.showinfo("Nothing", "No pages have been captured yet.")
            return
        self._set_status("Processing OCR...")
        threading.Thread(target=self._process_all, daemon=True).start()

    def _process_all(self):
        output_parts = []
        self._csv_tables = []

        for cap in self.captures:
            pnum = cap["page_num"]
            img = cap["image"]
            is_table = cap["is_table"]

            if is_table:
                marker = f"--- Page {pnum} (TABLE) ---"
                md, csv_text = _extract_table_img2table(img)
                if md:
                    output_parts.append(f"{marker}\n{md}")
                    if csv_text:
                        self._csv_tables.append({"page": pnum, "csv": csv_text})
                else:
                    fallback = _extract_table_fallback(img)
                    output_parts.append(f"{marker}\n{fallback}")
                    self._csv_tables.append({"page": pnum, "csv": fallback})
            else:
                marker = f"--- Page {pnum} ---"
                text = _ocr_image(img)
                output_parts.append(f"{marker}\n{text}")

        combined = "\n\n".join(output_parts)
        self.root.after(0, self._show_output, combined)

    def _show_output(self, text):
        self.txt_output.delete("1.0", tk.END)
        self.txt_output.insert(tk.END, text)
        self._set_status(f"Done - {self.page_counter} pages processed")

    # ---------------------------------------------------- save / export

    def _save_txt(self):
        content = self.txt_output.get("1.0", tk.END).strip()
        if not content:
            messagebox.showinfo("Empty", "No text to save. Process captures first.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            title="Save extracted text",
        )
        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)
                self._set_status(f"Saved to {path}")
            except Exception as exc:
                messagebox.showerror("Save error", str(exc))

    def _export_csv(self):
        if not self._csv_tables:
            messagebox.showinfo("No tables", "No tables were detected or captured. Use F8 to mark table pages.")
            return
        folder = filedialog.askdirectory(title="Select folder for CSV export")
        if not folder:
            return
        try:
            for idx, tbl in enumerate(self._csv_tables, 1):
                csv_path = os.path.join(folder, f"table_{idx}_page_{tbl['page']}.csv")
                with open(csv_path, "w", encoding="utf-8", newline="") as f:
                    f.write(tbl["csv"])
            self._set_status(f"Exported {len(self._csv_tables)} table(s) to {folder}")
        except Exception as exc:
            messagebox.showerror("Export error", str(exc))

    # ---------------------------------------------------- clear / reset

    def _clear_all(self):
        self._stop_monitoring()
        self.captures.clear()
        self._csv_tables.clear()
        self.page_counter = 0
        self._last_frame = None
        self._last_captured_image = None
        self.txt_output.delete("1.0", tk.END)
        self._update_page_label()
        self._set_status("Cleared - ready for new session")

    # ---------------------------------------------------- global hotkeys

    def _start_hotkey_listener(self):
        if pynput_keyboard is None:
            return

        def _on_press(key):
            try:
                if key == pynput_keyboard.Key.f9:
                    self.root.after(0, self._manual_capture)
                elif key == pynput_keyboard.Key.f8:
                    self.root.after(0, self._manual_capture_table)
                elif key == pynput_keyboard.Key.f10:
                    self.root.after(0, self._stop_and_process)
            except Exception:
                pass

        self._hotkey_listener = pynput_keyboard.Listener(on_press=_on_press)
        self._hotkey_listener.daemon = True
        self._hotkey_listener.start()

    # ---------------------------------------------------- shutdown

    def _on_close(self):
        self._monitoring = False
        if self._hotkey_listener:
            try:
                self._hotkey_listener.stop()
            except Exception:
                pass
        self.root.destroy()

    def run(self):
        self.root.mainloop()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    missing = []
    if pytesseract is None:
        missing.append("pytesseract")
    if pynput_keyboard is None:
        missing.append("pynput")
    if missing:
        print(
            f"WARNING: Missing packages: {', '.join(missing)}\n"
            "Some features will be unavailable. Run: pip install -r requirements.txt"
        )
    app = ScreenExtractorApp()
    app.run()


if __name__ == "__main__":
    main()
