# Building profiles from firmware

`build_profiles.py` handles the complete source-to-profile pipeline. It reads a
shared TOML policy, identifies the firmware from local binaries, selects the
target GPU DTB by its contents, extracts the active AOP list, and generates
tracked profiles plus optional local diffs and a release report.

This is a maintainer guide for adding or rebuilding a firmware set. Users who
only want to install an existing profile do not need firmware images or the
builder; they should use the [profile guide](profile-guide.md).

## Contents

- [Requirements and directory layout](#requirements-and-directory-layout)
- [Required firmware files](#required-firmware-files)
- [Where the input files come from](#where-the-input-files-come-from)
- [Source identity and trust boundary](#source-identity-and-trust-boundary)
- [Shared configuration](#shared-configuration)
- [Create a firmware profile set](#create-a-firmware-profile-set)
- [Inspect, build, check, and release](#inspect-build-check-and-release)
- [Automatic selection and validation](#automatic-selection-and-validation)
- [Outputs and failure behavior](#outputs-and-failure-behavior)
- [Maintainer checklist](#maintainer-checklist)

## Requirements and directory layout

Requirements:

- Python 3.11 or newer;
- `dtc` from `device-tree-compiler`;
- the two matching firmware files described below.

Repository layout:

```text
build_profiles.py
config.toml
docs/
profiles/<firmware-id>/
├── source/                    # inputs and generated evidence, ignored by Git
│   ├── vendor_boot.img
│   ├── aop.mbn
│   └── release/              # created only by --release
│       ├── release_report.md
│       └── diff/
│           └── diff_*.md
├── stock/
│   ├── stock.txt
│   └── stock.dts
└── uv/
    └── uv_*.txt
```

Firmware directories do not duplicate the configuration. Their name combines
the project device/region label with the ZUI version. For example, ZUI
`17.5.10.272` uses
`profiles/TB321FU_ROW_ZUI_17.5.10.272/`. Source hashes, the DTB index, table
shape, and active AOP values are derived on every run.

## Required firmware files

The builder requires two files named exactly `vendor_boot.img` and `aop.mbn`
from the same stock package:

| File | Data used by the builder |
|---|---|
| `vendor_boot.img` | Vendor boot identity, embedded DTBs, selected GPU table, and DTB metadata |
| `aop.mbn` | Command DB `gfx.lvl` data used for exact-AOP profiles and the modeled Generic comparison |

The builder does not use:

- `vbmeta.img` or `vbmeta_system.img`;
- `boot.img`, `init_boot.img`, or `dtbo.img`;
- `aop_devcfg.mbn`;
- `super` or other partition images;
- patched, debug, or numbered `vendor_boot_*` alternatives.

The source pair must come from one package. The builder checks that every stock
GPU vote is compatible with the recovered AOP list, but the two files do not
cryptographically identify each other. Package URL and archive metadata, plus
the system Android release and security patch, remain external provenance.

## Where the input files come from

Start with a complete, unmodified stock ROW firmware package for the exact ZUI
version being added. Obtain it from Lenovo's distribution or another source
whose origin and archive identity you can retain. This repository does not
provide firmware packages, and the builder cannot authenticate a third-party
download.

How the two inputs are stored depends on the package format:

- a service or factory package may contain raw files such as
  `image/vendor_boot.img` and `image/aop.mbn`; extract those archive members
  without modifying them;
- an OTA package may store partitions inside `payload.bin`; use a trusted
  payload extractor to extract the `vendor_boot` and `aop` partitions from the
  same payload;
- if an extractor calls the raw AOP partition `aop.img`, copy it into the
  source directory as `aop.mbn` without converting or unpacking it. The
  subsequent `--inspect` command validates the required little-endian RISC-V
  ELF32 layout and rejects an unrelated image.

For example, a factory-package extraction may provide this matching pair:

```text
<extracted-package>/
└── image/
    ├── vendor_boot.img
    └── aop.mbn
```

Copy both files from the package for the exact ZUI release being built. Do not
combine a `vendor_boot.img` from one release with an `aop.mbn` from another.

Do not use an image pulled after LTBox/KonaBess modification as the stock
source. Keep the original archive, its download location, and its checksum
outside the ignored `source/` workspace so another maintainer can reproduce
the extraction.

## Source identity and trust boundary

When an input uses AVB `Algorithm: NONE`, the builder still validates the
footer, descriptor bounds, logical vendor-boot payload layout, and salted body
hash. This detects a content edit when the appended metadata was not rewritten.
It does not authenticate the image or fingerprint because those descriptors
can also be changed. A local release report records the outcome for the current
input files.

Source provenance therefore rests on:

1. the stock-package origin retained by the maintainer;
2. the full-file SHA-256 values emitted in `source/release/release_report.md`;
3. matching archive-member metadata when independently reviewed;
4. maintainer review that `vendor_boot.img` and `aop.mbn` came from one package.

Hashes are generated evidence, not configuration inputs. The builder computes
and prints them on every run. This avoids stale per-firmware manifests while
keeping the trust boundary explicit.

The embedded vendor fingerprint remains useful as a consistency check. A
normal build requires its product identity and `user/release-keys` variant to
match the configured values. The region and version parsed from the fingerprint
must then reconstruct the canonical firmware directory name. Inspection allows
a missing fingerprint only for diagnosing malformed or unfamiliar images; the
metadata is never treated as authenticated.

## Shared configuration

[`config.toml`](../config.toml) is the only machine-readable policy file. TOML
is used because Python 3.11 parses it through the standard library.

| Section | Purpose |
|---|---|
| `project` | Device identity, firmware-directory convention, display labels, and stock fingerprint contract |
| `inputs` | Source-directory and required filename conventions |
| `target` | Semantic DTB selector: compatible, MSM ID, and GPU model |
| `paths` | Tracked stock and UV locations |
| `regulators.generic` | Picker and advisory threshold sourced from LTBox |
| `regulators.aop` | Command DB resource name; values are recovered from `aop.mbn` |
| `generation` | Step counts, profile/diff naming, and frequency variants |

LTBox is a separate project. Its picker supplied the initial Generic values,
but [`config.toml`](../config.toml) is the source of truth for this builder;
installing a newer LTBox does not change generated profile data automatically.
If the upstream picker changes, review that change explicitly before updating
the local policy.

The ignored release path is fixed at `source/release/`; it cannot be configured
globally or per firmware.

The configuration intentionally contains no source hashes, DTB indices, table
sizes, AOP values, or firmware paths. There is no firmware tuning file because
the repository has no empirically justified per-frequency exceptions. If such
a curve is introduced, keep its firmware-specific points and evidence separate
instead of duplicating the shared policy.

Unknown keys and invalid types fail closed. Configured paths must remain inside
the firmware directory, tracked output directories cannot overlap `source/`,
and generated names must be unique bare filenames. The ignored release path is
intentionally contained within `source/`.

## Create a firmware profile set

Run from the repository root. For a new firmware set, set `zui_version` to the
version in the matching stock-package metadata or build fingerprint. For
example:

```bash
set -eu
zui_version=17.5.10.272
firmware_id="TB321FU_ROW_ZUI_${zui_version}"

test ! -e "profiles/$firmware_id" || {
  echo "Refusing to overwrite profiles/$firmware_id" >&2
  exit 1
}

mkdir -p "profiles/$firmware_id/source"
cp /path/to/vendor_boot.img \
  "profiles/$firmware_id/source/vendor_boot.img"
cp /path/to/aop.mbn \
  "profiles/$firmware_id/source/aop.mbn"
```

The refusal guard above is for creating a new set. To rebuild an existing set,
keep its canonical firmware directory and tracked `stock/` and `uv/`
directories, then place the two matching inputs in its ignored `source/`
directory and continue with `--inspect`. Do not reuse a source file from a
different release.

The builder reconstructs the expected directory name from the embedded vendor
fingerprint and refuses a normal build if it does not match. Do not choose the
version from another package or infer it from a similar filename.

The entire `source/` directory is ignored by Git. Generated `stock/` and `uv/`
artifacts are tracked. Release reports and readable diffs remain local under
`source/release/`.

## Inspect, build, check, and release

Inspect both sources without writing outputs:

```bash
python3 build_profiles.py "profiles/$firmware_id" --inspect
```

Inspection reports:

- computed source hashes and vendor fingerprint;
- AVB body-hash consistency;
- every embedded DTB's root identity, MSM ID, GPU model, shape, and hash;
- all matching `gfx.lvl` templates that pass validation and their normalized
  values.

Generate the tracked artifacts:

```bash
python3 build_profiles.py "profiles/$firmware_id"
```

Rebuild in memory and compare every expected tracked artifact byte-for-byte:

```bash
python3 build_profiles.py "profiles/$firmware_id" --check
```

Generate ignored release evidence after validating the tracked outputs:

```bash
python3 build_profiles.py "profiles/$firmware_id" --release
```

`--release` first performs the same tracked-output validation as `--check`, then
writes ignored evidence under `source/release/`. If validation fails, no release
evidence is written or replaced. The builder owns that entire directory and
treats additional files as stale artifacts. Archive or copy it elsewhere before
adding manual notes.

Use `--dtb-index N` only when multiple DTBs match the semantic target but
contain different GPU tables, and a maintainer has independently established
which one is correct. The override must still satisfy the configured
compatible, MSM ID, and GPU model. It cannot be combined with `--inspect`.

## Automatic selection and validation

The target is not selected by a saved index or hash. For each DTB the builder:

1. decompiles it with `dtc`;
2. reads root `compatible` and `qcom,msm-id` properties;
3. finds an enabled `qcom,kgsl-3d0` node;
4. binds `qcom,gpu-model` and the GPU table to that same enabled node;
5. matches the shared target policy;
6. accepts multiple matches automatically only when their complete GPU tables
   are identical.

The AOP parser supports only the expected little-endian RISC-V ELF32 executable
layout. It searches file-backed load segments for complete Command DB resource
tables, validates entry and data offsets, normalizes the requested resource,
and accepts duplicates only when their active value lists are identical.

Before serialization the builder verifies:

- unique GPU group and level IDs;
- single-cell frequency and regulator properties;
- strictly descending, collision-free frequency ladders;
- stock votes present in both the generic picker and firmware AOP list;
- exact allowed vote and marker transformations;
- unchanged group/level order and all unrelated lines;
- valid unsigned 32-bit DT cells.

Profiles are serialized as deterministic compact JSON, compressed with gzip
using a fixed timestamp and OS byte, and Base64-encoded with the
`konabess://` prefix.

## Outputs and failure behavior

Normal generation and `--check` manage 12 tracked artifacts per firmware:

- two stock files;
- ten importable UV profiles.

`--release` additionally manages five ignored evidence files: four readable
diff documents and one deterministic release report. These counts are driven by
the shared policy rather than hardcoded profile names.

All outputs are constructed and validated in memory before writes begin.
Changed files are staged beside their destinations and replaced atomically one
at a time; unchanged files retain their contents and modification times. Each
replacement is atomic, but the full set is not a transaction if an
operating-system replacement fails midway.

Generation refuses to guess or silently clean up. It fails on:

- missing or invalid inputs;
- identity or semantic-selector mismatches;
- ambiguous or conflicting DTB/AOP data;
- malformed tables or invalid transformations;
- output path collisions;
- unexpected stale artifacts in generated directories.

`--check` additionally reports missing, changed, symlinked, stale, or
incorrectly permissioned tracked outputs. It does not require local release
evidence to exist. `--release` validates the tracked outputs before replacing
the ignored evidence files.

See [what requires manual evidence](validation-and-testing.md#what-requires-manual-evidence)
for checks outside the builder's scope.

## Maintainer checklist

For a new firmware release:

1. obtain stock files named exactly `vendor_boot.img` and `aop.mbn` from the
   same firmware package;
2. create the canonical directory and run `--inspect`;
3. review the fingerprint and semantic target instead of assuming an index;
4. generate the tracked artifacts and run `--check` against the source files;
5. run `--release` and inspect its local report and readable diffs;
6. independently decode the profile containers and verify unchanged
   non-target table fields;
7. record manual evidence separately as defined by the
   [validation guide](validation-and-testing.md#what-requires-manual-evidence);
8. record only checks actually performed for that source;
9. verify links in tracked files, language consistency, and `git diff --check`.
