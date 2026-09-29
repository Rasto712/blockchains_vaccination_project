# Demo walkthrough

The scripted demo plays every role on the local node with real transactions and checks every outcome.

## Run it

After the [README](../README.md) setup, with the venv active and `npm run node` running:

```bash
python -m integration.demo_workflow
```

It ends with `demo passed` or `demo FAILED at step N: reason`. It moves node time a day forward, so it runs only on the local chain 31337.

## Steps

Step numbers match the output's headings.

| Step | Actor | Action | Expected result |
|---|---|---|---|
| 0-1 | deployer | Check the node; deploy fresh contracts | Three contract addresses |
| 2 | guardian, school, doctor | Set up local files; register salted identity hashes | On-chain hash = local hash |
| 3 | guardian, then clinic | Attest the record's salted hash | Guardian rejected: NotTrustedClinic. Clinic: stored |
| 4 | school | Request MEASLES_STATUS | Denied: NO_CONSENT |
| 4 | guardian, school | Grant the school MEASLES_STATUS for 30 days; it requests again | Reward 0 -> 1. Allowed: `{"measles_status": "verified"}` only |
| 5 | guardian, doctor | Grant the doctor VACCINATION_SCHEDULE for 30 days; it requests | Reward 1 -> 2. Allowed: vaccine and date only |
| 6 | doctor | Request with a copy of the record, one byte changed | Denied: HASH_MISMATCH |
| 7 | guardian, school | Revoke the school; it requests | Denied: REVOKED |
| 7 | guardian, school | Regrant the school for 1 day; request at the exact expiry second | Reward stays 2. Denied: EXPIRED |
| 8 | script | Show rewards and the audit log | Guardian 2, others 0; 6 logged attempts |

## By hand in the menu

```bash
python -m scripts.deploy_local --reset
python -m app.main
```

Pick an actor, then try these actions in order: 1 setup; 2 register as guardian, school and doctor; 3 attest as clinic; 4 grant as guardian (school, then doctor); 6 school check; 7 doctor view; 5 revoke; 8 rewards; 9 audit. Tamper and exact expiry are script-only.
