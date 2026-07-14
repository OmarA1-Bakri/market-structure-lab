from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from src.profiles import VolumeProfile


class NodeKind(str, Enum):
    HVN = "hvn"
    LVN = "lvn"


@dataclass(frozen=True)
class ProfileNode:
    kind: NodeKind
    price: float
    volume: float
    prominence: float


def detect_profile_nodes(profile: VolumeProfile, *, min_prominence: float = 0.0) -> list[ProfileNode]:
    """Detect deterministic local high-volume and low-volume nodes.

    Endpoints are ignored because local prominence needs both left and right
    neighbors. Flat ties are ignored until a wider plateau rule is introduced.
    """
    if min_prominence < 0:
        raise ValueError("min_prominence must be greater than or equal to 0")

    prices = list(profile.price_volumes)
    nodes: list[ProfileNode] = []

    for index in range(1, len(prices) - 1):
        price = prices[index]
        left_volume = profile.price_volumes[prices[index - 1]]
        volume = profile.price_volumes[price]
        right_volume = profile.price_volumes[prices[index + 1]]

        if volume > left_volume and volume > right_volume:
            prominence = volume - max(left_volume, right_volume)
            if prominence >= min_prominence:
                nodes.append(
                    ProfileNode(
                        kind=NodeKind.HVN,
                        price=price,
                        volume=volume,
                        prominence=prominence,
                    )
                )
        elif volume < left_volume and volume < right_volume:
            prominence = min(left_volume, right_volume) - volume
            if prominence >= min_prominence:
                nodes.append(
                    ProfileNode(
                        kind=NodeKind.LVN,
                        price=price,
                        volume=volume,
                        prominence=prominence,
                    )
                )

    return nodes
