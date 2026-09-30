// Copies the Draco and Basis (KTX2) decoders that <model-viewer> needs for
// compressed models into public/decoders/, so they are served from the site
// itself instead of www.gstatic.com (Content-Security-Policy: no third-party
// script or connect origins). Runs before `astro build` / `astro dev`; the
// copies are git-ignored and always match the installed three.js.
import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const web = join(dirname(fileURLToPath(import.meta.url)), "..");
const libs = join(web, "node_modules/three/examples/jsm/libs");
const files = {
  draco: [
    "draco/gltf/draco_decoder.js",
    "draco/gltf/draco_decoder.wasm",
    "draco/gltf/draco_wasm_wrapper.js",
  ],
  basis: ["basis/basis_transcoder.js", "basis/basis_transcoder.wasm"],
};
for (const [folder, list] of Object.entries(files)) {
  const target = join(web, "public/decoders", folder);
  mkdirSync(target, { recursive: true });
  for (const file of list) copyFileSync(join(libs, file), join(target, file.split("/").pop()));
}
console.log("decoders copied to public/decoders/{draco,basis}");
