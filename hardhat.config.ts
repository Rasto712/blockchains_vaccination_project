// Hardhat 3 config. Solidity tests are in test/. Version is pinned in package.json, so change both together.
// The compiler version matches the .sol pragmas. Only a local network, no secrets.
import { defineConfig } from "hardhat/config";

export default defineConfig({
  solidity: { version: "0.8.28", settings: { optimizer: { enabled: true, runs: 200 } } },
  paths: { sources: "./contracts", tests: "./test" },
  networks: {
    localhost: { type: "http", chainType: "l1", url: "http://127.0.0.1:8545", chainId: 31337 },
  },
});
