import pytest

from infrahub_solution_ai_dc.roce_cabling import Endpoint, RoceCablingPlan


def test_paired_faces_and_nondefault_port_offsets() -> None:
    plan = RoceCablingPlan(
        leaf_count=3,
        spine_count=2,
        leaf_port_start=65,
        leaf_port_end=65,
        spine_port_start=7,
        spine_port_end=8,
    )
    assert list(plan.links()) == [
        (Endpoint("leaf", 1, 65, "A"), Endpoint("spine", 1, 7, "A")),
        (Endpoint("leaf", 1, 65, "B"), Endpoint("spine", 2, 7, "A")),
        (Endpoint("leaf", 2, 65, "A"), Endpoint("spine", 1, 7, "B")),
        (Endpoint("leaf", 2, 65, "B"), Endpoint("spine", 2, 7, "B")),
        (Endpoint("leaf", 3, 65, "A"), Endpoint("spine", 1, 8, "A")),
        (Endpoint("leaf", 3, 65, "B"), Endpoint("spine", 2, 8, "A")),
    ]


def test_full_design_uses_each_endpoint_once() -> None:
    plan = RoceCablingPlan(
        leaf_count=256,
        spine_count=128,
        leaf_port_start=65,
        leaf_port_end=128,
        spine_port_start=1,
        spine_port_end=128,
    )
    links = list(plan.links())
    assert len(links) == plan.link_count == 32_768
    assert len({leaf for leaf, _ in links}) == len({spine for _, spine in links}) == plan.link_count
    assert len({(leaf.device, spine.device) for leaf, spine in links}) == plan.link_count
    assert links[-1] == (Endpoint("leaf", 256, 128, "B"), Endpoint("spine", 128, 128, "B"))


@pytest.mark.parametrize(
    ("leaf_count", "spine_count", "leaf_start", "message"),
    [(0, 2, 65, "must be positive"), (3, 4, 65, "uplink endpoints"), (5, 2, 65, "supports 4"), (3, 2, 66, "ascending")],
)
def test_invalid_design_is_rejected(leaf_count: int, spine_count: int, leaf_start: int, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        RoceCablingPlan(leaf_count, spine_count, leaf_start, 65, 1, 2)
