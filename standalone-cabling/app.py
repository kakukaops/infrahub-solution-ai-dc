# ruff: noqa: INP001, RUF001
"""Local graphical planner for the four-subport RoCE cabling rules."""

from __future__ import annotations

import argparse
import contextlib
import json
import webbrowser
from base64 import b64decode
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from itertools import islice
from pathlib import Path
from typing import TYPE_CHECKING

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font, PatternFill
from validator import check_workbook

if TYPE_CHECKING:
    from collections.abc import Iterator

HEADERS = (
    "Leaf 编号",
    "Leaf 型号",
    "Leaf 物理口",
    "Leaf 端口信息",
    "线缆标签",
    "对端设备编号",
    "对端设备型号",
    "对端物理口",
    "对端端口信息",
    "链路类型",
)
DEFAULTS = {
    "leaf_count": 256,
    "spine_count": 128,
    "cabinet_count": 512,
    "leaf_start": 65,
    "leaf_end": 128,
    "spine_start": 1,
    "spine_end": 128,
    "downlink_start": 1,
    "downlink_end": 64,
    "cabinet_ports": 32,
    "cabinets_per_pair": 4,
    "leaf_prefix": "Leaf-",
    "spine_prefix": "Spine-",
    "cabinet_prefix": "计算柜-",
    "leaf_model": "",
    "spine_model": "",
    "cabinet_model": "",
    "rule": "both",
}
NUMERIC_LABELS = dict(
    zip(
        list(DEFAULTS)[:11],
        (
            "Leaf 台数",
            "Spine 台数",
            "计算柜数量",
            "Leaf 上联起始口",
            "Leaf 上联结束口",
            "Spine 起始口",
            "Spine 结束口",
            "Leaf 下联起始口",
            "Leaf 下联结束口",
            "每柜端口数",
            "每对 Leaf 接柜数",
        ),
        strict=True,
    )
)


class PlanError(ValueError):
    """Invalid local planning input."""


@dataclass(frozen=True)
class Plan:
    leaf_count: int
    spine_count: int
    cabinet_count: int
    leaf_start: int
    leaf_end: int
    spine_start: int
    spine_end: int
    downlink_start: int
    downlink_end: int
    cabinet_ports: int
    cabinets_per_pair: int
    leaf_prefix: str
    spine_prefix: str
    cabinet_prefix: str
    leaf_model: str
    spine_model: str
    cabinet_model: str
    rule: str = "both"

    @classmethod
    def from_input(cls, data: object) -> Plan:
        if not isinstance(data, dict) or data.keys() - {"rule"} != DEFAULTS.keys() - {"rule"}:
            msg = "参数不完整，请刷新页面后重试。"
            raise PlanError(msg)
        data = {"rule": "both", **data}
        for key, label in NUMERIC_LABELS.items():
            if type(data[key]) is not int or not 1 <= data[key] <= 1_000_000:
                msg = f"{label}须为 1–1,000,000 的整数。"
                raise PlanError(msg)
        for key in DEFAULTS.keys() - NUMERIC_LABELS.keys():
            value = data[key]
            if not isinstance(value, str) or len(value) > 100 or any(ord(char) < 32 for char in value):
                msg = "设备前缀和型号须为不超过 100 字的单行文本。"
                raise PlanError(msg)
            data[key] = value.strip()
        plan = cls(**data)
        plan.validate()
        return plan

    @property
    def counts(self) -> tuple[int, int]:
        return (
            self.leaf_count * self.spine_count * 2 if self.rule != "downlink" else 0,
            self.cabinet_count * self.cabinet_ports * 4 if self.rule != "uplink" else 0,
        )

    def validate(self) -> None:
        if self.rule not in {"both", "uplink", "downlink"}:
            msg = "请选择有效的布线规则。"
            raise PlanError(msg)
        prefixes = [self.leaf_prefix]
        if self.rule != "downlink":
            self.validate_uplinks()
            prefixes.append(self.spine_prefix)
        if self.rule != "uplink":
            self.validate_downlinks()
            prefixes.append(self.cabinet_prefix)
        if self.rule == "both" and max(self.leaf_start, self.downlink_start) <= min(self.leaf_end, self.downlink_end):
            msg = "Leaf 上联和下联物理口范围重叠。"
            raise PlanError(msg)
        if sum(self.counts) > 1_048_575:
            msg = "布线条数超过 Excel 单表的数据行上限。"
            raise PlanError(msg)
        if len(set(prefixes)) != len(prefixes) or not all(prefixes) or any(p[-1].isdigit() for p in prefixes):
            msg = "所选设备的编号前缀须非空、彼此不同，并以非数字字符结尾。"
            raise PlanError(msg)

    def validate_uplinks(self) -> None:
        if self.leaf_start > self.leaf_end or self.spine_start > self.spine_end:
            msg = "上联物理口起始编号不能大于结束编号。"
            raise PlanError(msg)
        if self.spine_count != 2 * (self.leaf_end - self.leaf_start + 1):
            msg = "Spine 台数须等于每台 Leaf 上联物理口数 × 2。"
            raise PlanError(msg)
        if self.leaf_count > 2 * (self.spine_end - self.spine_start + 1):
            msg = "Spine 的物理口不足，无法覆盖所有 Leaf。"
            raise PlanError(msg)

    def validate_downlinks(self) -> None:
        if self.downlink_start > self.downlink_end:
            msg = "Leaf 下联物理口起始编号不能大于结束编号。"
            raise PlanError(msg)
        if self.cabinet_ports % 2:
            msg = "每柜端口数须为偶数，才能按奇偶双归。"
            raise PlanError(msg)
        if self.downlink_end - self.downlink_start + 1 < self.cabinets_per_pair * (self.cabinet_ports // 2):
            msg = "每台 Leaf 的下联口不足，无法容纳配置的计算柜。"
            raise PlanError(msg)
        if 2 * ((self.cabinet_count - 1) // self.cabinets_per_pair + 1) > self.leaf_count:
            msg = "Leaf 数量不足，每组计算柜需要一对 Leaf。"
            raise PlanError(msg)

    def rows(self) -> Iterator[tuple[str | int, ...]]:
        ports_per_leaf = self.cabinet_ports // 2
        for leaf in range(1, self.leaf_count + 1):
            cabinet_start = ((leaf - 1) // 2) * self.cabinets_per_pair + 1
            cabinet_end = min(cabinet_start + self.cabinets_per_pair - 1, self.cabinet_count)
            leaf_name = f"{self.leaf_prefix}{leaf:03d}"
            spine_offset, spine_group = divmod(leaf - 1, 2)
            downlinks = range(
                self.downlink_start, self.downlink_start + max(0, cabinet_end - cabinet_start + 1) * ports_per_leaf
            )
            if self.rule == "uplink":
                downlinks = range(0)
            uplinks = range(0) if self.rule == "downlink" else range(self.leaf_start, self.leaf_end + 1)
            for port in sorted((*downlinks, *uplinks)):
                for subport in range(1, 5):
                    if port in uplinks:
                        spine = 2 * (port - self.leaf_start) + (subport - 1) // 2 + 1
                        target = (
                            f"{self.spine_prefix}{spine:03d}",
                            self.spine_model,
                            self.spine_start + spine_offset,
                            f"{self.spine_start + spine_offset}:{2 * spine_group + (subport - 1) % 2 + 1}",
                            "Leaf–Spine",
                        )
                    else:
                        slot, offset = divmod(port - self.downlink_start, ports_per_leaf)
                        cabinet = cabinet_start + slot
                        cabinet_port = 2 * offset + (leaf - 1) % 2 + 1
                        target = (
                            f"{self.cabinet_prefix}{cabinet:03d}",
                            self.cabinet_model,
                            cabinet_port,
                            f"{cabinet_port}:{subport}",
                            "Leaf–计算柜",
                        )
                    yield leaf_name, self.leaf_model, port, f"{port}:{subport}", "", *target


def make_excel(plan: Plan) -> bytes:
    book = Workbook(write_only=True)
    sheet = book.create_sheet("RoCE 布线表")
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:J{sum(plan.counts) + 1}"
    for column, width in zip("ABCDEFGHIJ", (29, 24, 16, 21, 16, 29, 24, 17, 21, 21), strict=True):
        sheet.column_dimensions[column].width = width
    headings = []
    for title in HEADERS:
        cell = WriteOnlyCell(sheet, value=title)
        cell.font = Font(name="Arial", color="FFFFFF", bold=True)
        cell.fill = PatternFill("solid", fgColor="1B3A52")
        headings.append(cell)
    sheet.append(headings)
    for row in plan.rows():
        cells = []
        for value in row:
            cell = WriteOnlyCell(sheet, value=value or None)
            if isinstance(value, str) and value:
                cell.data_type = "s"
            cells.append(cell)
        sheet.append(cells)
    stream = BytesIO()
    book.save(stream)
    return stream.getvalue()


class Handler(BaseHTTPRequestHandler):
    def send_file(
        self, data: bytes, content_type: str, filename: str | None = None, *, status: HTTPStatus = HTTPStatus.OK
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, data: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_file(json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8", status=status)

    def do_GET(self) -> None:
        if self.path == "/defaults":
            self.send_json(DEFAULTS)
        elif self.path in {"/", "/validate"}:
            self.send_file(Path(__file__).with_name("index.html").read_bytes(), "text/html; charset=utf-8")
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def read_plan(self) -> Plan:
        size = int(self.headers.get("Content-Length", "0"))
        if not 0 < size <= 32_768 or self.headers.get_content_type() != "application/json":
            msg = "请求格式无效。"
            raise PlanError(msg)
        return Plan.from_input(json.loads(self.rfile.read(size)))

    def do_POST(self) -> None:
        if self.path not in {"/preview", "/export", "/check"}:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if self.path == "/check":
            self.check_upload()
            return
        try:
            plan = self.read_plan()
        except ValueError as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            return
        if self.path == "/export":
            self.send_file(
                make_excel(plan),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "roce-cabling.xlsx",
            )
            return
        rows = [
            row
            for kind in ("Leaf–计算柜", "Leaf–Spine")
            for row in islice((item for item in plan.rows() if item[-1] == kind), 6)
        ]
        self.send_json({"headers": HEADERS, "rows": rows, "uplink": plan.counts[0], "downlink": plan.counts[1]})

    def read_upload(self) -> tuple[Plan, bytes, object]:
        size = int(self.headers.get("Content-Length", "0"))
        if not 0 < size <= 28 * 1024 * 1024 or self.headers.get_content_type() != "application/json":
            msg = "请上传不超过 20 MiB 的 XLSX 文件。"
            raise PlanError(msg)
        data = json.loads(self.rfile.read(size))
        if not isinstance(data, dict) or not isinstance(data.get("workbook"), str):
            msg = "请选择要校验的 XLSX 文件。"
            raise PlanError(msg)
        return Plan.from_input(data.get("params")), b64decode(data["workbook"], validate=True), data.get("sheet")

    def check_upload(self) -> None:
        try:
            plan, workbook, sheet = self.read_upload()
            result = check_workbook(workbook, plan.rows(), sheet)
        except ValueError as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            return
        self.send_json(result)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="Listening address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=0, help="Local listening port (default: automatic)")
    parser.add_argument("--no-browser", action="store_true", help="Print the local URL without opening a browser")
    args = parser.parse_args()
    with ThreadingHTTPServer((args.host, args.port), Handler) as server:
        browser_host = "127.0.0.1" if args.host == "0.0.0.0" else args.host  # noqa: S104
        url = f"http://{browser_host}:{server.server_port}/"
        print(f"打开界面：{url}    Ctrl+C 退出", flush=True)
        if not args.no_browser:
            webbrowser.open(url)
        with contextlib.suppress(KeyboardInterrupt):
            server.serve_forever()


if __name__ == "__main__":
    main()
