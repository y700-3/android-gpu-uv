# Technical profile reference

This document defines the data model, regulator sources, transformations, and
evidence behind the generated profiles. For installation and profile choice,
use the [profile guide](profile-guide.md). For source preparation and generator
commands, use the [build guide](building.md).

## Contents

- [Terminology](#terminology)
- [Supported firmware data](#supported-firmware-data)
- [Regulator requests are not millivolts](#regulator-requests-are-not-millivolts)
- [The two level lists](#the-two-level-lists)
- [Exact transformation rules](#exact-transformation-rules)
- [Modeled results for the supported tables](#modeled-results-for-the-supported-tables)
- [Cross-family aggressiveness](#cross-family-aggressiveness)
- [Per-frequency profile matrix](#per-frequency-profile-matrix)
- [Frequency variants](#frequency-variants)
- [Preserved table properties](#preserved-table-properties)
- [Profile format and naming](#profile-format-and-naming)
- [Inclusion and evidence policy](#inclusion-and-evidence-policy)

## Terminology

| Term | Meaning in this project |
|---|---|
| AOP | Always-On Processor firmware component whose Command DB contains the `gfx.lvl` resource |
| ARC | RPMh accelerator resource class used for voltage-domain level resources such as `gfx.lvl` |
| Command DB | Qualcomm firmware database that exposes named hardware-resource data such as `gfx.lvl` |
| DTB | Compiled device-tree blob containing the GPU power table and hardware selectors |
| Exact AOP | Profile family that shifts stock requests through the recovered firmware `gfx.lvl` list |
| Generic | Profile family that shifts stock requests through LTBox's Pineapple picker |
| `gfx.lvl` | Command DB resource from which the builder recovers the firmware AOP request list |
| KGSL | Kernel Graphics Support Layer, Qualcomm's Adreno kernel-driver stack; its public code is used only to model requests absent from the recovered AOP list |
| `qcom,level` | Device-tree field containing the encoded regulator request for a GPU power-table row |
| Regulator request | Encoded performance-state identifier stored in `qcom,level`; it is not a voltage in millivolts |
| RPMh | Resource Power Manager hardened, Qualcomm's mechanism for voting on shared power and performance resources |
| Pineapple | Qualcomm codename for the SM8650 / Snapdragon 8 Gen 3 platform, used by these DTBs and by the profile `chip` field |
| DVFS | Dynamic voltage and frequency scaling |
| OPP | Operating performance point: a frequency paired with the operating data required for it |
| CPR | Core Power Reduction, Qualcomm closed-loop rail control that can influence the physical voltage behind an encoded request |
| ACD | Adaptive Clock Distribution, characterized GPU droop-control data passed to the GMU |
| GMU | Graphics Management Unit responsible for low-level GPU power and clock control |
| SKU / speed-bin | Hardware-variant selectors that bind a table to a particular chip configuration |

## Supported firmware data

The builder independently recovered the following data from each supported
firmware set:

| Firmware | Selected table | Shape | Active `gfx.lvl` values |
|---|---|---|---|
| `TB321FU_ROW_ZUI_17.0.12.183` | DTB #4, Adreno750v2 | 4 groups, 47 rows (`12 / 14 / 9 / 12`) | 14 values |
| `TB321FU_ROW_ZUI_17.5.10.272` | DTB #4, Adreno750v2 | 4 groups, 47 rows (`12 / 14 / 9 / 12`) | 14 values |

The decoded stock GPU tables are identical, and both AOP binaries expose the
same active sequence:

```text
52, 56, 60, 64, 80, 128, 144, 192, 224, 256, 320, 384, 416, 432
```

The transformed table payloads are therefore also identical across these two
firmware sets. Their profile containers remain separate because the embedded
description records the source firmware. This equality is an observed fact for
these two inputs, not a compatibility rule for future versions.

## Regulator requests are not millivolts

`qcom,level` is an encoded regulator performance-state request. Its integer
value is not a voltage and cannot be converted to millivolts from a KonaBess
profile alone. The physical rail behavior can also depend on CPR, dependency
votes, shared rails, temperature, and implementation-specific control logic.

The spacing between adjacent encoded values is irregular. Consequently:

- one list step is not a fixed voltage reduction;
- AOP -2 does not mean twice the voltage change of AOP -1;
- two profiles cannot be compared by subtracting their raw integers;
- a recognized request is not proof that it is stable with a higher GPU
  frequency.

Voltage reduction can lower dynamic power, whose simplified relationship has a
voltage-squared component, but only while timing and output correctness remain
stable. Arm's [Armv8-A Power Management guide](https://documentation-service.arm.com/static/5efdc1b8dbdee951c1cd2baa)
provides general DVFS background; it does not characterize this tablet's
minimum stable curve.

## The two level lists

### Generic picker

Generic profiles use the ordered Pineapple picker configured in
[`config.toml`](../config.toml). It mirrors the names and values exposed by
[LTBox's Pineapple picker](https://github.com/miner7222/LTBox/blob/main/crates/ltbox-patch/src/konabess/regulator_levels.rs#L26-L55):

```text
16, 48, 52, 56, 60, 64, 72, 80, 96, 128, 144, 192,
224, 256, 288, 320, 336, 384, 400, 416, 432, 448, 464, 480
```

This list defines the serialized Generic transformation. It is an LTBox picker,
not a statement that every value appears in the target firmware.

LTBox is a separate project. The local list in `config.toml` determines the
generated profile bytes; installing a newer LTBox does not alter this list or
regenerate the profiles.

### Firmware AOP list

Exact AOP profiles use `gfx.lvl` values extracted from Command DB data inside
the matching `aop.mbn`. Qualcomm's
[Command DB implementation](https://android.googlesource.com/kernel/common/+/04f4f33c941c221645d2a58b46f4d698b0f5aa39/drivers/soc/qcom/cmd-db.c)
documents how resource auxiliary data is exposed to clients.

Every stock vote in both supported tables occurs in their recovered AOP list.
Values such as `72`, `96`, `288`, and `336` occur in the Generic picker but not
in the recovered `gfx.lvl` sequence.

### How an unsupported Generic request is modeled

The public Qualcomm Gen7 KGSL implementation resolves a dependency request to
the first available level whose value is greater than or equal to the request.
The relevant paths are
[`adreno_gen7_rpmh.c`](https://github.com/qualcomm-linux/kgsl/blob/a183ffbab70cc9fc2f29092bce45fcbe9111b410/adreno_gen7_rpmh.c)
and [`adreno_rpmh.c`](https://github.com/qualcomm-linux/kgsl/blob/a183ffbab70cc9fc2f29092bce45fcbe9111b410/adreno_rpmh.c).

The builder uses that ceiling rule to model the expected AOP position of a
Generic request. This is a documented comparison model, not proof of the
behavior of Lenovo's packaged KGSL binary; that module was not independently
matched to the cited public source for either supported firmware release.

## Exact transformation rules

Let `G` be the ascending Generic picker, `A` the ascending firmware `gfx.lvl`
list, `v` a stock request, and `n` the profile step count.

For a Generic profile:

```text
requested = G[max(0, index(G, v) - n)]
modeled_effective = first a in A where a >= requested
```

For an Exact AOP profile:

```text
requested = A[max(0, index(A, v) - n)]
modeled_effective = requested
```

The `max(0, ...)` operation clamps each transformation at the floor of its own
list. In Generic, a stock row at the AOP floor can still serialize a lower
picker value (`52 -> 16` here); the modeled ceiling mapping then returns it to
`52`. In Exact AOP, floor rows remain unchanged, while a row one AOP position
above the floor can move only once even in an AOP -3 profile.

Generic -3 always requests a value equal to or lower than Generic -2, and AOP
-3 is equal to or deeper than AOP -2 on every row. Step counts are not directly
comparable across the two families because `G` and `A` contain different
values.

## Modeled results for the supported tables

The following counts cover all 47 rows. A drop of `-N` means positions in the
recovered firmware AOP list under the public Qualcomm Gen7 ceiling model; it
does not mean millivolts or prove the behavior of the packaged driver.

| Profile | Serialized votes changed | Modeled result |
|---|---:|---|
| Generic -2 | 47 | 19 × -2; 24 × -1; 4 × 0 |
| Generic -3 | 47 | 11 × -3; 19 × -2; 13 × -1; 4 × 0 |
| Exact AOP -1 | 43 | 43 × -1; 4 × 0 |
| Exact AOP -2 | 43 | 39 × -2; 4 × -1; 4 × 0 |
| Exact AOP -3 | 43 | 35 × -3; 4 × -2; 4 × -1; 4 × 0 |

Generic profiles change every serialized `qcom,level` cell, including requests
modeled to resolve back to the stock AOP floor. Exact AOP profiles leave the
four floor rows unchanged.

Exact AOP -3 is deliberately classified as experimental. It applies the full
three-position drop to 35 of 47 rows and changes the 903 MHz request from stock
`384` to `224`. This is a substantial global reduction rather than a tuned
per-frequency curve. It may run correctly on some chips but produce artifacts,
GPU faults, freezes, or reboots on others; AOP -2 stability does not establish
AOP -3 stability.

## Cross-family aggressiveness

Each cell below compares the Exact AOP row with the modeled Generic row and
reports `less / same / more` aggressive rows:

| Exact profile | versus Generic -2 | versus Generic -3 |
|---|---:|---:|
| AOP -1 | 19 / 28 / 0 | 30 / 17 / 0 |
| AOP -2 | 0 / 27 / 20 | 11 / 27 / 9 |
| AOP -3 | 0 / 12 / 35 | 0 / 23 / 24 |

Under that model, the supported tables have the following partial order. Each
arrow leads to a profile that is nowhere milder than the previous one:

```text
                         ┌→ AOP -2 ────┐
Stock → AOP -1 → Generic -2            → AOP -3
                         └→ Generic -3 ┘
```

The diagram does not order AOP -2 against Generic -3. AOP -2 is milder on 11
rows, equal on 27, and deeper on 9.

At 903 MHz specifically, stock requests `384`; AOP -1 and the modeled Generic
-2 and -3 results use `320`; AOP -2 uses `256`; and AOP -3 uses `224`. This is
why a larger Generic filename number does not necessarily produce a deeper
modeled shift at the highest common frequency.

## Per-frequency profile matrix

Each cell uses the following format:

```text
modeled AOP positions down (serialized qcom,level -> modeled AOP value)
```

The arrow is shown only when a Generic profile writes a value that is absent
from the firmware `gfx.lvl` list. For example, `-1 (288 -> 320)` means that the
profile stores `288`; under the public KGSL rule it is modeled to resolve to
`320`, one firmware AOP position below stock. A cell such as `-2 (256)` already
uses a value present in the recovered list. These numbers are encoded requests,
not millivolts.

| Frequency, MHz | Stock | AOP -1 | Generic -2 | AOP -2 | Generic -3 | AOP -3 |
|---:|---:|---:|---:|---:|---:|---:|
| 1000 / 950 | `0 (416)` | `-1 (384)` | `-1 (384)` | `-2 (320)` | `-1 (336 -> 384)` | `-3 (256)` |
| 903 | `0 (384)` | `-1 (320)` | `-1 (320)` | `-2 (256)` | `-1 (288 -> 320)` | `-3 (224)` |
| 834 | `0 (320)` | `-1 (256)` | `-1 (256)` | `-2 (224)` | `-2 (224)` | `-3 (192)` |
| 770 | `0 (256)` | `-1 (224)` | `-2 (192)` | `-2 (192)` | `-3 (144)` | `-3 (144)` |
| 720 | `0 (224)` | `-1 (192)` | `-2 (144)` | `-2 (144)` | `-3 (128)` | `-3 (128)` |
| 680 | `0 (192)` | `-1 (144)` | `-2 (128)` | `-2 (128)` | `-2 (96 -> 128)` | `-3 (80)` |
| 629 | `0 (144)` | `-1 (128)` | `-1 (96 -> 128)` | `-2 (80)` | `-2 (80)` | `-3 (64)` |
| 578 | `0 (128)` | `-1 (80)` | `-1 (80)` | `-2 (64)` | `-1 (72 -> 80)` | `-3 (60)` |
| 500 | `0 (80)` | `-1 (64)` | `-1 (64)` | `-2 (60)` | `-2 (60)` | `-3 (56)` |
| 422 | `0 (64)` | `-1 (60)` | `-2 (56)` | `-2 (56)` | `-3 (52)` | `-3 (52)` |
| 366 | `0 (60)` | `-1 (56)` | `-2 (52)` | `-2 (52)` | `-2 (48 -> 52)` | `-2 (52)` |
| 310 | `0 (56)` | `-1 (52)` | `-1 (48 -> 52)` | `-1 (52)` | `-1 (16 -> 52)` | `-1 (52)` |
| 231 | `0 (52)` | `0 (52)` | `0 (16 -> 52)` | `0 (52)` | `0 (16 -> 52)` | `0 (52)` |

The 1000 and 950 MHz rows exist only in one SKU group. Other groups begin at
903 or 720 MHz but use the same request transformation wherever a listed
frequency is present. The 905 MHz variants use the 903 MHz row unchanged apart
from replacing the frequency marker itself.

## Frequency variants

The 903 MHz variant preserves every stock frequency. The 905 MHz marker uses
one exact rule:

```text
every qcom,gpu-freq cell equal to 903000000 -> 905000000
```

Exactly three cells change, in GPU groups 0, 1, and 3. The 1000 and 950 MHz
rows in another SKU group and every other frequency remain unchanged. The
paired 903/905 profiles have identical regulator requests and all
non-frequency table fields. Their container descriptions differ so that the
marker variant can be identified.

The marker delta is:

```text
(905 - 903) / 903 = 0.2215%
```

It is intended as a visible application marker, not a performance or
efficiency optimization. The 903 variant is the controlled choice when testing
only regulator changes.

## Preserved table properties

The generator changes only configured `qcom,level` cells and, in marker
variants, frequency cells exactly matching the configured source frequency. It
preserves:

- group and row order;
- `reg` IDs and initial power levels;
- SKU and speed-bin selectors;
- ACD words;
- bus minimum, target, and maximum votes;
- dependency votes;
- unrelated frequencies and metadata.

These are not interchangeable voltage controls. Qualcomm's
[Adreno OPP binding](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/Documentation/devicetree/bindings/opp/opp-v2-qcom-adreno.yaml)
notes that ACD data carries characterized droop thresholds, delay cycles, and
margins for the GMU. Changing those fields without separate evidence is outside
this project's undervolt model.

## Profile format and naming

An importable profile begins with `konabess://`, followed by a Base64 string.
Decoding it yields gzip data; decompressing that yields JSON with `chip`,
`desc`, and the full `freq` table. The builder serializes and compresses it
deterministically.

| Pattern | Meaning |
|---|---|
| `stock/stock.txt` | Complete importable stock profile |
| `stock/stock.dts` | Readable form of the same stock GPU table |
| `uv_<n>_level_<freq>mhz.txt` | `<n>` steps through the Generic picker |
| `uv_<n>_aop_level_<freq>mhz.txt` | `<n>` steps through the firmware AOP list |
| `source/release/diff/diff_<freq>mhz.md` | Local stock-versus-Generic evidence |
| `source/release/diff/diff_aop_<freq>mhz.md` | Local stock-versus-AOP evidence |

The profile description identifies its firmware, selected DTB, family, step
count, and marker where applicable.

## Inclusion and evidence policy

A tracked firmware-derived profile must have:

- a complete stock table from a semantically identified DTB;
- AOP levels recovered from the matching firmware input;
- a deterministic transformation that regenerates every changed cell;
- a reproducible local stock-to-profile diff;
- verification that unrelated properties remain unchanged;
- structural validation against the matching sources;
- a distinct and documented test purpose.

Those checks establish provenance and transformation correctness, not
electrical stability. Tool integration and physical-device testing are covered
in [validation and stability testing](validation-and-testing.md). The older
[MinusZero files](../profiles/MinusZero/) remain decoded reference data; their
unknown source and different table shape prevent treating them as generated
profiles for the supported firmware sets.
