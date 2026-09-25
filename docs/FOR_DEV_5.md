# For Dev 5

What you need from the Python side for the report, the README, the demo and the slides, and what I need from you. Read this next to [tasks/DEV_5.md](tasks/DEV_5.md) and [REPORT_OUTLINE.md](REPORT_OUTLINE.md). The other handoff notes are [FOR_DEV_1.md](FOR_DEV_1.md), [FOR_DEV_2.md](FOR_DEV_2.md) and [FOR_DEV_4.md](FOR_DEV_4.md).

## Where things stand

The Python side is done: the local record, salts and commitments (`app/records.py`), the release rule and the school and doctor views (`app/disclosure.py`), the menu (`app/main.py`) and the tamper step in `integration/demo_workflow.py`. It has 106 unit tests and none of them needs a node: the ones that touch the chain use a fake, because there is no working contract yet. Nothing runs on a real node until the contracts deploy and `chain.py` exists.

## Docs that are out of date

- README: I have updated "What works now", the layout (`tests/`, `runtime-data/`), the run steps (settings copy, tests, `-m`) and the broken errata link. You own it now, so please check it reads right to you.
- DEMO.md:3 says "The application currently only prints scaffold status." The menu exists now.
- Demo order: DEMO.md has revoke as step 5 and tamper as step 6. The scripted demo runs tamper first, then revoke, then the 1-day regrant and expiry. The result is the same, because the tamper step only uses the doctor grant. Either renumber DEMO.md (5 tamper, 6 revoke, 7 regrant and expiry, 8 results) or agree another order with Rasto, who owns `demo_workflow.main`. TESTING.md:11 has the same order as DEMO.md.
- Which tool runs which step: the menu (`python -m app.main`) covers steps 1 to 5:
  - Step 1 is "show my registration" as guardian. It prints the local and on-chain commitment and "local commitment matches: yes". The JSON itself is shown by opening the file.
  - Steps 2 to 5 are school check, grant, doctor view and revoke.

  Tamper and expiry only run from `python -m integration.demo_workflow`. If the menu is the backup, the presenter has to switch actors:
  - guardian, school and doctor each register as themselves
  - attest is done as clinic (anyone else gets "rejected: NotTrustedClinic")
  - grant and revoke are done as guardian
  - the school check and the doctor view always sign as school and doctor
- Audit: the menu's audit action prints one line per access attempt: UTC time, requester, scope, allowed or denied, and the reason. It is not in DEMO.md or in the evidence list in DEV_5.md, but it is good evidence for the audit log design in the Architecture section. Step 8 could also show the Python unit tests passing.
- SCAFFOLD_VALIDATION.md:5-6 still says every workflow placeholder raises NotImplementedError and that `app.main` prints status and exits. Either label it as the scaffold baseline or add the current check.
- TESTING.md:7 could say the Python tests exist (106) and need no node.
- DEV_5.md:12 and :18 only wait for Dev 2 and Dev 4 results. The Python unit test results exist now, and the Python rows for the results table come after the first real run.
- Errata: `docs/PLAN_ERRATA.md` was deleted. I removed the links to it from the README, TEAM_TASKS.md and CONTRACT_API.md.

## Facts for the report

Most of this goes in my Implementation subsection. It is here so the rest of the report says the same thing.

### Commitment scheme

- The commitment is SHA-256(prefix, then salt, then the exact file bytes). The prefix is `VACCINATION:v1` or `IDENTITY:v1` followed by a newline.
- Each commitment has its own 32-byte random salt from Python's `secrets` module.
- There is no canonicalisation, so a single whitespace or line-ending change breaks the commitment.
- Worked example: the example record (`data/examples/vaccination_record.json`, 269 bytes) with the salt `bytes(range(32))` gives `3f2242f3cce59c18d546a104712ece887fdaf0563cd6bd3f4cb5c932cc6ec5b9`. The same bytes and salt with the IDENTITY prefix give `5caf6a69e4d774d6b75cafe49a045f514f61ba3b0bf22deef7a5f2a8c7b23e1b`, which shows the prefix keeps the two kinds of hash apart.
- The flipped-bit test vector is no longer valid JSON, so only use it to show that one bit changes the whole hash.

### What is on-chain, off-chain and hashed

- Off-chain, in plain text and git ignored under `runtime-data/`:
  - `vaccination_record.json` holds `child_id` and one vaccination event: vaccine (must be MMR), covers (must include measles), date (a real YYYY-MM-DD date), clinic and batch.
  - `identities/<label>.json` holds only `unique_id` and `email`, and both are synthetic.
  - `private/*_salt.json` holds the salt as base64.

  Setup makes exactly these 8 files. Rasto's `deployment.json` lives there too.
- Hashed and stored on-chain as bytes32: the record commitment, plus one identity hash each for guardian, school and doctor. The deployer and the clinic never register.
- Python only ever sends addresses, those hashes, the scope, the duration in days and the observed hash on each request. It never sends a salt, raw JSON, any record field or a file path.
- The record is saved byte for byte, only inside `runtime-data/`, and never overwritten. Parsing is strict: bad UTF-8, a repeated key or an extra key is rejected. Each request reads the file once, and the hash and the released view come from that same read.

### What the school and doctor see

- The school gets only `{"measles_status": "verified"}`, printed as "allowed: measles status verified (logged on-chain in 0x...)".
- The doctor gets only `{"vaccinations": [{"vaccine": "MMR", "date": "2026-03-12"}]}`, printed as "allowed: MMR on 2026-03-12 (logged on-chain in 0x...)".
- Neither ever gets the child id, the covers list, the clinic, the batch, the salt, the raw JSON or the file path. The view is picked by scope, not by who asks.
- "Verified" means a clinic-attested MMR record that covers measles exists and matches the on-chain commitment. It does not mean the child is immune, or that the record is clinically complete.
- A failed or missing record shows "unavailable: local record could not be verified, not a clinical result". Never describe that as not vaccinated.

### Release rule and outcomes

- Data is released only if all four checks pass:
  1. the logged event says allowed
  2. the registered commitment matches the snapshot and is not zero
  3. the final recheck passes
  4. only the allowed fields for that scope are released

  ARCHITECTURE.md already matches this.
- The outcomes are allowed, denied (with the reason), unavailable and pending.
- For the audit log design:
  - A missing record, a missing salt or the wrong owner still leaves an access event, because Python sends the zero hash.
  - A late recheck failure leaves a second one.
  - An unknown requester, a node that is down and a reverted transaction leave none.

### Python unit tests

- There are 106 tests, all passing, using the standard library `unittest`:

  | File | Tests |
  | --- | --- |
  | `test_records.py` | 45 |
  | `test_access.py` | 22 |
  | `test_main.py` | 17 |
  | `test_disclosure.py` | 16 |
  | `test_tamper.py` | 6 |

  Run them with `python -m unittest discover -s tests`. They need no node.
- The 28 in `test_access.py` and `test_tamper.py` run against `tests/fake_chain.py`, which copies the agreed contract rules, and the rest need no chain at all. So they are evidence for the Python side only. On-chain evidence comes from Robin's Solidity suites and the scripted integration run, so please report them that way.
- The menu's chain actions have no unit tests. Rewards and audit are only checked on a real node.
- Good candidates for the "why it matters" column:
  - `test_vaccination_vector`
  - `test_one_flipped_bit_changes_the_commitment`
  - `test_changed_record_is_hash_mismatch`
  - `test_deleted_record_sends_the_zero_hash_and_is_unavailable`
  - `test_revoke_between_the_two_checks_is_denied_with_two_requests`
  - `test_view_comes_from_the_checked_snapshot`
  - `test_no_health_values_in_any_denial`
  - `test_copy_is_denied_and_the_original_still_verifies`

### Limitations for the Discussion

REPORT_OUTLINE.md already lists local role selection, Python mediating disclosure, and hashes not proving delivery or completeness. Points 1 and 2 below are details of those. The rest are new from the Python side.

1. The Python app is a trusted disclosure point. It holds the plain record and every salt, decides what gets released, and one process signs for all five demo roles.
2. The menu's school check and doctor view always sign as school and doctor, whatever actor is selected. The actor menu is a demo convenience, not authentication.
3. Hashing is whole-file. One commitment covers the exact bytes, so Python cannot prove a single attribute on its own, and the school's "verified" is worked out by Python, not proven on-chain.
4. The commitment is public, so anyone can copy it as the observed hash. The hash check detects local tampering but does not stop a requester who already has consent. Consent is the real control.
5. The record is frozen with exactly one MMR event. A second dose or a correction would need a new attestation, which the registry refuses.
6. Salts exist only on the local machine. Losing `runtime-data/private` after attestation makes the record unverifiable on that deployment for good.
7. The identity hash only commits to a local synthetic file. It proves nothing about who the person really is.
8. An unknown requester or a node that is down leaves no on-chain event.

### Contribution

- My commits:
  - `ec792cf`: cleanup of tooling, fixtures and interface docstrings, plus edits across README, CONTRACT_API, ARCHITECTURE, DEMO, TESTING, REPORT_OUTLINE and the task files
  - `0f525e4`: removed the "Rules for everyone" section from TEAM_TASKS.md
  - `4774a5a`: salt and commitment, which also deleted PLAN_ERRATA.md
  - `cf61b5d` and `1d8bb00`: the rest of the Python side

  The original scaffold is Rasto's commit `1d376ed`.
- The Dev 3 files carry a one-line AI note in their header, and I will write my own part of the AI statement.

## What I need from you

1. Where the shared report draft lives, and in what format.
2. The heading and number for my Python subsection under "3. Implementation", and a word or page budget for it. The limit is 10 pages including figures, so say whether you prefer short console transcripts to screenshots.
3. Which Discussion points you will write, so my Implementation text does not repeat them.
4. For the slides: how long each person's part is, and which template to use.
5. The test id scheme and results file. My proposal is:
   - SOL-IR-nn, SOL-CM-nn and SOL-RT-nn for Robin's tests
   - PY-nn for mine
   - INT-nn for the scripted run

   Please also send the numbered requirement ids from the Architecture section for the requirement column, and confirm which file you read. It is probably `evaluation/results/test_results.csv`, since the templates keep headers only. Rasto owns `evaluation/`, so tell him too.
6. An early run of the README on your own machine, which DEV_5.md already asks for, now that the README has the settings copy step. On Windows the fixtures hash the same, because `.gitattributes` pins `*.json` to LF.
