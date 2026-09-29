# Local data

AI assistance: parts of this project were written with Claude (Anthropic) and thoroughly reviewed.

The files in data/examples/ are synthetic fixtures, not trusted evidence. The menu's "set up local files and salts" (app/records.py) copies them into the runtime folders (runtime-data/ by default), validates them, generates the salts and saves them. No example file or configuration holds valid-looking on-chain commitments or keys.

The vaccination bytes are frozen before the clinic attests. A commitment is SHA-256(prefix || salt32 || raw_bytes), with the UTF-8 prefix VACCINATION:v1 followed by one newline, or IDENTITY:v1 followed by one newline. Each file has its own 32-byte random salt, kept locally as base64 in a file only its owner can read (records.py creates salt files with mode 0600). One read snapshot is both hashed and parsed; even a whitespace change invalidates the commitment.

A card is saved as `json.dumps(card, indent=2, ensure_ascii=False) + "\n"`, encoded as UTF-8 and written in binary mode (never write_text). records.py creates the file exclusively (os.open with O_EXCL, like "xb"), so an existing record is never replaced. That reproduces data/examples/vaccination_record.json byte for byte (269 bytes, LF). Fixed test vector (salt = bytes(range(32)), VACCINATION prefix): 3f2242f3cce59c18d546a104712ece887fdaf0563cd6bd3f4cb5c932cc6ec5b9. The path must be inside data_root: the settings key data_root (runtime-data by default, relative to the project root or absolute), so the frozen record is never saved anywhere else.

School sees status only, doctor sees vaccine/date only. Runtime files and salts are ignored by Git. The prototype has no general vaccination editing, database, versioning or production identity system.

Identity fixtures: one file per registering account in data/examples/identities/ (guardian.json, school.json, doctor.json), each holding only unique_id and email, written as `json.dumps(obj, indent=2) + "\n"`. These are synthetic values, never keys, passwords or real personal data. child-demo-001 is represented by guardian; deployer and clinic never register. At runtime the files are copied to identity_directory and each label gets its own salt at `<identity_salt_directory>/identity_<label>_salt.json`. Every salt file, the vaccination one included, is `{"salt_b64": "<44-char base64 of 32 bytes>"}`.
