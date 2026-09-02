# Choosing and using a profile

This guide is for people who want to install an existing profile. You do not
need to extract firmware images, run scripts, or understand DTB files. Use the
[latest stable desktop LTBox](https://github.com/miner7222/LTBox/releases/latest).
GPU tuning first appeared in LTBox v3.2.3. Since v3.2.6, the sidebar entry has
been named **Tune GPU** in English and **Тюнинг GPU** in Russian. Versions
v3.2.3–v3.2.5 used **GPU Clock/Voltage** / **Частота/напряжение GPU**. The
page title is **Edit GPU Clock/Voltage** / **Изменить частоту/напряжение
GPU**. KonaBess can import the same `.txt` files, but its backup and write steps
depend on the app version.

## Contents

- [Quick answer](#quick-answer)
- [Choose the correct release](#choose-the-correct-release)
- [Download the two files you need](#download-the-two-files-you-need)
- [Choose a profile](#choose-a-profile)
- [Why Exact AOP and Generic differ](#why-exact-aop-and-generic-differ)
- [Choose 903 or 905 MHz](#choose-903-or-905-mhz)
- [Apply, verify, and roll back](#apply-verify-and-roll-back)
- [Common questions](#common-questions)

## Quick answer

- First cautious test: `uv_1_aop_level_903mhz.txt`.
- Optional traditional intermediate or comparison: `uv_2_level_903mhz.txt`.
- Stronger next test after AOP -1 is stable: `uv_2_aop_level_903mhz.txt`.
- Aggressive experiment only: `uv_3_aop_level_903mhz.txt`; it may not be stable
  on every tablet.
- Normal rollback while LTBox can still run Tune GPU over ADB or Fastboot: the
  matching `stock.txt`.

No profile is guaranteed stable on every tablet. Test one file at a time and
keep the matching stock table and a separate copy of LTBox's pre-write backup.

## Choose the correct release

These profiles are for international **ROW** firmware, not Chinese **CN**
firmware. Both the region and version must match.

1. Open **Settings → About tablet** and find the ZUI version or build number.
   The wording can vary; look for a number such as `17.5.10.272`.
2. Confirm that the complete build string contains `_ROW`. If Settings does not
   show the region and you already have ADB access, connect the tablet with USB
   debugging enabled and run:

   ```bash
   adb shell getprop ro.vendor.build.fingerprint
   ```

3. Open the release with the same ZUI number:

| ROW version | Download page |
|---|---|
| `17.5.10.272` | [ZUI 17.5.10.272 release](https://github.com/ishad0w/tb321fu-android-gpu-uv/releases/tag/17.5.10.272) |
| `17.0.12.183` | [ZUI 17.0.12.183 release](https://github.com/ishad0w/tb321fu-android-gpu-uv/releases/tag/17.0.12.183) |

These two releases happen to contain identical GPU data. They remain separate
so the profile description and source record match the installed firmware.
Always use the matching release. If the tablet is CN, the version is not listed,
or the region is unknown, do not choose a profile by a similar-looking number.

## Download the two files you need

On the matching release page, expand **Assets**. Download and extract the file
ending in `_gpu_profiles.zip`.

After extracting the ZIP, open its top-level folder. Its name must end with the
same ZUI number as the release you downloaded. For release `17.5.10.272`, open
`TB321FU_ROW_ZUI_17.5.10.272`; a minimal selection looks like this:

```text
TB321FU_ROW_ZUI_17.5.10.272/
├── stock/
│   └── stock.txt
└── uv/
    └── uv_1_aop_level_903mhz.txt
```

You need exactly two importable files from that matching firmware folder:

1. `stock/stock.txt` — the original GPU table for rollback;
2. one file from `uv/` — the undervolt profile you want to test.

For desktop LTBox, keep the files on the computer where LTBox runs; its Import
button opens a desktop file picker. For an Android KonaBess workflow, copy them
to accessible storage on the tablet. In either case, keep another copy on a
different device or in cloud storage.

Do not import `stock.dts`, `diff_*.md`, or `release_report.md`. They are readable
evidence, not profiles.

## Choose a profile

Every file below is generated directly from stock; the list is not an
installation sequence. In the modeled comparison for both supported firmware
tables, AOP -1 is the mildest profile, followed by Generic -2. AOP -2 and
Generic -3 cross at different GPU points, so neither is always deeper. AOP -3
is the most aggressive published profile.

| Profile | Why choose it | Trade-off |
|---|---|---|
| `stock.txt` | Restore the release's stock table while LTBox can run Tune GPU over ADB or Fastboot | No undervolt; not raw-image recovery |
| `uv_1_aop_level_903mhz.txt` | First UV test or the mildest available change | Mildest table change; any power saving may also be small |
| `uv_2_level_903mhz.txt` | Reproduce the traditional Generic/MinusZero-style approach | Moderate but uneven modeled shift |
| `uv_2_aop_level_903mhz.txt` | Move beyond AOP -1 with a defined shift of up to two firmware-listed positions | Equal to or deeper than Generic -2 in the modeled comparison for both supported firmware tables; stability still depends on the tablet |
| `uv_3_level_903mhz.txt` | Test the stronger traditional Generic variant | In the modeled comparison, some points are milder than AOP -2 and others are deeper |
| `uv_3_aop_level_903mhz.txt` | Deliberately test the most aggressive published profile | Experimental and expected to carry the greatest instability risk |

> [!CAUTION]
> **Exact AOP -3 is an experimental and deliberately aggressive profile, not
> one recommended for general use.** A tablet that is fully stable on AOP -2 can
> still show artifacts, GPU faults, freezes, or reboots on AOP -3. Stability
> margins vary between individual chips, so this profile may work normally on
> one tablet and fail on another. Test it only with a working rollback path;
> one short benchmark run is not enough to establish stability.

The Generic comparisons are estimates based on Qualcomm's public Gen7 driver
code. Lenovo's packaged driver was not independently matched to that source.

Potential power and thermal benefits are most relevant during sustained
GPU-bound games, emulation, or rendering at middle and high frequencies. A
stable undervolt may reduce GPU power and heat or delay thermal throttling; it
does not automatically increase FPS. Expect little or no gain at idle, in
CPU-bound workloads, or when another limit dominates. Deeper is not always more
efficient: errors, resets, or clock fallback can erase any saving. Compare at
the same workload, FPS target, temperature, and power mode.

## Why Exact AOP and Generic differ

**Generic** uses LTBox's general list of encoded regulator requests. Some
entries in that list are absent from this firmware. Under the public driver
model, those requests round up to the next available value, so Generic -2 or
-3 can produce fewer than two or three modeled steps at some frequencies.

**Exact AOP** uses only values found in the matching firmware. AOP -2 means two
available list positions wherever the table is not already near its lower
limit. This makes the table edit easier to calculate; it does not prove lower
power or better stability. The positions are not fixed millivolt steps. Under
the same model at 903 MHz, Generic -2 and Generic -3 both resolve to the AOP -1
request, while AOP -2 and AOP -3 go deeper.

The technical reference contains the exact
[per-frequency profile matrix](technical-reference.md#per-frequency-profile-matrix),
all 47 row results, source links, and transformation formulas.

## Choose 903 or 905 MHz

- **903 MHz** preserves every stock frequency. Use it when you want to test
  only the undervolt.
- **905 MHz** changes every 903 MHz table cell to 905 MHz. It is a visible
  marker that can help confirm that the edited table is active.

The marker changes frequency by only `+0.2215%`. It provides no meaningful
performance gain and changes a second test variable. Choose 903 MHz unless you
specifically want the marker.

For the same family and step count, the 903 and 905 MHz profiles have identical
regulator requests and identical non-frequency table fields. The 905 MHz
variant changes only the three stock frequency cells equal to 903 MHz.

## Apply, verify, and roll back

The steps below use the current desktop labels; labels may change in later
releases. Before writing anything, keep two different forms of rollback:

- the release's `stock.txt` for restoring the GPU table while LTBox can still
  run Tune GPU over ADB or Fastboot;
- the complete `backup_konabess` folder created automatically by a successful
  LTBox inspection. It captures the image pair currently on the tablet.

`backup_konabess` is not downloaded from this repository or a release. LTBox
creates it after Tune GPU finishes reading the tablet and reports the saved
path in its output or log. Open that displayed path and copy the entire
`backup_konabess` folder elsewhere before continuing.

The folder is factory-stock only if the tablet's image pair was factory-stock
when LTBox read it. Keep its two images together and do not mix files from
different inspections. Every later successful inspection overwrites the fixed
backup folder with the tablet's then-current image pair. These are raw images
for an emergency EDL recovery procedure, not a one-click restore; emergency
recovery is outside this guide.

If LTBox does not yet recognize the tablet or you do not have the correct
TB321FU EDL loader, first follow its official
[connection guide](https://miner7222.github.io/ltbox/en/connecting-a-device.html)
and [EDL loader guide](https://miner7222.github.io/ltbox/en/root-a-device.html#edl-loader).

To apply a profile with LTBox:

1. Connect the tablet through authorized USB debugging or Bootloader/Fastboot.
   Open **Tune GPU**, choose the correct **EDL Loader**, and click **Next**.
2. When inspection finishes, copy `backup_konabess` elsewhere before
   continuing. If the table was already modified, do not label this copy as
   stock.
3. In **Select Target DTB**, verify the target. For the releases above, the
   source is DTB #4: `qcom,pineapple`, `Adreno750v2`, 4 groups and 47 levels.
   Select it only when LTBox shows matching details; otherwise stop.
4. On **Device GPU Table**, click **Import**, select the chosen `uv_*.txt`, and
   verify its description and values.
5. Click **Next**. On **Confirm KonaBess Operation**, review the summary and
   click **Start**.
6. Reboot and inspect the table again in LTBox. Check the changed rows against
   the matching readable `diff_*.md` attachment from the release. With a 905
   MHz profile, also check for the 903 → 905 MHz marker. Do this only after
   copying the pre-write backup elsewhere: this new inspection overwrites the
   automatic backup with the modified image pair.
7. Follow the [stability test procedure](validation-and-testing.md#device-stability-testing)
   before trying a deeper profile.

KonaBess users import the same `.txt` profile but should follow the backup and
write procedure for their KonaBess version.

If a profile is unstable and LTBox can still run Tune GPU over ADB or Fastboot,
import the matching `stock.txt`, write it through the same workflow, and reboot.
`stock.txt` restores only the GPU table. It is not raw-image recovery and cannot
recover a tablet that LTBox can no longer reach through this workflow.

## Common questions

### What does the filename mean?

`uv_2_aop_level_903mhz.txt` means: undervolt (`uv`), up to two positions down
(`2`), firmware-derived Exact AOP list (`aop`), and unchanged stock 903 MHz
frequency. A filename without `aop` is Generic. The numbers -1, -2, and -3 are
list positions, not millivolts or percentages.

### Do I need firmware images or the build script?

No. `vendor_boot.img`, `aop.mbn`, and `build_profiles.py` are only for
maintainers generating a new firmware set. Installing an existing release
requires `stock.txt` and one `uv_*.txt`.

### Is `Found N advisory finding(s)` in LTBox an error?

Not by itself. For the packaged profiles, independent decoding against the
current LTBox validator rules gives these expected counts:

| Profile | Expected advisories |
|---|---|
| Generic -2, 903 or 905 MHz | 8: four requests at `48` and four at `16` |
| Generic -3, 903 or 905 MHz | 12: four requests at `48` and eight at `16` |
| Exact AOP -1/-2/-3, 903 or 905 MHz | 0 |

The Generic findings are non-blocking range warnings for encoded requests
outside LTBox's observed stock range. Expected counts do not prove electrical
stability. If the count differs, inspect every finding and any hard error, then
verify the exact profile file, firmware release, and selected GPU table before
writing anything.
