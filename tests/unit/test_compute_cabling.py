import csv
from io import StringIO
from unittest.mock import MagicMock

import pytest
from infrahub_sdk.node import InfrahubNode

from generators.generate_compute_cabling import CombinedRoceCablingPlanCSV, ComputeCablingPlanCSV
from infrahub_solution_ai_dc.roce_cabling import ComputeCablingPlan, Endpoint


def test_full_cabinet_plan_uses_every_port_once() -> None:
    plan = ComputeCablingPlan(512, 32, 4, 256, 1, 64)
    links = list(plan.links())
    assert plan.required_leaf_count == 256
    assert plan.physical_pair_count == 16_384
    assert len(links) == plan.link_count == 32_768
    assert len({leaf for leaf, _ in links}) == len({cabinet for _, cabinet in links}) == len(links)
    assert all(leaf.face == cabinet.face for leaf, cabinet in links)
    for cabinet, cabinet_port, leaf, leaf_port in [
        (1, 1, 1, 1),
        (1, 2, 2, 1),
        (2, 1, 1, 17),
        (32, 1, 15, 49),
        (512, 31, 255, 64),
        (512, 32, 256, 64),
    ]:
        index = ((cabinet - 1) * 32 + cabinet_port - 1) * 2
        assert links[index] == (
            Endpoint("leaf", leaf, leaf_port, "A"),
            Endpoint("compute_cabinet", cabinet, cabinet_port, "A"),
        )
        assert links[index + 1] == (
            Endpoint("leaf", leaf, leaf_port, "B"),
            Endpoint("compute_cabinet", cabinet, cabinet_port, "B"),
        )


@pytest.mark.parametrize(
    ("ports", "leaf_count", "leaf_end", "message"),
    [(31, 256, 64, "even"), (32, 254, 64, "need 256 Leafs"), (32, 256, 63, "at least 64")],
)
def test_compute_preflight_rejects_bad_capacity(ports: int, leaf_count: int, leaf_end: int, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        ComputeCablingPlan(512, ports, 4, leaf_count, 1, leaf_end)


@pytest.fixture
def compute_design() -> dict:
    return {
        "id": "design",
        "name": {"value": "compute"},
        "cabinet_count": {"value": 1},
        "cabinet_port_count": {"value": 2},
        "cabinets_per_leaf_pair": {"value": 1},
        "leaf_port_start": {"value": 1},
        "leaf_port_end": {"value": 1},
        "medium": {"value": "smf"},
        "cabinet_device_type": {"node": {"id": "cabinet-type", "name": {"value": "GPU cabinet"}}},
        "roce_fabric": {
            "node": {
                "id": "fabric",
                "name": {"value": "fabric"},
                "leaf_count": {"value": 2},
                "rule": {"value": "paired_ab"},
                "medium": {"value": "smf"},
                "spine_count": {"value": 2},
                "spine_port_start": {"value": 1},
                "spine_port_end": {"value": 1},
                "spine_device_type": {"node": {"id": "spine-type", "name": {"value": "Spine model"}}},
                "leaf_port_start": {"value": 65},
                "leaf_port_end": {"value": 65},
                "leaf_device_type": {"node": {"id": "leaf-type", "name": {"value": "Leaf model"}}},
            }
        },
    }


def test_compute_csv_uses_source_fabric_names(compute_design: dict) -> None:
    transform = ComputeCablingPlanCSV(client=MagicMock(), infrahub_node=InfrahubNode)
    output = transform.transform({"NetworkComputeCablingDesign": {"edges": [{"node": compute_design}]}})
    assert output.startswith("\ufeff")
    rows = list(csv.reader(StringIO(output.removeprefix("\ufeff"))))
    assert len(rows) == 5
    assert rows[1] == [
        "fabric-leaf-001",
        "Leaf model",
        "1",
        "A 面 / 400G",
        "",
        "compute-cabinet-001",
        "GPU cabinet",
        "1",
        "A 面 / 400G",
    ]
    assert rows[4] == [
        "fabric-leaf-002",
        "Leaf model",
        "1",
        "B 面 / 400G",
        "",
        "compute-cabinet-001",
        "GPU cabinet",
        "2",
        "B 面 / 400G",
    ]


def test_combined_csv_sorts_both_link_types_by_leaf_and_labels_each_fiber(compute_design: dict) -> None:
    transform = CombinedRoceCablingPlanCSV(client=MagicMock(), infrahub_node=InfrahubNode)
    output = transform.transform({"NetworkComputeCablingDesign": {"edges": [{"node": compute_design}]}})
    rows = list(csv.DictReader(StringIO(output.removeprefix("\ufeff"))))
    assert [(row["Leaf 编号"], row["Leaf 物理端口"], row["目标设备类型"]) for row in rows] == [
        (f"fabric-leaf-{leaf:03d}", port, target)
        for leaf in (1, 2)
        for port, target in (("1", "计算柜"), ("1", "计算柜"), ("65", "Spine"), ("65", "Spine"))
    ]
    assert rows[1]["光纤标签"] == "fabric-leaf-001:1/B to compute-cabinet-001:1/B"
    assert rows[3]["光纤标签"] == "fabric-leaf-001:65/B to fabric-spine-002:1/A"
    assert rows[7]["光纤标签"] == "fabric-leaf-002:65/B to fabric-spine-002:1/B"
    assert {row["目标设备型号"] for row in rows} == {"GPU cabinet", "Spine model"}
