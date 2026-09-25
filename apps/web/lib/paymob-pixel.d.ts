/**
 * `paymob-pixel` ships a single self-registering bundle and no typings: loading
 * it assigns the SDK to `window.Pixel` and exports nothing, so the module itself
 * is declared as an untyped side-effect import here and the global it registers
 * is typed where it is used (`usePaymobApplePay.ts`).
 *
 * A script file on purpose (no top-level import/export): a shorthand
 * `declare module` inside a module file would be read as an augmentation of a
 * module TypeScript cannot resolve, which is an error.
 */
declare module 'paymob-pixel';
