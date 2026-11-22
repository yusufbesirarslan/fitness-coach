"""Exact, attempt-bound authority and durable record for R6 repair handoff."""

import hashlib
import json
import re
from dataclasses import asdict, dataclass


MODES = ('repair-stage', 'repair-apply')
SHA = r'[0-9a-f]{40}'
DIGEST = r'[0-9a-f]{64}'
IMAGE = 'sha256:' + DIGEST
RUN = r'[1-9][0-9]{0,19}'
ATTEMPT = r'[1-9][0-9]{0,8}'


def fingerprint(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def snapshot_for(value, sha):
    return isinstance(value, str) and re.fullmatch(rf'predeploy-{sha}-[0-9a-f]{{32}}', value) is not None


@dataclass(frozen=True)
class Stage:
    revision: str
    run: str
    attempt: str
    tree: str
    failed: str
    failed_tree: str
    original_receipt: str
    failed_run: str
    failed_attempt: str
    failed_job: str
    failed_snapshot: str
    incident_evidence: str


@dataclass(frozen=True)
class Apply:
    revision: str
    run: str
    attempt: str
    stage_receipt: str
    image: str
    failed_snapshot: str


def parse_authorization(value):
    if not isinstance(value, str) or len(value) > 800:
        raise ValueError('invalid repair authorization')
    stage = re.fullmatch(
        rf'repair-stage:({SHA}):({RUN}):({ATTEMPT}):({SHA}):({SHA}):({SHA}):({DIGEST}):'
        rf'({RUN}):({ATTEMPT}):({RUN}):(predeploy-{SHA}-[0-9a-f]{{32}}):({DIGEST})', value)
    if stage:
        authority = Stage(*stage.groups())
        if (authority.failed != authority.revision and authority.failed_run != authority.run
                and snapshot_for(authority.failed_snapshot, authority.failed)):
            return 'repair-stage', authority
    apply = re.fullmatch(rf'repair-apply:({SHA}):({RUN}):({ATTEMPT}):({DIGEST}):({IMAGE}):'
                         rf'(predeploy-{SHA}-[0-9a-f]{{32}})', value)
    if apply:
        return 'repair-apply', Apply(*apply.groups())
    raise ValueError('invalid repair authorization')


@dataclass(frozen=True)
class RepairReceipt:
    original: dict
    stage_authorization: str
    stage_snapshot: str
    image: str
    heads: tuple[str, ...]
    last_apply: int = 0
    last_snapshot: str = ''
    committed: bool = False

    @property
    def stage(self):
        return parse_authorization(self.stage_authorization)[1]

    @property
    def staged_identity(self):
        return fingerprint({'original': self.original, 'stage': self.stage_authorization,
                            'snapshot': self.stage_snapshot, 'image': self.image, 'heads': self.heads})

    def claim(self, authority, snapshot):
        stage = self.stage
        if (self.committed or authority.revision != stage.revision or authority.run != stage.run
                or int(authority.attempt) <= max(int(stage.attempt), self.last_apply)
                or authority.stage_receipt != self.staged_identity or authority.image != self.image
                or authority.failed_snapshot != stage.failed_snapshot
                or not snapshot_for(snapshot, stage.revision)
                or snapshot in (self.stage_snapshot, self.last_snapshot)):
            raise ValueError('repair apply does not match the pending stage and a fresh attempt')
        # The protected controller produces a new unique snapshot for each delivery.
        return int(authority.attempt)

    def payload(self):
        return {'repair_handoff_v1': asdict(self)}

    @classmethod
    def parse(cls, payload, parse_original):
        if not isinstance(payload, dict) or set(payload) != {'repair_handoff_v1'}:
            raise ValueError('invalid repair receipt envelope')
        receipt = cls(**payload['repair_handoff_v1'])
        mode, stage = parse_authorization(receipt.stage_authorization)
        original = parse_original(receipt.original)
        if (mode != 'repair-stage' or original.candidate != stage.failed
                or stage.revision == original.previous or not original.prepared or original.committed
                or fingerprint(receipt.original) != stage.original_receipt
                or not snapshot_for(receipt.stage_snapshot, stage.revision)
                or not re.fullmatch(IMAGE, receipt.image) or receipt.image == original.image
                or not isinstance(receipt.heads, list) or not receipt.heads
                or any(not isinstance(head, str) or not re.fullmatch(r'[a-z0-9_]{1,64}', head)
                       for head in receipt.heads)
                or sorted(set(receipt.heads)) != receipt.heads
                or type(receipt.last_apply) is not int or not 0 <= receipt.last_apply < 10**9
                or (receipt.last_apply != 0 and receipt.last_apply <= int(stage.attempt))
                or (receipt.last_apply != 0 and not snapshot_for(receipt.last_snapshot, stage.revision))
                or (receipt.last_apply == 0 and receipt.last_snapshot != '')
                or receipt.last_snapshot == receipt.stage_snapshot
                or type(receipt.committed) is not bool or (receipt.committed and not receipt.last_apply)):
            raise ValueError('invalid repair receipt')
        return cls(**{**asdict(receipt), 'heads': tuple(receipt.heads)})
