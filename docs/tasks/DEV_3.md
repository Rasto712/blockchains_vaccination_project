# Developer 3: Python console, JSON and minimal disclosure

## Files

app/main.py; app/records.py; app/disclosure.py; data/examples/vaccination_record.json; local identity/salt fixtures; app/models.py (shared). Developer 4 owns app/chain.py.

## Implementation steps

1. Create the one-child/one-vaccination fixture and one identity file and one salt per registering account (see data/README.md), using synthetic values only. Save locally under a configured data root; generate separate 32-byte salts and keep them out of ledger payloads.
2. Read exact bytes once, validate the expected JSON fields and compute the agreed SHA-256 commitment. Use that same snapshot for later display. Fail safely on malformed/missing data or salt.
3. Build a plain terminal menu: select demo actor, register, clinic-attest, grant, revoke, school-check, doctor-view, rewards and audit. Predefined local accounts replace a login/registration website.
4. Call Developer 4's RPC helper for all transactions. Show pending, denied and unavailable separately. Read the committed access event and perform the final permission check before showing any health data.
5. School output contains only authorized verified status. Doctor output contains vaccine/date only. Never return the file path, raw JSON, batch or salt. Missing evidence is unavailable rather than a medical NO.
6. Add a repeatable tamper demonstration: copy the record to runtime-data/tamper/, change one byte inside the batch value, run the doctor request against the copy (HASH_MISMATCH, no payload), then delete the copy. The original file is never written to. Do not build general medical-record editing, encryption, databases, version recovery or a GUI.

## Handoff

Day 1: agree request/result shapes with Developer 4. By Day 3: file/hash functions tested against fixed bytes. Day 4: consume real receipts/events rather than mock permission responses.

## Acceptance criteria

- The record is physically saved locally and loaded without a cloud service.
- Hashing is reproducible; altering any file byte breaks the original commitment.
- No health values appear on denied/unavailable responses or ordinary logs.
- One menu runs the school and doctor access demonstration (register, attest, grant, revoke, school-check, doctor-view, rewards, audit) with no source edits; the tamper and exact-expiry steps run via integration/demo_workflow.py and the Solidity expiry test.

Implementation details: [contract API](../CONTRACT_API.md), [architecture](../ARCHITECTURE.md), [validation](../TESTING.md).
