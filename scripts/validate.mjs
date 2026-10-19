// Orloom trusted-check target (node-validate-syntax). Not part of Hallam's runtime.
// The verifier only runs `node --check` on this file; Hallam's real tests are pytest.
import { existsSync } from "node:fs";
if (!existsSync(new URL("../pyproject.toml", import.meta.url))) throw new Error("pyproject.toml missing");
console.log("hallam layout ok");
