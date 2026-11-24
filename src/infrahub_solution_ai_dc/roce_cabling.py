from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator


@dataclass(frozen=True)
class Endpoint:
    role: str
    device: int
    port: int
    face: str

    @property
    def interface(self) -> str:
        return f"{self.port}/{self.face}"

    def hostname(self, design: str) -> str:
        return f"{design}-{self.role}-{self.device:03d}"


@dataclass(frozen=True)
class RoceCablingPlan:
    leaf_count: int
    spine_count: int
    leaf_port_start: int
    leaf_port_end: int
    spine_port_start: int
    spine_port_end: int

    def __post_init__(self) -> None:
        if self.leaf_count < 1 or self.spine_count < 1:
            msg = "Leaf and Spine counts must be positive"
            raise ValueError(msg)
        if not (1 <= self.leaf_port_start <= self.leaf_port_end and 1 <= self.spine_port_start <= self.spine_port_end):
            msg = "Physical port ranges must be positive and ascending"
            raise ValueError(msg)
        leaf_capacity = 2 * (self.leaf_port_end - self.leaf_port_start + 1)
        spine_capacity = 2 * (self.spine_port_end - self.spine_port_start + 1)
        if self.spine_count != leaf_capacity:
            msg = f"Each Leaf exposes {leaf_capacity} uplink endpoints but the design has {self.spine_count} Spines"
            raise ValueError(msg)
        if self.leaf_count > spine_capacity:
            msg = f"Each Spine supports {spine_capacity} endpoints but the design has {self.leaf_count} Leafs"
            raise ValueError(msg)

    @property
    def link_count(self) -> int:
        return self.leaf_count * self.spine_count

    def links(self) -> Iterator[tuple[Endpoint, Endpoint]]:
        for leaf in range(1, self.leaf_count + 1):
            port_offset, face_index = divmod(leaf - 1, 2)
            for spine in range(1, self.spine_count + 1):
                leaf_offset, leaf_face_index = divmod(spine - 1, 2)
                yield (
                    Endpoint("leaf", leaf, self.leaf_port_start + leaf_offset, "AB"[leaf_face_index]),
                    Endpoint("spine", spine, self.spine_port_start + port_offset, "AB"[face_index]),
                )


@dataclass(frozen=True)
class ComputeCablingPlan:
    cabinet_count: int
    cabinet_port_count: int
    cabinets_per_leaf_pair: int
    leaf_count: int
    leaf_port_start: int
    leaf_port_end: int

    def __post_init__(self) -> None:
        if self.cabinet_count < 1 or self.cabinets_per_leaf_pair < 1:
            msg = "Cabinet count and cabinets per Leaf pair must be positive"
            raise ValueError(msg)
        if self.cabinet_port_count < 2 or self.cabinet_port_count % 2:
            msg = "Each cabinet must have a positive even number of physical ports"
            raise ValueError(msg)
        if not 1 <= self.leaf_port_start <= self.leaf_port_end:
            msg = "Leaf downlink port range must be positive and ascending"
            raise ValueError(msg)
        required_ports = self.cabinets_per_leaf_pair * self.ports_per_leaf_per_cabinet
        if self.leaf_port_end - self.leaf_port_start + 1 < required_ports:
            msg = f"Each Leaf needs at least {required_ports} downlink physical ports"
            raise ValueError(msg)
        if self.required_leaf_count > self.leaf_count:
            msg = f"Cabinets need {self.required_leaf_count} Leafs, but the RoCE Fabric has {self.leaf_count}"
            raise ValueError(msg)

    @property
    def ports_per_leaf_per_cabinet(self) -> int:
        return self.cabinet_port_count // 2

    @property
    def required_leaf_count(self) -> int:
        return 2 * ((self.cabinet_count - 1) // self.cabinets_per_leaf_pair + 1)

    @property
    def physical_pair_count(self) -> int:
        return self.cabinet_count * self.cabinet_port_count

    @property
    def link_count(self) -> int:
        return self.physical_pair_count * 2

    def links(self) -> Iterator[tuple[Endpoint, Endpoint]]:
        for cabinet in range(1, self.cabinet_count + 1):
            pair, slot = divmod(cabinet - 1, self.cabinets_per_leaf_pair)
            for port in range(1, self.cabinet_port_count + 1):
                port_offset, side = divmod(port - 1, 2)
                leaf = pair * 2 + side + 1
                leaf_port = self.leaf_port_start + slot * self.ports_per_leaf_per_cabinet + port_offset
                for face in "AB":
                    yield Endpoint("leaf", leaf, leaf_port, face), Endpoint("compute_cabinet", cabinet, port, face)
