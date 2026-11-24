from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from infrahub_sdk.node import InfrahubNode

from generators.generate_roce_cabling import RoceCablingGenerator


async def test_retry_registers_only_devices_without_touching_existing_objects() -> None:
    client = MagicMock()
    client.group_context = SimpleNamespace(
        identifier="generate-roce-cabling",
        delete_unused_nodes=True,
        add_related_nodes=AsyncMock(),
    )
    generator = RoceCablingGenerator(
        client=client, branch="test", query="generate_roce_cabling", infrahub_node=InfrahubNode
    )
    generator.client = client
    design = {
        "id": "design-id",
        "name": {"value": "test-design"},
        "rule": {"value": "paired_ab"},
        "medium": {"value": "smf"},
        "leaf_count": {"value": 1},
        "spine_count": {"value": 2},
        "leaf_port_start": {"value": 65},
        "leaf_port_end": {"value": 65},
        "spine_port_start": {"value": 1},
        "spine_port_end": {"value": 1},
        "leaf_device_type": {"node": {"id": "leaf-type"}},
        "spine_device_type": {"node": {"id": "spine-type"}},
    }
    data = {"NetworkRoceCablingDesign": {"edges": [{"node": design}]}}
    generator.read_design(data)
    inventory = generator.inventory()
    devices = {name: SimpleNamespace(id=name, save=AsyncMock()) for name in inventory}
    interfaces = {endpoint: SimpleNamespace(save=AsyncMock()) for ports in inventory.values() for endpoint in ports}
    links = {
        generator.link_name(leaf, spine): SimpleNamespace(save=AsyncMock()) for leaf, spine in generator.plan.links()
    }
    generator.preflight = AsyncMock(return_value=(devices, interfaces, links))  # type: ignore[method-assign]

    await generator.generate(data)

    client.create.assert_not_called()
    for node in [*devices.values(), *interfaces.values(), *links.values()]:
        node.save.assert_not_awaited()
    assert client.group_context.identifier == "generate-roce-cabling-devices"
    assert client.group_context.delete_unused_nodes is False
    client.group_context.add_related_nodes.assert_awaited_once_with(ids=list(devices))


async def test_inventory_reads_follow_up_page_only_for_devices_with_more_ports() -> None:
    def device_page(device_id: str, count: int, start: int, stop: int) -> dict:
        return {
            "node": {
                "id": device_id,
                "interfaces": {
                    "count": count,
                    "edges": [{"node": {"id": f"{device_id}-{i}"}} for i in range(start, stop)],
                },
            }
        }

    client = MagicMock()
    client.execute_graphql = AsyncMock(
        side_effect=[
            {"NetworkDevice": {"edges": [device_page("leaf", 2, 0, 2), device_page("spine", 513, 0, 512)]}},
            {"NetworkDevice": {"edges": [device_page("spine", 513, 512, 513)]}},
        ]
    )
    generator = RoceCablingGenerator(
        query="generate_roce_cabling", client=client, infrahub_node=InfrahubNode, branch="test"
    )
    generator.client = client

    ports = await generator.read_ports(("leaf", "spine"))

    assert len(ports) == 515
    assert ports[-1] == ("spine", {"id": "spine-512"})
    assert client.execute_graphql.await_args.kwargs["variables"] == {
        "ids": ["spine"],
        "deviceLimit": 1,
        "offset": 512,
        "portLimit": 512,
    }
