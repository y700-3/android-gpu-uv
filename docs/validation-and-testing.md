# Validation and stability testing

Profile correctness has several independent layers. Passing an earlier layer
does not prove the later ones: a well-formed table can still be electrically
unstable on a particular tablet.

## Contents

- [Validation layers](#validation-layers)
- [Builder validation boundary](#builder-validation-boundary)
- [KGSL request mapping](#kgsl-request-mapping)
- [LTBox findings](#ltbox-findings)
- [What requires manual evidence](#what-requires-manual-evidence)
- [Device stability risks](#device-stability-risks)
- [Device stability testing](#device-stability-testing)
- [Failure investigation](#failure-investigation)
- [Evidence standard](#evidence-standard)

## Validation layers

| Layer | Question | Evidence |
|---|---|---|
| Container | Can the complete profile be decoded and parsed? | Base64, gzip, JSON, chip identity, and full-table parsing |
| Structural transformation | Did only the declared cells change? | Stock comparison, unique rows, descending frequencies, unchanged metadata, and local release diffs |
| Firmware semantics | What requests can this firmware actually express? | Selected DTB, exact `aop.mbn` `gfx.lvl`, KGSL mapping, source hashes, and local release report |
| Tool integration | Can the exact LTBox/KonaBess version validate and apply it to the intended table? | Parser, validator, normalization, replacement, and image round-trip checks |
| Device stability | Does one physical tablet remain correct across workloads and conditions? | Repeated transition-heavy, thermal, suspend/resume, and output-checked tests |

Container and structural success are necessary but do not establish safe
voltage margin. Tool integration confirms that the profile was applied as
intended, not that the resulting operating points are electrically reliable.

## Builder validation boundary

`build_profiles.py` establishes that the inputs are structurally parseable,
the configured semantic DTB and AOP data are present, and every generated table
matches its declared deterministic transformation. The exact mechanical checks
and [artifact lifecycle](building.md#outputs-and-failure-behavior) are documented
in the [build guide](building.md#automatic-selection-and-validation).

These checks do not authenticate an `Algorithm: NONE` image, prove that both
inputs came from one package, validate tool integration, or establish
electrical stability. Each later layer requires separate evidence.

## KGSL request mapping

The active `gfx.lvl` values come from each firmware's `aop.mbn`. Generic picker
profiles may request values absent from that list. Qualcomm's public Gen7 KGSL
path selects the first supported dependency value greater than or equal to the
request:

- [`adreno_gen7_rpmh.c`](https://github.com/qualcomm-linux/kgsl/blob/a183ffbab70cc9fc2f29092bce45fcbe9111b410/adreno_gen7_rpmh.c)
- [`adreno_rpmh.c`](https://github.com/qualcomm-linux/kgsl/blob/a183ffbab70cc9fc2f29092bce45fcbe9111b410/adreno_rpmh.c)

The builder uses this ceiling rule to report effective generic-profile drops.
Any independent comparison between a packaged KGSL module and the public
implementation must be documented separately from the generated report. Do
not carry that evidence over to another release.

Exact-AOP profiles request only values recovered directly from `gfx.lvl`, so
they avoid the generic picker's unsupported intermediate requests. This makes
their intended shift through firmware-supported AOP values easier to reason
about, but does not make the shift automatically stable.

## LTBox findings

The [LTBox 3.2.7 validator](https://github.com/miner7222/LTBox/blob/v3.2.7/crates/ltbox-patch/src/konabess/export.rs#L116-L125)
distinguishes blocking structural errors from advisory findings. An
outside-observed-range advisory can identify a low encoded request without
proving that the profile is malformed. Its significance depends on the exact
firmware mapping shown in the corresponding local release diff. Later LTBox
versions may use different advisory ranges or validation rules.

Interpret the results conservatively:

- advisory findings are not automatically hard errors;
- zero advisories do not prove electrical stability;
- successful parsing does not prove the correct DTB was selected;
- successful replacement does not prove the tablet can execute every point;
- a stable benchmark run does not prove transition, thermal, or output
  correctness under other workloads.

Record the LTBox version, error and advisory counts, round-trip evidence, and
known limitations in separate release notes or external evidence, not in the
builder-owned `source/release/` directory. Do not carry integration results
from one release to another merely because their table shapes are similar.

## What requires manual evidence

The [source trust boundary](building.md#source-identity-and-trust-boundary)
explains what the two builder inputs can establish about package provenance and
input pairing, and which metadata remains external. Separate review remains
necessary for:

- exact KGSL module identity and disassembly;
- LTBox/KonaBess version-specific behavior;
- external research links and interpretation;
- physical tablet stability, power, temperature, and performance results.

Manual release evidence must describe only checks actually performed for that
firmware and remain separate from the generated report. Omitting a claim is
better than carrying evidence over from a nearby patch release.

## Device stability risks

There is no universal undervolt curve that is safe for every sample of one SoC.
Minimum stable voltage depends on workload, process variation, temperature,
droop, aging, and transition behavior. Failure may appear as:

- visual corruption or intermittent artifacts;
- GPU, GMU, or IOMMU faults;
- application crashes, device hangs, or resets;
- failure only after heat soak or cold start;
- failure only while moving between operating points;
- silent incorrect computation without a visible crash.

Published GPU voltage-margin experiments show strong workload dependence and
the possibility of incorrect output; see
[Leng et al., MICRO 2015](https://www.cs.sjtu.edu.cn/~leng-jw/resources/Files/leng15micro-gpuvminexp.pdf).
The broader timing-fault risk of unsafe software-controlled voltage/frequency
points is demonstrated by
[CLKSCREW](https://www.usenix.org/system/files/conference/usenixsecurity17/sec17-tang.pdf).

## Device stability testing

Use the matching stock profile as the control and change one profile dimension
at a time.

### 1. Confirm application

- verify the imported description and firmware set;
- confirm the intended DTB/table in LTBox;
- when using a 905 MHz variant, verify only the expected 903 MHz points changed;
- retain `stock.txt` before extended testing.

### 2. Exercise sustained load

- run a long GPU-heavy workload, not just a short benchmark pass;
- include at least one game or emulator representative of real use;
- monitor frequency, temperature, clock transitions, and visible output;
- repeat after the device reaches thermal equilibrium.

### 3. Exercise transitions

Linux devfreq can automatically move supported devices among frequency states
under a governor; see the
[kernel devfreq documentation](https://docs.kernel.org/driver-api/devfreq.html).
Test behavior that repeatedly changes load:

- launch/exit cycles;
- menus alternating with heavy rendering;
- app switching and picture-in-picture;
- frame-rate changes;
- idle-to-load bursts;
- repeated benchmark scene transitions.

### 4. Exercise system state changes

- cold boot and first heavy load;
- heat-soaked load;
- idle and wake;
- suspend and resume;
- screen off/on;
- charging and battery operation;
- repeated reboot cycles.

### 5. Check correctness, not only survival

Prefer workloads with known output, image comparison, error counters, or
repeatable checksums where possible. A completed run with silent rendering or
compute errors is not a stable result.

### 6. Compare with stock

When a failure occurs, reproduce the same sequence with the matching stock
profile. A failure that persists at stock is not evidence against the UV curve.
Conversely, one clean stock run does not by itself localize a marginal UV row.

No finite test proves permanent stability. Repeated tests across workloads,
temperatures, transitions, and device states provide stronger evidence than a
single benchmark.

## Failure investigation

Inspect kernel and Android logs for terms such as `kgsl`, `gmu`, `gpu fault`,
`snapshot`, `hang`, `reset`, `iommu`, `smmu`, and `page fault`.

Record:

- exact firmware and profile filename;
- workload and scene;
- temperature and power state;
- observed frequency near the failure;
- whether the failure reproduces after reboot;
- whether stock reproduces it;
- relevant log excerpts.

If one frequency region is implicated, raise its vote by one directly supported
AOP position and retest the same sequence. Do not compensate by changing ACD,
bus, dependency, SKU, or speed-bin data without separate characterization.

## Evidence standard

A useful stability report identifies the source and test conditions rather
than saying only that a profile "works." At minimum include:

| Field | Example content |
|---|---|
| Device and firmware | `TB321FU`, complete ZUI build ID |
| Profile | Exact filename and SHA-256 if distributed separately |
| Tool | LTBox/KonaBess version and selected table |
| Test duration | Per workload and total elapsed time |
| Conditions | Temperature range, charging/battery, cold/heat-soaked |
| Workloads | Sustained, transition-heavy, suspend/resume, real applications |
| Correctness | Visual/output checks and fault-log review |
| Result | Stable so far, failure details, or stock comparison |

Use "stable in these tests" rather than claiming universal stability. The
[profile guide](profile-guide.md) defines the transformation represented by
each profile family.
