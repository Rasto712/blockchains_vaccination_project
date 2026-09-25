# Local data

Examples are synthetic design fixtures, not trusted evidence. Developer 3 implements copying them into runtime-data/, validation, salt generation and saving. Do not generate valid-looking on-chain commitments or keys in example configuration.

Freeze the exact vaccination bytes before the clinic attests. Use SHA-256(prefix || salt32 || raw_bytes), with UTF-8 prefixes VACCINATION:v1 followed by one newline, or IDENTITY:v1 followed by one newline. Keep separate 32-byte random salts locally as base64. Hash and parse one read snapshot; even whitespace changes invalidate the commitment.

School sees status only, doctor sees vaccine/date only. The child is represented by the guardian. Runtime files and salt metadata are ignored by Git. The prototype has no general vaccination editing, database, versioning or production identity system.
