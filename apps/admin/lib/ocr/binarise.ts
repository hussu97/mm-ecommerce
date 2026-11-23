/**
 * Black-and-white copy of a photo for OCR: Bradley's adaptive threshold, which
 * compares each pixel with the mean of its neighbourhood rather than with one
 * global level. A phone photo of a card on a patterned cloth, with a tinted
 * guilloche behind the print, gives Tesseract almost nothing as it is; this
 * keeps the strokes and drops the background and the tint.
 *
 * Pure (RGBA in, RGBA out) so the tests and a Node repro run the same code the
 * browser does.
 */

/** Window: 1/16 of the width either side. Threshold: 15% darker than the mean. */
const WINDOW_FRACTION = 1 / 16;
const DARKER_BY = 0.15;

export function binarise(rgba: Uint8ClampedArray, width: number, height: number): Uint8ClampedArray {
  const n = width * height;
  const grey = new Uint8ClampedArray(n);
  for (let i = 0; i < n; i++) {
    grey[i] = (rgba[i * 4] * 299 + rgba[i * 4 + 1] * 587 + rgba[i * 4 + 2] * 114) / 1000;
  }
  // Integral image, one row and column of zeros ahead.
  const stride = width + 1;
  const integral = new Float64Array(stride * (height + 1));
  for (let y = 0; y < height; y++) {
    let row = 0;
    for (let x = 0; x < width; x++) {
      row += grey[y * width + x];
      integral[(y + 1) * stride + x + 1] = integral[y * stride + x + 1] + row;
    }
  }
  const s = Math.max(1, Math.round(width * WINDOW_FRACTION));
  const out = new Uint8ClampedArray(n * 4);
  for (let y = 0; y < height; y++) {
    const y1 = Math.max(0, y - s);
    const y2 = Math.min(height - 1, y + s);
    for (let x = 0; x < width; x++) {
      const x1 = Math.max(0, x - s);
      const x2 = Math.min(width - 1, x + s);
      const count = (x2 - x1 + 1) * (y2 - y1 + 1);
      const sum =
        integral[(y2 + 1) * stride + x2 + 1] -
        integral[y1 * stride + x2 + 1] -
        integral[(y2 + 1) * stride + x1] +
        integral[y1 * stride + x1];
      const v = grey[y * width + x] * count <= sum * (1 - DARKER_BY) ? 0 : 255;
      const o = (y * width + x) * 4;
      out[o] = out[o + 1] = out[o + 2] = v;
      out[o + 3] = 255;
    }
  }
  return out;
}

/** Whether an OCR pass found something shaped like a machine-readable zone:
 *  two lines of 28+ MRZ characters, each with a run of digits or a '<<'. */
export function looksLikeMrz(text: string): boolean {
  const lines = text
    .split('\n')
    .map((l) => l.replace(/\s+/g, ''))
    .filter((l) => l.length >= 28 && /^[A-Z0-9<]+$/.test(l) && /\d{6}|<</.test(l));
  return lines.length >= 2;
}
