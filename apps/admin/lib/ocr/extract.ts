/**
 * Read the text off a document the super-admin picked — free, in the browser.
 *
 * A PDF that carries a text layer (the municipality OHC, MOHRE and ICP
 * e-documents do) is read directly with pdf.js: exact, instant, no OCR. A scan
 * or photo — or a PDF that is only an image — goes through Tesseract compiled
 * to WebAssembly (tesseract.js), which runs on this machine; its worker, core
 * and the English model are fetched once from the jsDelivr CDN and cached by
 * the browser. Nothing is sent to a paid service and the API never sees the
 * image until the super-admin saves.
 *
 * Tesseract runs twice — block layout (keeps "label : value" on one line) and
 * sparse text (finds values floating away from their label, like a card's
 * expiry date) — and the parsers read both. Documents with a machine-readable
 * zone (passport, Emirates ID) get a third pass over the bottom of the page
 * restricted to MRZ characters.
 *
 * A two-sided card (the Emirates ID) is read whole: a scanned PDF has both
 * pages rendered and stacked, and two photos are combined into one image
 * (`combineImages`) that is both read and stored, so the record keeps one file.
 */

export type ExtractProgress = (stage: string, fraction?: number) => void;

export type Extracted = {
  text: string;
  source: 'pdf-text' | 'ocr';
};

/** Text-layer items as pdf.js returns them. */
type PdfTextItem = { str?: string; hasEOL?: boolean };

/** Join pdf.js text items into lines. Exported for the parser tests. */
export function textFromPdfItems(items: PdfTextItem[]): string {
  let out = '';
  for (const item of items) {
    if (typeof item.str !== 'string') continue;
    out += item.str;
    out += item.hasEOL ? '\n' : ' ';
  }
  return out.replace(/ +\n/g, '\n').trim();
}

/** Below this many letters/digits, a PDF's text layer is treated as absent. */
const MIN_TEXT_LAYER = 40;
/** Render scale for an image-only PDF page — ~200 dpi for an A4 page. */
const PDF_RENDER_SCALE = 2.5;
/** Tesseract reads small print badly; a phone shot is upscaled to this width. */
const MIN_OCR_WIDTH = 1800;
const MRZ_CHARS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<';
/** Width two combined photos are drawn at — legible to Tesseract, and a JPEG
 *  of two card sides at this size stays far under the API's 10 MB limit. */
const COMBINED_WIDTH = 2000;

async function loadPdf(file: File) {
  const pdfjs = await import('pdfjs-dist');
  if (!pdfjs.GlobalWorkerOptions.workerSrc) {
    pdfjs.GlobalWorkerOptions.workerSrc = new URL(
      'pdfjs-dist/build/pdf.worker.min.mjs',
      import.meta.url,
    ).toString();
  }
  const data = new Uint8Array(await file.arrayBuffer());
  return pdfjs.getDocument({ data }).promise;
}

function canvasOf(width: number, height: number): HTMLCanvasElement {
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(width);
  canvas.height = Math.round(height);
  return canvas;
}

async function imageCanvas(file: File): Promise<HTMLCanvasElement> {
  const bitmap = await createImageBitmap(file);
  const scale = bitmap.width < MIN_OCR_WIDTH ? MIN_OCR_WIDTH / bitmap.width : 1;
  const canvas = canvasOf(bitmap.width * scale, bitmap.height * scale);
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('This browser cannot read images');
  ctx.imageSmoothingQuality = 'high';
  ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  bitmap.close();
  return canvas;
}

/** Draw *sources* one under another at a common width, on white. */
function stack(sources: Array<CanvasImageSource & { width: number; height: number }>, maxWidth?: number) {
  const widest = Math.max(...sources.map((s) => s.width));
  const width = maxWidth ? Math.min(maxWidth, widest) : widest;
  const heights = sources.map((s) => Math.round(s.height * (width / s.width)));
  const canvas = canvasOf(width, heights.reduce((sum, h) => sum + h, 0));
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('This browser cannot read images');
  ctx.fillStyle = '#fff';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.imageSmoothingQuality = 'high';
  let y = 0;
  sources.forEach((s, i) => {
    ctx.drawImage(s, 0, y, width, heights[i]);
    y += heights[i];
  });
  return canvas;
}

/**
 * The two sides of a card, picked as two photos, as one JPEG, one above the
 * other in the order the browser lists them (which is not reliably the order
 * clicked — so nothing downstream assumes which side is on top). The result is
 * what gets read *and* uploaded, so a cycle keeps a single scan of both sides.
 */
export async function combineImages(files: File[], name: string): Promise<File> {
  if (files.some((f) => !f.type.startsWith('image/'))) {
    throw new Error('Only photos (JPEG, PNG, WebP) can be combined — pick one PDF on its own.');
  }
  const bitmaps = await Promise.all(files.map((f) => createImageBitmap(f)));
  try {
    const canvas = stack(bitmaps, COMBINED_WIDTH);
    const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.9));
    if (!blob) throw new Error('This browser cannot combine images');
    return new File([blob], name, { type: 'image/jpeg' });
  } finally {
    bitmaps.forEach((b) => b.close());
  }
}

/** The bottom third of a page, where a passport's MRZ sits. */
function bottomBand(source: HTMLCanvasElement): HTMLCanvasElement {
  const top = Math.round(source.height * 0.62);
  const band = canvasOf(source.width, source.height - top);
  band.getContext('2d')?.drawImage(source, 0, top, source.width, band.height, 0, 0, band.width, band.height);
  return band;
}

async function ocr(
  canvas: HTMLCanvasElement,
  { mrz, twoSided, onProgress }: { mrz: boolean; twoSided: boolean; onProgress?: ExtractProgress },
): Promise<string> {
  const { createWorker, PSM } = await import('tesseract.js');
  onProgress?.('Loading the text reader…');
  const worker = await createWorker('eng', 1, {
    logger: (m: { status: string; progress: number }) => {
      if (m.status === 'recognizing text') onProgress?.('Reading the document…', m.progress);
    },
  });
  try {
    const passes: string[] = [];
    await worker.setParameters({
      tessedit_pageseg_mode: PSM.SINGLE_BLOCK,
      preserve_interword_spaces: '1',
    });
    passes.push((await worker.recognize(canvas)).data.text);
    await worker.setParameters({ tessedit_pageseg_mode: PSM.SPARSE_TEXT });
    passes.push((await worker.recognize(canvas)).data.text);
    if (mrz) {
      await worker.setParameters({
        tessedit_pageseg_mode: PSM.SINGLE_BLOCK,
        tessedit_char_whitelist: MRZ_CHARS,
      });
      // A stacked two-sided card may have its back (and MRZ) on top or below,
      // so it gets the MRZ pass over the whole image; the parser finds the
      // zone by its shape among whatever else that pass reads.
      passes.push((await worker.recognize(twoSided ? canvas : bottomBand(canvas))).data.text);
    }
    return passes.join('\n\n');
  } finally {
    await worker.terminate();
  }
}

export type ExtractOptions = {
  /** Add the MRZ pass (passport, Emirates ID). */
  mrz?: boolean;
  /** An image-only PDF's second page is the card's back: read it too. */
  twoSided?: boolean;
  onProgress?: ExtractProgress;
};

export async function extractText(
  file: File,
  { mrz = false, twoSided = false, onProgress }: ExtractOptions = {},
): Promise<Extracted> {
  if (file.type === 'application/pdf') {
    onProgress?.('Opening the PDF…');
    const pdf = await loadPdf(file);
    try {
      const pages: string[] = [];
      for (let n = 1; n <= Math.min(pdf.numPages, 2); n++) {
        const page = await pdf.getPage(n);
        pages.push(textFromPdfItems((await page.getTextContent()).items as PdfTextItem[]));
      }
      const text = pages.join('\n');
      if (text.replace(/[^\p{L}\p{N}]/gu, '').length >= MIN_TEXT_LAYER) {
        return { text, source: 'pdf-text' };
      }
      // An image-only PDF (a scan saved as PDF): render page one — and, for
      // a two-sided card, page two under it — and OCR that.
      onProgress?.('Rendering the scan…');
      const rendered: HTMLCanvasElement[] = [];
      for (let n = 1; n <= Math.min(pdf.numPages, twoSided ? 2 : 1); n++) {
        const page = await pdf.getPage(n);
        const viewport = page.getViewport({ scale: PDF_RENDER_SCALE });
        const canvas = canvasOf(viewport.width, viewport.height);
        const ctx = canvas.getContext('2d');
        if (!ctx) throw new Error('This browser cannot render PDFs');
        await page.render({ canvas, canvasContext: ctx, viewport }).promise;
        rendered.push(canvas);
      }
      const canvas = rendered.length === 1 ? rendered[0] : stack(rendered);
      return { text: await ocr(canvas, { mrz, twoSided, onProgress }), source: 'ocr' };
    } finally {
      await pdf.loadingTask.destroy();
    }
  }
  if (!file.type.startsWith('image/')) {
    throw new Error('Only images (JPEG, PNG, WebP) and PDFs can be read.');
  }
  onProgress?.('Preparing the image…');
  const canvas = await imageCanvas(file);
  return { text: await ocr(canvas, { mrz, twoSided, onProgress }), source: 'ocr' };
}
