"""Durable notification handoff, independent of a poll interval or animation length."""
from copy import deepcopy
import json
import os
from pathlib import Path
import threading
import uuid


class AssistantDeliveryQueue:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self):
        if not self.path.exists():
            return {'schema_version': 'AssistantDelivery-v1', 'pending': [], 'acknowledged': []}
        value = json.loads(self.path.read_text(encoding='utf8'))
        if value.get('schema_version') != 'AssistantDelivery-v1':
            raise ValueError('ASSISTANT_DELIVERY_STATE_INVALID')
        return value

    def _save(self, value):
        path = self.path.with_name('.' + self.path.name + '.' + uuid.uuid4().hex + '.tmp')
        with path.open('x', encoding='utf8') as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(path, self.path)

    def seed(self, message_ids):
        with self.lock:
            if not self.path.exists():
                value = self._load()
                value['acknowledged'] = list(dict.fromkeys(message_ids))
                self._save(value)

    def enqueue(self, delivery_id, presentation):
        with self.lock:
            value = self._load()
            if delivery_id in value['acknowledged'] or any(x['delivery_id'] == delivery_id for x in value['pending']):
                return False
            value['pending'].append(dict(presentation, delivery_id=delivery_id))
            self._save(value)
            return True

    def peek(self):
        with self.lock:
            pending = self._load()['pending']
            return deepcopy(pending[0]) if pending else None

    def ack(self, delivery_id):
        with self.lock:
            value = self._load()
            found = any(x['delivery_id'] == delivery_id for x in value['pending'])
            if found:
                value['pending'] = [x for x in value['pending'] if x['delivery_id'] != delivery_id]
                value['acknowledged'].append(delivery_id)
                self._save(value)
            return {'status': 'ACKNOWLEDGED' if found else 'ALREADY_ACKNOWLEDGED', 'delivery_id': delivery_id}
