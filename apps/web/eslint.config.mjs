import nextCoreWebVitals from "eslint-config-next/core-web-vitals";

/** Flat config for ESLint 9 + eslint-config-next 16 (Next.js removed `next lint`). */
const config = [
  {
    ignores: [
      ".next/**",
      "node_modules/**",
      "next-env.d.ts",
      "*.config.{js,mjs,ts}",
    ],
  },
  ...nextCoreWebVitals,
  {
    // eslint-config-next 16 pulls in eslint-plugin-react-hooks v7, which
    // promotes several React-Compiler-era rules to errors. They flag
    // long-standing, working code and are out of scope for this mechanical
    // framework-majors bump. Deferred to a dedicated follow-up so the upgrade
    // preserves the pre-existing lint contract (rules-of-hooks + exhaustive-deps).
    rules: {
      "react-hooks/set-state-in-effect": "off",
      "react-hooks/purity": "off",
    },
  },
];

export default config;
