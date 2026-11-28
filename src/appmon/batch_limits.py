"""Operational detail limits verified against complete indexed responses."""
import threading
import time

APPLE_DETAIL_MAX = 210
GOOGLE_RPC_MAX = 3000
GOOGLE_RESPONSE_MAX_BYTES = 64 * 1024 * 1024
GOOGLE_RESPONSE_SECONDS = 60


def window_key(market, methods):
    return market['country'],market.get('language','en'),tuple(sorted(set(methods)))


class BatchWindow:
    """Short-lived sizing hints shared by workers, scoped to the actual request."""
    def __init__(self):
        self.limits = {}
        self.lock = threading.Lock()

    def limit(self, key, configured):
        with self.lock:
            saved = self.limits.get(key)
            if saved and saved[1] > time.monotonic():
                return min(configured,saved[0])
            if saved:del self.limits[key]
            return configured

    def too_large(self, key, size):
        reduced = max(1,size//2)
        now = time.monotonic()
        with self.lock:
            saved = self.limits.get(key)
            if saved and saved[1] > now:
                reduced = min(reduced,saved[0])
            self.limits[key] = reduced,now+3600
