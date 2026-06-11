# CRACKED — Noise NF 2 (model NSW-421)

Full host-side control of a **Noise NF 2** smartwatch over BLE, achieved with a
from-scratch Python client (`watch_client/`). No phone app, no crypto.

The device turned out to be a **4th stack** not in the original FINDINGS.md
three-way split: the **ZH / Zhapp "Apricot" protobuf protocol** (`com.zhapp.ble`,
proto package `com.zh.ble.wear.protobuf`). It is the most accessible of all: the
entire protocol is **cleartext protobuf with no encryption and no crypto pairing**.

## Device

| Field | Value |
|-------|-------|
| Model | NSW-421 ("NF 2") |
| Firmware | 1.0.2 |
| BLE name | `NF 2_165A` |
| MAC | `c4:0c:83:82:16:5a` |
| Equipment no. | 32036 |
| Serial | 10005 |
| ATT MTU | 247 |

## GATT

Service `16186f00-0000-1000-8000-00807f9b34fb` (note non-standard `807f` base):

| Char | Props | Role |
|------|-------|------|
| `16186f01` | write-no-rsp + notify | **downlink** (watch→phone responses) |
| `16186f02` | write-no-rsp + notify | **uplink** (phone→watch commands) |
| `16186f03` | write-no-rsp + notify | bulk (sport/fitness history) |
| `16186f04` | write-no-rsp + notify | OTA / large-file (watch faces, firmware) |
| `16186f05` | write-no-rsp + notify | voice / Alexa audio |

Service `0xaa01` (`aa02`/`aa03`) is defined in the SDK but **unused** by NF 2.

## Wire protocol ("Apricot")

A logical message = one serialized `WearProtos.SEWear` protobuf, split into BLE
packets and flow-controlled. **No CRC at packet level, no encryption.**

```
SEWear { required uint32 id = 1;   // = SEFunctionId opcode
         oneof payload { ... } }   // typed submessage per command
```

### Packet framing (per BLE write)
```
[2-byte little-endian packet index][payload]      ; index is 1-based
index 0 is reserved for 6-byte control frames
```

### Control frames (6 bytes, index 0)
```
00 00 00 00 NN 00   announce: "N packets follow"   (either direction)
00 00 01 01 00 00   begin / go  (receiver ready to accept data)
00 00 01 02 00 00   busy
00 00 01 03 00 00   ack / done
00 00 01 05 nn 00   resend packet nn
```

### Uplink sequence (phone → watch, on f02)
1. write `announce(N)`
2. wait for watch `begin` (`00 00 01 01`) on f02
3. stream `[idx][chunk]` data packets (chunk = MTU-2 = 242)
4. watch replies `ack` (`00 00 01 03`) / `resend nn` as needed

### Downlink sequence (watch → phone, on f01)
1. watch sends `announce(N)` on f01
2. phone replies `go` (`00 00 01 01`) on f01
3. watch streams `[idx][chunk]`
4. phone replies `ack` (`00 00 01 03`) on f01; reassemble + `SEWear.parseFrom`

This is implemented in `watch_client/codec.py` + `transport.py`.

## Pairing / bind (no crypto)

Driven entirely by us; the watch only requires an on-screen accept. Sequence:

1. `INQUIRY_BINDING_STATUS` (16), `bind_account.request_binding_status=true`
   → watch returns current bound state.
2. `BINDING_CHECK` (17), **empty `bind_account`**
   → watch returns `SEBindCheck { device_verify, mac, serial_number,
     device_number, firmware_version, device_name, bind_check_result }`.
3. `BINDING_RESULT` (18), `bind_account.bind_result {
     user_id=<generated>, bind_result_type=SUCCESS, phone_type=ANDROID }`
   → watch returns `error_code = NO_ERROR (0)`; watch is now **bound**.
4. (re-`INQUIRY_BINDING_STATUS` → `true`)

`user_id` formula (from `ZhConnectHandler`): `substring(30)` of
`randomUUID() + randomInt(10..9999) + appUserId`. There is **no** numeric code or
key exchange — `device_verify=true` only means the watch shows an accept prompt.

## Capabilities demonstrated (all live, over our own client)

| Capability | How | Result |
|---|---|---|
| Read device info | `GET_DEVICE_INFO` (32) | fw/mac/serial/battery |
| Read battery | `GET_DEVICE_BATTERY_STATUS` (33) | 63%, NOT_CHARGING |
| List watch faces | `GET_WATCH_FACE_LIST` (80) | 3 faces + current flag |
| **Switch watch face** | `SET_WATCH_FACE` (81) | `setting_result=SUCCESS`, UI changed |
| Set system clock | `SET_SYSTEM_TIME` (48) | `error_code=0`, clock updated |
| **Buzz the watch** | `FIND_WEAR` (161) `find_phone_mode=START` | vibrate + watch icon on screen |
| Bind/pair | opcodes 16/17/18 | `bound=true` |
| Health time-series sync | `GET_FITNESS_TYPE_ID_LIST` (112) → `REQUEST_FITNESS_TYPE_ID` (113) → `CONFIRM` (115) | typed daily/activity records w/ timestamps |
| Raw HR sensor switch | `PHONE_SEND_HEART_RATE_SWITCH` (4098) | `error_code=0` (sensor on) |
| Factory file push | `FILE_INFO_UPDATE` (4097) incoming | internal RTC/voltage power logs |
| Watch-face install handshake | `PREPARE_INSTALL_WATCH_FACE` (83) | `prepare_status=READY` (f04 upload open) |
| **OTA firmware handshake** | `PREPARE_OTA` (144) `force=false` | `prepare_status=READY` |
| OTA resume support | `REQUEST_BREAKPOINT_CONTINUATION_STATE` (148) | `support=true` |

## Notable

- **No encryption anywhere.** `SEWear.parseFrom()` runs directly on bytes off the
  characteristic. The "Creek" `pspkey` RSA/ECC handshake from FINDINGS.md §10 does
  **not** apply to this device.
- **OTA path is wide open at the protocol layer:** the watch goes `READY` for an
  arbitrary firmware version with no signature check during the handshake. The only
  remaining anti-custom-firmware gate is whatever the **bootloader** verifies on
  the image itself (not yet tested). **We deliberately did not flash** — see safety.
- The watch volunteers internal **diagnostic log files** (RTC/voltage/battery) over
  the factory channel unprompted, and exposes raw HR / gsensor / triaxial sensor
  switches via `SEFactory`.

## Safety / brick-avoidance

We did **not** write to the OTA (f04) data channel or send any firmware/large-file
binary. Per HANDOFF.md rules, actual flashing should only be attempted after a full
partition dump + a verified hardware-recovery path (likely a SoC UART/USB download
mode). All OTA work here is **read-only handshake probing**.

## Toolkit (`watch_client/`)

| File | Purpose |
|---|---|
| `scan.py` | BLE scan + GATT dump + stack classification |
| `codec.py` | Apricot framing codec (announce/index/begin/ack/resend) |
| `transport.py` | bleak transport, bidirectional flow control |
| `client.py` | high-level send/recv of `SEWear` messages |
| `pb_loader.py` | loads the generated protobuf bindings |
| `proto/`, `pb/` | recovered `.proto` schema (18 files) + compiled Python |
| `milestone_a.py` | `GET_DEVICE_INFO` smoke test |
| `bind.py` | bind/pair flow |
| `milestone_b.py` | battery / faces / set-time / buzz demo |
| `health.py` | fitness/health time-series sync |
| `factory.py` | factory/diagnostic channel (logs, raw HR) |
| `watchface.py` | list/switch faces + install handshake probe |
| `ota_probe.py` | **read-only** OTA handshake probe |
| `cli.py` | interactive prompt to fire any opcode |

### Schema recovery (`tools/`)

- `extract_protos.py` — recover `.proto` from jadx-emitted `*Protos.java`
  (decodes the embedded `FileDescriptorProto` string literal).
- `dex_protos.py` — recover `.proto` directly from a `.dex` string pool
  (handles classes jadx failed to decompile, e.g. `BindAccountProtos`).

## Quick start

```sh
uv venv && uv pip install bleak protobuf grpcio-tools

# regenerate protobuf bindings (if needed)
python -m grpc_tools.protoc -Iwatch_client/proto \
    --python_out=watch_client/pb watch_client/proto/*.proto

# scan, then talk to the watch
python -m watch_client.scan --scan-only
python -m watch_client.milestone_a --addr <CB-UUID>
python -m watch_client.bind --addr <CB-UUID> --step full
python -m watch_client.milestone_b --addr <CB-UUID> --buzz
python -m watch_client.cli --addr <CB-UUID>
```
