/**
 * Developer 4: minimal Hardhat 3 configuration, not application business logic.
 * Solidity tests live under test/; Hardhat 3 runs them natively and cheatcodes come from forge-std (devDependency).
 * Pinned to Hardhat 3.18.0 in package.json; change both together. Do not mix Hardhat 2 config.
 * Compiler version matches all .sol pragmas. No public network or signing secrets.
 */
import { defineConfig } from "hardhat/config";

export default defineConfig({
  solidity: { version: "0.8.28", settings: { optimizer: { enabled: true, runs: 200 } } },
  paths: { sources: "./contracts", tests: "./test" },
  networks: {
    localhost: { type: "http", chainType: "l1", url: "http://127.0.0.1:8545", chainId: 31337 },
  },
});
