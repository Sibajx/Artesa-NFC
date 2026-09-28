// Visual review only: full-page screenshots of every page (not a test).
import base from "./playwright.config";
export default { ...base, testMatch: "**/*.shots.ts", reporter: "list" };
