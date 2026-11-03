const taskStates = {
  running: "采集中",
  pending: "等待领取",
  scheduled: "等待下次采集",
  paused: "已暂停",
  error: "失败待重试",
  pipeline: "基础流程",
  queued: "计划排队中",
  complete: "计划已配置",
};
export function createTaskLogViewer({ api, esc, stamp }, win = window) {
  const doc = win.document;
  let current = null;
  function close() {
    current?.close();
  }
  async function open(resource, id, label) {
    close();
    const dialog = doc.createElement("dialog");
    dialog.id = "task-logs";
    dialog.className = "task-log-drawer";
    dialog.setAttribute("aria-labelledby", "task-logs-title");
    dialog.innerHTML = `<div class="picker-heading"><div><h2 id="task-logs-title">采集日志</h2><p class="sub">${esc(label)}</p></div><button type="button" class="secondary small" data-log-close>关闭</button></div><div class="log-toolbar"><span id="log-task-state" class="badge">正在读取</span><label class="check-label"><input id="log-auto" type="checkbox" checked>自动更新（每 2 秒）</label><label class="check-label"><input id="log-follow" type="checkbox" checked>跟随最新</label><button class="secondary small" type="button" id="log-latest">回到最新</button><button class="secondary small" type="button" id="log-older" disabled>更早日志</button></div><p id="log-connection" class="sub" role="status">正在连接任务日志…</p><div class="task-log-scroll" tabindex="0" aria-label="采集日志内容"><ol id="log-lines" role="log" aria-live="polite" aria-relevant="additions"></ol><p class="empty" id="log-empty">正在读取…</p></div><p class="sub log-note">日志包含执行编号、限速等待、请求结果与采集进度。实时窗口保留最近 2000 行；查看更早日志会暂停自动更新。</p>`;
    doc.body.append(dialog);
    dialog.showModal();
    const $ = (s) => dialog.querySelector(s),
      box = $(".task-log-scroll");
    let timer = null,
      closed = false,
      generation = 0,
      cursor = 0,
      oldest = 0,
      rows = [],
      historyMode = false,
      busy = false,
      hasOlder = false;
    function stop() {
      closed = true;
      generation++;
      win.clearTimeout(timer);
      dialog.remove();
      if (current?.dialog === dialog) current = null;
    }
    current = {
      dialog,
      close: () => {
        dialog.close();
        stop();
      },
    };
    dialog.addEventListener("close", stop);
    $("[data-log-close]").onclick = () => current?.close();
    function draw() {
      $("#log-empty").hidden = rows.length > 0;
      $("#log-empty").textContent =
        "此任务暂无日志。功能启用后的执行及管理操作会持续记录在这里。";
      const list = $("#log-lines"),
        seen = new Set([...list.children].map((n) => Number(n.dataset.id)));
      const keep = new Set(rows.map((r) => r.id));
      [...list.children].forEach((n) => {
        if (!keep.has(Number(n.dataset.id))) n.remove();
      });
      for (const row of rows) {
        if (seen.has(row.id)) continue;
        const li = doc.createElement("li");
        li.dataset.id = row.id;
        li.className = "log-line log-" + row.level;
        li.innerHTML = `<time>${esc(stamp(row.created_at))}</time><span class="log-level">${esc({ info: "信息", warning: "警告", error: "失败" }[row.level] || row.level)}</span><span class="log-execution" title="${esc(row.execution_id || "管理操作")}">${row.execution_id ? "#" + esc(row.execution_id.slice(0, 8)) : "管理操作"}</span><span class="log-message">${esc(row.message)}</span>`;
        list.append(li);
      }
      if ($("#log-follow").checked && !historyMode)
        box.scrollTop = box.scrollHeight;
    }
    function schedule(delay = 2000) {
      win.clearTimeout(timer);
      if (!closed && !historyMode && $("#log-auto").checked)
        timer = win.setTimeout(() => fetchLogs("append"), delay);
    }
    async function fetchLogs(mode = "append") {
      if (closed || busy) return;
      busy = true;
      win.clearTimeout(timer);
      const version = ++generation;
      $("#log-latest").disabled = true;
      $("#log-older").disabled = true;
      try {
        const params = new URLSearchParams({ limit: 200 });
        if (mode === "older" && oldest) params.set("before_id", oldest);
        else if (mode === "append" && cursor) params.set("after_id", cursor);
        const result = await api(
          `/research/collection/logs/${encodeURIComponent(resource)}/${encodeURIComponent(id)}?${params}`,
        );
        if (closed || version !== generation) return;
        if (mode === "latest" || mode === "older") {
          rows = result.items;
          $("#log-lines").replaceChildren();
        } else {
          const ids = new Set(rows.map((r) => r.id));
          rows.push(...result.items.filter((r) => !ids.has(r.id)));
        }
        const trimmed = rows.length > 2000;
        rows = rows.slice(-2000);
        oldest = rows[0]?.id || 0;
        if (!historyMode) cursor = Math.max(cursor, result.next_cursor || 0);
        $("#log-task-state").textContent =
          taskStates[result.task.status] || result.task.status;
        $("#log-task-state").className =
          "badge " + (result.task.status === "error" ? "warn" : "");
        $("#log-connection").textContent =
          (historyMode
            ? "正在查看历史日志"
            : $("#log-auto").checked
              ? "实时更新中"
              : "自动更新已暂停") +
          " · 最近同步 " +
          stamp(result.server_time);
        hasOlder = mode === "append" ? hasOlder || trimmed : result.has_older;
        $("#log-older").disabled = !hasOlder || !oldest;
        draw();
        schedule(result.has_more ? 50 : 2000);
      } catch (error) {
        if (!closed) {
          $("#log-connection").textContent =
            "日志连接失败：" + error.message + "；自动更新开启时将重试";
          schedule(4000);
        }
      } finally {
        busy = false;
        if (!closed) {
          $("#log-latest").disabled = false;
          $("#log-older").disabled = !hasOlder || !oldest;
        }
      }
    }
    $("#log-auto").onchange = () => {
      if ($("#log-auto").checked) {
        historyMode = false;
        cursor = 0;
        rows = [];
        $("#log-lines").replaceChildren();
        fetchLogs("latest");
      } else {
        win.clearTimeout(timer);
        $("#log-connection").textContent = "自动更新已暂停";
      }
    };
    $("#log-follow").onchange = () => {
      if ($("#log-follow").checked) box.scrollTop = box.scrollHeight;
    };
    $("#log-latest").onclick = () => {
      historyMode = false;
      $("#log-auto").checked = true;
      cursor = 0;
      fetchLogs("latest");
    };
    $("#log-older").onclick = () => {
      historyMode = true;
      $("#log-auto").checked = false;
      win.clearTimeout(timer);
      fetchLogs("older");
    };
    await fetchLogs("latest");
  }
  win.addEventListener("hashchange", close);
  return { open, close };
}
