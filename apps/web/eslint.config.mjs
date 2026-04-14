import coreWebVitals from "eslint-config-next/core-web-vitals";
import typescript from "eslint-config-next/typescript";

export default [
  ...coreWebVitals,
  ...typescript,
  {
    rules: {
      // react-hooks v7 added this rule but it flags intentional patterns like
      // setMounted(true) in effects (SSR hydration safety) and animation state
      // transitions. Downgrade to warn so CI doesn't hard-fail on valid code.
      "react-hooks/set-state-in-effect": "warn",
    },
  },
];
