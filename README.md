# Lenovo Y700 Gen 3 (TB321FU) GPU undervolt

<p align="center"><a href="README.md">English</a> | <a href="README_ru.md">Русский</a></p>

Reproducible LTBox/KonaBess GPU power-table profiles for the Lenovo Legion
Y700 Gen 3 (`TB321FU`). Each firmware-specific set contains an importable stock
table plus generated undervolt profiles. When needed, the builder generates
firmware-specific diffs and a local release report from the matching files.

> [!WARNING]
> **Disclaimer:** Use this project entirely at your own risk. Modifying GPU
> power tables can cause instability, data loss, boot failure, or device
> damage. The repository owner, authors, and contributors accept no
> responsibility for any damage, loss, or other consequences resulting from
> the use of these files, profiles, instructions, or tools.

> Use only profiles from the directory for the exact installed firmware.
> Structural validation does not guarantee electrical stability on every
> tablet; keep the matching `stock.txt` and test conservatively.

## Profile sets

| Profile set | Contents |
|---|---|
| [`TB321FU_ROW_ZUI_17.0.12.183`](profiles/TB321FU_ROW_ZUI_17.0.12.183/) | Stock and UV profiles generated from that firmware |
| [`TB321FU_ROW_ZUI_17.5.10.272`](profiles/TB321FU_ROW_ZUI_17.5.10.272/) | Stock and UV profiles generated from that firmware |
| [`MinusZero`](profiles/MinusZero/) | Supplied reference files with unknown source firmware, plus decoded contents |

## Profile types

| Type | Meaning |
|---|---|
| Stock | Complete table extracted from the matching firmware |
| Exact AOP -1/-2/-3 | Floor-clamped shifts through that firmware's active `gfx.lvl` values |
| Generic picker -2/-3 | Shifts through the shared KonaBess/LTBox picker to compare requested and effective results |
| 903/905 MHz | Stock frequencies or an application marker |

No profile is a universal default; the [profile guide](docs/profile-guide.md)
defines every transformation, marker, and experimental scope.

## Quick start

1. Open the directory matching the complete installed firmware ID.
2. Keep that directory's `stock/stock.txt` for table-level rollback through
   LTBox. It is not a recovery backup for a device that no longer boots.
3. Select a profile after comparing its transformation family and frequency
   variant in the [profile guide](docs/profile-guide.md).
4. Import the selected profile in LTBox/KonaBess and apply it to the intended
   GPU table.
5. Reboot, confirm the expected table or marker, and follow the
   [stability test procedure](docs/validation-and-testing.md#device-stability-testing).

See [validation and testing](docs/validation-and-testing.md) for interpreting
LTBox findings and evaluating stability.

## Build profiles from firmware

The builder uses matching `vendor_boot.img` and `aop.mbn` files with one shared
[`config.toml`](config.toml); `vbmeta.img` is not an input. See the
[build guide](docs/building.md) for the directory layout, commands, generated
artifacts, failure behavior, and new-firmware workflow.

## Documentation

| Guide | Scope |
|---|---|
| [Profile guide](docs/profile-guide.md) | Regulator votes, profile families, exact transformations, naming, and the 905 MHz marker |
| [Build guide](docs/building.md) | Required files, directory layout, shared configuration, generation workflow, and maintainer checklist |
| [Validation and testing](docs/validation-and-testing.md) | Validation layers, KGSL mapping, LTBox advisories, risks, logs, and stability procedure |
