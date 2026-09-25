# Local data

Examples are synthetic design fixtures, not trusted evidence. Developer 3 implements copying them into runtime-data/, validation, salt generation and saving. Do not generate valid-looking on-chain commitments or keys in example configuration.

Freeze the exact vaccination bytes before the clinic attests. Use SHA-256(prefix || salt32 || raw_bytes), with UTF-8 prefixes VACCINATION:v1 followed by one newline, or IDENTITY:v1 followed by one newline. Keep separate 32-byte random salts locally as base64. Hash and parse one read snapshot; even whitespace changes invalidate the commitment.

To save a card, serialise it as `json.dumps(card, indent=2, ensure_ascii=False) + "\n"`, encode UTF-8 and write with Path.write_bytes (never write_text). That reproduces data/examples/vaccination_record.json byte for byte (269 bytes, LF). Fixed test vector (salt = bytes(range(32)), VACCINATION prefix): 3f2242f3cce59c18d546a104712ece887fdaf0563cd6bd3f4cb5c932cc6ec5b9. The path must be inside data_root.

School sees status only, doctor sees vaccine/date only. Runtime files and salt metadata are ignored by Git. The prototype has no general vaccination editing, database, versioning or production identity system.

Identity fixtures: one file per registering account in data/examples/identities/ (guardian.json, school.json, doctor.json), each holding only unique_id and email, written as `json.dumps(obj, indent=2) + "\n"`. These are synthetic values, never keys, passwords or real personal data. child-demo-001 is represented by guardian; deployer and clinic never register. At runtime the files are copied to identity_directory and each label gets its own salt at `<identity_salt_directory>/identity_<label>_salt.json`. Every salt file, the vaccination one included, is `{"salt_b64": "<44-char base64 of 32 bytes>"}`.
