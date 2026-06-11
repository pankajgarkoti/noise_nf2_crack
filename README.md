# NoiseFit Watch Reverse Engineering

Reverse-engineering the NoiseFit Android app (`com.noisefit`) to understand and
repurpose Noise smartwatch hardware (sensors, touchscreen, BLE).

## What's here

- **[`CRACKED.md`](./CRACKED.md)** — ✅ **DONE.** Full host-side control of a real
  **Noise NF 2 (NSW-421)** achieved over BLE with a from-scratch Python client
  (`watch_client/`). The device uses a 4th, simplest stack — ZH/Zhapp "Apricot"
  cleartext protobuf — fully reverse-engineered: bind without crypto, issue any
  command, sync health data, switch watch faces, buzz the hardware, OTA handshake.
- **[`FINDINGS.md`](./FINDINGS.md)** — append-only research log. Documents the app
  stack, BLE protocols, chipsets, OTA mechanisms, and complete wire formats.
- **`watch_client/`** — the working NF 2 client (transport, codec, recovered proto
  schema, bind, health sync, watch-face control, OTA probe, interactive CLI).
- **`tools/`** — `.proto` schema recovery from jadx output and from `.dex` string
  pools (for classes jadx couldn't decompile).
- **`watch_python_src/`** — the embedded **RT-Thread / PersimWear** Python source
  extracted verbatim from the APK (`assets/python/`). This is plaintext driver code
  for the high-end watch line: MCF framing, uRPC, UDB (micro debug bridge), OTA,
  dial/app management, raw flash access (FAL/SFUD), and a remote MSH shell.

## Key findings (summary)

The app is a universal driver bundling ~10 chipset/SDK families. Three protocol
stacks matter:

1. **IDO "VeryFit V3" on Realtek** (budget line) — closed binary BLE protocol;
   full command opcode map and C source tree leak from `libVeryFitMulti.so`.
2. **RT-Thread + PersimWear** (high-end line) — **open, documented** stack. Remote
   shell, arbitrary native FFI calls, flash dump/reflash, custom app + watch-face
   install, OTA, on-device Python. Complete wire format reverse-engineered.
3. **"Creek" (Flutter/Dart)** — Noise-native protobuf protocol over
   `flutter_blue_plus` with AES/RSA/ECC pairing (`pspkey`).

See `FINDINGS.md` sections 4 (IDO), 10 (Creek), and 15 (PersimWear wire format)
for protocol details.

## Reproducing the analysis

The input XAPK and all extracted/decompiled artifacts are **gitignored** (hundreds
of MB, reproducible). To regenerate:

```sh
# 1. Place com.noisefit_<version>.xapk in this directory
unzip -o com.noisefit_*.xapk -d extracted

# 2. Native libs (BLE protocol, Flutter AOT)
unzip -o extracted/config.arm64_v8a.apk -d extracted/libs_tmp

# 3. Embedded Python (plaintext driver source)
unzip -o extracted/com.noisefit.apk "assets/python/*" -d extracted/assets_tmp

# 4. Decompile Java/Kotlin (requires jadx + JDK)
jadx --no-res --deobf -d extracted/jadx_out extracted/com.noisefit.apk
```

## Tooling

- `unzip` (XAPK is a plain zip of split APKs)
- `strings` / `grep` for native `.so` analysis
- `jadx` for Java/Kotlin decompilation (install via `brew install jadx openjdk`)

## Disclaimer

For interoperability, research, and personal-device repurposing only. Respect the
relevant licenses and terms; do not use against devices you do not own.
