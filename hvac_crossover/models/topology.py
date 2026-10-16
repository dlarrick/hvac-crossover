"""System topology graph container and validation."""

from dataclasses import dataclass, field

from .delivery import HydronicEmitter
from .producers import Producer
from .spaces import Space


@dataclass
class SystemTopology:
    """
    Asset and connectivity graph mapping Producers to Delivery Units and Spaces.
    Validates physical constraints and unique ID references.
    """

    producers: list[Producer] = field(default_factory=list)
    spaces: list[Space] = field(default_factory=list)

    def __post_init__(self):
        self._validate()

    def _validate(self):
        # 1. Validate Producer IDs
        prod_ids = set()
        for p in self.producers:
            if p.id in prod_ids:
                raise ValueError(f"Duplicate producer ID found: '{p.id}'")
            prod_ids.add(p.id)

        # 2. Validate Space and Delivery Unit IDs
        space_ids = set()
        delivery_ids = set()
        for s in self.spaces:
            if s.id in space_ids:
                raise ValueError(f"Duplicate space ID found: '{s.id}'")
            space_ids.add(s.id)

            for d in s.emitters:
                if d.id in delivery_ids:
                    raise ValueError(f"Duplicate delivery unit ID found: '{d.id}'")
                delivery_ids.add(d.id)

                if d.producer_id not in prod_ids:
                    raise ValueError(
                        f"Delivery unit '{d.id}' in space '{s.id}' references unknown producer '{d.producer_id}'"
                    )

    def get_producer(self, producer_id: str) -> Producer:
        for p in self.producers:
            if p.id == producer_id:
                return p
        raise KeyError(f"Producer '{producer_id}' not found in topology")

    def get_space(self, space_id: str) -> Space:
        for s in self.spaces:
            if s.id == space_id:
                return s
        raise KeyError(f"Space '{space_id}' not found in topology")

    def get_shared_hydronic_spaces(self, boiler_id: str, zone_id: str) -> list[Space]:
        """Finds all spaces sharing the same hydronic zone loop valve."""
        shared = []
        for s in self.spaces:
            for d in s.emitters:
                if isinstance(d, HydronicEmitter) and d.producer_id == boiler_id and d.zone_id == zone_id:
                    shared.append(s)
                    break
        return shared

    def get_spaces_for_producer(self, producer_id: str) -> list[Space]:
        """Finds all spaces served by any delivery unit connected to this producer."""
        matched = []
        for s in self.spaces:
            for d in s.emitters:
                if d.producer_id == producer_id:
                    matched.append(s)
                    break
        return matched
