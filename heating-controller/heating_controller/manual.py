"""Controller-owned manual demand; automatic calculations never send commands here."""
from uuid import uuid4

from .adapters.trvzb import number


class ManualControl:
    def __init__(self, rooms):
        self.accepted_token = None
        self.enabled = False
        self.openings = {room.id: 0.0 for room in rooms}
        self.token = str(uuid4())

    def clear(self):
        # Keep manual ownership across reconnects, but never replay an opening.
        self.accepted_token = None
        self.openings = dict.fromkeys(self.openings, 0.0)
        self.token = str(uuid4())

    def apply(self, document):
        if not isinstance(document, dict) or set(document) != {'token', 'enabled', 'openings'}:
            raise ValueError('Expected token, enabled and openings')
        if document['token'] != self.token:
            raise ValueError('Manual state changed; refresh before retrying')
        enabled = document['enabled']
        if type(enabled) is not bool:
            raise ValueError('enabled must be boolean')
        values = document['openings']
        if not isinstance(values, dict) or set(values) != set(self.openings):
            raise ValueError('Expected an opening for each configured room')
        values = {key: number(value, 0, 100, 'opening') for key, value in values.items()}
        if (not enabled or enabled != self.enabled) and any(values.values()):
            raise ValueError('Mode changes must request all valves closed')
        self.accepted_token = self.token
        changed = enabled != self.enabled
        self.enabled, self.openings = enabled, values
        self.token = str(uuid4())
        return changed
