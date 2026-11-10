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
 * expiry date) — and the parsers read both. Passports get a third pass over
 * the bottom of the page restricted to MRZ characters.
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

/** The bottom third of a page, where a passport's MRZ sits. */
function bottomBand(source: HTMLCanvasElement): HTMLCanvasElement {
  const top = Math.round(source.height * 0.62);
  const band = canvasOf(source.width, source.height - top);
  band.getContext('2d')?.drawImage(source, 0, top, source.width, band.height, 0, 0, band.width, band.height);
  return band;
}

async function ocr(
  canvas: HTMLCanvasElement,
  { passport, onProgress }: { passport: boolean; onProgress?: ExtractProgress },
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
    if (passport) {
      await worker.setParameters({
        tessedit_pageseg_mode: PSM.SINGLE_BLOCK,
        tessedit_char_whitelist: MRZ_CHARS,
      });
      passes.push((await worker.recognize(bottomBand(canvas))).data.text);
    }
    return passes.join('\n\n');
  } finally {
    await worker.terminate();
  }
}

export async function extractText(
  file: File,
  { passport = false, onProgress }: { passport?: boolean; onProgress?: ExtractProgress } = {},
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
      // An image-only PDF (a scan saved as PDF): render page one and OCR it.
      onProgress?.('Rendering the scan…');
      const page = await pdf.getPage(1);
      const viewport = page.getViewport({ scale: PDF_RENDER_SCALE });
      const canvas = canvasOf(viewport.width, viewport.height);
      const ctx = canvas.getContext('2d');
      if (!ctx) throw new Error('This browser cannot render PDFs');
      await page.render({ canvas, canvasContext: ctx, viewport }).promise;
      return { text: await ocr(canvas, { passport, onProgress }), source: 'ocr' };
    } finally {
      await pdf.loadingTask.destroy();
    }
  }
  if (!file.type.startsWith('image/')) {
    throw new Error('Only images (JPEG, PNG, WebP) and PDFs can be read.');
  }
  onProgress?.('Preparing the image…');
  const canvas = await imageCanvas(file);
  return { text: await ocr(canvas, { passport, onProgress }), source: 'ocr' };
}
