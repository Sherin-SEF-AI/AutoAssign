"""Profiles for the synthetic world: traffic speeds, fleet mix, names."""

from __future__ import annotations

from dataclasses import dataclass

# (start minute of day IST, km/h) used to synthesise historical actuals.
SPEED_PROFILE_KMH: tuple[tuple[int, float], ...] = (
    (0, 38.0),
    (360, 28.0),
    (420, 15.0),
    (600, 22.0),
    (1020, 13.0),
    (1260, 30.0),
)
CIRCUITY = 1.35
DURATION_NOISE_SIGMA = 0.25
HANDLING_S = 240
RAIN_DAY_SHARE = 0.15
RAIN_MULTIPLIER = 1.30


def speed_at_minute(minute_of_day: int) -> float:
    speed = SPEED_PROFILE_KMH[0][1]
    for start, kmh in SPEED_PROFILE_KMH:
        if minute_of_day >= start:
            speed = kmh
    return speed


@dataclass(frozen=True, slots=True)
class FleetModel:
    model: str
    variant: str
    vehicle_class: str
    battery_kwh: float
    count: int
    seats: int
    luggage_class: str
    reg_prefix: str


FLEET: tuple[FleetModel, ...] = (
    FleetModel("tigor_ev", "Tata Tigor EV", "sedan", 26.0, 12, 4, "medium", "KA01"),
    FleetModel("windsor", "MG Windsor", "suv", 38.0, 8, 4, "large", "KA03"),
    FleetModel("zs_ev", "MG ZS EV", "suv", 50.3, 5, 4, "large", "KA05"),
    FleetModel("ec3", "Citroen eC3", "sedan", 29.2, 5, 4, "small", "KA51"),
)

SHIFT_PATTERNS_IST: tuple[tuple[int, int], ...] = ((5, 15), (9, 19), (13, 23))

FIRST_NAMES: tuple[str, ...] = (
    "Arjun",
    "Ravi",
    "Suresh",
    "Manjunath",
    "Kiran",
    "Prakash",
    "Naveen",
    "Mahesh",
    "Ramesh",
    "Vinay",
    "Santosh",
    "Harish",
    "Girish",
    "Anil",
    "Raghu",
    "Deepak",
    "Imran",
    "Salim",
    "Joseph",
    "Thomas",
    "Lokesh",
    "Shivu",
    "Basava",
    "Chetan",
    "Darshan",
    "Gopal",
    "Nagaraj",
    "Pavan",
    "Rakesh",
    "Yogesh",
)
LAST_NAMES: tuple[str, ...] = (
    "Gowda",
    "Reddy",
    "Kumar",
    "Shetty",
    "Rao",
    "Naik",
    "Hegde",
    "Patil",
    "Pai",
    "Khan",
    "Raju",
    "Murthy",
    "Swamy",
    "Iyer",
    "Prasad",
)

CORPORATE_ACCOUNTS: tuple[str, ...] = ("A1", "A2", "A3", "A4", "A5")
ETS_CLIENTS: tuple[str, ...] = ("ETS-ORBIT", "ETS-NIMBUS", "ETS-KESTREL", "ETS-LOTUS")

HUBS: tuple[tuple[str, float, float], ...] = (
    ("Hub South (Bommanahalli)", 12.9000, 77.6200),
    ("Hub North (Hebbal)", 13.0400, 77.5900),
)
HUB_AC_KW = 7.2
HUB_DC_KW = 30.0
HUB_AC_POINTS = 6
HUB_DC_POINTS = 2
