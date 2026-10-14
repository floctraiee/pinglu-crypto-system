# 骞抽檰杩愭渤澶氭簮鎰熺煡鏁版嵁鍥藉瘑鍙俊閲囬泦涓庣鏀规函婧愮郴缁?
澶氭簮鎰熺煡璁惧 鈫?杈圭紭缃戝叧 鈫?涓績骞冲彴鐨勫彲淇￠噰闆嗛摼璺€傝澶囩鐢?SM3 鎽樿閾?+ Merkle 鏍?缁勭粐璁板綍锛岀敤 SM2 瀵规壒娆＄鍚嶏紱涓績骞冲彴**涓嶉噰淇＄綉鍏崇殑閫氳繃鐘舵€?*锛岀敤棰勭櫥璁板叕閽ョ嫭绔嬮噸楠岋紝
骞跺澶栨彁渚涘崟鏉¤褰曠殑 Merkle 璇佹槑涓庣鍚嶅鏍搞€?
---

## 1. 鐜瑕佹眰

| 椤?| 鐗堟湰 |
|---|---|
| Python | 3.14锛堜笁浜虹粺涓€锛?|
| 鎿嶄綔绯荤粺 | Windows锛堝懡浠よ绀轰緥鎸?Windows CMD 鍐欙級 |
| MQTT Broker | Mosquitto 2.x锛堜粎 MQTT 鑱旇皟鏃堕渶瑕侊級 |

```bat
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

渚濊禆宸查攣鐗堟湰锛歚gmssl==3.2.2`銆乣fastapi==0.143.0`銆乣httpx==0.28.1`銆乣uvicorn==0.54.0`銆?`pytest==9.1.1`銆乣paho-mqtt==2.1.0`銆?
---

## 2. 鍗曟満鍚姩椤哄簭锛堝洓涓粓绔級

鎸夐『搴忓紑鍥涗釜 Windows CMD 缁堢锛岄兘鍦ㄤ粨搴撴牴鐩綍鎵ц銆?
**缁堢 1 鈥?MQTT Broker**锛堜粎 MQTT 閾捐矾闇€瑕侊紝绾?HTTP 鑱旇皟鍙烦杩囷級

```bat
mosquitto -c mosquitto.conf -v
```

> `mosquitto.conf` 鏈殢浠撳簱鎻愪緵鏃讹紝鐢ㄦ湰鏈?Mosquitto 榛樿閰嶇疆鍗冲彲锛堥粯璁?1883 绔彛锛夈€?
**缁堢 2 鈥?涓績骞冲彴**

```bat
.venv\Scripts\python.exe -m uvicorn center_platform.main:app --host 127.0.0.1 --port 8000
```

**缁堢 3 鈥?杈圭紭缃戝叧**锛堢敱 B 璐熻矗瀹炵幇锛?
```bat
.venv\Scripts\python.exe -m edge_gateway.main
```

**缁堢 4 鈥?璁惧妯℃嫙鍣?*

```bat
.venv\Scripts\python.exe -m device_simulator.publish --broker 127.0.0.1 --port 1883 --sample
```

> 鈿狅笍 **娉ㄦ剰**锛歚device_simulator.publish` 鍙礋璐?*鎶婂凡绛惧悕鐨勬壒娆″彂鍒?Broker**锛?> 鏀寔鐨勫弬鏁版槸 `--sample` / `--batch-file` / `--registry` / `--broker` / `--port` /
> `--username` / `--password-env` / `--timeout` / `--dry-run`銆?> 瀹?*娌℃湁** `--devices` / `--rate` / `--batch-size`锛涢渶瑕佸璁惧銆佹帶閫熺巼銆佹帶鎵归噺鏃惰鐢?> 涓嬮潰绗?3 鑺傜殑 `device_simulator.runner`銆?
### 鎵撳紑椤甸潰

| 鍦板潃 | 鐢ㄩ€?|
|---|---|
| http://127.0.0.1:8000/ | 涓績骞冲彴椤甸潰锛堝叚鍧楋細鎬昏 / 涓冪被鎰熺煡鏁版嵁 / 寮傚父瀹¤ / 鎵规缁嗚妭涓嶮erkle璇佹槑 / 绂荤嚎鎭㈠ / 鎬ц兘瀵规瘮锛?|
| http://127.0.0.1:8000/docs | Swagger 鎺ュ彛鏂囨。锛屽彲鐩存帴鎻愪氦涓€鎵?JSON |

---

## 3. 澶氳澶囩绾跨敓鎴愪笌寰呭彂闃熷垪

`device_simulator.runner` 璐熻矗銆屽厛鐢熸垚钀界洏銆佸啀鎸夐渶鍙戦€併€嶏細缃戠粶涓嶅彲杈句笉褰卞搷鐢熸垚涓庨摼锛?鎭㈠鍚庢寜璁惧椤哄簭琛ヤ紶锛?*缁濅笉璺宠繃鏃ф壒娆?*銆?
```bat
.venv\Scripts\python.exe -m device_simulator.runner ^
  --devices 7 --rate 1 --batch-size 10 --rounds 1 ^
  --keys-dir keys ^
  --registry test_data/registry_public.json ^
  --db-path simulator.db
```

鍔?`--broker 127.0.0.1 --port 1883` 鍚庯紝鍚屼竴鏉″懡浠や細鍦ㄧ敓鎴愬悗灏濊瘯鎶婂緟鍙戞壒娆″彂鍒?Broker銆?
| 鍙傛暟 | 鍚箟 |
|---|---|
| `--devices` | 璁惧鏁帮紝鎸変竷绫诲惊鐜垎閰?ID锛圵L-001鈥︺€乄E-001鈥︺€佲€︼級 |
| `--rate` | 姣忓彴璁惧姣忕鐢熸垚鐨勮褰曟暟 |
| `--batch-size` | 姣忓彴璁惧姣忔壒鐨勮褰曟暟 |
| `--rounds` | 鐢熸垚杞暟锛屾瘡杞瘡鍙拌澶囦竴鎵?|
| `--seed` | payload 闅忔満绉嶅瓙锛岀敤浜庡鐜?|
| `--keys-dir` | 璁惧绉侀挜鐩綍锛?*蹇呭～**锛屼笉杩?Git锛?|
| `--registry` | 鍏紑鐧昏琛ㄨ緭鍑鸿矾寰勶紙**蹇呭～**锛?|
| `--db-path` | 妯℃嫙鍣ㄧ姸鎬佸簱锛?*蹇呭～**锛?|

---

## 4. 鍒濆鍖栦笌閲嶇疆锛堥噸瑕侊級

**浠讳綍浜虹涓€娆¤窇涔嬪墠锛屽厛鎵ц涓€娆″垵濮嬪寲**锛屽惁鍒欎細鎾炰笂鍘嗗彶鏁版嵁鍐茬獊锛?
```bat
.venv\Scripts\python.exe init_project.py
```

瀹冧細锛?
1. 鐢熸垚/鏍￠獙涓冪被璁惧鐨勫浐瀹氬瘑閽ワ紙`keys/`锛屼笉杩?Git锛変笌鍏紑鐧昏琛紙`test_data/registry_public.json`锛夛紱
2. 閲嶅缓涓績搴?`center_platform/data/center.db` 鐨?`devices` 琛ㄥ唴瀹癸紙鎸夌櫥璁拌〃鐧昏锛屽苟鎵撳嵃鍏挜 SM3 鎸囩汗渚涙牳瀵癸級锛?3. **娓呯┖ `batches` / `records`锛屾妸 `simulator.db` 鐨勯摼灏剧姸鎬佸浣?*锛?4. 淇濈暀 `audits` 浣滀负鍘嗗彶璇佹嵁锛堥渶瑕佷竴骞舵竻绌烘椂鍔?`--reset-audits`锛夈€?
### 涓轰粈涔堝繀椤婚噸缃細WL-001 鍐茬獊

鍘嗗彶閬楃暀闂锛氫腑蹇冨簱閲屾浘鍏ュ簱杩囦竴鎵规ā鎷熷櫒鐢熸垚鐨?`WL-001-000001`锛屼笌
`test_data/normal_batch.json` 閲岀殑 `WL-001-000001` **鎵规鍙风浉鍚屻€佸唴瀹逛笉鍚?*銆?鐢变簬涓績鐢?`device_id + batch_id` 鍋氬敮涓€绾︽潫锛屼箣鍚庢瘡娆℃彁浜よ鏍蜂緥閮戒細琚垽
`BATCH_CONTENT_CONFLICT`锛屽苟鍦?`audits` 閲屼笉鏂疮绉悓鍚嶅啿绐佽褰曘€?
淇鏂瑰紡灏辨槸閲嶇疆閾惧熬锛歚init_project.py` 浼氭妸 `simulator.db` 閲?WL-001 鐨?`last_sequence` 澶嶄綅涓?0銆乣last_batch_number` 澶嶄綅涓?0锛屼娇涓嬩竴娆＄敓鎴愰噸鏂颁粠
`WL-001-000001`銆佸簭鍙?1銆乣previous_hash` 鍏?0 寮€濮嬶紝涓よ竟鍐呭閲嶆柊瀵归綈銆?
闇€瑕佸崟鐙鐞嗘煇鍙拌澶囨椂鐢細

```bat
.venv\Scripts\python.exe cleanup_wl001_conflict.py --dry-run          # 鍙墦鍗板皢鍒犻櫎浠€涔?.venv\Scripts\python.exe cleanup_wl001_conflict.py --device SL-001    # 鍙鐞?SL-001
```

> 涓婅堪鑴氭湰**鍏堝浠藉啀鏀瑰姩**锛氬浠藉啓鍏?`center_platform/data/`锛屾枃浠跺悕甯︽椂闂存埑锛?> 骞堕檮涓€浠芥敼鍔ㄥ墠鐨勮瘉鎹?JSON銆?
---

## 5. 閰嶇疆椤逛笌鐜鍙橀噺

| 鐜鍙橀噺 | 榛樿鍊?| 璇存槑 |
|---|---|---|
| `PINGLU_CENTER_DB` | `center_platform/data/center.db` | 涓績搴撹矾寰勩€傚紓鏈洪儴缃?/ 椤甸潰瀹炴祴鏃跺垏鎹?|
| `PINGLU_SIMULATOR_DB` | `simulator.db` | 绂荤嚎椤佃鍙栧緟鍙戦槦鍒楃殑璺緞 |
| `PINGLU_GATEWAY_STATUS` | 瑙佷笅 | 缃戝叧鍙鐘舵€佹枃浠惰矾寰勶紝绂荤嚎椤典紭鍏堣瀹?|

缃戝叧鐘舵€佹枃浠堕粯璁ゆ煡鎵句綅缃紙瀛樺湪鍗充紭鍏堜娇鐢紝鍚﹀垯鑷姩闄嶇骇璇?`PINGLU_SIMULATOR_DB`锛夛細

```
edge_gateway/data/gateway_status.json
edge_gateway/gateway_status.json
```

### 缃戝叧鍙鐘舵€佹枃浠舵牸寮忥紙缁?B 绔級

B 绔妸涓嬪垪缁撴瀯鍐欏埌涓婇潰鐨勮矾寰勫嵆鍙绂荤嚎椤佃嚜鍔ㄨ瘑鍒紝瀛楁鏍蜂緥瑙?`test_data/gateway_status_sample.json`锛?
```json
{
  "generated_at": "2026-10-14T17:20:00+08:00",
  "devices": [
    {
      "device_id": "SL-001",
      "device_type": "slope",
      "cached_batches": 3,
      "cached_records": 30,
      "resend_done": 2,
      "resend_pending": 3,
      "break_sequence": 21,
      "last_gateway_received_at": "2026-10-14T17:12:05+08:00",
      "last_center_ack_at": "2026-10-14T17:12:09+08:00",
      "note": "鏂綉鏈熼棿缂撳瓨锛屾鍦ㄨˉ浼?
    }
  ]
}
```

### 涓夊彴鐢佃剳鑱旇皟

| 鐢佃剳 | 瑙掕壊 | 鍚姩鍙傛暟 |
|---|---|---|
| 鐢佃剳 1 | 璁惧妯℃嫙鍣?| `--broker <鐢佃剳2IP> --port 1883` |
| 鐢佃剳 2 | MQTT Broker + 杈圭紭缃戝叧 | 缃戝叧鐨勪腑蹇?URL 鎸囧悜 `http://<鐢佃剳3IP>:8000` |
| 鐢佃剳 3 | 涓績骞冲彴 | `--host 0.0.0.0 --port 8000` |

- 涓績蹇呴』鐢?`--host 0.0.0.0` 鎵嶈兘琚彟澶栦袱鍙拌闂紱
- 鏀鹃€氶槻鐏鍏ョ珯锛氱數鑴?2 鐨?1883銆佺數鑴?3 鐨?8000锛?- 鎺ュ彛鑷祴锛歚http://<鐢佃剳3IP>:8000/docs`锛涚湅缁撴灉锛歚http://<鐢佃剳3IP>:8000/`銆?
---

## 6. 娴嬭瘯涓庡疄楠岃剼鏈?
```bat
rem 鍏ㄩ噺鍗曞厓涓庨泦鎴愭祴璇曪紙鍚堝苟鍓嶅繀椤诲叏缁匡級
.venv\Scripts\python.exe -m pytest -q

rem 涓績骞冲彴鍔熻兘鍐掔儫锛堜笉渚濊禆绯荤粺涓存椂鐩綍锛屾矙绠辩幆澧冨弸濂斤級
.venv\Scripts\python.exe 楠岃瘉鑴氭湰_涓績鍔熻兘鍐掔儫.py

rem 椤甸潰鎺ュ彛瀹炴祴锛堝厛璧蜂腑蹇冿紝鍐嶆寚瀹?base_url锛?.venv\Scripts\python.exe 楠岃瘉鑴氭湰_椤甸潰鎺ュ彛.py http://127.0.0.1:8000

rem 閫犱竴浠?7 绫昏澶?+ 澶氱寮傚父鐨勯〉闈㈡紨绀哄簱
.venv\Scripts\python.exe 楠岃瘉鑴氭湰_鎼缓椤甸潰鏁版嵁.py center_platform/data/demo.db
```

| 鑴氭湰 | 浣滅敤 |
|---|---|
| `tests/test_crypto.py` | 鎽樿閾俱€丮erkle銆丼M2/SM3銆佷竷绫昏澶?payload |
| `tests/test_contract_integration.py` | 鍚堝悓绾ч泦鎴愶紙姝ｅ父銆佺鏀广€侀噸澶嶃€佸啿绐併€佸苟鍙戯級 |
| `tests/test_center.py` | 涓績骞冲彴绔埌绔紙鍏ュ簱銆佹嫆缁濄€佸璁°€佽瘉鏄庯級 |
| `tests/test_device_runner.py` | 澶氳澶囩绾跨敓鎴愩€侀噸鍚画閾俱€佽ˉ浼犻『搴?|
| `tests/evaluate.py` | 鎸夌湡瀹炴爣绛剧畻妫€娴嬬巼 / 瀹氫綅鍑嗙‘鐜?/ 璇姤鐜?|
| `tests/benchmark_signing.py` | 1 / 10 / 20 鎵规绛惧悕瀵圭収锛岃緭鍑?CSV 渚涙€ц兘椤佃鍙?|
| `tests/stress_center.py` | 澶氳澶囧帇鍔涳細鐪熷疄鐢熸垚鏁般€佷腑蹇冮獙璇佹暟銆佸叆搴撴暟銆佸欢杩?P95 |

### 鎬ц兘椤电殑鏁板瓧浠庡摢鏉?
`tests/benchmark_signing.py` 鐨勮緭鍑?CSV 鏀惧埌 `docs/evidence/` 鎴栦粨搴撴牴鐩綍锛?鍛藉悕褰㈠ `benchmark_signing_<鏃ユ湡>.csv`锛岄〉闈㈢鈶ュ潡浼氳嚜鍔ㄨ鍙栵紱鏂囦欢涓嶅瓨鍦ㄦ椂椤甸潰濡傚疄鐣欑┖銆?
---

## 7. 浠撳簱绾﹀畾

- **涓嶈繘 Git**锛歚keys/`锛堢閽ワ級銆乣.venv/`銆乣*.db`锛圫QLite锛夈€佸惈涓汉璺緞鐨勬棩蹇椼€?  璇﹁ `.gitignore`銆?- **鍏紑鐧昏琛?*鍙惈 `device_id / device_type / longitude / latitude / public_key`锛?  涓嶅惈绉侀挜锛汢 涓庨槦闀垮鍏ュ悗搴旈€愬彴鏍稿鍏挜 SM3 鎸囩汗銆?- 鍚堝苟鍓嶅繀椤昏窇 `python -m pytest` 骞惰褰曠粨鏋溿€?- 闂璁板綍鏍煎紡锛?*澶嶇幇鍛戒护銆佹湡鏈涖€佸疄闄呫€佹棩蹇?*銆?
### 鎺ュ彛绾﹀畾

瀛楁銆佺紪鐮佽鍒欍€侀獙璇佷紭鍏堥『搴忎笌閿欒鐮佽 `docs/contract.md`銆?鎵€鏈夊搱甯屼笌绛惧悕瀵硅薄鎸?UTF-8 缂栫爜锛孞SON 浣跨敤
`sort_keys=True, ensure_ascii=False, separators=(",", ":")`锛?娴嬮噺鍊间笌缁忕含搴︿负鏍煎紡鍖栧崄杩涘埗瀛楃涓诧紱鏃堕棿缁熶竴甯?`+08:00`銆?