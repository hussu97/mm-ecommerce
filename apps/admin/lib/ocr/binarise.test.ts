import { describe, expect, it } from 'vitest';
import { binarise, looksLikeMrz } from './binarise';

describe('binarise', () => {
  it('keeps dark strokes and whitens a tinted, uneven background', () => {
    const w = 48;
    const h = 24;
    const rgba = new Uint8ClampedArray(w * h * 4);
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const i = (y * w + x) * 4;
        // A pink tint that darkens left to right, and a stroke at x = 30–31.
        const shade = 230 - x;
        const stroke = x === 30 || x === 31;
        rgba[i] = stroke ? 60 : shade;
        rgba[i + 1] = stroke ? 60 : shade - 30;
        rgba[i + 2] = stroke ? 60 : shade - 20;
        rgba[i + 3] = 255;
      }
    }
    const out = binarise(rgba, w, h);
    const at = (x: number, y: number) => out[(y * w + x) * 4];
    expect(at(30, 12)).toBe(0);
    expect(at(31, 12)).toBe(0);
    expect(at(5, 12)).toBe(255);
    expect(at(45, 12)).toBe(255);
    expect(out[3]).toBe(255);
  });
});

describe('looksLikeMrz', () => {
  it('spots a zone and ignores noise', () => {
    expect(
      looksLikeMrz('P<INDKUMAR<<RAVI<SURESH<<<<<<<<<<<<<<<<<<<<<<\nZ1234567<1IND9003152M3412318UE12345678901266'),
    ).toBe(true);
    expect(looksLikeMrz('Ta <<<\noe M<AKHTER<<\n4 84199796964791')).toBe(false);
  });
});
