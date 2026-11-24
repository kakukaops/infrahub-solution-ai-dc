from __future__ import annotations

from itertools import islice
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Any, TypeVar

from infrahub_sdk.batch import InfrahubBatch
from infrahub_sdk.generator import InfrahubGenerator

from infrahub_solution_ai_dc.protocols import NetworkDevice, NetworkInterface, NetworkLink
from infrahub_solution_ai_dc.roce_cabling import ComputeCablingPlan, Endpoint, RoceCablingPlan

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator


T = TypeVar("T")
INVENTORY_QUERY = Path(__file__).with_name("roce_cabling_inventory.gql").read_text(encoding="utf-8")


def batched(items: Iterable[T], size: int) -> Iterator[tuple[T, ...]]:
    iterator = iter(items)
    while chunk := tuple(islice(iterator, size)):
        yield chunk


class RoceCablingValidation(InfrahubGenerator):
    owner_field = "roce_cabling_design"
    plan: RoceCablingPlan | ComputeCablingPlan

    def read_design(self, data: dict[str, Any]) -> None:
        (design,) = (edge["node"] for edge in data["NetworkRoceCablingDesign"]["edges"])
        self.design_id: str = design["id"]
        self.design_name: str = design["name"]["value"]
        self.medium: str = design["medium"]["value"]
        self.models: dict[str, str] = {role: design[f"{role}_device_type"]["node"]["id"] for role in ("leaf", "spine")}
        if design["rule"]["value"] != "paired_ab":
            msg = f"Unsupported RoCE cabling rule: {design['rule']['value']}"
            raise ValueError(msg)
        self.plan = RoceCablingPlan(
            leaf_count=design["leaf_count"]["value"],
            spine_count=design["spine_count"]["value"],
            leaf_port_start=design["leaf_port_start"]["value"],
            leaf_port_end=design["leaf_port_end"]["value"],
            spine_port_start=design["spine_port_start"]["value"],
            spine_port_end=design["spine_port_end"]["value"],
        )

    def device_ranges(self) -> tuple[tuple[str, int, int, int], ...]:
        assert isinstance(self.plan, RoceCablingPlan)
        return (
            ("leaf", self.plan.leaf_count, self.plan.leaf_port_start, self.plan.leaf_port_end),
            ("spine", self.plan.spine_count, self.plan.spine_port_start, self.plan.spine_port_end),
        )

    def hostname(self, endpoint: Endpoint) -> str:
        return endpoint.hostname(self.design_name)

    def interface_fields(self, endpoint: Endpoint) -> dict[str, str]:
        return {"role": "spine" if endpoint.role == "leaf" else "leaf"}

    def inventory(self) -> dict[str, tuple[Endpoint, ...]]:
        inventory: dict[str, tuple[Endpoint, ...]] = {}
        for role, count, start, end in self.device_ranges():
            for index in range(1, count + 1):
                endpoints = tuple(Endpoint(role, index, port, face) for port in range(start, end + 1) for face in "AB")
                inventory[self.hostname(endpoints[0])] = endpoints
        return inventory

    async def generate(self, data: dict[str, Any]) -> None:
        self.read_design(data)
        inventory = self.inventory()
        await self.preflight(inventory)
        self.logger.info(
            "%s: validated %s, %d endpoints and %d 400G links",
            self.design_name,
            self.topology_description(),
            sum(map(len, inventory.values())),
            self.plan.link_count,
        )

    def topology_description(self) -> str:
        assert isinstance(self.plan, RoceCablingPlan)
        return f"{self.plan.leaf_count} Leafs, {self.plan.spine_count} Spines"

    async def shared_devices(self) -> dict[str, NetworkDevice]:
        return {}

    def port_in_scope(self, role: str, planned: bool, owner: str | None) -> bool:
        return planned or role != "leaf" or owner is None

    def expected_port_owner(self) -> str | None:
        return None

    async def read_ports(self, device_ids: tuple[str, ...]) -> list[tuple[str, dict[str, Any]]]:
        ports: list[tuple[str, dict[str, Any]]] = []
        pending = list(device_ids)
        page_size = 512
        offset = 0
        while pending:
            response = await self.client.execute_graphql(
                INVENTORY_QUERY,
                variables={"ids": pending, "deviceLimit": len(pending), "offset": offset, "portLimit": page_size},
                branch_name=self.branch_name,
            )
            devices = {edge["node"]["id"]: edge["node"]["interfaces"] for edge in response["NetworkDevice"]["edges"]}
            if devices.keys() != set(pending):
                msg = "Device inventory changed during preflight. Run the generator again."
                raise ValueError(msg)
            for device_id, interfaces in devices.items():
                page = interfaces["edges"]
                if len(page) != min(page_size, max(0, interfaces["count"] - offset)):
                    msg = f"Could not read a complete interface page for device {device_id}"
                    raise ValueError(msg)
                ports.extend((device_id, edge["node"]) for edge in page)
            offset += page_size
            pending = [device_id for device_id, interfaces in devices.items() if interfaces["count"] > offset]
        return ports

    async def read_existing_ports(
        self,
        expected_ports: dict[str, dict[str, Endpoint]],
        roles: dict[str, str],
    ) -> tuple[dict[Endpoint, str], dict[Endpoint, dict[str, Any]]]:
        endpoints: dict[Endpoint, str] = {}
        attached: dict[Endpoint, dict[str, Any]] = {}
        for groups in batched(batched(expected_ports, 4), 5):
            batch = await self.client.create_batch(return_exceptions=True)
            for device_ids in groups:
                batch.add(task=self.read_ports, device_ids=device_ids)
            errors = []
            async for _, result in batch.execute():
                if isinstance(result, Exception):
                    errors.append(result)
                    continue
                for device_id, interface in result:
                    interface_name = interface["name"]["value"]
                    endpoint = expected_ports[device_id].get(interface_name)
                    owner = interface["compute_cabling_design"]["node"]
                    owner_id = owner["id"] if owner else None
                    if not self.port_in_scope(roles[device_id], endpoint is not None, owner_id):
                        continue
                    if endpoint is None or endpoint in endpoints or owner_id != self.expected_port_owner():
                        msg = f"Unexpected or duplicate interface {interface_name} on {device_id}"
                        errors.append(ValueError(msg))
                        continue
                    endpoints[endpoint] = interface["id"]
                    if link := interface["link"]["node"]:
                        attached[endpoint] = link
            if errors:
                raise errors[0]
        return endpoints, attached

    async def preflight(
        self, inventory: dict[str, tuple[Endpoint, ...]]
    ) -> tuple[dict[str, NetworkDevice], dict[Endpoint, str], set[str]]:
        ownership: dict[str, Any] = {f"{self.owner_field}__ids": [self.design_id]}
        assigned = await self.client.filters(kind=NetworkDevice, include=["device_type"], **ownership)
        devices = {device.hostname.value: device for device in assigned}
        if devices.keys() - inventory.keys():
            msg = "The new design removes existing devices. Create a separate design to change the topology."
            raise ValueError(msg)

        shared = await self.shared_devices()
        if devices.keys() & shared.keys():
            msg = "A device is assigned both to the RoCE Fabric and the cabinet design"
            raise ValueError(msg)
        devices.update(shared)

        for names in batched(inventory.keys() - devices.keys(), 250):
            occupied = await self.client.filters(kind=NetworkDevice, hostname__values=list(names))
            if occupied:
                msg = f"Device name is already in use: {occupied[0].hostname.value}"
                raise ValueError(msg)

        expected_ports = {}
        roles = {}
        for hostname, device in devices.items():
            role = inventory[hostname][0].role
            if (device.role.value, device.device_type.id, device.index.value) != (
                role,
                self.models[role],
                inventory[hostname][0].device,
            ):
                msg = f"Existing device {hostname} has a different role or model"
                raise ValueError(msg)
            roles[device.id] = role
            expected_ports[device.id] = {endpoint.interface: endpoint for endpoint in inventory[hostname]}

        endpoints, attached = await self.read_existing_ports(expected_ports, roles)

        links: set[str] = set()
        for leaf, spine in self.plan.links():
            left, right = attached.pop(leaf, None), attached.pop(spine, None)
            if left is None and right is None:
                continue
            name = self.link_name(leaf, spine)
            if (
                left is None
                or right is None
                or left["id"] != right["id"]
                or left["name"]["value"] != name
                or left["medium"]["value"] != self.medium
                or {edge["node"]["id"] for edge in left["endpoints"]["edges"]} != {endpoints[leaf], endpoints[spine]}
            ):
                msg = f"Existing link conflicts with {name}"
                raise ValueError(msg)
            links.add(name)
        if attached:
            msg = "An existing link occupies a port outside the planned connections"
            raise ValueError(msg)
        return devices, endpoints, links

    def link_name(self, leaf: Endpoint, spine: Endpoint) -> str:
        return f"{self.hostname(leaf)}-{leaf.interface}__{self.hostname(spine)}-{spine.interface}"


class RoceCablingGenerator(RoceCablingValidation):
    def owns_device(self, _endpoint: Endpoint) -> bool:
        return True

    async def save_nodes(self, nodes: Iterable[NetworkDevice | NetworkInterface | NetworkLink]) -> int:
        created = 0
        for chunk in batched(nodes, 250):
            batch = InfrahubBatch(max_concurrent_execution=10, return_exceptions=True)
            for node in chunk:
                batch.add(task=node.save, node=node, allow_upsert=False, update_group_context=False)
            errors = [result async for _, result in batch.execute() if isinstance(result, Exception)]
            if errors:
                raise errors[0]
            created += len(chunk)
        return created

    async def generate(self, data: dict[str, Any]) -> None:
        started = perf_counter()
        self.read_design(data)
        inventory = self.inventory()
        devices, endpoints, links = await self.preflight(inventory)
        self.logger.info(
            "%s: preflight complete in %.1fs (%d devices, %d interfaces, %d links reused)",
            self.design_name,
            perf_counter() - started,
            len(devices),
            len(endpoints),
            len(links),
        )

        stage_started = perf_counter()
        created = 0
        for names in batched(inventory.keys() - devices.keys(), 250):
            for hostname in names:
                endpoint = inventory[hostname][0]
                devices[hostname] = await self.client.create(
                    kind=NetworkDevice,
                    data={
                        "hostname": hostname,
                        "role": endpoint.role,
                        "index": endpoint.device,
                        "device_type": self.models[endpoint.role],
                        self.owner_field: self.design_id,
                    },
                )
            created += await self.save_nodes(devices[name] for name in names)
        self.logger.info("%s: created %d devices in %.1fs", self.design_name, created, perf_counter() - stage_started)

        stage_started = perf_counter()
        created = 0
        missing_ports = (endpoint for ports in inventory.values() for endpoint in ports if endpoint not in endpoints)
        for planned in batched(missing_ports, 250):
            nodes_by_endpoint = {
                endpoint: await self.client.create(
                    kind=NetworkInterface,
                    data={
                        "name": endpoint.interface,
                        "device": devices[self.hostname(endpoint)].id,
                        "description": f"{endpoint.port}/{endpoint.face} — 400G",
                        **self.interface_fields(endpoint),
                    },
                )
                for endpoint in planned
            }
            created += await self.save_nodes(nodes_by_endpoint.values())
            endpoints.update((endpoint, node.id) for endpoint, node in nodes_by_endpoint.items())
        self.logger.info(
            "%s: created %d interfaces in %.1fs", self.design_name, created, perf_counter() - stage_started
        )

        stage_started = perf_counter()
        created = 0
        missing_links = (
            (name, leaf, spine)
            for leaf, spine in self.plan.links()
            if (name := self.link_name(leaf, spine)) not in links
        )
        for planned_links in batched(missing_links, 250):
            nodes = [
                await self.client.create(
                    kind=NetworkLink,
                    name=name,
                    medium=self.medium,
                    endpoints=[endpoints[leaf], endpoints[spine]],
                )
                for name, leaf, spine in planned_links
            ]
            created += await self.save_nodes(nodes)
        self.logger.info("%s: created %d links in %.1fs", self.design_name, created, perf_counter() - stage_started)

        # The design owns devices, each device owns its interfaces, and links are reconciled by
        # their endpoints. Keep the SDK group small and retain legacy membership without reaping it.
        context = self.client.group_context
        if context.identifier:
            context.identifier = f"{context.identifier}-devices"
            context.delete_unused_nodes = False
            await context.add_related_nodes(
                ids=[device.id for name, device in devices.items() if self.owns_device(inventory[name][0])]
            )
        self.logger.info("%s: verified %d 400G RoCE links", self.design_name, self.plan.link_count)
