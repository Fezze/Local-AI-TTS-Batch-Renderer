"""Content-addressed, immutable normalized documents used by batch workers."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from .atomic_io import write_json_atomic
from .scheduler_scan import _document_from_payload


def save_snapshot(output_dir, document):
    payload = {'metadata': asdict(document.metadata),
               'chapters': [asdict(c) for c in document.chapters],
               'navigation': [asdict(n) for n in document.navigation]}
    envelope = {'version': 1, 'source': str(document.path.resolve()), 'document': payload}
    digest = hashlib.sha256(json.dumps(envelope, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    path = Path(output_dir) / '.cache' / 'documents' / f'{digest}.json'
    if not path.exists():
        write_json_atomic(path, envelope)
    return path


class SnapshotReader:
    def __init__(self):
        self.path = None
        self.document = None

    def load(self, path, source):
        path = Path(path).resolve()
        if path != self.path:
            payload = json.loads(path.read_text(encoding='utf-8'))
            digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            if digest != path.stem or payload.get('version') != 1:
                raise ValueError('Invalid document snapshot identity')
            document = _document_from_payload(Path(payload['source']), payload['document'])
            self.path, self.document = path, document
        if self.document.path.resolve() != Path(source).resolve():
            raise ValueError('Document snapshot belongs to another source')
        return self.document
