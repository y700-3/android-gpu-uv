# Lenovo Y700 Gen 3 (TB321FU) GPU undervolt

<p align="center"><a href="README.md">English</a> | <a href="README_ru.md">Русский</a></p>

Ready-to-import GPU undervolt profiles for the Lenovo Legion Y700 Gen 3
(`TB321FU`) for LTBox's **Tune GPU** workflow and KonaBess.

The repository provides stock tables and two undervolt approaches for
supported ROW firmware:

- **Exact AOP** uses encoded regulator levels from the firmware itself, so one
  step in the filename means one position in that firmware list until its lower
  limit;
- **Generic** uses the traditional picker used by LTBox, following the same
  general approach as the older MinusZero profiles.

If you only want to choose a file and install it, start with the
**[profile guide](docs/profile-guide.md)**. It explains where to download the
profiles, what to keep for rollback, which profile fits each use case, and how
to test it.

## Available releases

- [ZUI 17.5.10.272](https://github.com/ishad0w/tb321fu-android-gpu-uv/releases/tag/17.5.10.272)
- [ZUI 17.0.12.183](https://github.com/ishad0w/tb321fu-android-gpu-uv/releases/tag/17.0.12.183)

## Documentation

- [Choose and use a profile](docs/profile-guide.md)
- [Test stability and investigate failures](docs/validation-and-testing.md)
- [Technical profile reference](docs/technical-reference.md)
- [Build profiles from firmware](docs/building.md)

> [!WARNING]
> Use this project entirely at your own risk. Modifying GPU power tables can
> cause instability, data loss, boot failure, or device damage. The repository
> owner, authors, and contributors accept no responsibility for any resulting
> damage, loss, or other consequences.
