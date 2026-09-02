# MinusZero reference profiles

This directory contains two `konabess://` profiles whose source firmware is
unknown. Their stored payloads have been decoded and structurally verified; the
identities below specify the exact files reviewed.

Neither payload identifies its source firmware: the embedded `desc` field is
empty. Structural verification confirms that the files are readable and
internally consistent; it does not establish compatibility with every
firmware build or guarantee stability on a particular device. These are
reference data, not a profile set generated from identified firmware files. No
firmware-derived profile in this repository uses either file as its source or
default. Compare either file with the complete stock GPU table from the
intended firmware before use.

See the shared [technical profile reference](../../docs/technical-reference.md)
for regulator-request semantics, profile-family definitions, and naming.

## Contents

- [File identity](#file-identity)
- [Shared structure](#shared-structure)
- [Group 0](#group-0)
- [Group 1](#group-1)
- [Compatibility status](#compatibility-status)

## File identity

| File | SHA-256 | Size | Chip | Description | Groups / levels |
|---|---|---:|---|---|---:|
| `uv_2_level_905mhz.txt` | `032a343812bff0ef9e768ff1dae63a58ed8bfeaf02cc915251aeedc5122d9217` | 835 bytes | `pineapple` | empty | 2 / 26 |
| `uv_3_level_905mhz.txt` | `d85eb7f3727765384b10418961fb86bb7412d6403eb7974731aee21336b42223` | 835 bytes | `pineapple` | empty | 2 / 26 |

Each file is a Base64-encoded gzip JSON document with the standard fields
`chip`, `desc`, and `freq`. Both payloads parse successfully with the LTBox
validator and have no blocking structural validation errors.

## Shared structure

| Profile group | Header selector | Levels | Initial level | Frequency range |
|---:|---|---:|---:|---:|
| `0` | `qcom,sku-codes = <0x3 0x100f1 0x200f1 0x100f2>` | 14 | 13 | 1000 to 231 MHz |
| `1` | `qcom,sku-codes = <0x0>` | 12 | 11 | 905 to 100 MHz |

Both groups also preserve `#size-cells = <0>` and
`#address-cells = <1>`. Every `reg` cell is sequential and matches its row ID.

## Group 0

The complete group 0 table is byte-for-byte identical between the two files:

| ID | MHz | Regulator vote | ACD | Bus min | Bus freq | Bus max |
|---:|---:|---|---|---:|---:|---:|
| 0 | 1000 | `0x1a0` · `TURBO_L1` | `0x882a5ffd` | 9 | 9 | 9 |
| 1 | 950 | `0x1a0` · `TURBO_L1` | — | 7 | 8 | 9 |
| 2 | 903 | `0x180` · `TURBO` | `0x882a5ffd` | 7 | 8 | 9 |
| 3 | 834 | `0x120` · `NOM_L0` | `0x882a5ffd` | 7 | 8 | 9 |
| 4 | 770 | `0xe0` · `SVS_L2` | `0x882a5ffd` | 6 | 7 | 9 |
| 5 | 720 | `0xc0` · `SVS_L1` | `0x882a5ffd` | 6 | 7 | 9 |
| 6 | 680 | `0x90` · `SVS_L0` | `0x882a5ffd` | 5 | 7 | 9 |
| 7 | 629 | `0x80` · `SVS` | `0x882a5ffd` | 3 | 6 | 7 |
| 8 | 578 | `0x60` · `LOW_SVS_L2` | `0x882c5ffd` | 2 | 5 | 7 |
| 9 | 500 | `0x50` · `LOW_SVS_L1` | `0xc02a5ffd` | 1 | 5 | 5 |
| 10 | 422 | `0x40` · `LOW_SVS` | `0xc02d5ffd` | 1 | 5 | 5 |
| 11 | 366 | `0x3c` · `LOW_SVS_D0` | `0xc02e5ffd` | 1 | 3 | 3 |
| 12 | 310 | `0x38` · `LOW_SVS_D1` | `0xc82c5ffd` | 1 | 1 | 3 |
| 13 | 231 | `0x34` · `LOW_SVS_D2` | `0xc82f5ffd` | 1 | 1 | 1 |

The payload does not identify the stock table from which this group was
derived, so the reference file alone cannot establish its absolute offset from
stock.

## Group 1

The two files have identical frequencies, ACD values, bus votes, row order,
and headers in group 1. They differ only in nine `qcom,level` cells, IDs 0
through 8.

| ID | MHz | `uv_2` vote | `uv_3` vote | ACD | Bus min | Bus freq | Bus max |
|---:|---:|---|---|---|---:|---:|---:|
| 0 | 905 | `0x140` · `NOM_L1` | `0x120` · `NOM_L0` | `0x882a5ffd` | 9 | 9 | 9 |
| 1 | 834 | `0x100` · `NOM` | `0xe0` · `SVS_L2` | `0x882a5ffd` | 7 | 8 | 9 |
| 2 | 770 | `0xc0` · `SVS_L1` | `0x90` · `SVS_L0` | `0x882a5ffd` | 6 | 7 | 9 |
| 3 | 720 | `0x90` · `SVS_L0` | `0x80` · `SVS` | `0x882a5ffd` | 6 | 7 | 9 |
| 4 | 680 | `0x80` · `SVS` | `0x60` · `LOW_SVS_L2` | `0x882a5ffd` | 5 | 7 | 9 |
| 5 | 629 | `0x60` · `LOW_SVS_L2` | `0x50` · `LOW_SVS_L1` | `0x882a5ffd` | 3 | 6 | 7 |
| 6 | 578 | `0x50` · `LOW_SVS_L1` | `0x48` · `LOW_SVS_P1` | `0x882c5ffd` | 2 | 5 | 7 |
| 7 | 500 | `0x40` · `LOW_SVS` | `0x3c` · `LOW_SVS_D0` | `0xc02a5ffd` | 1 | 5 | 5 |
| 8 | 422 | `0x38` · `LOW_SVS_D1` | `0x10` · `RETENTION` | `0xc02d5ffd` | 1 | 5 | 5 |
| 9 | 300 | `0x10` · `RETENTION` | `0x10` · `RETENTION` | `0xc02e5ffd` | 1 | 3 | 3 |
| 10 | 200 | `0x10` · `RETENTION` | `0x10` · `RETENTION` | `0xc82c5ffd` | 1 | 1 | 3 |
| 11 | 100 | `0x10` · `RETENTION` | `0x10` · `RETENTION` | `0xc82f5ffd` | 1 | 1 | 1 |

Both files contain the same frequency sequence, including the 905, 300, 200,
and 100 MHz points. The filenames label the intended -2 and -3 variants, while
the decoded payloads differ in only nine regulator-vote cells. Determining the
exact offset from stock requires the stock table for the firmware being
evaluated.

The -3 file has five non-blocking LTBox validator warnings: four rows at vote
`16` and the 100 MHz row. The -2 file has four: three rows at vote `16` and
the 100 MHz row.

## Compatibility status

No firmware association is documented for these files, so the repository
makes no firmware-compatibility or device-stability claim for them.
Verification covers only container integrity, decoded structure, file identity,
and the exact difference between the two payloads. The filenames are labels;
without the matching stock table, they do not prove uniform -2 or -3
transformations.
