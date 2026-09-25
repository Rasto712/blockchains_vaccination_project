# Future two-to-three-minute demonstration

The application currently only prints scaffold status. Implement the following before presenting it as a working project:

1. Show the synthetic JSON locally and the registered commitment on-chain.
2. Attempt school access before consent; show denied event and no medical payload.
3. Guardian grants school consent for 30 days; show first reward and a status-only response.
4. Grant doctor scope for 30 days; show vaccine/date only.
5. Revoke school consent; denied (REVOKED).
6. Tamper (doctor grant still active): copy runtime-data/vaccination_record.json to runtime-data/tamper/vaccination_record.json and change one byte inside the batch value (for example ABC123-DEMO to ABC124-DEMO), so it still parses and validates (broken or invalid data makes Python send the zero hash and show unavailable instead). Run the doctor request with a copy of settings whose vaccination_file points to the copy (operator setting, never requester input); denied (HASH_MISMATCH), no payload. Delete the copy; the original still verifies. The frozen file is never opened for writing.
7. Re-grant school for 1 day (no second reward), set the next block timestamp to expiresAt (evm_setNextBlockTimestamp) and request; denied (EXPIRED). Run this last: node time cannot go back.
8. Show passing Solidity tests and measured gas/timing tables.
