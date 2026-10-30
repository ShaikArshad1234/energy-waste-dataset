"""Reproducible random streams.

Only Mersenne-Twister `random()` (whose output for a given seed is stable across Python versions) is used;
every helper is built on it. Each component gets its own stream derived from (seed, label), so changing one
component never shifts the numbers of another.
"""
import hashlib
import math
import random


class Rng:
    def __init__(self, seed, label):
        digest = hashlib.sha256(f"{seed}:{label}".encode()).digest()
        self._r = random.Random(int.from_bytes(digest[:8], "big"))

    def random(self):
        return self._r.random()

    def uniform(self, a, b):
        return a + (b - a) * self._r.random()

    def randint(self, a, b):
        """Inclusive."""
        return a + int(self._r.random() * (b - a + 1))

    def chance(self, p):
        return self._r.random() < p

    def choice(self, seq):
        return seq[int(self._r.random() * len(seq))]

    def gauss(self, mu=0.0, sigma=1.0):
        u1 = max(self._r.random(), 1e-12)
        u2 = self._r.random()
        return mu + sigma * math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)

    def shuffle(self, items):
        for i in range(len(items) - 1, 0, -1):
            j = int(self._r.random() * (i + 1))
            items[i], items[j] = items[j], items[i]

    def sample(self, seq, k):
        pool = list(seq)
        self.shuffle(pool)
        return pool[:k]

    def weighted(self, items, weights):
        total = sum(weights)
        x = self._r.random() * total
        acc = 0.0
        for item, w in zip(items, weights):
            acc += w
            if x < acc:
                return item
        return items[-1]
