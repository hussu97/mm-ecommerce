/**
 * The Firebase client, loaded only when somebody actually verifies a number.
 *
 * The SDK is roughly a quarter-megabyte and is needed by one component, on two
 * screens, for one action. Importing it at module scope would put it in the
 * bundle every visitor downloads so that a minority can prove a phone number,
 * so `getFirebaseAuth()` imports it dynamically at the moment it is used.
 *
 * The API key here is public by design: it identifies the project, it is not a
 * credential, and it is restricted server-side — HTTP referrers on the key
 * itself, an AE-only SMS region policy, and an authorized-domain list on the
 * Identity Platform config. What actually protects the flow is that the server
 * verifies the resulting token's signature and audience; the browser is not
 * trusted with anything.
 */

const CONFIG = {
  apiKey: process.env.NEXT_PUBLIC_FIREBASE_API_KEY ?? '',
  authDomain: process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN ?? '',
  projectId: process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID ?? '',
  appId: process.env.NEXT_PUBLIC_FIREBASE_APP_ID ?? '',
};

/**
 * Whether phone verification can run at all in this build.
 *
 * Callers hide the feature rather than showing a button that fails: a preview
 * deploy without the Vercel vars set should look like a site that does not
 * offer verification, not a broken one.
 */
export function isFirebaseConfigured(): boolean {
  return Boolean(CONFIG.apiKey && CONFIG.authDomain && CONFIG.projectId);
}

/**
 * The initialised auth instance, with the SDK fetched on first use.
 *
 * `getApps()` guards re-initialisation: React strict mode mounts twice in
 * development, and a second `initializeApp` with the same name throws.
 */
export async function getFirebaseAuth() {
  const [{ getApp, getApps, initializeApp }, { getAuth }] = await Promise.all([
    import('firebase/app'),
    import('firebase/auth'),
  ]);
  const app = getApps().length ? getApp() : initializeApp(CONFIG);
  const auth = getAuth(app);
  // The OTP SMS should arrive in the language the customer is reading the site
  // in. `null` would use the device locale, which is often neither.
  auth.languageCode = document?.documentElement?.lang || 'en';
  return auth;
}

/**
 * Firebase's own invisible reCAPTCHA, which it will not send an SMS without.
 *
 * This is a second bot check on top of our Turnstile, and it is not optional —
 * the web SDK requires an `AppVerifier` and refuses to send otherwise. Made
 * invisible so the customer meets one visible challenge at most.
 */
export async function getRecaptchaVerifier(containerId: string) {
  const { RecaptchaVerifier } = await import('firebase/auth');
  const auth = await getFirebaseAuth();
  return new RecaptchaVerifier(auth, containerId, { size: 'invisible' });
}
