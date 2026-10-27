"""Decides when the screen shows the end-of-match screen.

Signatures are tiny grayscale thumbnails. A frame counts as the end screen
when it is close to any learned template for `confirm` checks in a row, and
not within `cooldown` seconds of the previous end.
"""


def distance(a, b):
    """Mean absolute pixel difference, 0 (identical) to 255."""
    return sum(abs(x - y) for x, y in zip(a, b)) / max(len(a), 1)


class EndScreenMatcher:
    def __init__(self, templates, threshold=18.0, confirm=2, cooldown=90.0):
        self.templates = list(templates)
        self.threshold = threshold
        self.confirm = confirm
        self.cooldown = cooldown
        self.hits = 0
        self.last_end = float("-inf")

    def learn(self, sig):
        self.templates.append(sig)

    def best(self, sig):
        return min((distance(sig, t) for t in self.templates), default=None)

    def feed(self, sig, now):
        """Returns True once per end screen."""
        d = self.best(sig)
        if d is None or d > self.threshold:
            self.hits = 0
            return False
        self.hits += 1
        if self.hits >= self.confirm and now - self.last_end >= self.cooldown:
            self.last_end = now
            return True
        return False

    def mark_end(self, now):
        """Record a manual end so auto-detect doesn't double count it."""
        self.last_end = now

    def recently_ended(self, now):
        return now - self.last_end < self.cooldown
