# Demo walkthrough

AI assistance: parts of this project were written with Claude (Anthropic) and thoroughly reviewed.

`python -m integration.demo_workflow` runs the whole story on a local Hardhat node, with real transactions and the synthetic record, and checks every outcome. This page follows its transcript in the order it runs. The menu can do the same story by hand, except tamper and exact expiry (see the last section).

## Running it

From the project root, after the setup in the [README](../README.md) (`npm ci`, `npm run compile`, the venv with requirements.txt, and config/settings.json):

```bash
npm run node                           # terminal 1: chain 31337 on 127.0.0.1:8545
python -m integration.demo_workflow    # terminal 2; add --settings PATH for another settings file
```

- The script deploys fresh contracts itself, as `python -m scripts.deploy_local --reset` does, so it replaces deployment_file (runtime-data/deployment.json by default).
- Local files and salts are created only when missing, so every identity hash and the record commitment stay the same from run to run.
- Each run moves node time forward about one day. Node time cannot go back, so the script runs only on chain 31337 and does the expiry step last. It can be run again on the same node.
- It ends with `demo passed: every outcome above was checked` (exit 0) or `demo FAILED at step N: reason` (exit 1), where N is the transcript section.
- The transcript shows the actor, the action, the outcome and reason, the fields released, and each transaction's hash, block and gas. It never shows a salt, and the only health data it shows is the synthetic record in section 3. Local files are shown relative to the project or as `<data_root>/...`.

## What the transcript shows

The transcript is split into sections `== 0. ... ==` to `== 8. ... ==`. Sections 0 and 1 prepare the run:

- **0, check the node**: the RPC URL, chain 31337 and the current node time.
- **1, deploy**: IdentityRegistry (with the clinic account as trustedClinic), ConsentRewardToken and ConsentManager, then setMinterOnce(ConsentManager) from the deployer. Each line shows the transaction, block and gas, and each contract its address. Then deployment_file is saved.

The story follows in eight steps. The transcript section of each step is in brackets.

1. **Register and attest [2, 3].** Missing local files are created (three identities, the record, four salts) and the five accounts are listed. Guardian, school and doctor each call registerUser with their salted identity hash, and each on-chain hash is checked against the local one. Deployer and clinic never register. The script then prints the 269-byte local record and its commitment, SHA-256("VACCINATION:v1\n" + 32-byte salt + the exact file bytes). The guardian's own registerVaccination is rejected with NotTrustedClinic and stores nothing. The clinic's succeeds, and the on-chain commitment matches the local one.
2. **School before consent [4].** The school's requestAccess(guardian, MEASLES_STATUS) is denied with NO_CONSENT. It is logged on-chain and releases nothing.
3. **School consent [4].** The guardian grants the school MEASLES_STATUS for 30 days. As the first grant of this consent, it mints one reward unit to the guardian (0 -> 1) in the same transaction; the school holds 0. The school's request is now allowed and releases only `{"measles_status": "verified"}`: no vaccine, date, clinic, batch or child ID.
4. **Doctor consent [5].** The guardian grants the doctor VACCINATION_SCHEDULE for 30 days (reward 1 -> 2). The doctor's request is allowed and releases vaccine and date only, `{"vaccinations": [{"vaccine": "MMR", "date": "2026-03-12"}]}`: no child ID, coverage list, clinic or batch.
5. **Tampered copy [6].** While the doctor grant is active, the script copies the record to `<data_root>/tamper/vaccination_record.json` and changes one byte of the batch value (ABC123-DEMO to ABC124-DEMO). The copy is still a valid card, so Python sends the copy's own hash; an unreadable or invalid record would make Python send the zero hash and answer unavailable instead. The doctor request runs with a copy of the settings whose vaccination_file points to the copy, an operator setting and never requester input. It is denied with HASH_MISMATCH, logged, and releases nothing. The copy is then deleted, even after a failure (the empty tamper/ folder stays), and the original still matches the registered commitment. The original file is never opened for writing, and no new commitment is registered.
6. **Revoke [7].** The guardian revokes the school's consent. The school's next request is denied with REVOKED.
7. **Regrant and exact expiry [7].** The guardian grants the school again, for 1 day. This consent was already rewarded once, so the balance stays at 2. expiresAt is the regrant's block time plus 86,400 s. The script mines an empty block at expiresAt - 1 (evm_setNextBlockTimestamp, then evm_mine), where the checkAccess view still allows; a view logs and releases nothing. It then sets the next block's time to expiresAt. The school's request is mined at exactly expiresAt and denied with EXPIRED, because consent is invalid at the expiry second.
8. **Rewards and audit [8].** The reward balances are guardian 2 and 0 for deployer, clinic, school and doctor. The on-chain audit log has six AccessAttempt events, all for the guardian's record, in this order: school NO_CONSENT, school ALLOWED, doctor ALLOWED, doctor HASH_MISMATCH, school REVOKED, school EXPIRED.

Afterwards the menu works on the demo's contracts: the accounts are registered, the doctor's 30-day grant is still active and the school's 1-day regrant has expired.

## By hand in the menu

`python -m app.main` reads only config/settings.json. Deploy first with `python -m scripts.deploy_local`, adding `--reset` when deployment_file already exists (for example after the demo); existing local files and salts are kept. Pick an actor, then an action by number: 1 set up local files and salts, 2 register, 3 attest, 4 grant, 5 revoke, 6 school check, 7 doctor view, 8 rewards, 9 audit, 10 show my registration, 11 switch actor, 12 quit.

1. As guardian: 1 (setup) and 2 (register). Switch to school and to doctor, and register each.
2. As clinic: 3 (attest the guardian's record). Any other actor gets "rejected: NotTrustedClinic".
3. As guardian: 10 prints the local and on-chain identity hash and record commitment, and that both match.
4. 6 (school check): "denied: NO_CONSENT".
5. As guardian: 4, school, MEASLES_STATUS, 30 days ("reward balance of guardian: 0 -> 1"), then 6: "allowed: measles status verified".
6. As guardian: 4, doctor, VACCINATION_SCHEDULE, 30 days (1 -> 2), then 7: "allowed: MMR on 2026-03-12".
7. As guardian: 5, school, MEASLES_STATUS, then 6: "denied: REVOKED".
8. 8 (rewards): guardian 2, everyone else 0. 9 (audit): NO_CONSENT, ALLOWED, ALLOWED, REVOKED.

The school check and the doctor view always sign as school and doctor, whatever actor is selected, and each answer names the transaction that logged it. Tamper and exact expiry run only in the script, because they need a tampered copy of the record and control of node time.

The tests and measurements behind this story are in [TESTING.md](TESTING.md).
