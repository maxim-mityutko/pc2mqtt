"""Specialized state handling, independent of MQTT discovery and platform APIs."""

from datetime import datetime, timezone
from queue import Empty, Full, Queue


class PlaybackState:
    """Two-second playback debounce with a fixed one-minute heartbeat."""

    def __init__(self):
        self.active_since = None
        self.last = None
        self.next_update = 0

    def refresh(self):
        self.next_update = 0

    def sample(self, playing, now):
        if playing:
            if self.active_since is None:
                self.active_since = now
        else:
            self.active_since = None
        active = self.active_since is not None and now - self.active_since >= 2
        heartbeat_due = now >= self.next_update
        if not heartbeat_due and not (active and self.last != 'ON'):
            return None
        self.last = 'ON' if active else 'OFF'
        if heartbeat_due:
            self.next_update = now + 60
        return self.last

    def failed(self, now, first_failure):
        self.active_since = None
        if first_failure or now >= self.next_update:
            self.next_update = now + 60
            return True
        return False


class ProcessCommands:
    """Serialize asynchronous actions and suppress repeats after a terminal action."""

    def __init__(self, start, logger):
        self.start = start
        self.logger = logger
        self.pending = Queue(maxsize=1)
        self.process = None
        self.key = None
        self.stopping = False

    def enqueue(self, key):
        if self.process is not None or self.stopping:
            return
        try:
            self.pending.put_nowait(key)
        except Full:
            pass

    def clear(self):
        while True:
            try:
                self.pending.get_nowait()
            except Empty:
                return

    def poll(self, supported, entities):
        if self.process is None and not self.stopping:
            try:
                key = self.pending.get_nowait()
            except Empty:
                pass
            else:
                if key in supported:
                    try:
                        self.process = self.start(key)
                        self.key = key
                    except (OSError, ValueError, NotImplementedError) as exc:
                        self.logger.error('Unable to %s: %s', key, exc)
        if self.process is not None:
            result = self.process.poll()
            if result is None:
                return
            if result != 0:
                self.logger.error('Power command %s failed with exit code %s', self.key, result)
            elif entities[self.key].stop_after:
                self.stopping = True
            self.process = None


def broker_ip(client):
    connection = client.socket()
    return connection.getsockname()[0] if connection is not None else None


def last_seen(client):
    return datetime.now(timezone.utc).isoformat(timespec='seconds')
