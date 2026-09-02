#!/usr/bin/env python3
"""Build LTBox/KonaBess profiles from two firmware files and shared policy."""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import io
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import tomllib
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping


VENDOR_BOOT_MAGIC = b"VNDRBOOT"
FDT_MAGIC = b"\xd0\x0d\xfe\xed"
URI_PREFIX = "konabess://"
DT_CELL_MAX = 0xFFFFFFFF

GPU_GROUP_RE = re.compile(
    r"^(?:(?:[A-Za-z_][\w-]*):\s*)*qcom,gpu-pwrlevels-(\d+)\s*\{$"
)
GPU_LEVEL_RE = re.compile(
    r"^(?:(?:[A-Za-z_][\w-]*):\s*)*qcom,gpu-pwrlevel@(\d+)\s*\{$"
)
GROUP_RE = re.compile(r"^qcom,gpu-pwrlevels-(\d+) \{$")
LEVEL_RE = re.compile(r"^qcom,gpu-pwrlevel@(\d+) \{$")
PROPERTY_RE = re.compile(r"^([^=]+?)\s*=\s*<([^>]+)>;$")
LEVEL_PROPERTY_RE = re.compile(
    r"^(?P<prefix>\s*qcom,level\s*=\s*<)"
    r"(?P<value>0[xX][0-9a-fA-F]+|\d+)(?P<suffix>>;\s*)$"
)
GPU_FREQUENCY_RE = re.compile(
    r"^(?P<prefix>\s*qcom,gpu-freq\s*=\s*<)"
    r"(?P<value>0[xX][0-9a-fA-F]+|\d+)(?P<suffix>>;\s*)$"
)
COMMAND_DB_ENTRY = struct.Struct("<8sIIIHH")
VENDOR_BOOT_HEADER_SIZES = {3: 2112, 4: 2128}
VENDOR_BOOT_PAGE_SIZES = {2048, 4096, 8192, 16384}
DTS_NODE_RE = re.compile(
    r"^(?:(?:[A-Za-z_][\w-]*):\s*)*(?P<name>[^\s{}:]+)\s*\{$"
)
DTS_PROPERTY_RE = re.compile(r"^(?P<name>[^=;]+?)\s*=\s*(?P<value>.*);$")
DTS_STRING_RE = re.compile(r'"((?:\\.|[^"\\])*)"')
RESOURCE_ID_RE = re.compile(rb"[A-Za-z0-9][A-Za-z0-9._-]{0,7}\Z")


class PipelineError(ValueError):
    """A concise, user-facing pipeline or configuration error."""


@dataclass(frozen=True)
class VendorBootInfo:
    header_version: int
    dtb_offset: int
    dtb_size: int
    payload_end: int


@dataclass(frozen=True)
class AopInfo:
    size: int
    sha256: str
    values: tuple[int, ...]
    offsets: tuple[int, ...]
    template_count: int


@dataclass(frozen=True)
class DtbCandidate:
    index: int
    sha256: str
    model: str
    compatibles: tuple[str, ...]
    chip: str
    msm_id: tuple[int, ...]
    gpu_model: str
    table: str
    group_count: int
    level_count: int
    group_ids: tuple[int, ...]
    levels_per_group: tuple[int, ...]

    @property
    def compatible(self) -> str:
        return self.compatibles[0] if self.compatibles else ""


@dataclass(frozen=True)
class LevelRow:
    level: int
    frequency: int
    stock_vote: int


@dataclass(frozen=True)
class GroupInfo:
    group: int
    header: dict[str, list[int]]
    levels: list[LevelRow]


@dataclass
class DtsNode:
    name: str
    start: int
    end: int | None
    parent: int | None
    properties: dict[str, str]


@dataclass(frozen=True)
class FirmwareConfig:
    firmware_id: str
    version: str
    id_template: str
    device: str
    label: str
    profile_label: str
    fingerprint_identity: str
    fingerprint_variant: str
    vendor_fingerprint: str = ""


@dataclass(frozen=True)
class SourceConfig:
    vendor_boot: Path
    aop: Path
    dtb_index: int
    chip: str
    compatible: str
    msm_id: tuple[int, ...]
    gpu_model: str
    fingerprint_property: str


@dataclass(frozen=True)
class OutputConfig:
    stock_profile: Path
    stock_dts: Path
    uv_dir: Path
    release_dir: Path

    @property
    def diff_dir(self) -> Path:
        return self.release_dir / "diff"

    @property
    def release_report(self) -> Path:
        return self.release_dir / "release_report.md"


@dataclass(frozen=True)
class RegulatorConfig:
    picker_values: tuple[int, ...]
    picker_names: tuple[str, ...]
    aop_values: tuple[int, ...]
    aop_provenance: str
    aop_source_sha256: str
    aop_resource: str
    ltbox_observed_min_vote: int

    @property
    def picker_name(self) -> dict[int, str]:
        return dict(zip(self.picker_values, self.picker_names, strict=True))

    @property
    def aop_index(self) -> dict[int, int]:
        return {value: index for index, value in enumerate(self.aop_values)}


@dataclass(frozen=True)
class FrequencyVariant:
    name: str
    source_hz: int | None
    target_hz: int | None

    @property
    def replacement(self) -> tuple[int, int] | None:
        if self.source_hz is None:
            return None
        assert self.target_hz is not None
        return self.source_hz, self.target_hz


@dataclass(frozen=True)
class GenerationConfig:
    generic_steps: tuple[int, ...]
    aop_steps: tuple[int, ...]
    comparison_generic_step: int
    generic_profile_filename: str
    aop_profile_filename: str
    generic_diff_filename: str
    aop_diff_filename: str
    variants: tuple[FrequencyVariant, ...]


@dataclass(frozen=True)
class ProfileFamily:
    method: str
    steps: tuple[int, ...]
    profile_filename: str
    diff_filename: str


@dataclass(frozen=True)
class BuildConfig:
    config_path: Path
    root: Path
    firmware: FirmwareConfig
    source: SourceConfig
    output: OutputConfig
    regulators: RegulatorConfig
    generation: GenerationConfig


@dataclass(frozen=True)
class GeneratedProfile:
    steps: int
    text: str
    vote_changes: int
    frequency_changes: int
    drop_distribution: dict[int, int]


@dataclass(frozen=True)
class BuildArtifacts:
    tracked: dict[Path, bytes]
    release: dict[Path, bytes]


def reject_unknown(table: Mapping[str, Any], allowed: set[str], context: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise PipelineError(f"unknown {context} key(s): {', '.join(unknown)}")


def require_table(parent: Mapping[str, Any], key: str, context: str) -> Mapping[str, Any]:
    value = parent.get(key)
    if not isinstance(value, dict):
        raise PipelineError(f"{context}.{key} must be a TOML table")
    return value


def require_str(table: Mapping[str, Any], key: str, context: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PipelineError(f"{context}.{key} must be a non-empty string")
    if "\0" in value:
        raise PipelineError(f"{context}.{key} cannot contain a NUL character")
    return value


def require_int(table: Mapping[str, Any], key: str, context: str) -> int:
    value = table.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise PipelineError(f"{context}.{key} must be an integer")
    return value


def optional_int(table: Mapping[str, Any], key: str, context: str) -> int | None:
    if key not in table:
        return None
    return require_int(table, key, context)


def require_int_list(table: Mapping[str, Any], key: str, context: str) -> tuple[int, ...]:
    value = table.get(key)
    if not isinstance(value, list) or not value:
        raise PipelineError(f"{context}.{key} must be a non-empty integer array")
    if any(isinstance(item, bool) or not isinstance(item, int) for item in value):
        raise PipelineError(f"{context}.{key} must contain only integers")
    return tuple(value)


def require_str_list(table: Mapping[str, Any], key: str, context: str) -> tuple[str, ...]:
    value = table.get(key)
    if not isinstance(value, list) or not value:
        raise PipelineError(f"{context}.{key} must be a non-empty string array")
    if any(
        not isinstance(item, str) or not item.strip() or "\0" in item
        for item in value
    ):
        raise PipelineError(f"{context}.{key} must contain only non-empty strings")
    return tuple(value)


def scoped_path(root: Path, raw: str, context: str) -> Path:
    try:
        path = Path(raw)
    except (OSError, ValueError) as error:
        raise PipelineError(f"{context} is not a valid path: {error}") from error
    if path.is_absolute():
        raise PipelineError(f"{context} must be relative to the configuration directory")
    try:
        resolved = (root / path).resolve()
    except (OSError, ValueError) as error:
        raise PipelineError(f"{context} is not a valid path: {error}") from error
    if not resolved.is_relative_to(root):
        raise PipelineError(f"{context} must stay inside the configuration directory")
    return resolved


def validate_increasing(values: tuple[int, ...], context: str) -> None:
    if any(value < 0 or value > DT_CELL_MAX for value in values):
        raise PipelineError(f"{context} must contain unsigned 32-bit DT cells")
    if any(left >= right for left, right in zip(values, values[1:])):
        raise PipelineError(f"{context} must be strictly increasing and unique")


def render_template(template: str, context: str, **values: object) -> str:
    try:
        rendered = template.format(**values)
    except (AttributeError, IndexError, KeyError, TypeError, ValueError) as error:
        raise PipelineError(f"cannot render {context}: {error}") from error
    if not rendered:
        raise PipelineError(f"{context} rendered an empty value")
    return rendered


def render_filename(template: str, context: str, **values: object) -> str:
    rendered = render_template(template, context, **values)
    path = Path(rendered)
    if path.is_absolute() or path.name != rendered or rendered in {".", ".."}:
        raise PipelineError(f"{context} must render a plain filename, found {rendered!r}")
    return rendered


def plain_name(raw: str, context: str) -> str:
    path = Path(raw)
    if path.is_absolute() or path.name != raw or raw in {".", ".."}:
        raise PipelineError(f"{context} must be a plain file or directory name")
    return raw


def profile_families(generation: GenerationConfig) -> tuple[ProfileFamily, ...]:
    return (
        ProfileFamily(
            "generic",
            generation.generic_steps,
            generation.generic_profile_filename,
            generation.generic_diff_filename,
        ),
        ProfileFamily(
            "aop",
            generation.aop_steps,
            generation.aop_profile_filename,
            generation.aop_diff_filename,
        ),
    )


def parse_project_config(data: Mapping[str, Any], root: Path) -> FirmwareConfig:
    project_table = require_table(data, "project", "top-level")
    reject_unknown(
        project_table,
        {
            "device",
            "firmware_id_pattern",
            "firmware_id_template",
            "firmware_label",
            "profile_label",
            "fingerprint_identity",
            "fingerprint_variant",
        },
        "project",
    )
    firmware_id = root.name
    pattern_text = require_str(project_table, "firmware_id_pattern", "project")
    try:
        pattern = re.compile(pattern_text)
    except re.error as error:
        raise PipelineError(f"project.firmware_id_pattern is invalid: {error}") from error
    match = pattern.fullmatch(firmware_id)
    if not match or "version" not in match.groupdict() or not match.group("version"):
        raise PipelineError(
            f"firmware directory {firmware_id!r} does not match "
            "project.firmware_id_pattern with a named 'version' group"
        )
    version = match.group("version")
    template_values = {"firmware_id": firmware_id, "version": version}
    profile_label = render_template(
        require_str(project_table, "profile_label", "project"),
        "project.profile_label",
        **template_values,
    )
    firmware = FirmwareConfig(
        firmware_id=firmware_id,
        version=version,
        id_template=require_str(project_table, "firmware_id_template", "project"),
        device=require_str(project_table, "device", "project"),
        label=render_template(
            require_str(project_table, "firmware_label", "project"),
            "project.firmware_label",
            **template_values,
        ),
        profile_label=profile_label,
        fingerprint_identity=require_str(
            project_table, "fingerprint_identity", "project"
        ),
        fingerprint_variant=require_str(
            project_table, "fingerprint_variant", "project"
        ),
    )
    return firmware


def parse_source_config(data: Mapping[str, Any], root: Path) -> SourceConfig:
    inputs_table = require_table(data, "inputs", "top-level")
    reject_unknown(
        inputs_table,
        {"directory", "vendor_boot", "aop", "fingerprint_property"},
        "inputs",
    )
    input_directory = plain_name(
        require_str(inputs_table, "directory", "inputs"), "inputs.directory"
    )
    vendor_boot_name = plain_name(
        require_str(inputs_table, "vendor_boot", "inputs"), "inputs.vendor_boot"
    )
    aop_name = plain_name(require_str(inputs_table, "aop", "inputs"), "inputs.aop")

    target_table = require_table(data, "target", "top-level")
    reject_unknown(target_table, {"compatible", "msm_id", "gpu_model"}, "target")
    compatible = require_str(target_table, "compatible", "target")
    chip = infer_chip(compatible, "")
    if not chip:
        raise PipelineError("target.compatible must identify a qcom chip")
    source = SourceConfig(
        vendor_boot=scoped_path(
            root,
            f"{input_directory}/{vendor_boot_name}",
            "inputs.vendor_boot",
        ),
        aop=scoped_path(root, f"{input_directory}/{aop_name}", "inputs.aop"),
        dtb_index=-1,
        chip=chip,
        compatible=compatible,
        msm_id=require_int_list(target_table, "msm_id", "target"),
        gpu_model=require_str(target_table, "gpu_model", "target"),
        fingerprint_property=require_str(
            inputs_table, "fingerprint_property", "inputs"
        ),
    )
    if any(value < 0 or value > DT_CELL_MAX for value in source.msm_id):
        raise PipelineError("target.msm_id must contain unsigned 32-bit DT cells")
    return source


def parse_output_config(
    data: Mapping[str, Any], root: Path, source: SourceConfig
) -> OutputConfig:
    paths_table = require_table(data, "paths", "top-level")
    reject_unknown(paths_table, {"stock", "stock_dts", "uv_dir"}, "paths")
    return OutputConfig(
        stock_profile=scoped_path(root, require_str(paths_table, "stock", "paths"), "paths.stock"),
        stock_dts=scoped_path(
            root, require_str(paths_table, "stock_dts", "paths"), "paths.stock_dts"
        ),
        uv_dir=scoped_path(root, require_str(paths_table, "uv_dir", "paths"), "paths.uv_dir"),
        release_dir=source.vendor_boot.parent / "release",
    )


def parse_regulator_config(data: Mapping[str, Any]) -> RegulatorConfig:
    regulators_table = require_table(data, "regulators", "top-level")
    reject_unknown(regulators_table, {"generic", "aop"}, "regulators")
    generic_table = require_table(regulators_table, "generic", "regulators")
    reject_unknown(
        generic_table,
        {"values", "names", "ltbox_observed_min_vote"},
        "regulators.generic",
    )
    picker_values = require_int_list(generic_table, "values", "regulators.generic")
    picker_names = require_str_list(generic_table, "names", "regulators.generic")
    if len(picker_values) != len(picker_names):
        raise PipelineError("regulators.generic.values and names must have equal lengths")
    validate_increasing(picker_values, "regulators.generic.values")
    if len(set(picker_names)) != len(picker_names):
        raise PipelineError("regulators.generic.names must be unique")

    aop_table = require_table(regulators_table, "aop", "regulators")
    reject_unknown(aop_table, {"resource"}, "regulators.aop")
    aop_resource = require_str(aop_table, "resource", "regulators.aop")
    try:
        resource_bytes = aop_resource.encode("ascii")
    except UnicodeEncodeError as error:
        raise PipelineError("regulators.aop.resource must be ASCII") from error
    if len(resource_bytes) > 7 or not RESOURCE_ID_RE.fullmatch(resource_bytes):
        raise PipelineError(
            "regulators.aop.resource must be a valid 1..7 byte Command DB ID"
        )
    regulators = RegulatorConfig(
        picker_values=picker_values,
        picker_names=picker_names,
        aop_values=(),
        aop_provenance="",
        aop_source_sha256="",
        aop_resource=aop_resource,
        ltbox_observed_min_vote=require_int(
            generic_table, "ltbox_observed_min_vote", "regulators.generic"
        ),
    )
    if regulators.ltbox_observed_min_vote < 0:
        raise PipelineError("regulators.generic.ltbox_observed_min_vote cannot be negative")
    return regulators


def parse_frequency_variants(
    generation_table: Mapping[str, Any],
) -> tuple[FrequencyVariant, ...]:
    variants_data = generation_table.get("frequency_variants")
    if not isinstance(variants_data, list) or not variants_data:
        raise PipelineError("generation.frequency_variants must contain at least one table")
    variants: list[FrequencyVariant] = []
    for index, raw_variant in enumerate(variants_data):
        context = f"generation.frequency_variants[{index}]"
        if not isinstance(raw_variant, dict):
            raise PipelineError(f"{context} must be a TOML table")
        reject_unknown(
            raw_variant,
            {
                "name",
                "from_hz",
                "to_hz",
            },
            context,
        )
        from_hz = optional_int(raw_variant, "from_hz", context)
        to_hz = optional_int(raw_variant, "to_hz", context)
        if (from_hz is None) != (to_hz is None):
            raise PipelineError(f"{context}.from_hz and to_hz must be set together")
        if from_hz is not None and (
            from_hz <= 0
            or from_hz > DT_CELL_MAX
            or to_hz is None
            or to_hz <= 0
            or to_hz > DT_CELL_MAX
            or from_hz == to_hz
        ):
            raise PipelineError(
                f"{context} must define two different frequencies in the "
                "unsigned 32-bit DT cell range"
            )
        variants.append(
            FrequencyVariant(
                name=require_str(raw_variant, "name", context),
                source_hz=from_hz,
                target_hz=to_hz,
            )
        )
    if len({variant.name for variant in variants}) != len(variants):
        raise PipelineError("generation.frequency_variants names must be unique")
    return tuple(variants)


def parse_generation_config(data: Mapping[str, Any]) -> GenerationConfig:
    generation_table = require_table(data, "generation", "top-level")
    reject_unknown(
        generation_table,
        {
            "generic_steps",
            "aop_steps",
            "comparison_generic_step",
            "generic_profile_filename",
            "aop_profile_filename",
            "generic_diff_filename",
            "aop_diff_filename",
            "frequency_variants",
        },
        "generation",
    )
    generic_steps = require_int_list(generation_table, "generic_steps", "generation")
    aop_steps = require_int_list(generation_table, "aop_steps", "generation")
    if any(step <= 0 for step in generic_steps + aop_steps):
        raise PipelineError("generation step counts must be positive")
    if len(set(generic_steps)) != len(generic_steps) or len(set(aop_steps)) != len(aop_steps):
        raise PipelineError("generation step arrays must not contain duplicates")
    comparison_step = require_int(
        generation_table, "comparison_generic_step", "generation"
    )
    if comparison_step not in generic_steps:
        raise PipelineError("generation.comparison_generic_step must be generated")
    variants = parse_frequency_variants(generation_table)

    return GenerationConfig(
        generic_steps=generic_steps,
        aop_steps=aop_steps,
        comparison_generic_step=comparison_step,
        generic_profile_filename=require_str(
            generation_table, "generic_profile_filename", "generation"
        ),
        aop_profile_filename=require_str(
            generation_table, "aop_profile_filename", "generation"
        ),
        generic_diff_filename=require_str(
            generation_table, "generic_diff_filename", "generation"
        ),
        aop_diff_filename=require_str(
            generation_table, "aop_diff_filename", "generation"
        ),
        variants=variants,
    )


def load_config(firmware_directory: Path, config_file: Path) -> BuildConfig:
    config_path = config_file.resolve()
    try:
        with config_path.open("rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError as error:
        raise PipelineError(f"missing shared configuration: {config_path}") from error
    except UnicodeDecodeError as error:
        raise PipelineError(f"configuration is not valid UTF-8: {config_path}") from error
    except tomllib.TOMLDecodeError as error:
        raise PipelineError(f"invalid TOML in {config_path}: {error}") from error

    reject_unknown(
        data,
        {"schema_version", "project", "inputs", "target", "paths", "regulators", "generation"},
        "top-level",
    )
    if require_int(data, "schema_version", "top-level") != 1:
        raise PipelineError("unsupported schema_version; expected 1")
    root = firmware_directory.resolve()
    if not root.is_dir():
        raise PipelineError(f"firmware profile-set directory does not exist: {root}")

    source = parse_source_config(data, root)
    config = BuildConfig(
        config_path=config_path,
        root=root,
        firmware=parse_project_config(data, root),
        source=source,
        output=parse_output_config(data, root, source),
        regulators=parse_regulator_config(data),
        generation=parse_generation_config(data),
    )
    validate_output_paths(config)
    return config


def validate_output_paths(config: BuildConfig) -> None:
    stock_dir = config.output.stock_profile.parent
    tracked_dirs = (stock_dir, config.output.uv_dir)
    if config.output.stock_dts.parent != stock_dir:
        raise PipelineError("paths.stock and paths.stock_dts must share one output directory")
    if len(set(tracked_dirs)) != len(tracked_dirs):
        raise PipelineError("stock and UV output directories must be distinct")
    for directory in tracked_dirs:
        if directory.parent != config.root:
            raise PipelineError(
                "each tracked output directory must be a direct child of the "
                f"configuration directory: {directory}"
            )
        if any(
            source.is_relative_to(directory)
            for source in (config.source.vendor_boot, config.source.aop)
        ):
            raise PipelineError(
                f"generated output directory overlaps the source input: {directory}"
            )
    if (
        config.source.vendor_boot.parent != config.source.aop.parent
        or config.source.vendor_boot.parent.parent != config.root
    ):
        raise PipelineError(
            "firmware inputs must share a dedicated direct child directory"
        )
    source_dir = config.source.vendor_boot.parent
    if config.output.release_dir.parent != source_dir:
        raise PipelineError("release output must be a direct child of the source directory")
    if config.output.release_dir in tracked_dirs:
        raise PipelineError("release and tracked output directories must be distinct")
    candidates = [config.output.stock_profile, config.output.stock_dts]
    for family in profile_families(config.generation):
        for variant in config.generation.variants:
            common = {"frequency": variant.name}
            candidates.append(
                config.output.diff_dir
                / render_filename(
                    family.diff_filename,
                    f"generation.{family.method}_diff_filename",
                    **common,
                )
            )
            for step in family.steps:
                candidates.append(
                    config.output.uv_dir
                    / render_filename(
                        family.profile_filename,
                        f"generation.{family.method}_profile_filename",
                        steps=step,
                        frequency=variant.name,
                    )
                )
    candidates.append(config.output.release_report)
    if len(candidates) != len(set(candidates)):
        raise PipelineError("configured output paths collide")
    if config.source.vendor_boot in candidates or config.source.aop in candidates:
        raise PipelineError("refusing to overwrite a firmware source file")
    if config.config_path in candidates:
        raise PipelineError("refusing to overwrite the shared configuration file")


def align_up(value: int, alignment: int) -> int:
    return (value + alignment - 1) // alignment * alignment


def read_u32_le(data: bytes, offset: int, field: str) -> int:
    if offset + 4 > len(data):
        raise PipelineError(f"truncated vendor_boot header while reading {field}")
    return struct.unpack_from("<I", data, offset)[0]


def elf_load_regions(blob: bytes) -> tuple[tuple[int, int], ...]:
    """Validate the target AOP ELF and return its file-backed PT_LOAD ranges."""
    if len(blob) < 52 or not blob.startswith(b"\x7fELF"):
        raise PipelineError("aop.mbn must be an ELF32 AOP executable")
    if blob[4:7] != b"\x01\x01\x01":
        raise PipelineError("aop.mbn must use ELF32 little-endian version 1")
    elf_type, machine, elf_version = struct.unpack_from("<HHI", blob, 16)
    header_size, phentsize, phnum = struct.unpack_from("<HHH", blob, 40)
    if elf_type != 2 or machine != 0xF3 or elf_version != 1 or header_size != 52:
        raise PipelineError("aop.mbn is not a supported RISC-V ELF32 executable")
    if phentsize != 32:
        raise PipelineError("aop.mbn has an unexpected ELF32 program-header size")
    phoff = struct.unpack_from("<I", blob, 28)[0]

    def range_at(offset: int) -> tuple[int, int, int]:
        return (
            struct.unpack_from("<I", blob, offset)[0],
            struct.unpack_from("<I", blob, offset + 4)[0],
            struct.unpack_from("<I", blob, offset + 16)[0],
        )

    if phnum == 0 or phoff + phentsize * phnum > len(blob):
        raise PipelineError("aop.mbn has an invalid ELF program-header table")
    ranges: list[tuple[int, int]] = []
    for index in range(phnum):
        entry_offset = phoff + index * phentsize
        segment_type, file_offset, file_size = range_at(entry_offset)
        if segment_type != 1 or file_size == 0:
            continue
        end = file_offset + file_size
        if end > len(blob):
            raise PipelineError(f"aop.mbn ELF segment {index} exceeds the file")
        ranges.append((file_offset, end))
    if not ranges:
        raise PipelineError("aop.mbn ELF contains no file-backed PT_LOAD segments")
    return tuple(ranges)


def normalize_aop_values(raw: bytes) -> tuple[int, ...]:
    if len(raw) < 4 or len(raw) > 512 or len(raw) % 2:
        raise PipelineError("invalid gfx.lvl auxiliary-data length")
    padded = struct.unpack(f"<{len(raw) // 2}H", raw)
    used = [index for index, value in enumerate(padded) if value]
    if not used:
        raise PipelineError("empty gfx.lvl auxiliary-data table")
    values = tuple(padded[used[0] : used[-1] + 1])
    if (
        0 in values
        or len(values) < 2
        or any(left >= right for left, right in zip(values, values[1:]))
    ):
        raise PipelineError(
            "gfx.lvl values are not one strictly increasing non-zero sequence"
        )
    return values


def arc_entry_at(
    blob: bytes, offset: int, lower: int, upper: int
) -> tuple[bytes, int, int] | None:
    if offset < lower or offset + COMMAND_DB_ENTRY.size > upper:
        return None
    raw_id, _priority0, _priority1, address, length, data_offset = (
        COMMAND_DB_ENTRY.unpack_from(blob, offset)
    )
    name = raw_id.rstrip(b"\0")
    if (
        not name
        or raw_id[len(name) :] != bytes(8 - len(name))
        or not RESOURCE_ID_RE.fullmatch(name)
        or ((address >> 16) & 0x7) != 3
        or length > 512
        or length % 2
        or data_offset % 2
    ):
        return None
    return name, length, data_offset


def find_static_aop_tables(blob: bytes, resource: str) -> list[tuple[int, tuple[int, ...]]]:
    resource_name = resource.encode("ascii")
    resource_id = resource_name + bytes(8 - len(resource_name))
    found: list[tuple[int, tuple[int, ...]]] = []
    for lower, upper in elf_load_regions(blob):
        cursor = lower
        while True:
            hit = blob.find(resource_id, cursor, upper)
            if hit < 0:
                break
            cursor = hit + 1
            target = arc_entry_at(blob, hit, lower, upper)
            if target is None or target[0] != resource_name or target[1] == 0:
                continue
            first = hit
            while arc_entry_at(blob, first - COMMAND_DB_ENTRY.size, lower, upper):
                first -= COMMAND_DB_ENTRY.size
            end = hit + COMMAND_DB_ENTRY.size
            while arc_entry_at(blob, end, lower, upper):
                end += COMMAND_DB_ENTRY.size
            entries = [
                arc_entry_at(blob, offset, lower, upper)
                for offset in range(first, end, COMMAND_DB_ENTRY.size)
            ]
            if len(entries) < 3 or any(entry is None for entry in entries):
                continue
            valid_entries = [entry for entry in entries if entry is not None]
            expected_offset = 0
            contiguous = True
            for _name, length, data_offset in valid_entries:
                if data_offset != expected_offset:
                    contiguous = False
                    break
                expected_offset += length
            if not contiguous or first % 4:
                continue
            span = expected_offset
            data_base = first - span
            if data_base < lower or data_base % 4:
                continue
            _, length, data_offset = target
            try:
                values = normalize_aop_values(
                    blob[data_base + data_offset : data_base + data_offset + length]
                )
            except PipelineError:
                continue
            found.append((hit, values))
    return sorted(set(found))


def read_aop(path: Path, resource: str) -> AopInfo:
    try:
        blob = path.read_bytes()
    except FileNotFoundError as error:
        raise PipelineError(
            f"missing matching aop image: {path}; exact-AOP profiles require aop.mbn"
        ) from error
    candidates = find_static_aop_tables(blob, resource)
    if not candidates:
        raise PipelineError(
            f"aop.mbn contains no validated Command DB {resource!r} table"
        )
    grouped: dict[tuple[int, ...], list[int]] = {}
    for offset, values in candidates:
        grouped.setdefault(values, []).append(offset)
    if len(grouped) != 1:
        details = "; ".join(
            f"offsets {', '.join(f'0x{offset:x}' for offset in offsets)}: {values}"
            for values, offsets in grouped.items()
        )
        raise PipelineError(f"aop.mbn has conflicting {resource} tables: {details}")
    values, offsets = next(iter(grouped.items()))
    return AopInfo(
        size=len(blob),
        sha256=sha256_bytes(blob),
        values=values,
        offsets=tuple(offsets),
        template_count=len(offsets),
    )


def extract_dtb_section(image: bytes) -> tuple[VendorBootInfo, memoryview]:
    if image[:8] != VENDOR_BOOT_MAGIC:
        raise PipelineError("source is not an Android vendor_boot image")
    header_version = read_u32_le(image, 8, "header version")
    if header_version not in (3, 4):
        raise PipelineError(
            f"unsupported vendor_boot header version {header_version}; expected 3 or 4"
        )
    page_size = read_u32_le(image, 12, "page size")
    vendor_ramdisk_size = read_u32_le(image, 24, "vendor ramdisk size")
    header_size = read_u32_le(image, 2096, "header size")
    dtb_size = read_u32_le(image, 2100, "DTB size")
    if page_size not in VENDOR_BOOT_PAGE_SIZES:
        raise PipelineError(f"unsupported vendor_boot page size {page_size}")
    expected_header_size = VENDOR_BOOT_HEADER_SIZES[header_version]
    if header_size != expected_header_size:
        raise PipelineError(
            f"vendor_boot v{header_version} header size is {header_size}; "
            f"expected {expected_header_size}"
        )
    if dtb_size == 0:
        raise PipelineError("vendor_boot has an empty DTB section")
    table_size = 0
    bootconfig_size = 0
    if header_version == 4:
        table_size = read_u32_le(image, 2112, "vendor ramdisk table size")
        table_entries = read_u32_le(image, 2116, "vendor ramdisk table entry count")
        table_entry_size = read_u32_le(image, 2120, "vendor ramdisk table entry size")
        bootconfig_size = read_u32_le(image, 2124, "bootconfig size")
        if table_entry_size != 108 or table_size != table_entries * table_entry_size:
            raise PipelineError("vendor_boot v4 has an inconsistent vendor ramdisk table")
    dtb_offset = align_up(header_size, page_size) + align_up(vendor_ramdisk_size, page_size)
    dtb_end = dtb_offset + dtb_size
    if dtb_end > len(image):
        raise PipelineError(
            f"DTB section 0x{dtb_offset:x}..0x{dtb_end:x} exceeds image size"
        )
    table_offset = dtb_offset + align_up(dtb_size, page_size)
    bootconfig_offset = table_offset + align_up(table_size, page_size)
    payload_end = bootconfig_offset + align_up(bootconfig_size, page_size)
    if payload_end > len(image):
        raise PipelineError(
            f"vendor_boot payload ends at 0x{payload_end:x}, beyond image size"
        )
    return (
        VendorBootInfo(header_version, dtb_offset, dtb_size, payload_end),
        memoryview(image)[dtb_offset:dtb_end],
    )


def split_dtbs(section: bytes | memoryview) -> list[bytes]:
    blobs: list[bytes] = []
    position = 0
    while position < len(section):
        while position < len(section) and section[position] == 0:
            position += 1
        if position == len(section):
            break
        if section[position : position + 4] != FDT_MAGIC:
            preview = section[position : position + 16].hex(" ")
            raise PipelineError(
                f"invalid FDT magic at DTB-section offset 0x{position:x}: {preview}"
            )
        if position + 8 > len(section):
            raise PipelineError(f"truncated FDT header at offset 0x{position:x}")
        total_size = struct.unpack_from(">I", section, position + 4)[0]
        if total_size < 40 or position + total_size > len(section):
            raise PipelineError(
                f"invalid FDT size {total_size} at section offset 0x{position:x}"
            )
        blobs.append(bytes(section[position : position + total_size]))
        position += total_size
    if not blobs:
        raise PipelineError("vendor_boot DTB section contains no FDT blobs")
    return blobs


def decompile_dtb(blob: bytes, index: int) -> str:
    try:
        process = subprocess.run(
            ["dtc", "-q", "-I", "dtb", "-O", "dts"],
            input=blob,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError as error:
        raise PipelineError("dtc is required; install device-tree-compiler") from error
    if process.returncode != 0:
        message = process.stderr.decode("utf-8", errors="replace").strip()
        raise PipelineError(f"dtc could not decompile DTB #{index}: {message}")
    try:
        return process.stdout.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise PipelineError(f"dtc produced invalid UTF-8 for DTB #{index}") from error


def parse_dts_nodes(dts: str) -> tuple[list[str], list[DtsNode]]:
    lines = dts.splitlines()
    nodes: list[DtsNode] = []
    stack: list[int] = []
    for line_number, raw_line in enumerate(lines, 1):
        stripped = raw_line.strip()
        if match := DTS_NODE_RE.fullmatch(stripped):
            nodes.append(
                DtsNode(
                    name=match.group("name"),
                    start=line_number - 1,
                    end=None,
                    parent=stack[-1] if stack else None,
                    properties={},
                )
            )
            stack.append(len(nodes) - 1)
        elif stripped == "};":
            if not stack:
                raise PipelineError(f"unmatched DTS node terminator at line {line_number}")
            nodes[stack.pop()].end = line_number
        elif stack and (match := DTS_PROPERTY_RE.fullmatch(stripped)):
            name = match.group("name").strip()
            properties = nodes[stack[-1]].properties
            if name in properties:
                raise PipelineError(f"duplicate DTS property {name!r} at line {line_number}")
            properties[name] = match.group("value").strip()
    if stack:
        raise PipelineError("DTS ends inside a node")
    return lines, nodes


def string_property(node: DtsNode, name: str) -> tuple[str, ...]:
    raw = node.properties.get(name, "")
    return tuple(match.group(1) for match in DTS_STRING_RE.finditer(raw))


def cells_property(node: DtsNode, name: str) -> tuple[int, ...]:
    raw = node.properties.get(name, "")
    match = re.fullmatch(r"<([^>]*)>", raw)
    return tuple(parse_cells(match.group(1))) if match else ()


def node_is_enabled(nodes: list[DtsNode], node_index: int, stop: int | None = None) -> bool:
    current: int | None = node_index
    while current is not None:
        status = string_property(nodes[current], "status")
        if status and status[0] not in {"ok", "okay"}:
            return False
        if current == stop:
            break
        current = nodes[current].parent
    return True


def node_is_descendant(nodes: list[DtsNode], node_index: int, ancestor: int) -> bool:
    current = nodes[node_index].parent
    while current is not None:
        if current == ancestor:
            return True
        current = nodes[current].parent
    return False


def infer_chip(compatible: str, model: str) -> str:
    if compatible.startswith("qcom,"):
        return compatible.split(",", 1)[1]
    match = re.search(r"\b([A-Za-z][A-Za-z0-9_-]+)\s+(?:v\d+\s+)?SoC\b", model)
    return match.group(1).lower() if match else ""


def extract_gpu_table(
    lines: list[str], nodes: list[DtsNode], gpu_node: int
) -> tuple[str, tuple[int, ...], tuple[int, ...]]:
    blocks: list[str] = []
    group_ids: list[int] = []
    levels_per_group: list[int] = []
    for node_index, node in enumerate(nodes):
        match = re.fullmatch(r"qcom,gpu-pwrlevels-(\d+)", node.name)
        if not match:
            continue
        if not node_is_descendant(nodes, node_index, gpu_node):
            continue
        if not node_is_enabled(nodes, node_index, gpu_node):
            continue
        assert node.end is not None
        group_id = int(match.group(1))
        block = [f"qcom,gpu-pwrlevels-{group_id} {{"]
        group_level_count = 0
        for raw_line in lines[node.start + 1 : node.end]:
            stripped = raw_line.strip()
            level_match = GPU_LEVEL_RE.fullmatch(stripped)
            if level_match:
                stripped = f"qcom,gpu-pwrlevel@{int(level_match.group(1))} {{"
                group_level_count += 1
            if stripped:
                block.append(stripped)
        blocks.append("\n".join(block))
        group_ids.append(group_id)
        levels_per_group.append(group_level_count)
    if len(group_ids) != len(set(group_ids)):
        raise PipelineError("DTB contains duplicate qcom,gpu-pwrlevels-N IDs")
    table = "\n".join(blocks) + ("\n" if blocks else "")
    return table, tuple(group_ids), tuple(levels_per_group)


def inspect_dtb(blob: bytes, index: int) -> DtbCandidate:
    dts = decompile_dtb(blob, index)
    lines, nodes = parse_dts_nodes(dts)
    roots = [node for node in nodes if node.parent is None and node.name == "/"]
    if len(roots) != 1:
        raise PipelineError(f"DTB #{index} does not contain exactly one root node")
    root = roots[0]
    root_compatible = string_property(root, "compatible")
    root_model = (string_property(root, "model") or ("",))[0]
    gpu_nodes = [
        node_index
        for node_index, node in enumerate(nodes)
        if "qcom,kgsl-3d0" in string_property(node, "compatible")
        and node_is_enabled(nodes, node_index)
    ]
    if len(gpu_nodes) > 1:
        raise PipelineError(f"DTB #{index} contains multiple enabled KGSL GPU nodes")
    gpu_model = ""
    table = ""
    group_ids: tuple[int, ...] = ()
    levels_per_group: tuple[int, ...] = ()
    if gpu_nodes:
        gpu_node = gpu_nodes[0]
        models = string_property(nodes[gpu_node], "qcom,gpu-model")
        gpu_model = models[0] if models else ""
        table, group_ids, levels_per_group = extract_gpu_table(lines, nodes, gpu_node)
    soc_compatible = next(
        (value for value in root_compatible if value.startswith("qcom,")),
        root_compatible[0] if root_compatible else "",
    )
    return DtbCandidate(
        index=index,
        sha256=sha256_bytes(blob),
        model=root_model,
        compatibles=root_compatible,
        chip=infer_chip(soc_compatible, root_model),
        msm_id=cells_property(root, "qcom,msm-id"),
        gpu_model=gpu_model,
        table=table,
        group_count=len(group_ids),
        level_count=sum(levels_per_group),
        group_ids=group_ids,
        levels_per_group=levels_per_group,
    )


def inspect_candidates(blobs: list[bytes]) -> list[DtbCandidate]:
    return [inspect_dtb(blob, index) for index, blob in enumerate(blobs)]


def print_candidates(candidates: list[DtbCandidate]) -> None:
    for candidate in candidates:
        gpu = "no GPU table"
        if candidate.group_count:
            shape = ", ".join(
                f"{group_id}:{levels}"
                for group_id, levels in zip(
                    candidate.group_ids, candidate.levels_per_group, strict=True
                )
            )
            gpu = (
                f"{candidate.group_count} groups / {candidate.level_count} levels "
                f"({shape})"
            )
        model = candidate.model or candidate.compatible or "unknown model"
        print(f"#{candidate.index:02d}  chip={candidate.chip or 'unknown'}")
        print(
            "     compatible="
            + (", ".join(candidate.compatibles) if candidate.compatibles else "unknown")
        )
        print(f"     model={model}")
        print(
            "     msm-id="
            + (" ".join(f"0x{cell:x}" for cell in candidate.msm_id) or "unknown")
        )
        print(f"     gpu-model={candidate.gpu_model or 'unknown'}")
        print(f"     GPU={gpu}")
        print(f"     sha256={candidate.sha256}")


def parse_cells(text: str) -> list[int]:
    try:
        cells = [int(cell, 0) for cell in text.split()]
    except ValueError as error:
        raise PipelineError(f"GPU table contains a non-integer cell: {text!r}") from error
    if any(cell < 0 or cell > DT_CELL_MAX for cell in cells):
        raise PipelineError(
            f"GPU table cell is outside the unsigned 32-bit range: {text!r}"
        )
    return cells


def parse_groups(table: str) -> list[GroupInfo]:
    groups: list[GroupInfo] = []
    current_group: int | None = None
    current_header: dict[str, list[int]] = {}
    current_level: int | None = None
    current_properties: dict[str, list[int]] = {}
    rows: list[LevelRow] = []
    seen_levels: set[int] = set()

    for line_number, raw_line in enumerate(table.splitlines(), 1):
        line = raw_line.strip()
        if match := GROUP_RE.fullmatch(line):
            if current_group is not None:
                raise PipelineError(f"nested GPU group at table line {line_number}")
            current_group = int(match.group(1))
            current_header = {}
            rows = []
            seen_levels = set()
        elif match := LEVEL_RE.fullmatch(line):
            if current_group is None or current_level is not None:
                raise PipelineError(f"invalid power level at table line {line_number}")
            current_level = int(match.group(1))
            if current_level in seen_levels:
                raise PipelineError(
                    f"duplicate level {current_level} in group {current_group}"
                )
            seen_levels.add(current_level)
            current_properties = {}
        elif line == "};":
            if current_level is not None:
                try:
                    frequency_cells = current_properties["qcom,gpu-freq"]
                    vote_cells = current_properties["qcom,level"]
                except (KeyError, IndexError) as error:
                    raise PipelineError(
                        f"group {current_group} level {current_level} lacks frequency or vote"
                    ) from error
                if len(frequency_cells) != 1 or len(vote_cells) != 1:
                    raise PipelineError(
                        f"group {current_group} level {current_level} must use "
                        "single-cell qcom,gpu-freq and qcom,level properties"
                    )
                frequency = frequency_cells[0]
                vote = vote_cells[0]
                rows.append(LevelRow(current_level, frequency, vote))
                current_level = None
                current_properties = {}
            elif current_group is not None:
                groups.append(GroupInfo(current_group, current_header, rows))
                current_group = None
                current_header = {}
                rows = []
            else:
                raise PipelineError(f"unmatched terminator at table line {line_number}")
        elif match := PROPERTY_RE.fullmatch(line):
            name = match.group(1).strip()
            cells = parse_cells(match.group(2))
            properties = current_properties if current_level is not None else current_header
            if current_group is None:
                raise PipelineError(f"property outside a GPU group at table line {line_number}")
            if name in properties:
                raise PipelineError(
                    f"duplicate property {name!r} at table line {line_number}"
                )
            properties[name] = cells
        elif line:
            raise PipelineError(f"unrecognized table line {line_number}: {line}")
    if current_group is not None or current_level is not None:
        raise PipelineError("GPU table ends inside a node")
    if len({group.group for group in groups}) != len(groups):
        raise PipelineError("GPU table contains duplicate group IDs")
    return groups


def validate_frequency_ladders(groups: list[GroupInfo], context: str) -> None:
    """Require the descending, collision-free frequency order used by KGSL tables."""
    for group in groups:
        frequencies = [level.frequency for level in group.levels]
        if not frequencies:
            raise PipelineError(f"{context} group {group.group} has no power levels")
        for higher, lower in zip(frequencies, frequencies[1:]):
            if higher <= lower:
                raise PipelineError(
                    f"{context} group {group.group} frequencies must be strictly "
                    "descending and unique"
                )


def deterministic_gzip(data: bytes) -> bytes:
    buffer = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", compresslevel=9, fileobj=buffer, mtime=0) as stream:
        stream.write(data)
    compressed = bytearray(buffer.getvalue())
    compressed[9] = 255
    return bytes(compressed)


def encode_profile(chip: str, description: str, table: str) -> str:
    document = {"chip": chip, "desc": description, "freq": table}
    raw = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return URI_PREFIX + base64.b64encode(deterministic_gzip(raw)).decode("ascii")


def target_from_scale(
    vote: int, steps: int, scale: tuple[int, ...], label: str
) -> tuple[int, int]:
    try:
        index = scale.index(vote)
    except ValueError as error:
        raise PipelineError(f"stock vote {vote} is absent from {label}") from error
    target_index = max(0, index - steps)
    return scale[target_index], index - target_index


def effective_aop_vote(request: int, regulators: RegulatorConfig) -> int:
    for supported in regulators.aop_values:
        if supported >= request:
            return supported
    raise PipelineError(
        f"requested vote {request} exceeds the configured firmware AOP maximum"
    )


def effective_drop(stock: int, request: int, regulators: RegulatorConfig) -> tuple[int, int]:
    try:
        stock_index = regulators.aop_index[stock]
    except KeyError as error:
        raise PipelineError(f"stock vote {stock} is absent from the firmware AOP list") from error
    effective = effective_aop_vote(request, regulators)
    return effective, stock_index - regulators.aop_index[effective]


def profile_target(
    vote: int, steps: int, method: str, regulators: RegulatorConfig
) -> tuple[int, int]:
    if method == "generic":
        return target_from_scale(vote, steps, regulators.picker_values, "generic picker")
    if method == "aop":
        return target_from_scale(vote, steps, regulators.aop_values, "firmware AOP list")
    raise PipelineError(f"unknown profile method {method!r}")


def transform_table(
    stock: str,
    method: str,
    steps: int,
    variant: FrequencyVariant,
    regulators: RegulatorConfig,
) -> tuple[str, int, int, int]:
    vote_rows = 0
    vote_changes = 0
    frequency_changes = 0
    output: list[str] = []
    for line in stock.splitlines():
        if match := LEVEL_PROPERTY_RE.fullmatch(line):
            stock_vote = int(match.group("value"), 0)
            target, _ = profile_target(stock_vote, steps, method, regulators)
            line = f'{match.group("prefix")}0x{target:x}{match.group("suffix")}'
            vote_rows += 1
            vote_changes += target != stock_vote
        elif match := GPU_FREQUENCY_RE.fullmatch(line):
            stock_frequency = int(match.group("value"), 0)
            if variant.replacement and stock_frequency == variant.replacement[0]:
                target_frequency = variant.replacement[1]
                line = (
                    f'{match.group("prefix")}0x{target_frequency:x}'
                    f'{match.group("suffix")}'
                )
                frequency_changes += 1
        output.append(line)
    if vote_rows == 0:
        raise PipelineError("stock table contains no qcom,level properties")
    if variant.replacement and frequency_changes == 0:
        raise PipelineError(
            f"frequency variant {variant.name!r} found no "
            f"{variant.replacement[0]} Hz source rows"
        )
    transformed = "\n".join(output) + "\n"
    verify_transformation(stock, transformed, method, steps, variant, regulators)
    return transformed, vote_rows, vote_changes, frequency_changes


def verify_transformation(
    stock: str,
    transformed: str,
    method: str,
    steps: int,
    variant: FrequencyVariant,
    regulators: RegulatorConfig,
) -> None:
    before_lines = stock.splitlines()
    after_lines = transformed.splitlines()
    if len(before_lines) != len(after_lines):
        raise PipelineError("profile transformation changed the table line count")
    for line_number, (before, after) in enumerate(zip(before_lines, after_lines), 1):
        before_vote = LEVEL_PROPERTY_RE.fullmatch(before)
        after_vote = LEVEL_PROPERTY_RE.fullmatch(after)
        before_frequency = GPU_FREQUENCY_RE.fullmatch(before)
        after_frequency = GPU_FREQUENCY_RE.fullmatch(after)
        if before_vote:
            expected, _ = profile_target(
                int(before_vote.group("value"), 0), steps, method, regulators
            )
            if not after_vote or int(after_vote.group("value"), 0) != expected:
                raise PipelineError(f"incorrect vote transformation at line {line_number}")
        elif before_frequency:
            stock_frequency = int(before_frequency.group("value"), 0)
            expected = stock_frequency
            if variant.replacement and stock_frequency == variant.replacement[0]:
                expected = variant.replacement[1]
            if not after_frequency or int(after_frequency.group("value"), 0) != expected:
                raise PipelineError(f"incorrect frequency transformation at line {line_number}")
        elif before != after:
            raise PipelineError(f"unconfigured table change at line {line_number}")
    before_groups = parse_groups(stock)
    after_groups = parse_groups(transformed)
    if [group.group for group in after_groups] != [group.group for group in before_groups]:
        raise PipelineError("profile transformation changed GPU group order")
    for before_group, after_group in zip(before_groups, after_groups, strict=True):
        if [level.level for level in after_group.levels] != [
            level.level for level in before_group.levels
        ]:
            raise PipelineError(
                f"profile transformation changed level order in group {before_group.group}"
            )
    validate_frequency_ladders(after_groups, "generated profile")


def format_mhz(frequency: int) -> str:
    if frequency % 1_000_000 == 0:
        return str(frequency // 1_000_000)
    return f"{frequency / 1_000_000:.6f}".rstrip("0").rstrip(".")


def format_vote(vote: int, regulators: RegulatorConfig) -> str:
    name = regulators.picker_name.get(vote, "UNKNOWN")
    return f"`0x{vote:x}` · `{name}`"


def format_cells(cells: list[int] | None) -> str:
    if not cells:
        return "—"
    return " ".join(f"0x{cell:x}" for cell in cells)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def extract_avb_properties(
    image: bytes, vendor_boot_info: VendorBootInfo
) -> dict[str, str]:
    """Read AVB property descriptors embedded in an image with an AVB footer."""
    if len(image) < 64 or image[-64:-60] != b"AVBf":
        return {}
    try:
        _magic, _major, _minor, original_size, vbmeta_offset, vbmeta_size = (
            struct.unpack("!4sIIQQQ28x", image[-64:])
        )
    except struct.error as error:
        raise PipelineError("vendor_boot has a malformed AVB footer") from error
    if vbmeta_size < 256 or vbmeta_offset + vbmeta_size > len(image):
        raise PipelineError("vendor_boot AVB footer points outside the image")
    minimum_covered_size = vendor_boot_info.payload_end
    header = image[vbmeta_offset : vbmeta_offset + 256]
    if header[:4] != b"AVB0":
        raise PipelineError("vendor_boot AVB footer does not point to an AVB0 header")
    authentication_size = struct.unpack_from("!Q", header, 12)[0]
    auxiliary_size = struct.unpack_from("!Q", header, 20)[0]
    descriptors_offset = struct.unpack_from("!Q", header, 96)[0]
    descriptors_size = struct.unpack_from("!Q", header, 104)[0]
    auxiliary_start = vbmeta_offset + 256 + authentication_size
    auxiliary_end = auxiliary_start + auxiliary_size
    descriptor_start = auxiliary_start + descriptors_offset
    descriptor_end = descriptor_start + descriptors_size
    if auxiliary_end > vbmeta_offset + vbmeta_size or descriptor_end > auxiliary_end:
        raise PipelineError("vendor_boot has invalid AVB auxiliary-data bounds")
    properties: dict[str, str] = {}
    verified_self_hashes = 0
    cursor = descriptor_start
    while cursor < descriptor_end:
        if cursor + 16 > descriptor_end:
            raise PipelineError("vendor_boot has a truncated AVB descriptor")
        tag, following = struct.unpack_from("!QQ", image, cursor)
        end = cursor + 16 + following
        if following % 8 or end > descriptor_end:
            raise PipelineError("vendor_boot has an invalid AVB descriptor size")
        if tag == 0:
            if following < 16:
                raise PipelineError("vendor_boot has a truncated AVB property descriptor")
            key_size, value_size = struct.unpack_from("!QQ", image, cursor + 16)
            expected_following = align_up(16 + key_size + 1 + value_size + 1, 8)
            if following != expected_following:
                raise PipelineError("vendor_boot has an invalid AVB property size")
            payload = cursor + 32
            key_end = payload + key_size
            value_start = key_end + 1
            value_end = value_start + value_size
            if (
                value_end + 1 > end
                or image[key_end : key_end + 1] != b"\0"
                or image[value_end : value_end + 1] != b"\0"
            ):
                raise PipelineError("vendor_boot has a malformed AVB property payload")
            try:
                key = image[payload:key_end].decode("utf-8")
                value = image[value_start:value_end].decode("utf-8")
            except UnicodeDecodeError as error:
                raise PipelineError("vendor_boot AVB property is not valid UTF-8") from error
            if key in properties and properties[key] != value:
                raise PipelineError(f"vendor_boot repeats AVB property {key!r}")
            properties[key] = value
        elif tag == 2:
            if cursor + 132 > end:
                raise PipelineError("vendor_boot has a truncated AVB hash descriptor")
            (
                _tag,
                _following,
                image_size,
                raw_algorithm,
                partition_name_size,
                salt_size,
                digest_size,
                _flags,
                _reserved,
            ) = struct.unpack_from("!QQQ32sIIII60s", image, cursor)
            expected_following = align_up(
                116 + partition_name_size + salt_size + digest_size, 8
            )
            if following != expected_following:
                raise PipelineError("vendor_boot has an invalid AVB hash descriptor size")
            payload = cursor + 132
            partition_end = payload + partition_name_size
            salt_end = partition_end + salt_size
            digest_end = salt_end + digest_size
            if digest_end > end:
                raise PipelineError("vendor_boot has an out-of-bounds AVB hash payload")
            try:
                algorithm = raw_algorithm.rstrip(b"\0").decode("ascii")
                partition_name = image[payload:partition_end].decode("utf-8")
            except UnicodeDecodeError as error:
                raise PipelineError("vendor_boot AVB hash metadata is not valid text") from error
            if partition_name == "vendor_boot":
                if (
                    image_size != original_size
                    or image_size < minimum_covered_size
                    or image_size > vbmeta_offset
                ):
                    raise PipelineError("vendor_boot AVB hash covers an unexpected image size")
                salt = image[partition_end:salt_end]
                expected_digest = image[salt_end:digest_end]
                if algorithm not in {"sha256", "sha512"}:
                    raise PipelineError(
                        f"vendor_boot uses unsupported AVB hash {algorithm!r}"
                    )
                digest_builder = hashlib.new(algorithm)
                digest_builder.update(salt)
                digest_builder.update(memoryview(image)[:image_size])
                digest = digest_builder.digest()
                if not expected_digest or len(expected_digest) != len(digest):
                    raise PipelineError("vendor_boot AVB hash digest has an invalid length")
                if digest != expected_digest:
                    raise PipelineError(
                        "vendor_boot content does not match its embedded AVB hash descriptor"
                    )
                verified_self_hashes += 1
        cursor = end
    if verified_self_hashes == 0:
        raise PipelineError("vendor_boot AVB metadata has no supported body self-hash")
    return properties


def apply_firmware_identity(
    config: BuildConfig,
    image: bytes,
    vendor_boot_info: VendorBootInfo,
    *,
    required: bool,
) -> BuildConfig:
    properties = extract_avb_properties(image, vendor_boot_info)
    fingerprint = properties.get(config.source.fingerprint_property, "")
    if not fingerprint:
        if required:
            raise PipelineError(
                "vendor_boot has no configured AVB fingerprint property; "
                "a source-backed build requires parseable identity metadata"
            )
        print(
            "warning: vendor_boot has no configured AVB fingerprint property; "
            "inspection cannot validate the firmware directory name",
            file=sys.stderr,
        )
        return config
    try:
        product_path, build_part, build_variant = fingerprint.split(":", 2)
        brand, product, device = product_path.split("/", 2)
        _vendor_release, _build_id, incremental = build_part.split("/", 2)
        _build_type, _tags = build_variant.split("/", 1)
    except ValueError as error:
        raise PipelineError(
            f"cannot parse vendor_boot fingerprint {fingerprint!r}"
        ) from error
    if f"{brand}/{product}/{device}" != config.firmware.fingerprint_identity:
        raise PipelineError(
            "vendor_boot fingerprint does not match the configured stock product identity"
        )
    if build_variant != config.firmware.fingerprint_variant:
        raise PipelineError(
            "vendor_boot fingerprint is not the configured stock build variant"
        )
    match = re.fullmatch(
        r"ZUI_(?P<version>[0-9]+(?:\.[0-9]+)+)_"
        r"(?P<date>[0-9]{6})_(?P<region>[A-Za-z0-9]+)",
        incremental,
    )
    if not match:
        raise PipelineError(
            f"vendor_boot fingerprint has unsupported build token {incremental!r}"
        )
    values = {
        "device": device,
        "region": match.group("region"),
        "version": match.group("version"),
    }
    canonical_id = render_template(
        config.firmware.id_template, "project.firmware_id_template", **values
    )
    if canonical_id != config.firmware.firmware_id:
        raise PipelineError(
            "firmware directory does not match the source fingerprint: "
            f"expected {canonical_id!r}, found {config.firmware.firmware_id!r}"
        )
    if values["version"] != config.firmware.version:
        raise PipelineError("directory and vendor_boot fingerprint versions differ")
    return replace(
        config,
        firmware=replace(
            config.firmware,
            vendor_fingerprint=fingerprint,
        ),
    )


def count_effective_drops(
    groups: list[GroupInfo], method: str, steps: int, regulators: RegulatorConfig
) -> dict[int, int]:
    counts: Counter[int] = Counter()
    for group in groups:
        for level in group.levels:
            request, _ = profile_target(level.stock_vote, steps, method, regulators)
            _, drop = effective_drop(level.stock_vote, request, regulators)
            counts[drop] += 1
    return dict(sorted(counts.items()))


def compare_profiles(
    groups: list[GroupInfo],
    left_method: str,
    left_steps: int,
    right_method: str,
    right_steps: int,
    regulators: RegulatorConfig,
) -> dict[str, int]:
    counts = {"less": 0, "same": 0, "more": 0}
    for group in groups:
        for level in group.levels:
            left_request, _ = profile_target(
                level.stock_vote, left_steps, left_method, regulators
            )
            right_request, _ = profile_target(
                level.stock_vote, right_steps, right_method, regulators
            )
            _, left_drop = effective_drop(level.stock_vote, left_request, regulators)
            _, right_drop = effective_drop(level.stock_vote, right_request, regulators)
            if left_drop < right_drop:
                counts["less"] += 1
            elif left_drop > right_drop:
                counts["more"] += 1
            else:
                counts["same"] += 1
    return counts


def frequency_change_summary(variant: FrequencyVariant) -> str:
    if not variant.replacement:
        return "stock frequencies"
    source, target = variant.replacement
    return f"{format_mhz(source)} → {format_mhz(target)} MHz marker"


def render_diff_summary(
    config: BuildConfig,
    method: str,
    variant: FrequencyVariant,
    profiles: list[GeneratedProfile],
    groups: list[GroupInfo],
    stock_profile: str,
) -> list[str]:
    steps_label = " / ".join(f"-{profile.steps}" for profile in profiles)
    family_label = (
        "Generic picker"
        if method == "generic"
        else "Exact AOP"
    )
    marker_prefix = ""
    if variant.replacement:
        marker_prefix = f"{format_mhz(variant.replacement[1])} MHz marker + "
    title = f"# Stock → {marker_prefix}{family_label} {steps_label}"
    total_rows = sum(len(group.levels) for group in groups)
    lines = [
        title,
        "",
        f"Device: {config.firmware.device}; firmware: `{config.firmware.label}`.",
        f"Platform: `{config.source.chip}`; selected DTB: `#{config.source.dtb_index}`.",
        "All profiles preserve group metadata, bus votes, ACD values, dependency",
        "votes, SKU and speed-bin bindings, row order, and level counts.",
        "",
        f"- `qcom,level` rows checked: **{total_rows}**",
        f"- frequency rule: **{frequency_change_summary(variant)}**",
        f"- changed `qcom,gpu-freq` cells: **{profiles[0].frequency_changes}**",
        f"- SHA-256 of source `{config.output.stock_profile.name}`: "
        f"`{sha256_text(stock_profile)}`",
    ]
    for profile in profiles:
        zero_label = "floor" if method == "aop" else "modeled unchanged"
        distribution = ", ".join(
            f"{count} × -{drop}" if drop else f"{count} × 0 ({zero_label})"
            for drop, count in sorted(profile.drop_distribution.items(), reverse=True)
        )
        lines.extend(
            [
                f"- -{profile.steps} changed votes: **{profile.vote_changes}**",
                f"- -{profile.steps} modeled AOP shifts: **{distribution}**",
                f"- SHA-256 of the -{profile.steps} profile: `{sha256_text(profile.text)}`",
            ]
        )
    return lines


def render_diff_transformation(
    method: str, regulators: RegulatorConfig
) -> list[str]:
    lines = ["", "## Transformation", ""]
    if method == "generic":
        lines.extend(
            [
                "Each stock vote is moved down by the configured number of positions",
                "in the generic KonaBess/LTBox picker and clamped at its floor.",
                "Unsupported requests are modeled with the public Qualcomm Gen7 ceiling",
                "rule: the first recovered firmware AOP value greater than or equal to",
                "the request. This does not verify that Lenovo's packaged KGSL binary",
                "matches the cited public implementation.",
            ]
        )
    else:
        lines.extend(
            [
                "Each stock vote is moved down by the configured number of positions",
                "in the exact firmware `gfx.lvl` list and clamped at its floor.",
                "Every request occurs in the recovered firmware AOP list, so this",
                "comparison does not require the unsupported-request ceiling model.",
            ]
        )
    lines.extend(
        [
            "",
            f"AOP provenance: {regulators.aop_provenance}; SHA-256:",
            f"`{regulators.aop_source_sha256}`.",
            "",
            "Recovered active firmware `gfx.lvl` values:",
            "",
            "```text",
            ", ".join(str(vote) for vote in regulators.aop_values),
            "```",
            "",
        ]
    )
    return lines


def render_diff_effective_mapping(
    config: BuildConfig,
    method: str,
    profiles: list[GeneratedProfile],
    groups: list[GroupInfo],
) -> list[str]:
    regulators = config.regulators
    lines: list[str] = []
    if method == "generic":
        headers = ["Stock"]
        rules = ["---:"]
        for profile in profiles:
            headers.extend(
                [
                    f"Requested -{profile.steps}",
                    f"Modeled -{profile.steps}",
                    "AOP positions down",
                ]
            )
            rules.extend(["---:", "---:", "---:"])
        lines.extend(["| " + " | ".join(headers) + " |", "|" + "|".join(rules) + "|"])
        unique_votes = sorted(
            {level.stock_vote for group in groups for level in group.levels}, reverse=True
        )
        for stock_vote in unique_votes:
            cells = [str(stock_vote)]
            for profile in profiles:
                request, _ = profile_target(
                    stock_vote, profile.steps, method, regulators
                )
                effective, drop = effective_drop(stock_vote, request, regulators)
                cells.extend([str(request), str(effective), str(drop)])
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")
    else:
        reference = config.generation.comparison_generic_step
        lines.extend(
            [
                f"Compared with the modeled Generic picker -{reference} profile:",
                "",
                "| Exact profile | Less aggressive rows | Identical rows | More aggressive rows |",
                "|---|---:|---:|---:|",
            ]
        )
        for profile in profiles:
            comparison = compare_profiles(
                groups,
                "aop",
                profile.steps,
                "generic",
                reference,
                regulators,
            )
            lines.append(
                f"| AOP -{profile.steps} | {comparison['less']} | "
                f"{comparison['same']} | {comparison['more']} |"
            )
        lines.append("")
    return lines


def render_diff_group(
    group: GroupInfo,
    method: str,
    variant: FrequencyVariant,
    profiles: list[GeneratedProfile],
    regulators: RegulatorConfig,
) -> list[str]:
    if variant.replacement:
        header = ["ID", "Stock MHz", "Profile MHz", "Stock vote"]
        rule = ["---:", "---:", "---:", "---"]
    else:
        header = ["ID", "Frequency, MHz", "Stock vote"]
        rule = ["---:", "---:", "---"]
    for profile in profiles:
        header.extend([f"Profile -{profile.steps}", "AOP positions down"])
        rule.extend(["---", "---:"])
    lines = [
        f"## `qcom,gpu-pwrlevels-{group.group}`",
        "",
        f"- `initial-pwrlevel`: {format_cells(group.header.get('qcom,initial-pwrlevel'))}",
        f"- `speed-bin`: {format_cells(group.header.get('qcom,speed-bin'))}",
        f"- `sku-codes`: {format_cells(group.header.get('qcom,sku-codes'))}",
        "",
        "| " + " | ".join(header) + " |",
        "|" + "|".join(rule) + "|",
    ]
    for level in group.levels:
        cells = [str(level.level), format_mhz(level.frequency)]
        if variant.replacement:
            profile_frequency = (
                variant.replacement[1]
                if level.frequency == variant.replacement[0]
                else level.frequency
            )
            cells.append(format_mhz(profile_frequency))
        cells.append(format_vote(level.stock_vote, regulators))
        for profile in profiles:
            request, _ = profile_target(
                level.stock_vote, profile.steps, method, regulators
            )
            _, drop = effective_drop(level.stock_vote, request, regulators)
            cells.extend([format_vote(request, regulators), str(drop)])
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    return lines


def render_diff_notes(
    method: str,
    variant: FrequencyVariant,
    profiles: list[GeneratedProfile],
    groups: list[GroupInfo],
    regulators: RegulatorConfig,
) -> list[str]:
    lines = [
        "## Important",
        "",
        "`qcom,level` is an encoded regulator-vote identifier, not millivolts.",
        "One list position is not a fixed voltage delta.",
    ]
    if method == "generic":
        low_requests = sorted(
            {
                profile_target(level.stock_vote, profile.steps, method, regulators)[0]
                for profile in profiles
                for group in groups
                for level in group.levels
                if profile_target(level.stock_vote, profile.steps, method, regulators)[0]
                < regulators.ltbox_observed_min_vote
            }
        )
        if low_requests:
            mapped = ", ".join(
                f"{value} → {effective_aop_vote(value, regulators)}"
                for value in low_requests
            )
            lines.extend(
                [
                    "",
                    "LTBox may report non-blocking outside-stock-range advisories for",
                    "the configured low requests. Modeled public-rule mappings:",
                    f"`{mapped}`.",
                ]
            )
    if variant.replacement:
        source_hz, target_hz = variant.replacement
        delta = (target_hz - source_hz) / source_hz * 100
        lines.extend(
            [
                "",
                f"The {format_mhz(source_hz)} → {format_mhz(target_hz)} MHz change is a",
                f"{delta:.4f}% diagnostic marker, not a performance optimization.",
            ]
        )
    lines.append("")
    return lines


def build_diff(
    config: BuildConfig,
    method: str,
    variant: FrequencyVariant,
    profiles: list[GeneratedProfile],
    groups: list[GroupInfo],
    stock_profile: str,
) -> str:
    output = render_diff_summary(
        config, method, variant, profiles, groups, stock_profile
    )
    output.extend(render_diff_transformation(method, config.regulators))
    output.extend(render_diff_effective_mapping(config, method, profiles, groups))
    for group in groups:
        output.extend(
            render_diff_group(
                group, method, variant, profiles, config.regulators
            )
        )
    output.extend(
        render_diff_notes(
            method, variant, profiles, groups, config.regulators
        )
    )
    return "\n".join(output)


def candidate_matches(config: BuildConfig, candidate: DtbCandidate) -> bool:
    return (
        config.source.compatible in candidate.compatibles
        and candidate.msm_id == config.source.msm_id
        and candidate.gpu_model == config.source.gpu_model
        and bool(candidate.table)
    )


def select_candidate(
    config: BuildConfig,
    candidates: list[DtbCandidate],
    requested_index: int | None,
) -> DtbCandidate:
    matches = [candidate for candidate in candidates if candidate_matches(config, candidate)]
    if requested_index is not None:
        if requested_index < 0 or requested_index >= len(candidates):
            raise PipelineError(
                f"DTB index {requested_index} is outside 0..{len(candidates) - 1}"
            )
        candidate = candidates[requested_index]
        if candidate not in matches:
            raise PipelineError(
                f"DTB #{requested_index} does not match target compatible="
                f"{config.source.compatible!r}, msm-id={config.source.msm_id}, "
                f"gpu-model={config.source.gpu_model!r}"
            )
        return candidate
    if not matches:
        raise PipelineError(
            "no GPU DTB matches target compatible="
            f"{config.source.compatible!r}, msm-id={config.source.msm_id}, "
            f"gpu-model={config.source.gpu_model!r}; run --inspect"
        )
    tables = {candidate.table for candidate in matches}
    if len(tables) != 1:
        indices = ", ".join(f"#{candidate.index}" for candidate in matches)
        raise PipelineError(
            f"multiple target DTBs have different GPU tables ({indices}); "
            "rerun with an explicit --dtb-index"
        )
    selected = min(matches, key=lambda candidate: candidate.index)
    return selected


def validate_selected(config: BuildConfig, candidate: DtbCandidate) -> list[GroupInfo]:
    if not candidate.table:
        raise PipelineError(f"DTB #{candidate.index} has no GPU power table")
    groups = parse_groups(candidate.table)
    validate_frequency_ladders(groups, "stock table")
    stock_votes = {level.stock_vote for group in groups for level in group.levels}
    missing_picker = sorted(stock_votes - set(config.regulators.picker_values))
    missing_aop = sorted(stock_votes - set(config.regulators.aop_values))
    if missing_picker:
        raise PipelineError(f"stock votes absent from generic picker: {missing_picker}")
    if missing_aop:
        raise PipelineError(f"stock votes absent from firmware AOP list: {missing_aop}")
    return groups


def profile_description(
    config: BuildConfig,
    method: str,
    steps: int,
    variant: FrequencyVariant,
) -> str:
    marker = ""
    if variant.target_hz is not None:
        marker = f"{format_mhz(variant.target_hz)} MHz marker and "
    if method == "generic":
        transformation = f"generic picker -{steps}"
    else:
        level_word = "level" if steps == 1 else "levels"
        transformation = f"exact -{steps} firmware AOP GFX {level_word}"
    return (
        f"{config.firmware.profile_label} stock DTB "
        f"#{config.source.dtb_index}, {marker}{transformation}"
    )


def format_drop_distribution(
    distribution: Mapping[int, int], *, zero_label: str
) -> str:
    return "; ".join(
        f"{count} × -{drop}" if drop else f"{count} × 0 ({zero_label})"
        for drop, count in sorted(distribution.items(), reverse=True)
    )


def artifact_name(config: BuildConfig, path: Path) -> str:
    try:
        return path.relative_to(config.root).as_posix()
    except ValueError as error:
        raise PipelineError(f"generated artifact escapes the profile set: {path}") from error


def render_artifact_manifest(
    config: BuildConfig, outputs: Mapping[Path, bytes]
) -> list[str]:
    lines = [
        "| Artifact | Size, bytes | SHA-256 |",
        "|---|---:|---|",
    ]
    for path, data in sorted(
        outputs.items(), key=lambda item: artifact_name(config, item[0])
    ):
        lines.append(
            f"| `{artifact_name(config, path)}` | {len(data)} | `{sha256_bytes(data)}` |"
        )
    return lines


def render_release_report(
    config: BuildConfig,
    candidate: DtbCandidate,
    groups: list[GroupInfo],
    *,
    vendor_boot_size: int,
    vendor_boot_sha256: str,
    vendor_boot_info: VendorBootInfo,
    dtb_count: int,
    aop: AopInfo,
    tracked_outputs: Mapping[Path, bytes],
    diff_outputs: Mapping[Path, bytes],
    generated: Mapping[tuple[str, str], list[GeneratedProfile]],
) -> str:
    compatibles = ", ".join(f"`{value}`" for value in candidate.compatibles)
    msm_id = " ".join(f"0x{value:x}" for value in candidate.msm_id)
    group_shape = " / ".join(str(count) for count in candidate.levels_per_group)
    aop_offsets = ", ".join(f"0x{offset:x}" for offset in aop.offsets)
    lines = [
        f"# Release evidence: {config.firmware.label}",
        "",
        "This deterministic report is generated from the two local firmware inputs",
        "and the shared repository policy. It contains source- and policy-derived",
        "facts only; it includes no manually supplied test evidence.",
        "",
        "## Source-derived identity",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Firmware set | `{config.firmware.firmware_id}` |",
        f"| Vendor fingerprint | `{config.firmware.vendor_fingerprint}` |",
        f"| `{config.source.vendor_boot.name}` | {vendor_boot_size} bytes; "
        f"SHA-256 `{vendor_boot_sha256}` |",
        f"| Vendor boot structure | v{vendor_boot_info.header_version}; "
        f"{dtb_count} DTBs; DTB section offset `0x{vendor_boot_info.dtb_offset:x}`, "
        f"size {vendor_boot_info.dtb_size} bytes |",
        "| Embedded vendor-boot body hash | Internally consistent; this is an "
        "integrity check, not source authentication |",
        f"| Selected DTB | `#{candidate.index}`; SHA-256 `{candidate.sha256}` |",
        f"| DTB identity | `{candidate.model}`; {compatibles}; MSM ID `{msm_id}`; "
        f"GPU `{candidate.gpu_model}` |",
        f"| GPU table | {candidate.group_count} groups, {candidate.level_count} "
        f"levels; group shape `{group_shape}` |",
        f"| `{config.source.aop.name}` | {aop.size} bytes; SHA-256 `{aop.sha256}` |",
        f"| AOP resource | `{config.regulators.aop_resource}`; "
        f"{aop.template_count} identical templates at `{aop_offsets}` |",
        "",
        "Active firmware AOP values:",
        "",
        "```text",
        ", ".join(str(value) for value in aop.values),
        "```",
        "",
        "Every stock `qcom,level` value is present in this active sequence.",
        "",
        "## Tracked artifact manifest",
        "",
    ]
    lines.extend(render_artifact_manifest(config, tracked_outputs))
    lines.extend(
        [
            "",
            "## Transformation summary",
            "",
            "Modeled shifts are positions in the recovered firmware AOP list, not",
            "millivolts. Generic rows use the public Qualcomm Gen7 ceiling rule;",
            "this does not verify Lenovo's packaged KGSL binary. Generic and exact-AOP",
            "step numbers are not interchangeable.",
            "",
            "| Profile | Family | Frequency rule | Changed votes | Changed "
            "frequencies | Modeled AOP shifts |",
            "|---|---|---|---:|---:|---|",
        ]
    )
    for family in profile_families(config.generation):
        family_label = "Generic picker" if family.method == "generic" else "Exact AOP"
        zero_label = "modeled unchanged" if family.method == "generic" else "floor"
        for variant in config.generation.variants:
            for profile in generated[(family.method, variant.name)]:
                filename = render_filename(
                    family.profile_filename,
                    f"generation.{family.method}_profile_filename",
                    steps=profile.steps,
                    frequency=variant.name,
                )
                path = config.output.uv_dir / filename
                distribution = format_drop_distribution(
                    profile.drop_distribution, zero_label=zero_label
                )
                lines.append(
                    f"| `{artifact_name(config, path)}` | {family_label} -{profile.steps} "
                    f"| {frequency_change_summary(variant)} | {profile.vote_changes} | "
                    f"{profile.frequency_changes} | {distribution} |"
                )
    lines.extend(
        [
            "",
            "## Cross-family modeled comparison",
            "",
            "Each cell compares the exact-AOP row with the named generic-picker row",
            "under the public Qualcomm Gen7 ceiling model: `less / same / more`",
            "aggressive. This is not verified packaged-driver behavior. Frequency",
            "markers do not affect these values, so the comparison covers both variants.",
            "",
            "| Exact profile | "
            + " | ".join(
                f"Generic -{step}" for step in config.generation.generic_steps
            )
            + " |",
            "|---|" + "---:|" * len(config.generation.generic_steps),
        ]
    )
    for aop_step in config.generation.aop_steps:
        cells = []
        for generic_step in config.generation.generic_steps:
            comparison = compare_profiles(
                groups,
                "aop",
                aop_step,
                "generic",
                generic_step,
                config.regulators,
            )
            cells.append(
                f"{comparison['less']} / {comparison['same']} / {comparison['more']}"
            )
        lines.append(f"| Exact AOP -{aop_step} | " + " | ".join(cells) + " |")
    lines.extend(
        [
            "",
            "## Local diff manifest",
            "",
        ]
    )
    lines.extend(render_artifact_manifest(config, diff_outputs))
    lines.extend(
        [
            "",
            "## Evidence boundary",
            "",
            "This report establishes reproducible parsing, source-file hashes, selected",
            "DTB identity and table shape, recovered AOP data, declared transformations,",
            "and deterministic artifact bytes for the supplied local inputs.",
            "",
            "It does not authenticate the firmware package or prove that the two inputs",
            "came from one package. It also does not establish Android release metadata,",
            "the exact packaged KGSL implementation, LTBox/KonaBess integration behavior,",
            "or electrical stability on a physical device. Those require separate",
            "release-specific evidence and device testing.",
            "",
        ]
    )
    return "\n".join(lines)


def build_outputs(
    config: BuildConfig,
    candidate: DtbCandidate,
    groups: list[GroupInfo],
    *,
    vendor_boot_size: int,
    vendor_boot_sha256: str,
    vendor_boot_info: VendorBootInfo,
    dtb_count: int,
    aop: AopInfo,
    include_release_evidence: bool,
) -> BuildArtifacts:
    stock_description = (
        f"{config.firmware.profile_label} stock, DTB #{config.source.dtb_index}"
    )
    stock_profile = encode_profile(
        config.source.chip, stock_description, candidate.table
    )
    tracked: dict[Path, bytes] = {
        config.output.stock_profile: stock_profile.encode("utf-8"),
        config.output.stock_dts: candidate.table.encode("utf-8"),
    }
    generated: dict[tuple[str, str], list[GeneratedProfile]] = {}
    expected_vote_rows = sum(len(group.levels) for group in groups)
    for family in profile_families(config.generation):
        for variant in config.generation.variants:
            profiles: list[GeneratedProfile] = []
            for steps in family.steps:
                table, vote_rows, vote_changes, frequency_changes = transform_table(
                    candidate.table, family.method, steps, variant, config.regulators
                )
                if vote_rows != expected_vote_rows:
                    raise PipelineError(
                        f"{family.method} -{steps} transformed {vote_rows} vote rows; "
                        f"expected {expected_vote_rows}"
                    )
                description = profile_description(
                    config, family.method, steps, variant
                )
                profile_text = encode_profile(config.source.chip, description, table)
                filename = render_filename(
                    family.profile_filename,
                    f"generation.{family.method}_profile_filename",
                    steps=steps,
                    frequency=variant.name,
                )
                path = config.output.uv_dir / filename
                profile = GeneratedProfile(
                    steps=steps,
                    text=profile_text,
                    vote_changes=vote_changes,
                    frequency_changes=frequency_changes,
                    drop_distribution=count_effective_drops(
                        groups, family.method, steps, config.regulators
                    ),
                )
                profiles.append(profile)
                tracked[path] = profile_text.encode("utf-8")
            generated[(family.method, variant.name)] = profiles

    release: dict[Path, bytes] = {}
    if not include_release_evidence:
        return BuildArtifacts(tracked=tracked, release=release)
    for family in profile_families(config.generation):
        for variant in config.generation.variants:
            filename = render_filename(
                family.diff_filename,
                f"generation.{family.method}_diff_filename",
                frequency=variant.name,
            )
            diff_text = build_diff(
                config,
                family.method,
                variant,
                generated[(family.method, variant.name)],
                groups,
                stock_profile,
            )
            release[config.output.diff_dir / filename] = diff_text.encode("utf-8")
    report = render_release_report(
        config,
        candidate,
        groups,
        vendor_boot_size=vendor_boot_size,
        vendor_boot_sha256=vendor_boot_sha256,
        vendor_boot_info=vendor_boot_info,
        dtb_count=dtb_count,
        aop=aop,
        tracked_outputs=tracked,
        diff_outputs=release,
        generated=generated,
    )
    release[config.output.release_report] = report.encode("utf-8")
    return BuildArtifacts(tracked=tracked, release=release)


def atomic_write_outputs(outputs: Mapping[Path, bytes]) -> tuple[int, int]:
    changed: dict[Path, bytes] = {}
    unchanged = 0
    mode_updates: list[Path] = []
    for path, data in outputs.items():
        if path.exists() and not path.is_symlink() and path.read_bytes() == data:
            if path.stat().st_mode & 0o777 != 0o644:
                mode_updates.append(path)
            else:
                unchanged += 1
        else:
            changed[path] = data
    staged: dict[Path, Path] = {}
    try:
        for path, data in changed.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="wb", prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
            ) as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
                os.chmod(handle.name, 0o644)
                staged[path] = Path(handle.name)
        for path, temporary in staged.items():
            os.replace(temporary, path)
            print(f"wrote {path}")
        for path in mode_updates:
            path.chmod(0o644)
            print(f"fixed mode {path}")
    finally:
        for temporary in staged.values():
            if temporary.exists():
                temporary.unlink()
    return len(changed) + len(mode_updates), unchanged


def find_stale_artifacts(
    outputs: Mapping[Path, bytes], managed_directories: tuple[Path, ...]
) -> list[Path]:
    expected = set(outputs)
    stale: list[Path] = []
    for directory in set(managed_directories):
        if not directory.exists():
            continue
        if directory.is_symlink() or not directory.is_dir():
            raise PipelineError(
                f"managed output directory must be a regular directory: {directory}"
            )
        for path in directory.rglob("*"):
            if path.is_dir() and not path.is_symlink():
                continue
            if path not in expected:
                stale.append(path)
    return sorted(stale)


def check_outputs(
    outputs: Mapping[Path, bytes],
    managed_directories: tuple[Path, ...],
    *,
    label: str,
) -> bool:
    failures: list[str] = []
    for path, expected in outputs.items():
        if not path.exists():
            failures.append(f"missing: {path}")
        elif path.is_symlink():
            failures.append(f"generated artifact must not be a symlink: {path}")
        elif path.read_bytes() != expected:
            failures.append(f"different: {path}")
        elif path.stat().st_mode & 0o777 != 0o644:
            failures.append(f"wrong mode (expected 0644): {path}")
    for path in find_stale_artifacts(outputs, managed_directories):
        failures.append(f"stale generated artifact: {path}")
    if failures:
        for failure in failures:
            print(f"check failed: {failure}", file=sys.stderr)
        return False
    print(
        f"check passed: {len(outputs)} {label} artifacts are byte-identical"
    )
    return True


def read_source(config: BuildConfig) -> tuple[bytes, str, VendorBootInfo, list[bytes]]:
    try:
        image = config.source.vendor_boot.read_bytes()
    except FileNotFoundError as error:
        raise PipelineError(
            f"missing vendor_boot image: {config.source.vendor_boot}"
        ) from error
    actual_hash = sha256_bytes(image)
    info, section = extract_dtb_section(image)
    return image, actual_hash, info, split_dtbs(section)


def warn_unused_source_files(config: BuildConfig) -> None:
    directory = config.source.vendor_boot.parent
    if not directory.is_dir():
        return
    used = {
        config.source.vendor_boot,
        config.source.aop,
        config.output.release_dir,
    }
    extras = sorted(path.name for path in directory.iterdir() if path not in used)
    if extras:
        print(
            "warning: unused files in source/: " + ", ".join(extras),
            file=sys.stderr,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "firmware_directory",
        type=Path,
        help="profile-set directory containing source/vendor_boot.img and source/aop.mbn",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).resolve().with_name("config.toml"),
        help="shared project configuration (default: config.toml beside this script)",
    )
    parser.add_argument(
        "--dtb-index",
        type=int,
        help="resolve an otherwise ambiguous semantic DTB match",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--inspect",
        "--list-dtbs",
        dest="inspect",
        action="store_true",
        help="inspect firmware inputs without writing files",
    )
    mode.add_argument(
        "--check", action="store_true", help="regenerate in memory and compare existing artifacts"
    )
    mode.add_argument(
        "--release",
        action="store_true",
        help="validate tracked artifacts, then write ignored local release evidence",
    )
    args = parser.parse_args()
    if args.inspect and args.dtb_index is not None:
        parser.error("--dtb-index cannot be combined with --inspect")
    return args


def main() -> int:
    args = parse_args()
    try:
        config = load_config(args.firmware_directory, args.config)
        warn_unused_source_files(config)
        print(f"profile set: {config.firmware.firmware_id}")
        image, source_hash, info, blobs = read_source(config)
        config = apply_firmware_identity(
            config, image, info, required=not args.inspect
        )
        candidates = inspect_candidates(blobs)
        print(
            f"vendor_boot v{info.header_version}: {len(blobs)} DTBs, "
            f"section offset=0x{info.dtb_offset:x}, size={info.dtb_size}"
        )
        print(f"vendor_boot SHA-256: {source_hash}")
        if config.firmware.vendor_fingerprint:
            print("vendor_boot AVB body self-hash: internally consistent")
            print(f"vendor fingerprint: {config.firmware.vendor_fingerprint}")
        if args.inspect:
            print_candidates(candidates)
            aop = read_aop(config.source.aop, config.regulators.aop_resource)
            offsets = ", ".join(f"0x{offset:x}" for offset in aop.offsets)
            print(f"aop.mbn SHA-256: {aop.sha256}")
            print(
                f"{config.regulators.aop_resource}: {len(aop.values)} active values "
                f"from {aop.template_count} identical templates ({offsets})"
            )
            print("AOP values: " + ", ".join(str(value) for value in aop.values))
            return 0
        candidate = select_candidate(config, candidates, args.dtb_index)
        aop = read_aop(config.source.aop, config.regulators.aop_resource)
        offsets = ", ".join(f"0x{offset:x}" for offset in aop.offsets)
        config = replace(
            config,
            source=replace(
                config.source,
                dtb_index=candidate.index,
            ),
            regulators=replace(
                config.regulators,
                aop_values=aop.values,
                aop_provenance=(
                    f"{config.source.aop.name} Command DB "
                    f"{config.regulators.aop_resource} "
                    f"({aop.template_count} identical templates at {offsets})"
                ),
                aop_source_sha256=aop.sha256,
            ),
        )
        groups = validate_selected(config, candidate)
        artifacts = build_outputs(
            config,
            candidate,
            groups,
            vendor_boot_size=len(image),
            vendor_boot_sha256=source_hash,
            vendor_boot_info=info,
            dtb_count=len(blobs),
            aop=aop,
            include_release_evidence=args.release,
        )
        tracked_directories = (
            config.output.stock_profile.parent,
            config.output.uv_dir,
        )
        release_directories = (config.output.release_dir,)
        print(
            f"selected DTB #{candidate.index}: chip={candidate.chip}, "
            f"{candidate.group_count} groups, {candidate.level_count} levels"
        )
        print(
            f"aop.mbn: {len(aop.values)} active {config.regulators.aop_resource} "
            f"values, SHA-256 {aop.sha256}"
        )
        if args.check:
            return (
                0
                if check_outputs(
                    artifacts.tracked,
                    tracked_directories,
                    label="tracked",
                )
                else 1
            )
        if args.release:
            if not check_outputs(
                artifacts.tracked,
                tracked_directories,
                label="tracked",
            ):
                print(
                    "release evidence was not written because tracked artifacts "
                    "failed validation",
                    file=sys.stderr,
                )
                return 1
            stale = find_stale_artifacts(
                artifacts.release, release_directories
            )
            if stale:
                rendered = ", ".join(str(path) for path in stale)
                raise PipelineError(
                    "stale release artifacts must be moved or removed before "
                    f"generation: {rendered}"
                )
            changed, unchanged = atomic_write_outputs(artifacts.release)
            print(
                f"generated {len(artifacts.release)} local release artifacts: "
                f"{changed} updated, {unchanged} unchanged"
            )
            return 0
        stale = find_stale_artifacts(
            artifacts.tracked, tracked_directories
        )
        if stale:
            rendered = ", ".join(str(path) for path in stale)
            raise PipelineError(
                "stale tracked artifacts must be moved or removed before generation: "
                f"{rendered}"
            )
        changed, unchanged = atomic_write_outputs(artifacts.tracked)
        print(
            f"verified and generated {len(artifacts.tracked)} tracked artifacts: "
            f"{changed} updated, {unchanged} unchanged"
        )
        return 0
    except (OSError, UnicodeError, PipelineError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
