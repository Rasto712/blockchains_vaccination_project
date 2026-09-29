# Two-to-three-minute demonstration

Status (2026-09-28): every step below runs on a local node. `python -m integration.demo_workflow` runs steps 1-7 in this order, checks each outcome and ends with the balances and audit log of step 8 (the script's own step numbers are in brackets). The menu, `python -m app.main`, is the backup for steps 1-4 and 6; tamper and expiry only run from the scripted demo.

Before presenting: `npm run compile`, then `npm run node` in one terminal and `python -m integration.demo_workflow` in another (add `--settings PATH` for another settings file). The script deploys fresh contracts itself, replacing deployment.json, and moves node time forward about one day per run, so it can be rehearsed on the same node. Afterwards the menu works on the demo's contracts: the accounts are registered, the doctor's 30-day grant is still active and the school's 1-day regrant has expired.

1. Show the synthetic JSON locally and the registered commitment on-chain. The guardian's own attest is rejected (NotTrustedClinic); the clinic's succeeds. [2, 3]
2. Attempt school access before consent; show denied event (NO_CONSENT) and no medical payload. [4]
3. Guardian grants school consent for 30 days; show first reward (guardian 0 -> 1) and a status-only response. [4]
4. Grant doctor scope for 30 days (guardian 1 -> 2); show vaccine/date only. [5]
5. Tamper (doctor grant still active): copy runtime-data/vaccination_record.json to runtime-data/tamper/vaccination_record.json (tamper/ inside the settings data_root) and change one byte inside the batch value (for example ABC123-DEMO to ABC124-DEMO), so it still parses and validates (broken or invalid data makes Python send the zero hash and show unavailable instead). Run the doctor request with a copy of settings whose vaccination_file points to the copy (operator setting, never requester input); denied (HASH_MISMATCH), no payload. Delete the copy; the original still verifies. The frozen file is never opened for writing. [6]
6. Revoke school consent; denied (REVOKED). [7]
7. Re-grant school for 1 day (no second reward: guardian stays at 2), set the next block timestamp to expiresAt (evm_setNextBlockTimestamp) and request; denied (EXPIRED). Run this last: node time cannot go back. [7]
8. Show the reward balances (guardian 2, everyone else 0) and the on-chain audit log: six AccessAttempt events, NO_CONSENT, ALLOWED, ALLOWED, HASH_MISMATCH, REVOKED, EXPIRED [8]. Then show the passing Solidity tests (`npm test`, 38 passing), the Python unit tests (`python -m unittest discover -s tests`, 337 OK) and the measured gas/timing tables in evaluation/results/ ([TESTING.md](TESTING.md)).

Menu backup: guardian, school and doctor each register as themselves, attest is done as clinic (anyone else gets "rejected: NotTrustedClinic"), grant and revoke are done as guardian, and the school check and doctor view always sign as school and doctor. "Show my registration" as guardian prints both commitments for step 1; the audit action prints the log for step 8.

AI assistance: this file was updated to the scripted demo with Claude (Anthropic); review before submission.
