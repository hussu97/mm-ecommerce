/**
 * What a product image looks like before it has arrived.
 *
 * `next/image` is lazy below the fold, and lazy means there is a moment — a
 * long one on a phone on 4G — where the tile is an empty box. At twelve
 * products a page that was two boxes; at fifty it is most of the page, and a
 * grid of empty rectangles filling in one at a time reads as broken rather
 * than as loading.
 *
 * A base64 literal rather than something built at runtime: `btoa` is not in
 * Node and `Buffer` is not in the browser, and this string renders in both.
 * Eight pixels of vertical gradient in the same tones as the tile it sits in
 * (`#f9f5f0`, the card's own background) — `next/image` blurs it up to fill the
 * frame and cross-fades to the photograph, so a tile that has not loaded looks
 * like a tile that is about to rather than like a hole.
 */
export const PRODUCT_IMAGE_BLUR =
  'data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCA4IDgiPjxkZWZzPjxsaW5lYXJHcmFkaWVudCBpZD0iZyIgeDE9IjAiIHkxPSIwIiB4Mj0iMCIgeTI9IjEiPjxzdG9wIG9mZnNldD0iMCUiIHN0b3AtY29sb3I9IiNmOWY1ZjAiLz48c3RvcCBvZmZzZXQ9IjEwMCUiIHN0b3AtY29sb3I9IiNlY2UyZDYiLz48L2xpbmVhckdyYWRpZW50PjwvZGVmcz48cmVjdCB3aWR0aD0iOCIgaGVpZ2h0PSI4IiBmaWxsPSJ1cmwoI2cpIi8+PC9zdmc+';
