# NoiseFit Watch Reverse Engineering — Findings Log

> Append-only log. Each entry timestamped/sectioned as discovered.
> Goal: understand the NoiseFit app + watch hardware/protocol to repurpose the watch.

---

## 0. Target

- **App:** NoiseFit (`com.noisefit`) v5.1.4, versionCode 743
- **Package source:** `com.noisefit_5.1.4.xapk` (267 MB)
- **min SDK:** 26 (Android 8.0), **target SDK:** 35 (Android 15)

## 1. XAPK Contents

Split-APK layout:
- `com.noisefit.apk` (203 MB) — base APK
- `config.arm64_v8a.apk` (46 MB) — native libs for arm64
- `config.en.apk` — English resources split
- `config.hdpi.apk` — hdpi drawable split

## 2. High-Level Tech Stack

- **Flutter app** — `assets/flutter_assets/`, `libflutter.so`, `libapp.so`, `modules_*.zip`
- 16 dex files (`classes.dex` .. `classes16.dex`) — large Kotlin/Java layer too
- Mapbox (`libmapbox-maps.so`) — GPS/route maps
- Microsoft Cognitive Speech + whisper/ggml — on-device/cloud voice
- Python 3.11 embedded (`libpython3.11.so`) — Chaquopy? investigate

## 3. KEY: Watch Comms Stack (the important part)

- **`libVeryFitMulti.so`** = **IDO "VeryFit Multi" SDK** (Shenzhen IDO Technology).
  - JNI class: `com.veryfit.multi.nativeprotocol.Protocol`
  - This native lib builds/parses ALL BLE protocol packets (health data,
    contacts, music, OTA, watch faces, GPS algo, etc.)
- **Chipset: Realtek** — `libRtkMediaCodec.so` exposes `com.realtek.sdk.media.*`
  (Opus codec for BLE audio/calls). Typical Noise watches = RTL8763 series BLE SoC.
- **OTA/firmware:** `libabpartool.so` (A/B partition tool), `libezip*.so`,
  `libminilzo.so` (LZO), `libactRes.so`/`libindexconvert.so` (watch-face "dial" res).

### Watch-face / dial assets (pushed to watch)
- `assets/dial/style/4362/template.bin`
- `assets/gui_dial.bin`, `assets/gui_dial_368_448.bin`, `assets/gui_dial_titan.bin`
- `assets/header.bin`, `assets/header_tp.bin`

### Notable native libs (arm64)
- libVeryFitMulti.so (BLE protocol) | libRtkMediaCodec.so (Realtek Opus)
- libabpartool.so (OTA A/B) | libezip*/libminilzo (compression)
- libscannative/libbarhopper_v3 (QR/barcode) | libwhisper/ggml (ASR)
- libpython3.11.so (embedded Python)

### VeryFit Protocol JNI methods seen (sample)
- tranDatasppisStart, AppControlAllConfigSync, appGpsAlgProcessRealtime,
  callBackEnable, GetMode/initType/initParameter, getSyncActivityDataStatus,
  getSyncGpsDataStatus, funcTableOutputOnJsonFile
- firmware_version1/2/3, "ota mode can not send cmd", music/contact sync cmds

---

## 4. IDO VeryFit V3 Protocol — Deep Dive (`libVeryFitMulti.so`)

### Native JNI entrypoints (class `com.veryfit.multi.nativeprotocol.Protocol`)
Inbound parsers:  `ReceiveDatafromBle`, `ReceiveDatafromSPP`
Mode/control:     `SetMode/GetMode`, `initType`, `initParameter`, `setMtu`, `SetPatch`,
                  `callBackEnable`, `setStreamByteFlag`, `SysEvtSet`, `unBindClearJNIData`
Sync (read data): `StartSyncHealthData/StopSyncHealthData`, `startSyncActivityData/stop`,
                  `startSyncGpsData/stop`, `startSyncGpsProzisData`,
                  `StartSyncConfigInfo/Stop`, `SetSyncHealthOffset`,
                  `getSyncActivityDataStatus`, `getSyncGpsDataStatus`
Config push:      `AppControlAllConfigSync`
GPS:              `appGpsAlgProcessRealtime`, `mkGpsSimulationData`
File transfer (FOTA + dial + photo), BLE channel:
                  `tranDataStart/Stop/Continue/SetBuff/SetPRN/SendComplete/ManualStop`,
                  `tranDataisStart`, `tranDataStartWithTryTime`
File transfer over SPP channel (audio/voice):
                  `tranDatasppStart/Stop/Continue/SetBuff/SetPRN/SendComplete/...`,
                  `tranDatasppisStartP7`, `tranDatasppStartWithTryTime`
Asset builders (run on phone, produce watch-format files):
                  `mkIsfFile`, `mkIwfFile` (watch face), `mkPhoto`, `Png2Bmp`,
                  `makeFileCompression`
Misc:             `ProtocolGetVersion`, `ProtocolJNITest`, `WriteJsonData`,
                  `funcTableOutputOnJsonFile`, `smoothData`, `EnableLog`

### Packet integrity / crypto
- CRC: `crc16_compute` (packet checksum), plus zlib `crc32*` (for files)
- Encryption: OPTIONAL AES, gated by func flag `ex_table_main11.use_aes_encode`
- Binding handshake: `protocol_v3_send_bind_device_table` with `auth_code`
  (feature `support_send_bind_device_table`). Also `BindAuth` / `BindCodeAuth`
  in control func table. => watch requires a bind/auth step before commands.
- iBeacon support: VBUS_EVT_IBEACON_GET/WRITE_UUID (can set the watch as a beacon!)

### Command opcodes (decoded from func_table symbol names; hex = cmd/sub)
- 0x33,0x04  sync v3 health data (subtypes: 0x03 hi/lo HR, 0x06 swim, 0x07 sleep,
             0x08 wear flag, 0x09 noise, 0x0B per-minute, 0x0C per-BP, 0x10 temp)
- 0x33,0x08  set watch dial (+size)
- 0x33,0x09  show detection time / HR custom mode
- 0x33,0x0A  music control (+singer/name)
- 0x33,0x11  calling quick reply / ui sports control
- 0x33,0x12  exchange data reply (realtime speed/pace)
- 0x33,0x31  get watch list / capacity size
- 0x33,0x32  set wallpaper dial / photo position move
- 0x33,0x33  sport sort / medium icon
- 0x33,0x36  schedule reminder
- 0x33,0x37  long city name (weather)
- 0x33,0x39  sync contacts
- 0x33,0x3A  v3 weather (48h / dynamic config)
- 0x33,0x3B  world time
- 0x33,0x3E  watch dial sort
- 0x33,0x60  notify add app name
- 0x33,0x75  habit data
- 0x33,0x7x  sports plan / run plan
- 0x33,0x400x BLE music
- 0x02,0xXX  GET config (0x43 goals, 0x47 walk remind, 0xA8 menu list,
             0xB5 dev log, 0xB6 health switch, 0xEA activity switch, 0xEB fw/bt ver)
- 0x03,0xXX  SET config (0x01 timezone, 0x02 alarm, 0x11 unit/temp, 0x32 night/bright,
             0x41 menstrual, 0x44 spo2, 0x45 pressure/stress, 0x60 drink, 0x62 shake,
             0x63 smart HR, 0xE6 sci-sleep, 0xE7 temp, 0xE8 fitness guidance)
- 0x05,0xXX  call notifications (0x02 missed call, 0x05 call time)
- 0x06,0xXX  notify (0x07 over find phone, 0x32/0x33 notice icon/disable func)
- 0x0A,0x06  weather sun time
- 0x11,0x02  alexa time | 0x12,0x23 alexa default lang | 0x13 app->BLE voice
- 0xD1,0xXX  large data transfer (0x08 original size) — FOTA/dial/photo channel
- 0x21,0x21  tran flash telink log (NOTE: "telink" — some variants use Telink SoC)

### Leaked C source tree (module map) — `src/main/cpp/protocol/...`
- protocol.c, protocol_v3/{protocol_v3.c, _queue.c, _health_client.c}
- base_help/: protocol_write.c, data_storage.c, app_timer_help.c, vbus.c,
  andriod_gps_realtim_process.c, mem.c
- data_tran/data_tran.c  +  platform/android/data_tran_spt/data_tran_spp.c
- protocol_v3_mod/ subdirs: alarm, ble_beep, ble_to_app (dail_change),
  bp_cal_control, habit_information, message (ancs, notice), mini_program,
  music, other (sync_contact, alg_version, app_pack_name, language_library,
  main_ui_sort, schedule_reminder, smart_competitor), peripheral (bind_device_table,
  peripheral_info, scales_model_map_table, make_qn_file), run_plan, sports,
  v3_activity (data_train, gps_data, hr_data), v3_alexa (weather, fast_message,
  voice_alarm/reminder/reply_txt), v3_health_sync (resolve_* for every metric:
  activity, body_power, bp, gps, heart_rate, hrv, noise, pressure, respir_rate,
  sleep, spo2, sport, swimming, temperature), v3_plan_sync, v3_table
  (function_table), weather, world_time

### Realtek media (calls/voice over BLE)
- `com.realtek.sdk.media.RtkMediaCodec` (encode/decode/init), `OpusCodec` (Opus)
- Confirms Realtek BLE SoC + voice-call/voice-assistant audio path


## 5. MAJOR: Two distinct watch platforms supported by this app

The app supports (at least) TWO different watch software stacks:

### Platform A — IDO "VeryFit V3" classic protocol (`libVeryFitMulti.so`)
- Closed binary BLE protocol (section 4). Most NoiseFit budget bands/watches.
- Custom packet framing + CRC16, optional AES, bind/auth handshake.

### Platform B — RT-Thread RTOS watch w/ "UDB" debug bridge (embedded Python!)
- **This is the big one for hacking.** Higher-end Noise watches run **RT-Thread**.
- The app ships a FULL embedded **CPython 3.11 (Chaquopy)** stack under
  `assets/python/` that drives the watch over an open, documented protocol.
- Components are literally the open-source **RT-Thread `mcf` + `urpc` + `udb`**
  (Apache-2.0, authors: armink, BalanceTWK, liukang, yuanjie, etc.).

#### B.1 MCF link-layer frame format (mcf/link/link.py) — FULLY KNOWN
```
[0xFC][len_hi][len_lo][frame_id][flags][ payload ... ][crc16_hi][crc16_lo][0xCF]
       \____ total frame length (incl. header+tail) ____/
crc16 computed over frame[1:-3]  (i.e. excludes head byte and the crc+end)
HEAD=0xFC  END=0xCF  HEAD_LEN=5  TAIL_LEN=1  ACK_LEN=2(crc)
Links: SOCKET=0x01, UART=0x02, USB=0x03
```
Transport protos (mcf/trans/packet.py): D2D / ARP / USER (ProtoType).

#### B.2 uRPC layer (urpc/) — Remote Procedure Call over MCF
- `exec_svc(dst, "<service_name>", payload, ...)` — call a registered service
- `exec_ffi_func(dst, "<c_function_name>", [Arg...], ...)` — **call ANY native
  C function on the watch by symbol name** with typed args (U8/U16/U32/ARRAY).
  Arg encoding in urpc/src/ffi.py (type byte, optional 2-byte len, value LE).

#### B.3 Built-in services (urpc/services/) — the watch's "ADB"
- **shell.py**: `msh_exec` => run RT-Thread **MSH/FinSH shell commands** remotely.
  Also `urpc_shell_start/end`, `cin`/`cout` for an interactive console! = RCE-by-design
- **device.py**: UDB daemon (`udbd`) — `_dev_info` keys: `udbd.mtu`, `udbd.ver`,
  `udbd.zlib.decompress`; services `devices`, `distribute_id`, `connect`,
  `disconnect`, `kill_server`, `heartbeat`. (This is `udb` = micro debug bridge ~ adb)
- **file.py**: filesystem ops (read/write/continue_write/mkdir/crc32/sha)
- **fal.py**: Flash Abstraction Layer access (raw flash partitions!)
- **sfud.py**: Serial Flash Universal Driver (external SPI flash chip access!)
- **sal.py**: Socket Abstraction Layer (network)
- **httpclient.py**: HTTP client on device
- **log.py**: device logging
- **app.py**: app service

#### B.4 wearable/ high-level API (built on uRPC FFI)
- **files/** push.py, pull.py, delete.py — full FS sync (CRC32 dedup, resume)
- **dial.py**: FFI `svc_dial_install / _uninstall / _apply / _hide / _unhide /
  svc_dial_get_current / svc_dial_info / svc_set_dial_order_info`
  => install custom WATCH FACES by pushing a file then calling svc_dial_install
- **user_app.py**: `user_app_install / user_app_uninstall / app_launch(uri) /
  svc_user_app_installed_info` + message bus `msg_recv` + base64 `dataChannel`
  => INSTALL & RUN CUSTOM THIRD-PARTY WATCH APPS. Two-way phone<->app messaging.
- **ota/**: full OTA engine. `ota_main(ota_path)`, upgrade strategies in
  ota/upgrade/: Check/CheckAble/Dir/Enter/FAL/File/Quit/Remove/SetupUpgradeInfo.
  Reports progress to watch via `svc_ota_set_sync_progress`. Pushes via file svc,
  flashes via FAL. "once in upgrade mode device cannot revert, retry hard."
- contacts.py, notification.py, time.py, weather (settings), tsdb.py
  (time-series DB -> sqlite3 export of health data!), system_data/storage.
- boot/: device boot handshake; py_patch/: hot-patch python on device.

#### B.5 Serialization
- **ubjson** (Universal Binary JSON) for compact msgs; plain JSON elsewhere.
- json_lpc.py = JSON-based local procedure call wrapper + callbacks.

### Why this matters for repurposing
Platform B is essentially a tiny computer you can:
- get a remote shell on (msh_exec)
- call arbitrary firmware C functions on (exec_ffi_func)
- push/pull arbitrary files to flash (file svc)
- read/write raw flash partitions (fal/sfud)
- install your own apps & watch faces (user_app/dial)
- flash your own firmware (ota)
ALL the framing, RPC, and service names are in plaintext Python in the APK.


## 6. Multiple BLE SDKs bundled (universal driver for many Noise models)

Decompiled `com/` shows the app embeds MANY watch SDKs + SoC vendor stacks.
Each Noise product line maps to an underlying chipset/SDK:

### NoiseFit product-line packages
- com.noisefit_colorfit_pro  — ColorFit Pro line
- com.noisefit_cf2           — ColorFit 2
- com.noisefit_evolve2       — Evolve 2
- com.noisefit_nav_plus      — Nav / Nav Plus
- com.noisefit_hybrid        — Hybrid (analog+smart)
- com.noisefit_topstep       — Topstep SDK based
- com.noisefit_ryeex_sdk     — Ryeex SDK based
- com.noisefit_zhsdk         — ZH (zhapp/zjw) SDK based
- com.noisefit_sdk_dfuture   — Dfuture SDK based
- com.noise.wear / com.noisefit.watch — RT-Thread/PersimWear high-end line

### Underlying SDK / SoC vendor packages present
- com.ido / com.veryfit          -> IDO VeryFit (Realtek SoC)  [Platform A]
- com.realthread.persimwear      -> RT-Thread PersimWear OS    [Platform B]
- com.crrepa (+ ble.nrf.dfu, ble.sifli.dfu, ble.ota.goodix)
      -> CRP/Moyoung SDK; supports Nordic nRF / SiFli / Goodix DFU
- com.realsil / com.realtek      -> Realtek RealSil BLE + OTA
- com.sifli                      -> SiFli SoC SDK
- com.jieli                      -> JieLi (AC69xx) SoC SDK
- com.bluetrum                   -> Bluetrum (AB chips) SoC SDK
- com.actions                    -> Actions Semi SoC SDK
- com.qcteam                     -> QCTeam BT SDK
- com.ryeex / com.topstep / com.zhapp / com.zjw -> respective vendor SDKs
- com.polidea (RxAndroidBle)     -> reactive BLE wrapper used app-wide

### OTA / DFU mechanisms by stack
- PersimWear: zip pkg w/ config.json + `rtthread.rbl` (RT-Thread OTA bootloader
  image), pushed via file svc, flashed via FAL. Strategies: Dir/File/FAL/Enter/
  Quit/Remove/Setup/Check/CheckAble (wearable/ota/upgrade/*.py).
- IDO VeryFit: native `tranData*` FOTA channel + libabpartool (A/B partitions).
- CRP: Nordic nRF DFU (com.crrepa.ble.nrf.dfu.*), SiFli DFU, Goodix OTA.
- Realtek: RealSil OTA.

## 7. NoiseFit-native BLE protocol (`com.noise.wear`) — Protobuf based

Separate from IDO; this is Noise's own clean Kotlin BLE stack (used by PersimWear
line and possibly others). Key pieces:
- `engine/DeviceEngine` + `service/DeviceService` (singleton BLE engine)
- `protocol/protobuf/` — uses **Protocol Buffers** for message bodies
- `tool/UUIDTool` — GATT UUIDs built from 16-bit shorts on standard base
  `0000xxxx-0000-1000-8000-00805f9b34fb`; CCCD = 0x2902 (notifications)
- `tool/CRCTool`, `tool/CRCCoder` — CRC integrity
- `tool/Base64Coder`, `tool/ImgUtil` (image/watchface conversion)
- entities: ConnectionState, DeviceBattery, DeviceCommand/ControlCommand,
  FirmwareUpdate, FindPhone, Call(Answer/Reject/Mute), DeviceReset, Encrypt,
  BleAdvertise, DeviceRequest, ResponseResult, NSResult
- **Encryption modes** (`entity/Encrypt`): OPEN (none) / AES / JSON.
  => OPEN mode exists = unencrypted comms possible, ideal for sniffing/RE.

### PersimWear Java<->Python bridge (`com.realthread.persimwear`)
- module/: PyBridge, PyLpc, JavaLpc, URPC, AssetExtractor (unpacks assets/python)
- api/: ApplicationManager (install/launch apps), DialManager (watch faces),
  Files, Firmware (OTA), Contacts, WearMessage (msg bus), WearSystem, Settings,
  Tsdb (health time-series), Speed, Dcm, Env, SystemStorage, PersimLog
- common/: ZipHandler, FileChecker/CheckFile (crc/sha verify), WearNotification,
  Promise, ExecService, Permission

## 8. uRPC raw-flash & shell capabilities (confirmed from urpc/services/fal.py)
FFI functions callable on device:
- fal_partition_find(name)               -> partition handle
- fal_partition_read(part, addr, buf, len)
- fal_partition_write(part, addr, buf, len)
- fal_partition_erase(part, addr, size)
- fal_partition_write_file(file, part, offset)
- fal_crc32_calculate(part, addr, size)
=> Full dump/reflash of ANY flash partition over the wire.
Plus sfud.py = direct external SPI-flash chip access; shell.py = msh_exec RCE.

## 9. Hardware summary (inferred)
- High-end line: RT-Thread + PersimWear UI, project codes C10/C12/C15/C16/C18/C19,
  external SPI flash (sfud), FAL partitions, .rbl firmware. SoC likely Realtek
  RTL8763 family (matches libRtkMediaCodec + RealSil presence) but could vary.
- Budget line: IDO VeryFit on Realtek; other models on Nordic/SiFli/Goodix/JieLi/
  Bluetrum/Actions depending on product.
=> Identify YOUR exact watch via its BLE name/adv + which connect path the app uses.

## 10. THE MODERN STACK: "Creek" SDK (Flutter/Dart) — primary protocol

The current NoiseFit app UI is Flutter; the real protocol logic lives in Dart
"Creek" packages (Creek = Noise internal codename; cf. com.noisefit.noisefit_creek).
All source paths are visible in libapp.so (AOT) strings.

### Creek package family
- creek_sdk (core), creek_blue_manage (BLE conn mgr, uses **flutter_blue_plus**),
  creek_dial_sdk (watch-face authoring), creek_index_convert (dial res convert),
  creek_voice_assistant (+ azure_speech), creek_sleep_stage_algorithm, creektool,
  creek_ffmpeg_kit_flutter
- Flutter MethodChannels: MethodChannelCreekBlueManage, MethodChannelCreekIndexConvert

### Creek BLE manager internals (package:creek_blue_manage/*)
- creek_command_protocol.dart, cmd_id_manager.dart (CmdId enum, cmdIdToCoding/Decoding,
  CmdIdMapper), creek_head_model.dart (packet header), creek_parsiong.dart (parser),
  creek_transport_Interconnection.dart, creek_retry.dart, creek_foundation_command.dart,
  creek_ble_manger.dart, creek_device_manage.dart, creek_device_info.dart,
  creek_file_philips.dart (Philips sleep?), creek_global_monitoring/notice.dart
- Local DB (sqflite): creek_db_manage + per-metric models: activity, goals,
  heart_rate, hrv, noise, philipsSleep, respiratory, sleep, sleep_stage, spo,
  sport, stress, temperature, sync_health/state/time, ota_config

### Creek Protobuf schema (package:creek_blue_manage/proto/*.pb.dart) — the message set
binding, pspkey, deviceinfo, userinfo, system, update, activeOta, mtu, log, event,
health, healthhead, healthRealtime, healthsnapshot, monitor, sleepMonitor,
waterMonitor, vitalityScore, trainingLoad, cardioFitness, sportPrescription,
bloodPressure, respiratory(in health), menstrual, medicine, standing, hydrate/
waterAssistant, morning, sport, tracking, geo, ephemeris, offlineMap, weather/
zsWeather, prayer, alarm, schedule, calendar, event, focus, disturb, gesture,
hotKey, volumeAdjust, ring, screen, watchdial, watchdirection, watchSensor,
font, language, wordtime/time, music, call, message, contacts, contactssos,
findphone, card, alipay, qrcodeList, applets/appList/appTable, skill, voice,
alexa, irCalibrationButton, commonError, table, netWork, monitor
=> Essentially every watch capability has a typed protobuf request/response.

### Crypto / pairing handshake (Creek)
- Packet body encryption modes (from com.noise.wear.entity.Encrypt): OPEN / AES / JSON
- Public-key handshake: proto `pspkey` + Dart `requestPSPkey`, `keyId`+`publicKey`,
  uses pointycastle: RSA (RSAPublicKey/RSAPrivateKey) and ECC present.
  Stored as DEVICE_INFO.pspkey. `bind_method_support` field gates binding method.
- AES via package:encrypt + pointycastle. CRC via creek tools.

### Device info model (full DEVICE_INFO SQLite schema captured in log notes)
Includes: device_id, major/minor/micro version, pair_flag, platform, shape,
dev_type, mac_addr, bt_addr, bt_name, gpsSocName, width/height/angle, recoveryMode,
product_id, factory_id, customer_id, production_date, batch_num, serial_num,
color_code, fw/nw/font versions, bind_method_support, ring* (smart ring support!),
phoneBookMax, parameters TEXT, protobuf TEXT, pspkey.
(Note "ring*" columns => the same app/protocol also drives Noise smart RINGS.)

## 11. Observed GATT UUID candidates (from full decompile)
Standard base = 0000xxxx-0000-1000-8000-00805f9b34fb unless noted.
- 0x1101 SPP (BT Classic serial) — audio/voice/large transfer on some models
- 0xfe78 — Realtek/Realsil (RTK) service
- 0x1530/0x1531/0x1532 — Nordic legacy DFU (nRF models)
- 0xffd3/ffd4/ffd5/ffd8 — JieLi-style vendor service
- 0x2001/2002/2003, 0x3d01/3d02, 0x2002.. — custom data/notify chars
- non-standard bases seen: *-3c17-d293-8e48-14fe2e4da212 (Telink-style),
  16186f0x-...-00807f9b34fb (note 807f, custom)
- 6A24EEAB-... / 258EAFA5-... / d4438b13-... likely Mapbox/3rd-party, not watch.
Exact Creek service/char UUIDs are resolved at runtime via flutter_blue_plus
(serviceUuid/characteristicUuid getters) — sniff to confirm per-model.

## 12. PRACTICAL: how to start talking to YOUR watch
1. Identify the stack: connect with nRF Connect / a BLE scanner, note the
   advertised name + GATT services. Match service UUID against section 11:
   - RT-Thread/PersimWear (C10..C19, .rbl) -> use MCF framing (sec 5.B.1) over
     its UART-like notify/write characteristic; then uRPC (shell/file/fal/dial/app).
   - Creek protobuf -> do pspkey handshake, then protobuf cmds (sec 10).
   - IDO VeryFit -> feed bytes into Protocol.ReceiveDatafromBle JNI / replicate
     V3 packet format (sec 4), bind via bind_device_table + auth_code.
3. For PersimWear watches the easiest win: replicate mcf/link/link.py framing +
   urpc exec_svc("urpc_shell_start") / shell_exec("<msh cmd>") for a remote shell;
   then fal_partition_read to DUMP firmware, file push to add apps/dials.
4. Health data export: tsdb on device -> tsdb_to_sqlite3 (already in python pkg).

## 13. Repurposing ideas (given the above)
- PersimWear line: push & launch custom apps (user_app_install/app_launch URI),
  custom watch faces (dial svc), get MSH shell, dump/reflash flash (fal/sfud),
  run on-device Python patches (py_patch). Closest to a true open dev board.
- Use as a BLE sensor hub: read HR/SpO2/accel/temperature/noise via health protos
  or VeryFit sync; the watch can also be set as an iBeacon (VeryFit VBUS_EVT_IBEACON).
- Drive the touchscreen/UI from your own app via the message bus + data channel.
- Voice: Opus audio path (Realtek RtkMediaCodec) + Alexa/Azure speech pipeline.

## 14. Tooling used
- Extract: unzip (XAPK is a plain zip of split APKs).
- Native libs: strings/grep on libVeryFitMulti.so, libapp.so (Flutter AOT),
  libRtkMediaCodec.so, libabpartool.so.
- Embedded Python: plaintext under assets/python (mcf/urpc/wearable) — READ DIRECTLY.
- Java/Kotlin: jadx --no-res --deobf (16 dex, ~37k classes) -> extracted/jadx_out.
- Env installed via brew: openjdk, jadx.

## 15. COMPLETE WIRE FORMAT — PersimWear MCF/D2D/uRPC (enough to build a client)

Full stack (outer -> inner):
```
MCF link frame  ->  TransPacket  ->  D2D packet  ->  uRPC payload
```

### 15.1 MCF link frame (mcf/link/link.py)
```
offset  field
0       0xFC                      (HEAD)
1..2    frame_len (big-endian)    total length incl head+tail (and crc if used)
3       frame_id                  incrementing 0..255
4       flags                     (0)
5..     payload (= TransPacket)
[-3..-2] crc16 (big-endian)       OPTIONAL (only if link uses crc); over frame[1:-3]
[-1]    0xCF                      (END)
```
HEAD_LEN=5, TAIL_LEN=1, ACK/CRC_LEN=2. crc16 = MCF crc16 (see mcf_utils.crc16).
Link types: SOCKET=1, UART=2, USB=3 (over BLE it's a UART-like notify/write char).

### 15.2 TransPacket (mcf/trans/packet.py)
```
byte 0   proto type   (D2D / ARP / USER ; D2D is what uRPC uses)
byte 1.. payload      (= D2D packet)
```

### 15.3 D2D packet (mcf/trans/d2d.py)  — 4-byte header
```
byte 0   src_id
byte 1   dst_id              (daemon/device id; client uses 1 for the watch daemon)
byte 2   pkt_id              (incrementing 0..255)
byte 3   pkt_info bitfield:
            bits 7..6  type   (REQ=0, RSP=1, ACK=2, BROADCAST=3, PROXY=4)
            bit  5     need_ack
            bit  4     need_rsp
            bits 3..2  priority
            bits 1..0  reserve
byte 4.. payload              (= uRPC payload)
```
dst_id 0xFF = broadcast.

### 15.4 uRPC payload (urpc/src/urpc.py)
Service call:
```
<svc_name ascii> 0x00 <ver:1> <input bytes...>
```
FFI call is a service named "ffi" whose input is:
```
Arg(func_name) || Arg(arg1) || Arg(arg2) ...
```
Arg encoding (urpc/src/ffi.py):
```
byte 0   type:  U8=0x01 U16=0x02 U32=0x04 ; |0x80 ARRAY ; |0x40 EDITABLE(out param)
if ARRAY: bytes 1..2 = value_len (LE)
then value bytes (LE), unless EDITABLE (caller leaves space; device fills on return)
```
Response: first returned Arg = function return (uint32 even if void); then one
Arg per EDITABLE input (the device-filled output buffers, e.g. read data).

### 15.5 Handshake / session
- daemon_id = 1, default block_size (MTU) = 512.
- Link-up: exec_ffi_func(1, "_link_up") then exec_svc(1, "_link_up2", {"version": UDBD_SERVER_VER_NUM}).
- Always-allowed pre-connect svcs: "_link_up"/"_link_up2", "_ping", "_dev_info".
- Heartbeat: svc "_ping" (Arg U8 0xFF). Device info: ffi "_dev_info" with keys
  "udbd.mtu", "udbd.ver", "udbd.zlib.decompress".

### 15.6 Ready-to-use FFI/service calls (device side, dst=1)
- Shell/RCE:   ffi "msh_exec"(buf, len)  | svc "urpc_shell_start"/"urpc_shell_end",
               svc "cin" (write), local svc "cout" (read console)
- Flash dump:  ffi "fal_partition_find"(name) -> handle;
               ffi "fal_partition_read"(part, addr:U32, buf:U8|ARRAY|EDITABLE, len:U32)
               ffi "fal_partition_write"/"fal_partition_erase"/"fal_crc32_calculate"
               ffi "fal_partition_write_file"(file, part, offset)
- Files:       svc file ops (read/write/continue_write/mkdir/crc32/sha) via FileSvc
- Dials:       ffi "svc_dial_install"(path, alias) / "svc_dial_uninstall"/"apply"/
               "hide"/"unhide" ; svc "svc_dial_get_current"/"svc_dial_info";
               ffi "svc_set_dial_order_info"(json)
- Apps:        ffi "user_app_install"(path, launch:U32) / "user_app_uninstall"(id) /
               "app_launch"(uri) ; svc "svc_user_app_installed_info"
- App msg bus: ffi "msg_recv"(json_or_ubjson, len) ; svc "msg_recv_ubjson"
- OTA:         svc "svc_ota_set_sync_progress"(json) + push rtthread.rbl + FAL flash

### 15.7 Minimal recipe to get a shell + dump firmware
1. Connect BLE, find PersimWear UART service (write char + notify char w/ CCCD 0x2902).
2. Subscribe notify; implement MCF framing (15.1) reassembly.
3. Send link-up (15.5).
4. ffi "fal_partition_find"("download" or "app" or "filesystem") to enumerate.
   (Get exact names by reading the device's partition table or via msh "list_fal".)
5. Loop ffi "fal_partition_read" to dump each partition -> reconstruct firmware.
6. For a shell: svc "urpc_shell_start", svc "cin"("ps\n"), read via "cout".
   Or directly ffi "msh_exec"("<cmd>\0", len).

## 16. CRC16 = standard MODBUS CRC16 (reflected poly 0xA001)
mcf/link/link.py uses mcf_utils.crc16 which is the classic table-driven MODBUS
CRC16 (low-byte + high-byte lookup tables, init 0xFFFF). Reference Python:
```python
def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if (crc & 1) else (crc >> 1)
    return crc & 0xFFFF
```
(Note: link.py packs crc as big-endian: hi=crc//256, lo=crc%256.)

## 17. Artifacts saved in workspace
- FINDINGS.md                  : this log
- extracted/                   : unpacked XAPK
  - com.noisefit.apk, config.*.apk, manifest.json
  - libs_tmp/lib/arm64-v8a/    : extracted .so (VeryFitMulti, app, RtkMediaCodec, abpartool)
  - assets_tmp/assets/python/  : embedded RT-Thread/PersimWear Python source (plaintext!)
  - jadx_out/sources/          : decompiled Java/Kotlin (~37k classes; in progress)
- watch_python_src/            : COPY of the embedded Python (108 .py files) for easy reading

## 18. TL;DR
Your Noise watch is almost certainly one of these:
  (A) IDO VeryFit on Realtek  — closed binary BLE protocol (libVeryFitMulti.so).
  (B) RT-Thread + PersimWear  — OPEN, documented stack: MCF framing + uRPC + UDB.
      This one is a hacker's dream: remote MSH shell, arbitrary FFI C calls,
      raw flash dump/reflash (FAL/SFUD), custom app + watch-face install, OTA,
      on-device Python. ALL protocol code shipped in plaintext in the APK.
  (C) Creek (Flutter/Dart) protobuf protocol — modern Noise-native stack over
      flutter_blue_plus, AES/RSA/ECC handshake (pspkey), huge proto schema.
Next step: BLE-scan the watch, match its GATT services (sec 11), pick the stack,
and build a client from the wire spec in sec 4 (A) / sec 15 (B) / sec 10 (C).
