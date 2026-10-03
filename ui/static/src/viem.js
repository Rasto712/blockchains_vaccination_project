/* The entry for npm run build:ui: only the parts of Viem that ui/static/chain.js uses. esbuild bundles them
   into ui/static/vendor/viem.js, which the page loads as the global Viem. The bundle ships with the project, so the page
   works offline and nothing is built at run time; rebuild it only after changing this file or the viem version. */
/*! viem (https://viem.sh) - MIT License - Copyright (c) 2023-present weth, LLC */
export {
  createPublicClient, createWalletClient, http,
  ChainMismatchError, ContractFunctionRevertedError, ContractFunctionZeroDataError, HttpRequestError,
  RpcRequestError, TimeoutError,
} from 'viem';
export { hardhat } from 'viem/chains';
