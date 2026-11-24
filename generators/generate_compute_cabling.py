from __future__ import annotations

import csv
from io import StringIO
from itertools import chain
from typing import Any

from infrahub_sdk.node import InfrahubNode
from infrahub_sdk.transforms import InfrahubTransform

from infrahub_solution_ai_dc.protocols import NetworkDevice
from infrahub_solution_ai_dc.roce_cabling import ComputeCablingPlan, Endpoint

from .generate_roce_cabling import RoceCablingGenerator, RoceCablingValidation


class ComputeCablingValidation(RoceCablingValidation):
    owner_field = "compute_cabling_design"
    plan: ComputeCablingPlan

    def read_design(self, data: dict[str, Any]) -> None:
        (design,) = (edge["node"] for edge in data["NetworkComputeCablingDesign"]["edges"])
        fabric = design["roce_fabric"]["node"]
        self.design_id = design["id"]
        self.design_name = design["name"]["value"]
        self.medium = design["medium"]["value"]
        self.fabric_id: str = fabric["id"]
        self.fabric_name: str = fabric["name"]["value"]
        models = {
            "leaf": fabric["leaf_device_type"]["node"],
            "compute_cabinet": design["cabinet_device_type"]["node"],
        }
        self.models = {role: model["id"] for role, model in models.items()}
        self.model_names: dict[str, str] = {role: model["name"]["value"] for role, model in models.items()}
        self.plan = ComputeCablingPlan(
            cabinet_count=design["cabinet_count"]["value"],
            cabinet_port_count=design["cabinet_port_count"]["value"],
            cabinets_per_leaf_pair=design["cabinets_per_leaf_pair"]["value"],
            leaf_count=fabric["leaf_count"]["value"],
            leaf_port_start=design["leaf_port_start"]["value"],
            leaf_port_end=design["leaf_port_end"]["value"],
        )
        if max(self.plan.leaf_port_start, fabric["leaf_port_start"]["value"]) <= min(
            self.plan.leaf_port_end, fabric["leaf_port_end"]["value"]
        ):
            msg = "Compute downlink port range overlaps the RoCE Fabric uplink range"
            raise ValueError(msg)

    def device_ranges(self) -> tuple[tuple[str, int, int, int], ...]:
        return (
            ("leaf", self.plan.required_leaf_count, self.plan.leaf_port_start, self.plan.leaf_port_end),
            ("compute_cabinet", self.plan.cabinet_count, 1, self.plan.cabinet_port_count),
        )

    def hostname(self, endpoint: Endpoint) -> str:
        if endpoint.role == "leaf":
            return endpoint.hostname(self.fabric_name)
        return f"{self.design_name}-cabinet-{endpoint.device:03d}"

    def topology_description(self) -> str:
        return (
            f"{self.plan.cabinet_count} cabinets, {self.plan.required_leaf_count} Leafs, "
            f"{self.plan.physical_pair_count} physical port pairs"
        )

    def expected_port_owner(self) -> str:
        return self.design_id

    def port_in_scope(self, role: str, planned: bool, owner: str | None) -> bool:
        return planned or role != "leaf" or owner == self.design_id

    def interface_fields(self, endpoint: Endpoint) -> dict[str, str]:
        return {
            "role": "server" if endpoint.role == "leaf" else "leaf",
            "compute_cabling_design": self.design_id,
        }

    async def shared_devices(self) -> dict[str, NetworkDevice]:
        devices = await self.client.filters(
            kind=NetworkDevice,
            roce_cabling_design__ids=[self.fabric_id],
            role__value="leaf",
            include=["device_type"],
        )
        by_name = {device.hostname.value: device for device in devices}
        expected = {f"{self.fabric_name}-leaf-{index:03d}" for index in range(1, self.plan.required_leaf_count + 1)}
        missing = sorted(expected - by_name.keys())
        if missing:
            msg = f"Generate the RoCE Fabric first. Missing Leaf devices include: {', '.join(missing[:5])}"
            raise ValueError(msg)
        return {name: by_name[name] for name in sorted(expected)}


class ComputeCablingGenerator(ComputeCablingValidation, RoceCablingGenerator):
    def owns_device(self, endpoint: Endpoint) -> bool:
        return endpoint.role == "compute_cabinet"


class ComputeCablingPlanCSV(InfrahubTransform):
    query = "generate_compute_cabling"

    def transform(self, data: dict[str, Any]) -> str:
        design = ComputeCablingValidation(query=self.query, client=self.client, infrahub_node=InfrahubNode)
        design.read_design(data)
        output = StringIO(newline="")
        output.write("\ufeff")
        writer = csv.writer(output)
        writer.writerow(
            [
                "Leaf 编号",
                "Leaf 型号",
                "Leaf 物理端口",
                "Leaf 端口信息",
                "线缆标签",
                "计算柜编号",
                "计算柜型号",
                "计算柜物理端口",
                "计算柜端口信息",
            ]
        )
        for leaf, cabinet in design.plan.links():
            writer.writerow(
                [
                    design.hostname(leaf),
                    design.model_names["leaf"],
                    leaf.port,
                    f"{leaf.face} 面 / 400G",
                    "",
                    design.hostname(cabinet),
                    design.model_names["compute_cabinet"],
                    cabinet.port,
                    f"{cabinet.face} 面 / 400G",
                ]
            )
        return output.getvalue()


class CombinedRoceCablingPlanCSV(InfrahubTransform):
    query = "generate_compute_cabling"

    def transform(self, data: dict[str, Any]) -> str:
        compute = ComputeCablingValidation(query=self.query, client=self.client, infrahub_node=InfrahubNode)
        compute.read_design(data)
        (design,) = (edge["node"] for edge in data["NetworkComputeCablingDesign"]["edges"])
        fabric = design["roce_fabric"]["node"]
        uplink = RoceCablingValidation(query=self.query, client=self.client, infrahub_node=InfrahubNode)
        uplink.read_design({"NetworkRoceCablingDesign": {"edges": [{"node": fabric}]}})
        target_models = {
            "spine": fabric["spine_device_type"]["node"]["name"]["value"],
            "compute_cabinet": compute.model_names["compute_cabinet"],
        }
        target_types = {"spine": "Spine", "compute_cabinet": "计算柜"}

        output = StringIO(newline="")
        output.write("\ufeff")
        writer = csv.writer(output)
        writer.writerow(
            [
                "Leaf 编号",
                "Leaf 型号",
                "Leaf 物理端口",
                "Leaf 端口信息",
                "光纤标签",
                "目标设备类型",
                "目标设备编号",
                "目标设备型号",
                "目标物理端口",
                "目标端口信息",
            ]
        )
        links = sorted(
            chain(uplink.plan.links(), compute.plan.links()),
            key=lambda pair: (pair[0].device, pair[0].port, pair[0].face),
        )
        for leaf, target in links:
            leaf_name = compute.hostname(leaf)
            target_name = uplink.hostname(target) if target.role == "spine" else compute.hostname(target)
            writer.writerow(
                [
                    leaf_name,
                    compute.model_names["leaf"],
                    leaf.port,
                    f"{leaf.face} 面 / 400G",
                    f"{leaf_name}:{leaf.interface} to {target_name}:{target.interface}",
                    target_types[target.role],
                    target_name,
                    target_models[target.role],
                    target.port,
                    f"{target.face} 面 / 400G",
                ]
            )
        return output.getvalue()
