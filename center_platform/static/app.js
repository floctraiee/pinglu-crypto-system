/* 平陆运河国密可信采集与篡改溯源系统 · 中心平台前端
 *
 * 六块内容：总览 / 七类感知数据 / 异常审计 / 批次细节与 Merkle 证明 /
 *           离线恢复 / 性能对比
 *
 * 约束（按任务书）：
 * - 所有内容来自中心真实接口与 SQLite，不使用写死的演示文本；
 * - 断网时可看待传数，恢复后进度变化，且明确标注不是实时数据；
 * - 性能页每个数字都标注来源 CSV、测试日期与设备规模。
 */

(function () {
  "use strict";

  var REFRESH_MS = 5000;

  var DEVICE_TYPES = [
    { key: "water_level", prefix: "WL", name: "水位站" },
    { key: "weather", prefix: "WE", name: "气象站" },
    { key: "navigation_mark", prefix: "NM", name: "航标" },
    { key: "slope", prefix: "SL", name: "边坡监测" },
    { key: "lock", prefix: "LK", name: "船闸" },
    { key: "drone", prefix: "DR", name: "无人机" },
    { key: "survey_boat", prefix: "SB", name: "测量船" }
  ];

  var TYPE_BY_KEY = {};
  var TYPE_BY_PREFIX = {};

  DEVICE_TYPES.forEach(function (item) {
    TYPE_BY_KEY[item.key] = item;
    TYPE_BY_PREFIX[item.prefix] = item;
  });

  // 中心已有的历史登记项（不在七类正式设备里）用类型名兜底显示。
  var EXTRA_TYPE_NAMES = { water_level_legacy: "水位站（旧）" };

  var state = {
    online: false,
    lastSuccessAt: null,
    failCount: 0,
    snapshot: null,
    devices: [],
    batches: [],
    records: [],
    audits: [],
    offline: null,
    performance: null,
    proof: null,
    filters: { type: "", device: "", batch: "" },
    selections: { proofBatch: "", proofSequence: "" }
  };

  var el = {};

  // ---------------------------------------------------------------- 工具

  function $(id) {
    return document.getElementById(id);
  }

  function escapeHtml(value) {
    if (value === null || value === undefined) return "";
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function shortHash(value, head, tail) {
    if (typeof value !== "string" || value.length <= head + tail + 1) return value;
    return value.slice(0, head) + "…" + value.slice(-tail);
  }

  function typeName(key) {
    if (TYPE_BY_KEY[key]) return TYPE_BY_KEY[key].name;
    return EXTRA_TYPE_NAMES[key] || key || "未知类型";
  }

  function typePrefix(key) {
    return TYPE_BY_KEY[key] ? TYPE_BY_KEY[key].prefix : "??";
  }

  function typeFromDeviceId(deviceId) {
    if (typeof deviceId !== "string") return null;
    var prefix = deviceId.split("-")[0];
    return TYPE_BY_PREFIX[prefix] ? TYPE_BY_PREFIX[prefix].key : null;
  }

  function num(value) {
    return typeof value === "number" && isFinite(value) ? value : null;
  }

  function fmt(value, digits) {
    var n = num(value);
    if (n === null) return "—";
    return n.toFixed(digits === undefined ? 2 : digits);
  }

  function batchNumber(batchId) {
    if (typeof batchId !== "string" || batchId.indexOf("-") < 0) return null;
    var suffix = batchId.slice(batchId.lastIndexOf("-") + 1);
    return /^\d+$/.test(suffix) ? parseInt(suffix, 10) : null;
  }

  function describeStatus(status, reason) {
    if (status === "accepted") return { cls: "tag", text: "accepted" };
    if (status === "duplicate") return { cls: "tag info", text: "duplicate" };
    if (status === "rejected") return { cls: "tag bad", text: "rejected" };
    return { cls: "tag mute", text: status || reason || "—" };
  }

  function reasonText(code) {
    var map = {
      OK: "通过",
      UNKNOWN_DEVICE: "设备未登记",
      INVALID_SIGNATURE: "批次签名不通过",
      INVALID_RECORD_HASH: "记录摘要不匹配（被改值）",
      INVALID_MERKLE_ROOT: "Merkle 根与记录摘要不一致",
      INVALID_HASH_CHAIN: "摘要链断开",
      INVALID_SEQUENCE: "序号与可信下一序号不一致",
      INVALID_BATCH_RANGE: "数量或起止序号与记录不一致",
      INVALID_BATCH_SEQUENCE: "批次号未递增",
      INVALID_TIMESTAMP: "跨批次时间未递增",
      INVALID_BATCH_STRUCTURE: "字段不符合约定结构",
      DUPLICATE_BATCH: "内容相同的重复提交",
      BATCH_CONTENT_CONFLICT: "同批次号但内容不同（冲突）"
    };
    if (!code) return "—";
    return map[code] ? code + "（" + map[code] + "）" : code;
  }

  function setText(id, value) {
    var node = $(id);
    if (node) node.textContent = value === null || value === undefined ? "—" : String(value);
  }

  async function getJSON(url, options) {
    var response = await fetch(url, options || { cache: "no-store" });

    if (!response.ok) {
      var detail = "";
      try {
        detail = (await response.json()).detail || "";
      } catch (err) {
        detail = "";
      }
      throw new Error(url + " → HTTP " + response.status + (detail ? "：" + detail : ""));
    }

    return response.json();
  }

  function postJSON(url, body) {
    return getJSON(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
  }

  // ------------------------------------------------------ ① 总览

  function renderFlow() {
    var stats = state.snapshot.stats;
    var auditCounts = countAudits();
    var singleType = state.filters.type ? typeName(state.filters.type) : "七类设备";

    var nodes = [
      {
        title: "设备端（模拟器 / A 端）",
        number: stats.devices,
        unit: "台已登记设备",
        desc: "生成记录 → SM3 摘要链 → Merkle 根 → SM2 签名；私钥不出设备端。"
      },
      {
        title: "网关（B 端）",
        number: state.offline ? state.offline.summary.cached_batches : "—",
        unit: "缓存待发批次",
        desc: "MQTT 订阅 → 初验 → 转发中心；离线时缓存，恢复后补传。",
        pending: true
      },
      {
        title: "中心平台（本页）",
        number: stats.accepted_batches,
        unit: "通过批次",
        desc: "独立重验：设备登记 → 签名 → 序号范围 → 每条摘要 → 链 → Merkle 根 → 历史重放。"
      },
      {
        title: "溯源查询",
        number: stats.records,
        unit: "条已入库记录",
        desc: "单条 Merkle 证明 + SM2 验签，证明记录确实在签名根之下且未被篡改。"
      }
    ];

    var html = "";

    nodes.forEach(function (node, index) {
      if (index > 0) html += '<div class="flow-arrow">➜</div>';
      html +=
        '<div class="flow-node' + (node.pending ? " pending" : "") + '">' +
        "<b>" + escapeHtml(node.title) + "</b>" +
        '<div class="n">' + escapeHtml(node.number) + "</div>" +
        '<div class="d">' + escapeHtml(node.unit) + " · " + escapeHtml(node.desc) + "</div>" +
        "</div>";
    });

    el.flow.innerHTML = html;

    setText("s-types", coveredTypeCount() + " / 7");
    setText("s-devices", stats.devices);
    setText("s-batches", stats.batches);
    setText("s-accepted", stats.accepted_batches);
    setText("s-records", stats.records);
    setText("s-audits", stats.audits);

    el.verifyLine.innerHTML =
      '<span class="tag">通过批次 ' + stats.accepted_batches + "</span>" +
      '<span class="tag bad">拒绝 ' + auditCounts.rejected + "</span>" +
      '<span class="tag info">重复 ' + auditCounts.duplicate + "</span>" +
      '<span class="tag mute">审计 ' + stats.audits + "</span>" +
      '<span class="muted">数据范围：' + escapeHtml(singleType) + "（筛选项同时作用于第②块）</span>";
  }

  function coveredTypeCount() {
    var seen = {};

    state.batches.forEach(function (item) {
      var header = item.header || {};
      var key = typeFromDeviceId(header.device_id) ||
        (state.deviceById[header.device_id] || {}).device_type;

      if (key && TYPE_BY_KEY[key]) seen[key] = true;
    });

    return Object.keys(seen).length;
  }

  function countAudits() {
    var result = { rejected: 0, duplicate: 0, accepted: 0 };

    state.audits.forEach(function (item) {
      if (result[item.status] !== undefined) result[item.status] += 1;
    });

    return result;
  }

  // -------------------------------------------- ② 七类感知数据

  function syncFilterOptions() {
    var typeSelect = el.filterType;
    var current = state.filters.type;

    if (typeSelect.options.length <= 1) {
      DEVICE_TYPES.forEach(function (item) {
        var option = document.createElement("option");
        option.value = item.key;
        option.textContent = item.name + "（" + item.prefix + "）";
        typeSelect.appendChild(option);
      });
    }

    typeSelect.value = current;

    var deviceIds = state.devices
      .map(function (item) { return item.device_id; })
      .sort();
    var wanted = [""].concat(deviceIds);

    if (!sameOptions(el.filterDevice, wanted)) {
      fillSelect(el.filterDevice, wanted, function (id) { return id || "全部设备"; });
    }

    el.filterDevice.value = state.filters.device;
    rebuildBatchOptions();
  }

  function rebuildBatchOptions() {
    var accepted = acceptedBatches().map(function (item) { return item.batch_id; });
    var wanted = [""].concat(accepted);

    if (!sameOptions(el.filterBatch, wanted)) {
      fillSelect(el.filterBatch, wanted, function (id) { return id || "全部批次"; });
    }

    el.filterBatch.value = state.filters.batch;

    // 证明页的批次下拉只列已入库的合法批次。
    var wantedProof = [""].concat(accepted);
    if (!sameOptions(el.proofBatch, wantedProof)) {
      fillSelect(el.proofBatch, wantedProof, function (id) { return id || "— 选择批次 —"; });
      el.proofBatch.value = state.selections.proofBatch;
    }
  }

  function fillSelect(select, values, labeler) {
    select.innerHTML = "";
    values.forEach(function (value) {
      var option = document.createElement("option");
      option.value = value;
      option.textContent = labeler(value);
      select.appendChild(option);
    });
  }

  function sameOptions(select, values) {
    if (select.options.length !== values.length) return false;
    for (var i = 0; i < values.length; i += 1) {
      if (select.options[i].value !== values[i]) return false;
    }
    return true;
  }

  function acceptedBatches() {
    return state.batches.filter(function (item) {
      return item.status === "accepted";
    });
  }

  function filteredRecords() {
    return state.records.filter(function (item) {
      var deviceKey = state.deviceById[item.device_id]
        ? state.deviceById[item.device_id].device_type
        : typeFromDeviceId(item.device_id);

      if (state.filters.type && deviceKey !== state.filters.type) return false;
      if (state.filters.device && item.device_id !== state.filters.device) return false;
      if (state.filters.batch && item.batch_id !== state.filters.batch) return false;
      return true;
    });
  }

  function filteredBatches() {
    return state.batches.filter(function (item) {
      var header = item.header || {};
      var deviceId = header.device_id;
      var deviceKey = state.deviceById[deviceId]
        ? state.deviceById[deviceId].device_type
        : typeFromDeviceId(deviceId);

      if (state.filters.type && deviceKey !== state.filters.type) return false;
      if (state.filters.device && deviceId !== state.filters.device) return false;
      if (state.filters.batch && header.batch_id !== state.filters.batch) return false;
      return true;
    });
  }

  function renderTypes() {
    var records = filteredRecords();
    var batches = filteredBatches();

    // 有筛选时只画命中的类型；无筛选时七类固定顺序全画，缺失的显示尚未登记。
    var types = state.filters.type
      ? DEVICE_TYPES.filter(function (item) { return item.key === state.filters.type; })
      : DEVICE_TYPES;

    if (!types.length) types = DEVICE_TYPES;

    var html = "";

    types.forEach(function (type) {
      var typeRecords = records.filter(function (item) {
        return (state.deviceById[item.device_id] || {}).device_type === type.key ||
          typeFromDeviceId(item.device_id) === type.key;
      });

      var typeBatches = batches.filter(function (item) {
        var deviceId = (item.header || {}).device_id;
        var key = (state.deviceById[deviceId] || {}).device_type ||
          typeFromDeviceId(deviceId);
        return key === type.key;
      });

      var registered = state.devices.filter(function (item) {
        return item.device_type === type.key;
      });

      var latest = typeRecords.length ? typeRecords[typeRecords.length - 1] : null;
      var missing = registered.length === 0;

      html +=
        '<div class="type-card' + (missing ? " missing" : "") + '">' +
        "<h3><span>" + escapeHtml(type.name) +
        ' <span class="tag mute">' + escapeHtml(type.prefix) + "</span></span>" +
        '<code>' + escapeHtml(type.key) + "</code></h3>" +
        '<div class="type-kv">' +
        "<div>已登记设备 <b>" + registered.length + "</b></div>" +
        "<div>本类批次 <b>" + typeBatches.length + "</b></div>" +
        "<div>本类记录 <b>" + typeRecords.length + "</b></div>" +
        "</div>";

      if (missing) {
        html += '<div class="muted small" style="margin-top:8px">该类型尚无设备登记到中心，等待登记后出现数据。</div>';
      } else if (latest) {
        html +=
          '<div class="payload">设备 ' + escapeHtml(latest.device_id) +
          " · 序号 " + escapeHtml(latest.sequence) +
          " · " + escapeHtml((latest.record || {}).timestamp || "") +
          "<br>" + escapeHtml(JSON.stringify((latest.record || {}).payload || {})) +
          "</div>";
      } else {
        html += '<div class="muted small" style="margin-top:8px">已登记，但还没有入库记录。</div>';
      }

      html += "</div>";
    });

    el.typeGrid.innerHTML = html;

    el.typesCount.textContent =
      "当前筛选命中 " + records.length + " 条记录 / " + batches.length + " 个批次";
  }

  // -------------------------------------------- ③ 异常审计

  function renderAudits() {
    var counts = countAudits();
    setText("a-rejected", counts.rejected);
    setText("a-duplicate", counts.duplicate);
    setText("a-total", state.audits.length);

    if (!state.audits.length) {
      el.audits.innerHTML = '<tr><td colspan="9" class="empty">暂无审计事件。提交被拒或重复的批次会出现在这里。</td></tr>';
      el.auditNote.textContent = "";
      return;
    }

    var rows = state.audits.map(function (item) {
      var sequences = Array.isArray(item.affected_sequences) && item.affected_sequences.length
        ? '<span class="seq-list">' + escapeHtml(item.affected_sequences.join(", ")) + "</span>"
        : '<span class="muted">—</span>';

      var status = describeStatus(item.status, item.reason_code);
      var raw = item.raw
        ? '<code title="' + escapeHtml(JSON.stringify(item.raw).slice(0, 400)) + '">有原文</code>'
        : '<span class="muted">—</span>';

      return "<tr>" +
        "<td>" + escapeHtml(item.id) + "</td>" +
        "<td><code>" + escapeHtml(item.device_id || "—") + "</code></td>" +
        "<td><code>" + escapeHtml(item.batch_id || "—") + "</code></td>" +
        "<td>" + sequences + "</td>" +
        "<td>" + escapeHtml(reasonText(item.reason_code)) + "</td>" +
        "<td>" + renderEdgeVerdict(item) + "</td>" +
        '<td><span class="' + status.cls + '">' + escapeHtml(status.text) + "</span></td>" +
        "<td>" + escapeHtml(item.created_at || "—") + "</td>" +
        "<td>" + raw + "</td>" +
        "</tr>";
    });

    el.audits.innerHTML = rows.join("");

    // 说明「边缘判定」这一列的来源与口径
    var withEdge = state.audits.filter(function (item) {
      return item.edge_verdict;
    }).length;
    var withoutEdge = state.audits.length - withEdge;

    var notes = [
      "「边缘判定」取自网关初验结论（网关缓存库 pending_batches.edge_result），" +
      "「中心复核」是中心独立重验的结果——中心不采信网关结论。"
    ];

    if (withEdge) {
      notes.push("已关联网关初验结论 " + withEdge + " 条");
    }

    if (withoutEdge) {
      var gatewayUnreadable = state.audits.some(function (item) {
        return item.edge_source === "gateway_db_unavailable";
      });

      notes.push(
        withoutEdge + " 条没有网关结论" +
        (gatewayUnreadable
          ? "（网关缓存库当前不可读：网关未启动或尚未持久化，属正常）"
          : "（该批次不是经网关链路进入的，例如直接 HTTP 提交或历史数据）")
      );
    }

    el.auditNote.textContent = notes.join("；");
  }

  function renderEdgeVerdict(item) {
    var verdict = item.edge_verdict;

    if (!verdict) {
      var reason = item.edge_source === "gateway_db_unavailable"
        ? "网关缓存库不可读"
        : "网关未留存该批次初验结论";
      return '<span class="muted" title="' + escapeHtml(reason) + '">—</span>';
    }

    if (verdict.valid === true) {
      return '<span class="tag">初验通过</span>' +
        '<div class="muted small">' + escapeHtml(verdict.reason_code || "OK") + "</div>";
    }

    if (verdict.valid === false) {
      var seqs = Array.isArray(verdict.affected_sequences) && verdict.affected_sequences.length
        ? " · 序号 " + verdict.affected_sequences.join(",")
        : "";
      return '<span class="tag warn">初验未通过</span>' +
        '<div class="muted small">' + escapeHtml(verdict.reason_code || "—") +
        escapeHtml(seqs) + "</div>";
    }

    return '<span class="muted">—</span>';
  }

  // -------------------------------------------- ④ 证明

  function renderProof() {
    var proof = state.proof;

    if (!proof) {
      el.proofResult.innerHTML =
        '<div class="muted">选择一条已入库的合法记录，显示原始摘要、Merkle 路径、签名根与 SM2 验签结果。</div>';
      return;
    }

    if (proof.error) {
      el.proofResult.innerHTML = '<div class="error">' + escapeHtml(proof.error) + "</div>";
      return;
    }

    var pathRows = (proof.proof || []).map(function (step, index) {
      return '<div class="path-row"><span class="lv">第 ' + (index + 1) + " 层</span>" +
        '<span class="tag mute">' + escapeHtml(step.side === "left" ? "左兄弟" : "右兄弟") + "</span>" +
        "<code>" + escapeHtml(step.hash) + "</code></div>";
    }).join("");

    if (!pathRows) pathRows = '<div class="muted small">该批次只有一条记录，无需兄弟节点。</div>';

    var verify = proof.signature_verified === true
      ? '<span class="tag">SM2 验签通过</span>'
      : proof.signature_verified === false
        ? '<span class="tag bad">SM2 验签失败</span>'
        : '<span class="tag mute">' + escapeHtml(proof.signature_note || "未验签") + "</span>";

    el.proofResult.innerHTML =
      '<div class="proof-grid">' +
      '<div class="proof-item"><span>设备 ID</span><code>' + escapeHtml(proof.device_id) + "</code></div>" +
      '<div class="proof-item"><span>批次 ID</span><code>' + escapeHtml(proof.batch_id) + "</code></div>" +
      '<div class="proof-item"><span>记录序号</span><code>' + escapeHtml(proof.sequence) + "</code></div>" +
      '<div class="proof-item"><span>记录原始摘要（叶）</span><code>' + escapeHtml(proof.record_hash) + "</code></div>" +
      '<div class="proof-item"><span>签名批次 Merkle 根</span><code>' + escapeHtml(proof.root) + "</code></div>" +
      '<div class="proof-item"><span>重新验证结果</span>' +
      (proof.verified ? '<span class="tag">verified = true</span>' : '<span class="tag bad">verified = false</span>') +
      "</div>" +
      '<div class="proof-item"><span>SM2 签名校验</span>' + verify + "</div>" +
      "</div>" +
      '<div class="muted small" style="margin-bottom:6px">Merkle 路径（自底向上）</div>' +
      '<div class="path">' + pathRows + "</div>";

    if (proof.signature_note) {
      el.proofResult.innerHTML +=
        '<div class="muted small" style="margin-top:8px">' + escapeHtml(proof.signature_note) + "</div>";
    }
  }

  async function queryProof() {
    var batchId = el.proofBatch.value;
    var sequence = el.proofSequence.value;

    if (!batchId || !sequence) {
      state.proof = { error: "请先选择批次与记录序号。" };
      renderProof();
      return;
    }

    state.selections.proofBatch = batchId;
    state.selections.proofSequence = sequence;

    try {
      var data = await getJSON(
        "/batches/" + encodeURIComponent(batchId) + "/proof/" + encodeURIComponent(sequence)
      );

      var batch = state.batches.filter(function (item) {
        return (item.header || {}).batch_id === batchId;
      })[0];

      var deviceId = batch ? (batch.header || {}).device_id : null;
      var registration = deviceId ? state.deviceById[deviceId] : null;

      var result = {
        device_id: deviceId || "—",
        batch_id: batchId,
        sequence: data.sequence,
        record_hash: data.record_hash,
        root: data.root,
        verified: data.verified,
        proof: data.proof,
        signature_verified: null,
        signature_note: ""
      };

      if (!batch || !registration) {
        result.signature_note = !batch
          ? "未找到该批次，无法复核签名。"
          : "该设备未在中心登记，无法取得可信公钥复核签名。";
        state.proof = result;
        renderProof();
        return;
      }

      try {
        // 原始批次从中心库按 device_id + batch_id 只读取回，不用列表接口的摘要。
        var stored = await getJSON(
          "/batch-raw?batch_id=" + encodeURIComponent(batchId) +
          "&device_id=" + encodeURIComponent(deviceId)
        );
        var check = await postJSON("/verify-signature", {
          batch: stored.raw,
          public_key: registration.public_key
        });

        result.signature_verified = check.verified;
        result.signature_note = check.note || "";
      } catch (err) {
        result.signature_note = "签名复核未完成：" + err.message;
      }

      state.proof = result;
    } catch (err) {
      state.proof = { error: "证明查询失败：" + err.message };
    }

    renderProof();
  }

  // -------------------------------------------- ⑤ 离线恢复

  function renderOffline(data) {
    var summary = data.summary || {};

    setText("o-cached", summary.cached_batches);
    setText(
      "o-records",
      summary.cached_records === null || summary.cached_records === undefined
        ? "—"
        : summary.cached_records
    );
    setText("o-done", summary.resend_done);
    setText("o-pending", summary.resend_pending);

    var progress = num(summary.progress_percent);
    setText("o-progress", progress === null ? "—" : progress.toFixed(1) + "%");
    el.oBar.style.width = (progress === null ? 0 : Math.min(100, Math.max(0, progress))) + "%";

    // 实时延迟 P95：网关收到批次 → 中心给出终态（任务书目标 ≤1 秒）
    var latency = data.latency_ms || null;
    var p95 = latency ? num(latency.p95) : null;

    if (p95 === null) {
      setText("o-p95", "—");
    } else {
      setText("o-p95", p95.toFixed(1));
    }

    var p95Card = el.oP95 ? el.oP95.parentNode : null;

    if (p95Card) {
      p95Card.classList.toggle("ok", p95 !== null && p95 <= 1000);
      p95Card.classList.toggle("bad", p95 !== null && p95 > 1000);
    }

    var devices = data.devices || [];

    if (!data.available || !devices.length) {
      el.offline.innerHTML =
        '<tr><td colspan="9" class="empty">' + escapeHtml(data.source_note || "暂无离线补传数据。") + "</td></tr>";
    } else {
      el.offline.innerHTML = devices.map(function (item) {
        var ranges = Array.isArray(item.pending_ranges) && item.pending_ranges.length
          ? item.pending_ranges.join("、")
          : "—";

        return "<tr>" +
          "<td><code>" + escapeHtml(item.device_id) + "</code></td>" +
          "<td>" + escapeHtml(typeName(item.device_type)) + "</td>" +
          "<td>" + escapeHtml(item.cached_batches) + "</td>" +
          "<td>" + (item.cached_records === null || item.cached_records === undefined
            ? '<span class="muted">—</span>' : escapeHtml(item.cached_records)) + "</td>" +
          "<td>" + escapeHtml(item.resend_done) + "</td>" +
          "<td>" + (item.resend_pending > 0
            ? '<span class="seq-list">' + escapeHtml(item.resend_pending) + "</span>"
            : escapeHtml(item.resend_pending)) + "</td>" +
          "<td>" + escapeHtml(item.break_sequence === null || item.break_sequence === undefined ? "—" : item.break_sequence) + "</td>" +
          "<td>" + escapeHtml(ranges) + "</td>" +
          "<td>" + escapeHtml(item.last_gateway_received_at || "—") + "</td>" +
          "</tr>";
      }).join("");
    }

    var parts = [
      "数据来源：" + (data.source_note || data.source || "—"),
      "采集时间 " + (data.collected_at || "—")
    ];

    if (latency && latency.count) {
      parts.push(
        "实时延迟（网关收到→中心终态）P50 " + latency.p50 + " ms / P95 " +
        latency.p95 + " ms / P99 " + latency.p99 + " ms（样本 " + latency.count + "）"
      );
    }

    if (summary.gateway_audit_events) {
      parts.push("网关本地审计事件 " + summary.gateway_audit_events + " 条");
    }

    if (!state.online) {
      parts.push("注意：中心当前不可达，这是断网前的缓存快照");
    }

    el.offlineNote.textContent = parts.join("；");
  }

  // -------------------------------------------- ⑥ 性能

  function renderPerformance(data) {
    if (!data.available) {
      el.perfScale.innerHTML = "";
      el.performance.innerHTML =
        '<tr><td colspan="11" class="empty">' + escapeHtml(data.message || "暂无性能数据。") +
        "<br>预期文件：" + escapeHtml(data.expected_file || "—") +
        "<br>预期列：" + escapeHtml((data.expected_columns || []).join(", ")) +
        "</td></tr>";
      el.perfMeta.textContent = "";
      el.perfNote.textContent =
        "性能页不使用估计值：CSV 缺失时如实留空，等 tests/benchmark_signing.py 产出后再显示。";
      return;
    }

    var rows = data.rows || [];

    var scaleHtml = rows.map(function (row) {
      return '<div class="scale-item"><span>每批 ' + escapeHtml(row.batch_size) + " 条</span>" +
        "<b>" + fmt(row.sign_ms_p95, 2) + " ms</b>" +
        '<span>签名 P95 · 共 ' + escapeHtml(row.sign_calls === null ? "—" : row.sign_calls) + " 次签名</span></div>";
    }).join("");

    el.perfScale.innerHTML = scaleHtml;

    el.perfMeta.textContent =
      "来源 CSV：" + (data.file || "—") +
      " · 文件时间 " + (data.file_mtime || "—") +
      " · 测试日期 " + (data.tested_at || "—") +
      " · 设备规模 " + (data.device_scale || "—");

    el.performance.innerHTML = rows.map(function (row) {
      return "<tr>" +
        "<td><b>" + escapeHtml(row.batch_size) + "</b></td>" +
        "<td>" + escapeHtml(row.batch_count) + "</td>" +
        "<td>" + escapeHtml(row.sign_calls) + "</td>" +
        "<td>" + fmt(row.sign_ms_total, 2) + "</td>" +
        "<td>" + fmt(row.sign_ms_avg, 3) + "</td>" +
        "<td><b>" + fmt(row.sign_ms_p95, 3) + "</b></td>" +
        "<td>" + escapeHtml(row.verify_calls) + "</td>" +
        "<td>" + fmt(row.verify_ms_avg, 3) + "</td>" +
        "<td>" + fmt(row.verify_ms_p95, 3) + "</td>" +
        "<td>" + escapeHtml(row.payload_bytes) + "</td>" +
        "<td>" + escapeHtml(row.records) + "</td>" +
        "</tr>";
    }).join("");

    el.perfNote.textContent =
      "每个数字对应上方的 CSV 文件、测试日期与设备规模；1 / 10 / 20 三种批次大小横向对照。";
  }

  // -------------------------------------------- 连接状态

  function setConnection(ok, detail) {
    state.online = ok;
    el.conn.classList.toggle("online", ok);
    el.conn.classList.toggle("offline", !ok);
    el.connText.textContent = ok ? "中心在线 · 实时数据" : "中心离线 · 显示缓存快照";
    el.staleBanner.hidden = ok;

    if (ok) {
      state.failCount = 0;
      state.lastSuccessAt = new Date();
    } else {
      state.failCount += 1;
      el.staleDetail.textContent = detail ? "（" + detail + "）" : "";
    }
  }

  // -------------------------------------------- 主刷新

  async function refresh() {
    try {
      var results = await Promise.all([
        getJSON("/stats"),
        getJSON("/devices"),
        getJSON("/batches"),
        getJSON("/records"),
        getJSON("/audits"),
        getJSON("/offline-status"),
        getJSON("/performance")
      ]);

      state.snapshot = { stats: results[0] };
      state.devices = results[1] || [];
      state.batches = results[2] || [];
      state.records = results[3] || [];
      state.audits = results[4] || [];
      state.offline = results[5];
      state.performance = results[6];

      state.deviceById = {};
      state.devices.forEach(function (item) {
        state.deviceById[item.device_id] = item;
      });

      setConnection(true, "");

      renderFlow();
      syncFilterOptions();
      renderTypes();
      renderAudits();
      renderProof();
      renderOffline(state.offline);
      renderPerformance(state.performance);

      var now = new Date();
      el.lastUpdate.textContent = "最后更新 " + now.toLocaleString("zh-CN") +
        " · 连续失败 " + state.failCount + " 次";
    } catch (err) {
      setConnection(false, err.message);

      // 断网时保留上一份成功快照：页面显示"待传数"，不清空。
      if (state.offline) {
        renderOffline(state.offline);
      }

      if (state.snapshot) {
        el.lastUpdate.textContent = "最后成功读取 " +
          (state.lastSuccessAt ? state.lastSuccessAt.toLocaleString("zh-CN") : "—") +
          "；当前不可达，正在自动重试。";
      } else {
        el.lastUpdate.textContent = "无法连接中心：" + err.message;
      }
    }
  }

  // -------------------------------------------- 事件绑定

  function bind() {
    el.filterType.addEventListener("change", function () {
      state.filters.type = el.filterType.value;
      renderFlow();
      renderTypes();
    });

    el.filterDevice.addEventListener("change", function () {
      state.filters.device = el.filterDevice.value;
      renderFlow();
      renderTypes();
    });

    el.filterBatch.addEventListener("change", function () {
      state.filters.batch = el.filterBatch.value;
      renderTypes();
    });

    el.btnReset.addEventListener("click", function () {
      state.filters = { type: "", device: "", batch: "" };
      el.filterType.value = "";
      el.filterDevice.value = "";
      el.filterBatch.value = "";
      renderFlow();
      renderTypes();
    });

    el.proofBatch.addEventListener("change", function () {
      state.selections.proofBatch = el.proofBatch.value;
      state.selections.proofSequence = "";
      rebuildProofSequences();
    });

    el.btnProof.addEventListener("click", queryProof);

    setInterval(refresh, REFRESH_MS);
    window.addEventListener("online", refresh);
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) refresh();
    });
  }

  function rebuildProofSequences() {
    var batchId = el.proofBatch.value;
    var batch = state.batches.filter(function (item) {
      return (item.header || {}).batch_id === batchId;
    })[0];

    var values = [""];

    if (batch) {
      var start = batch.header.start_sequence;
      var end = batch.header.end_sequence;
      for (var seq = start; seq <= end; seq += 1) values.push(String(seq));
    }

    fillSelect(el.proofSequence, values, function (value) {
      return value || "— 选择序号 —";
    });

    el.proofSequence.value = state.selections.proofSequence || "";
  }

  function init() {
    el = {
      conn: $("conn"),
      connText: $("conn-text"),
      staleBanner: $("stale-banner"),
      staleDetail: $("stale-detail"),
      flow: $("flow"),
      verifyLine: $("verify-line"),
      typeGrid: $("type-grid"),
      typesCount: $("types-count"),
      filterType: $("filter-type"),
      filterDevice: $("filter-device"),
      filterBatch: $("filter-batch"),
      btnReset: $("btn-reset"),
      audits: $("audits"),
      auditNote: $("audit-note"),
      proofBatch: $("proof-batch"),
      proofSequence: $("proof-sequence"),
      btnProof: $("btn-proof"),
      proofResult: $("proof-result"),
      offline: $("offline"),
      offlineNote: $("offline-note"),
      oBar: $("o-bar"),
      oP95: $("o-p95"),
      perfScale: $("perf-scale"),
      perfMeta: $("perf-meta"),
      performance: $("performance"),
      perfNote: $("perf-note"),
      lastUpdate: $("last-update")
    };

    state.deviceById = {};
    bind();
    refresh();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
