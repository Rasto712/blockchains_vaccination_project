# One-week architecture

This replaces the earlier Java/Fabric design. Implement Python plus exactly three Solidity contracts on the local Hardhat lab network. The old Java scaffold is preserved in archive/previous_java_scaffold.zip on the Desktop project.

```mermaid
flowchart TB
    Actors[Guardian / clinic / school / doctor]
    App[Python console]
    Data[(Local JSON and private salts)]
    Consent[ConsentManager.sol]
    Registry[IdentityRegistry.sol]
    Token[ConsentRewardToken.sol]
    Actors --> App
    App --> Data
    App --> Consent
    App --> Registry
    App --> Token
    Consent --> Registry
    Consent --> Token
```

IdentityRegistry stores hashed account identity and one frozen vaccination commitment per guardian, attested by a fixed clinic. ConsentManager owns exact owner/requester/scope expiry/revocation, access events and lifetime reward deduplication. ConsentRewardToken maintains non-transferable units and only permits the configured manager to mint.

Python is the trusted disclosure boundary. It reads and hashes the same local byte snapshot, submits a logged access attempt, checks the successful event/receipt, rechecks current permission and returns only allowlisted fields. Anyone with OS-level plaintext access can copy the file outside the app; this is a synthetic local demo, not production healthcare access control.

```mermaid
sequenceDiagram
    actor Requester
    participant Python
    participant JSON as Local JSON
    participant Manager as ConsentManager
    participant Registry as IdentityRegistry
    Requester->>Python: Request one scope
    Python->>JSON: Read one byte snapshot plus salt
    Python->>Python: Validate and hash
    Python->>Manager: requestAccess(owner, scope, observedHash)
    Manager->>Registry: Check registered identities and evidence
    Manager->>Manager: Check consent, expiry, revocation and hash
    Manager-->>Python: Committed allowed/denied event
    Python->>Manager: Final permission recheck
    alt Every required check passes
        Python-->>Requester: Permitted fields only
    else Denied, invalid or unavailable
        Python-->>Requester: No health payload
    end
```

Well-formed business denial must eventually return false with an event, not revert. Current placeholder methods deliberately revert until implemented; do not confuse that with final denial behavior. Malformed transactions/RPC failures cannot create on-chain events. An event records authorization, not physical delivery. Revocation cannot retract already-disclosed data.
