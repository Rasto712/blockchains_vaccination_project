# Local data

This folder holds the synthetic example files the app starts from, and this page gives the formula for their on-chain commitments. None of it is real personal data, a key or a password.

| File | What it is |
| --- | --- |
| examples/vaccination_record.json | The child's card (child-demo-001): one MMR dose |
| examples/identities/ | One identity (unique_id, email) each for guardian, school and doctor |

Setup (menu action 1, or the scripted demo) copies them to runtime-data/ (git ignored). It gives each file its own random 32-byte salt, stored in a file only your user account can read.

## Commitment

```text
commitment = SHA-256(prefix + salt + exact file bytes)
prefix     = "VACCINATION:v1\n" (card) or "IDENTITY:v1\n" (identity)
```

Any byte change, even whitespace, gives a different commitment. Test vector: the example card with salt bytes 0, 1, ..., 31 gives `3f2242f3cce59c18d546a104712ece887fdaf0563cd6bd3f4cb5c932cc6ec5b9`.
