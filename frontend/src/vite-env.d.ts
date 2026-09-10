/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

/** Build stamp injected by `vite.config.ts` — see `buildVersion()` there. */
declare const __APP_VERSION__: string;
declare const __APP_BUILD_TIME__: string;
declare const __APP_ENV__: string;
