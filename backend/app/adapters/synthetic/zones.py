"""Bangalore zones used by the synthetic generator. Approximate centroids."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ZoneRole = Literal["mixed", "residential", "tech_park", "airport", "transit"]


@dataclass(frozen=True, slots=True)
class Zone:
    name: str
    lat: float
    lng: float
    role: ZoneRole


ZONES: tuple[Zone, ...] = (
    Zone("MG Road / CBD", 12.9750, 77.6050, "mixed"),
    Zone("Koramangala", 12.9352, 77.6245, "residential"),
    Zone("Indiranagar", 12.9784, 77.6408, "residential"),
    Zone("HSR Layout", 12.9116, 77.6389, "residential"),
    Zone("Jayanagar", 12.9308, 77.5838, "residential"),
    Zone("JP Nagar", 12.9063, 77.5857, "residential"),
    Zone("Banashankari", 12.9255, 77.5468, "residential"),
    Zone("Rajajinagar", 12.9915, 77.5520, "residential"),
    Zone("Malleshwaram", 13.0035, 77.5700, "residential"),
    Zone("Yeshwanthpur", 13.0280, 77.5400, "residential"),
    Zone("Hebbal", 13.0358, 77.5970, "residential"),
    Zone("Yelahanka", 13.1007, 77.5963, "residential"),
    Zone("KR Puram", 13.0070, 77.6950, "residential"),
    Zone("Kengeri", 12.9177, 77.4826, "residential"),
    Zone("Bannerghatta Road", 12.8770, 77.6020, "residential"),
    Zone("Sarjapur Road", 12.9050, 77.7000, "residential"),
    Zone("Marathahalli", 12.9591, 77.6974, "tech_park"),
    Zone("Bellandur / ORR", 12.9260, 77.6762, "tech_park"),
    Zone("Whitefield ITPL", 12.9850, 77.7340, "tech_park"),
    Zone("Electronic City", 12.8452, 77.6602, "tech_park"),
    Zone("Manyata Tech Park", 13.0450, 77.6190, "tech_park"),
    Zone("KIAL Airport", 13.1986, 77.7066, "airport"),
    Zone("Majestic / KSR station", 12.9767, 77.5713, "transit"),
)

RESIDENTIAL = tuple(z for z in ZONES if z.role == "residential")
TECH_PARKS = tuple(z for z in ZONES if z.role == "tech_park")
AIRPORT = next(z for z in ZONES if z.role == "airport")
CITY_ZONES = tuple(z for z in ZONES if z.role != "airport")
JITTER_SIGMA_M = 800.0
JITTER_CLIP_M = 2000.0
