"""A small stand-in for app.chain, for unit tests only. Never import it from app/ or integration/.
Signatures match app/chain.py, so a wrong keyword fails the test. Access follows the frozen precedence:
unsupported scope, not registered, missing evidence, no consent, revoked, expired, then the hash.
AI note: parts of this file were written with help from Claude and checked by hand.
"""
from unittest import mock

from app import chain, records
from app.models import Reason, TransactionRejected

ZERO_HASH = bytes(32)
DAY = 24 * 60 * 60
CONTRACTS = ("IdentityRegistry", "ConsentManager", "ConsentRewardToken")
FAKED = (
    "connect", "select_account", "load_contract", "register_user", "register_vaccination",
    "get_user_info", "grant_consent", "revoke_consent", "check_access", "request_access",
)


class FakeChain:
    def __init__(self, settings):
        # letters in the addresses, so a case mix-up would show
        self.addresses = {label: f"0x{index + 1:02x}{'ab' * 19}" for label, index in settings["actor_account_indices"].items()}
        self.users = {}
        self.consents = {}
        self.events = []
        self.now = 1_700_000_000
        # every call as (name, arguments)
        self.calls = []
        # name -> exception raised on the next call of that name
        self.failures = {}
        # name -> function run once just before the next call of that name
        self.before = {}
        self.transactions = 0

    def calls_to(self, name):
        return [arguments for called, arguments in self.calls if called == name]

    def connect(self, settings):
        self._start("connect")
        return self

    def select_account(self, client, actor_label, settings):
        self._start("select_account", actor_label=actor_label)
        if actor_label not in self.addresses:
            raise ValueError("unknown actor label")
        return self.addresses[actor_label]

    def load_contract(self, client, name, deployment_path):
        self._start("load_contract", name=name)
        if name not in CONTRACTS:
            raise ValueError("unknown contract")
        return name

    def register_user(self, registry, account, identity_hash):
        self._start("register_user", account=account, identity_hash=identity_hash)
        self._check_hash(identity_hash)
        if identity_hash == ZERO_HASH:
            raise TransactionRejected("ZeroHash")
        if account.lower() in self.users:
            raise TransactionRejected("AlreadyRegistered")
        self.users[account.lower()] = {"identity_hash": identity_hash, "vaccination_hash": ZERO_HASH}
        return self._receipt()

    def register_vaccination(self, registry, clinic, guardian, record_hash):
        self._start("register_vaccination", clinic=clinic, guardian=guardian, record_hash=record_hash)
        self._check_hash(record_hash)
        if clinic.lower() != self.addresses["clinic"].lower():
            raise TransactionRejected("NotTrustedClinic")
        user = self.users.get(guardian.lower())
        if user is None:
            raise TransactionRejected("NotRegistered")
        if record_hash == ZERO_HASH:
            raise TransactionRejected("ZeroHash")
        if user["vaccination_hash"] != ZERO_HASH:
            raise TransactionRejected("EvidenceAlreadyRegistered")
        user["vaccination_hash"] = record_hash
        return self._receipt()

    def get_user_info(self, registry, account):
        self._start("get_user_info", account=account)
        user = self.users.get(account.lower())
        if user is None:
            return {"registered": False, "identity_hash": ZERO_HASH, "vaccination_hash": ZERO_HASH}
        return {"registered": True, "identity_hash": user["identity_hash"], "vaccination_hash": user["vaccination_hash"]}

    def grant_consent(self, manager, guardian, requester, scope, duration_days):
        self._start("grant_consent", guardian=guardian, requester=requester, scope=scope, duration_days=duration_days)
        if scope not in (1, 2):
            raise TransactionRejected("UnsupportedScope")
        if not 1 <= duration_days <= 365:
            raise TransactionRejected("InvalidDuration")
        if guardian.lower() not in self.users or requester.lower() not in self.users:
            raise TransactionRejected("NotRegistered")
        key = (guardian.lower(), requester.lower(), int(scope))
        current = self.consents.get(key)
        if current and not current["revoked"] and self.now < current["expires_at"]:
            raise TransactionRejected("ConsentStillActive")
        self.consents[key] = {"expires_at": self.now + duration_days * DAY, "revoked": False}
        return self._receipt()

    def revoke_consent(self, manager, guardian, requester, scope):
        self._start("revoke_consent", guardian=guardian, requester=requester, scope=scope)
        if scope not in (1, 2):
            raise TransactionRejected("UnsupportedScope")
        current = self.consents.get((guardian.lower(), requester.lower(), int(scope)))
        if current is None:
            raise TransactionRejected("NoConsentToRevoke")
        current["revoked"] = True
        return self._receipt()

    def check_access(self, manager, owner, requester, scope):
        self._start("check_access", owner=owner, requester=requester, scope=scope)
        allowed, reason = self._evaluate(owner, requester, scope)
        # plain int, the way web3 decodes the Reason enum
        return {"allowed": allowed, "reason": int(reason)}

    def request_access(self, manager, requester, owner, scope, observed_hash):
        self._start("request_access", requester=requester, owner=owner, scope=scope, observed_hash=observed_hash)
        self._check_hash(observed_hash)
        allowed, reason = self._evaluate(owner, requester, scope)
        # any difference, zero included, is a mismatch, but only once everything else allows
        if allowed and observed_hash != self.users[owner.lower()]["vaccination_hash"]:
            allowed, reason = False, Reason.HASH_MISMATCH
        event = {
            "owner": owner, "requester": requester, "scope": int(scope), "timestamp": self.now,
            "allowed": allowed, "reason": int(reason), "transaction_hash": self._receipt()["transaction_hash"],
        }
        self.events.append(event)
        return dict(event)

    def _evaluate(self, owner, requester, scope):
        if scope not in (1, 2):
            return False, Reason.UNSUPPORTED_SCOPE
        if owner.lower() not in self.users or requester.lower() not in self.users:
            return False, Reason.NOT_REGISTERED
        if self.users[owner.lower()]["vaccination_hash"] == ZERO_HASH:
            return False, Reason.MISSING_EVIDENCE
        consent = self.consents.get((owner.lower(), requester.lower(), int(scope)))
        if consent is None:
            return False, Reason.NO_CONSENT
        if consent["revoked"]:
            return False, Reason.REVOKED
        if self.now >= consent["expires_at"]:
            return False, Reason.EXPIRED
        return True, Reason.ALLOWED

    def _start(self, function, **arguments):
        self.calls.append((function, arguments))
        hook = self.before.pop(function, None)
        if hook:
            hook()
        failure = self.failures.pop(function, None)
        if failure:
            raise failure

    def _check_hash(self, value):
        if not isinstance(value, bytes) or len(value) != 32:
            raise TypeError("hashes are raw 32-byte values")

    def _receipt(self):
        self.transactions += 1
        return {"transaction_hash": f"0x{self.transactions:064x}", "status": 1, "gas_used": 0, "block_number": self.transactions, "logs": []}


def install(test, fake):
    """Swap the fake into app.chain for one test, so every "chain.x(...)" caller sees it."""
    for name in FAKED:
        patcher = mock.patch.object(chain, name, getattr(fake, name))
        patcher.start()
        test.addCleanup(patcher.stop)


def prepare_demo(fake, settings):
    """Runtime files, the three registrations and the clinic attestation, as in demo step 1."""
    records.setup_runtime(settings)
    for label in records.REGISTERING_LABELS:
        identity_hash = records.prepare_identity(*records.identity_paths(settings, label))
        fake.register_user("IdentityRegistry", account=fake.addresses[label], identity_hash=identity_hash)
    snapshot = records.load_snapshot(
        records.settings_path(settings, "vaccination_file"),
        records.settings_path(settings, "vaccination_salt_file"),
    )
    fake.register_vaccination(
        "IdentityRegistry", clinic=fake.addresses["clinic"], guardian=fake.addresses["guardian"],
        record_hash=snapshot["commitment"],
    )
    fake.calls.clear()
    return snapshot["commitment"]
