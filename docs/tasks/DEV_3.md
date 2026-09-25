# Developer 3: Python console, JSON and minimal disclosure

Planned effort: 10-14 hours (8-12 core + 2 hours contingency).

## Files

app/main.py; app/records.py; app/disclosure.py; data/examples/vaccination_record.json; local identity/salt fixtures. Developer 4 owns app/chain.py.

## Implementation steps

1. Create the one-child/one-vaccination fixture and identity fixtures using synthetic values only. Save locally under a configured data root; generate separate 32-byte salts and keep them out of ledger payloads.
2. Read exact bytes once, validate the expected JSON fields and compute the agreed SHA-256 commitment. Use that same snapshot for later display. Fail safely on malformed/missing data or salt.
3. Build a plain terminal menu: select demo actor, register, clinic-attest, grant, revoke, school-check, doctor-view, rewards and audit. Predefined local accounts replace a login/registration website.
4. Call Developer 4's RPC helper for all transactions. Show pending, denied and unavailable separately. Read the committed access event and perform the final permission check before showing any health data.
5. School output contains only authorized verified status. Doctor output contains vaccine/date only. Never return the file path, raw JSON, batch or salt. Missing evidence is unknown/unavailable rather than a medical NO.
6. Add a repeatable tamper demonstration using a copied fixture and a restore step. Do not build general medical-record editing, encryption, databases, version recovery or a GUI.

## Handoff

Day 1: agree request/result shapes with Developer 4. By Day 3: file/hash functions tested against fixed bytes. Day 4: consume real receipts/events rather than mock permission responses.

## Acceptance criteria

- The record is physically saved locally and loaded without a cloud service.
- Hashing is reproducible; altering any file byte breaks the original commitment.
- No health values appear on denied/unavailable responses or ordinary logs.
- One menu can run the complete school and doctor demonstration with no source edits.

Implementation details: [contract API](../CONTRACT_API.md), [architecture](../ARCHITECTURE.md), [validation](../TESTING.md).
