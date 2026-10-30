"""Generation parameters. Starter and full datasets use the same generator; only the scale differs."""
from dataclasses import asdict, dataclass

DEFAULT_SEED = 20261022


@dataclass(frozen=True)
class Params:
    seed: int = DEFAULT_SEED
    plants: int = 2
    meters: int = 20          # total, spread over plants: 1 MAIN + (n-1) SUB per plant
    days: int = 90
    batches: int = 2000
    start_date: str = "2026-07-01"   # first plant-local day
    interval_minutes: int = 15

    def validate(self):
        if self.interval_minutes != 15:
            raise ValueError("only 15-minute sampling is supported")
        if not (1 <= self.plants <= 4):
            raise ValueError("plants must be 1..4")
        if self.meters < self.plants * 3:
            raise ValueError("need at least 3 meters per plant (1 MAIN + 2 SUB)")
        if self.days < 7:
            raise ValueError("days must be >= 7")
        if self.batches < self.plants * 10:
            raise ValueError("need at least 10 batches per plant")

    def as_dict(self):
        return asdict(self)


STARTER = Params(plants=2, meters=8, days=14, batches=80)
FULL = Params()

# name, country, tz name, utc offset min, currency, (off, shoulder, peak) rate/kWh, demand charge/kW/month, base temp C
PLANT_DEFS = [
    ("Pune Assembly Plant", "IN", "Asia/Kolkata", 330, "INR", (5.5, 8.0, 11.5), 350.0, 29.0),
    ("Dubai Fabrication Plant", "AE", "Asia/Dubai", 240, "AED", (0.28, 0.38, 0.52), 12.0, 38.0),
    ("Chennai Components Plant", "IN", "Asia/Kolkata", 330, "INR", (5.2, 7.6, 11.0), 330.0, 31.0),
    ("Abu Dhabi Casting Plant", "AE", "Asia/Dubai", 240, "AED", (0.27, 0.37, 0.50), 11.5, 39.0),
]
