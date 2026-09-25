/**
 * Developer 4: minimal Hardhat 3 configuration, not application business logic.
 * Solidity tests live under test/ and are built in to Hardhat 3.
 * Pin to the lab environment if its exact version differs; do not mix Hardhat 2 config.
 * Compiler version matches all .sol pragmas. No public network or signing secrets.
 */
import { defineConfig } from "hardhat/config";

export default defineConfig({
  solidity: "0.8.28",
  paths: { sources: "./contracts", tests: "./test" },
  networks: {
    localhost: { type: "http", chainType: "l1", url: "http://127.0.0.1:8545", chainId: 31337 },
  },
});
