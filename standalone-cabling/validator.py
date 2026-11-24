# ruff: noqa: INP001, RUF001
"""Compare actual workbook endpoints with a generated reference plan."""

from __future__ import annotations

import re
from collections import Counter
from io import BytesIO
from typing import TYPE_CHECKING, Any, NamedTuple
from xml.etree.ElementTree import ParseError  # noqa: S405
from zipfile import BadZipFile, ZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

HEADINGS = (
    ("Leaf 编号", "Leaf 设备", "Leaf 设备编号"),
    ("Leaf 物理口", "Leaf 物理端口", "Leaf 端口"),
    ("Leaf 端口信息", "Leaf 子端口"),
    ("对端设备编号", "Spine 编号", "计算柜编号"),
    ("对端物理口", "对端物理端口", "Spine 物理端口", "计算柜物理端口", "Spine 物理口", "计算柜物理口"),
    ("对端端口信息", "对端子端口", "Spine 端口信息", "计算柜端口信息"),
)
PORT = re.compile(r"([0-9]{1,7})(?:\s*[:：]\s*([1-4]))?")
SUBPORT = re.compile(r"(?:([0-9]{1,7})\s*[:：]\s*)?([1-4])")


class Endpoint(NamedTuple):
    device: str
    port: int
    subport: int

    def __str__(self) -> str:
        return f"{self.device} · {self.port}:{self.subport}"


def text(value: object) -> str:
    return "" if value is None else str(value).strip()


def read_endpoint(device: object, port: object, port_info: object) -> Endpoint:
    name = text(device)
    match = PORT.fullmatch(text(port))
    info = text(port_info)
    if not name or name.startswith("=") or match is None or int(match[1]) < 1:
        msg = "设备编号或物理端口无效，端口可写为 65:1"
        raise ValueError(msg)
    physical_port = int(match[1])
    inline = match[2]
    if info:
        separate = SUBPORT.fullmatch(info)
        if separate is None:
            msg = "子端口必须为 1–4，例如 1 或 65:1"
            raise ValueError(msg)
        if (separate[1] and int(separate[1]) != physical_port) or (inline and inline != separate[2]):
            msg = "物理口列和端口信息列的端点不一致"
            raise ValueError(msg)
        inline = separate[2]
    if inline is None:
        msg = "缺少子端口号"
        raise ValueError(msg)
    return Endpoint(name, physical_port, int(inline))


def column_indices(header: tuple[Any, ...]) -> list[int | None]:
    indices = []
    labels = [text(value).casefold() for value in header]
    for i, aliases in enumerate(HEADINGS):
        matches = [j for j, label in enumerate(labels) if label in {alias.casefold() for alias in aliases}]
        if len(matches) > 1 or (not matches and i not in {2, 5}):
            msg = f"首行表头须含一个「{aliases[0]}」列。"
            raise ValueError(msg)
        indices.append(matches[0] if matches else None)
    return indices


def scan(rows: Iterator[tuple[Any, ...]], expected: dict[Endpoint, Endpoint], sheet_name: str) -> dict[str, Any]:
    columns = column_indices(next(rows, ()))
    issues = []
    counts: Counter[str] = Counter()
    first_use: dict[Endpoint, int] = {}
    matched = set()
    actual_rows = 0

    def issue(row: int | None, kind: str, actual: str, wanted: str, detail: str = "") -> None:
        counts[kind] += 1
        issues.append([row, kind, actual, wanted, detail])

    for number, row in enumerate(rows, 2):
        if not any(text(value) for value in row):
            continue
        actual_rows += 1
        parts = [row[i] if i is not None and i < len(row) else None for i in columns]
        try:
            left, right = read_endpoint(*parts[:3]), read_endpoint(*parts[3:])
        except ValueError as error:
            issue(number, "格式错误", " / ".join(map(text, parts)), "", str(error))
            continue
        actual = f"{left} → {right}"
        correct = expected.get(left)
        wanted = f"{left} → {correct}" if correct else ""
        for endpoint in {left, right}:
            first = first_use.setdefault(endpoint, number)
            if first != number:
                issue(number, "重复占用", actual, wanted, f"{endpoint} 已出现在第 {first} 行。")
        if correct is None:
            issue(number, "计划外端口", actual, "", "该 Leaf 端点不在所选规则与参数的计划中。")
        elif right != correct:
            issue(number, "错接", actual, wanted)
        else:
            matched.add(left)
    for left, right in expected.items():
        if left not in matched:
            issue(None, "缺失", "", f"{left} → {right}")
    return {
        "sheet": sheet_name,
        "actual": actual_rows,
        "expected": len(expected),
        "matched": len(matched),
        "counts": counts,
        "issues": issues,
    }


def reference_endpoints(reference: Iterable[tuple[str | int, ...]]) -> dict[Endpoint, Endpoint]:
    return {read_endpoint(row[0], row[2], row[3]): read_endpoint(row[5], row[7], row[8]) for row in reference}


def check_workbook(content: bytes, reference: Iterable[tuple[str | int, ...]], sheet_name: object) -> dict[str, Any]:
    if len(content) > 20 * 1024 * 1024:
        msg = "XLSX 文件超过 20 MiB。"
        raise ValueError(msg)
    try:
        with ZipFile(BytesIO(content)) as zipped:
            if sum(meta.file_size for meta in zipped.infolist()) > 200 * 1024 * 1024:
                msg = "工作簿解压后超过 200 MiB。"
                raise ValueError(msg)
        book = load_workbook(BytesIO(content), read_only=True, data_only=False, keep_links=False)
    except (BadZipFile, InvalidFileException, KeyError, ParseError) as error:
        msg = "无法打开工作簿，请使用有效的 .xlsx 文件。"
        raise ValueError(msg) from error
    try:
        if sheet_name and (not isinstance(sheet_name, str) or sheet_name not in book.sheetnames):
            msg = f"找不到工作表。可用工作表：{'、'.join(book.sheetnames)}"
            raise ValueError(msg)
        worksheet = book[sheet_name] if isinstance(sheet_name, str) and sheet_name else book.worksheets[0]
        worksheet.reset_dimensions()
        return scan(worksheet.iter_rows(values_only=True), reference_endpoints(reference), worksheet.title)
    except (BadZipFile, KeyError, ParseError) as error:
        msg = "工作表内容损坏，请重新保存为 .xlsx 后重试。"
        raise ValueError(msg) from error
    finally:
        book.close()
