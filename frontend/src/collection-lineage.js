import "./collection-lineage.css";

export function createCollectionLineage(ctx) {
  const { api, esc, num, stamp, market, currentRevision, toast } = ctx;
  let hours = 24,
    selected = null,
    direction = "both",
    camera = null,
    generation = 0;
  const stages = ["提交来源", "任务队列", "批次分配", "外部数据源", "结果入库"];
  const stateNames = {
    paused: "暂停",
    pending: "等待",
    running: "采集中",
    error: "失败待重试",
    scheduled: "定时",
    out_of_scope: "未启用市场",
    done: "完成",
  };
  function related(data, node) {
    if (!node)
      return {
        nodes: new Set(data.nodes.map((n) => n.id)),
        edges: new Set(data.edges.map((e) => e.id)),
      };
    const nodes = new Set([node.id]),
      edges = new Set();
    const walk = (upstream) => {
      const queue = [node.id],
        seen = new Set(queue);
      while (queue.length) {
        const id = queue.shift();
        for (const edge of data.edges) {
          if (
            node.kind &&
            edge.kind &&
            node.kind !== edge.kind &&
            !(node.kind === "pool" && edge.kind === "details")
          )
            continue;
          if ((upstream ? edge.target : edge.source) !== id) continue;
          const next = upstream ? edge.source : edge.target;
          nodes.add(next);
          edges.add(edge.id);
          if (!seen.has(next)) {
            seen.add(next);
            queue.push(next);
          }
        }
      }
    };
    if (direction !== "down") walk(true);
    if (direction !== "up") walk(false);
    return { nodes, edges };
  }
  async function render(host, rev, fresh = false) {
    const ticket = ++generation;
    host.innerHTML = '<p role="status">正在读取队列和来源统计…</p>';
    const data = await api(
      "/research/collection/lineage?" +
        new URLSearchParams({
          store: market().store || "all",
          hours,
          refresh: fresh,
        }),
    );
    if (!host.isConnected || rev !== currentRevision() || ticket !== generation)
      return;
    const map = new Map(data.nodes.map((n) => [n.id, n]));
    if (selected && !map.has(selected)) selected = null;
    const ordered = [...data.nodes].sort((a, b) => a.stage - b.stage);
    const columns = stages.map((_, i) => ordered.filter((n) => n.stage === i));
    const width = 1588,
      height = Math.max(...columns.map((c) => c.length)) * 108 + 54;
    const positions = new Map();
    columns.forEach((nodes, column) =>
      nodes.forEach((node, row) =>
        positions.set(node.id, { x: column * 330 + 14, y: row * 108 + 48 }),
      ),
    );
    const metric = (label, value) =>
      `<span>${label} <b>${num(value)}</b></span>`;
    const queues = data.nodes.filter((n) => n.stage === 1 && n.kind);
    host.innerHTML = `<div class="lineage-canvas"><div class="lineage-canvas-tools" role="toolbar" aria-label="血缘图筛选与画布操作"><div class="lineage-toolbar" role="group" aria-label="筛选与定位"><label>观察窗口<select data-lineage-hours>${[
      [1, "近 1 小时"],
      [24, "近 24 小时"],
      [168, "近 7 天"],
    ]
      .map(
        ([v, n]) =>
          `<option value="${v}" ${hours === v ? "selected" : ""}>${n}</option>`,
      )
      .join(
        "",
      )}</select></label><label>定位节点<input type="search" data-lineage-search placeholder="任务 / 数据源 / 数据表"></label><label>关联方向<select data-lineage-direction>${[
      ["both", "上下游"],
      ["up", "上游"],
      ["down", "下游"],
    ]
      .map(
        ([v, n]) =>
          `<option value="${v}" ${direction === v ? "selected" : ""}>${n}</option>`,
      )
      .join(
        "",
      )}</select></label></div><button class="secondary" data-lineage-refresh>刷新</button><div class="lineage-zoom"><button class="secondary" data-zoom="out" aria-label="缩小血缘图">−</button><output data-zoom-label>100%</output><button class="secondary" data-zoom="in" aria-label="放大血缘图">＋</button><button class="secondary" data-zoom="fit">适配全图</button><button class="secondary" data-zoom="reset" title="恢复初始缩放和位置">恢复初始</button></div><button class="secondary" data-lineage-clear>全部关系</button><button class="secondary" data-lineage-flow aria-pressed="true">暂停流动</button></div>
      <div class="lineage-caption"><div class="lineage-summary"><span>${data.store === "all" ? "全部商店" : data.store === "apple" ? "App Store" : "Google Play"} · 全部国家 / 语言</span>${metric(
        "共享任务",
        queues.reduce((n, q) => n + q.value, 0),
      )}${metric(
        "可领取",
        queues.reduce((n, q) => n + q.eligible, 0),
      )}${metric("活跃消费者", data.workers.length)}<span>${data.paused ? "请求已全局暂停" : "共享限速已启用"}</span><span>快照 ${esc(stamp(data.at))}</span></div>
      <details class="lineage-notes"><summary>统计说明 · 拖动画布平移，Ctrl / ⌘ + 滚轮缩放</summary>${data.notes.map((note) => `<p>${esc(note)}</p>`).join("")}<p>请求统计起点 ${data.http_since ? esc(stamp(data.http_since)) : "首次采集请求后建立"} · 队列快照缓存最多 60 秒</p></details></div>
      <div class="lineage-viewport" tabindex="0" aria-label="采集血缘画布，可拖动、滚轮或方向键平移"><div class="lineage-world is-flowing"><svg class="lineage-edges" width="${width}" height="${height}" aria-hidden="true"><defs><marker id="lineage-arrow" viewBox="0 0 6 6" refX="5" refY="3" markerUnits="userSpaceOnUse" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 6 3 L 0 6 z"/></marker></defs>${data.edges
        .map((edge, index) => {
          const a = positions.get(edge.source),
            b = positions.get(edge.target);
          const start = a.x + 240,
            end = b.x - 6,
            mid = (start + end) / 2;
          return `<path id="lineage-path-${index}" data-lineage-edge="${esc(edge.id)}" d="M ${start} ${a.y + 43} C ${mid} ${a.y + 43} ${mid} ${b.y + 43} ${end} ${b.y + 43}" marker-end="url(#lineage-arrow)"/>${edge.label ? `<text class="lineage-edge-label" data-edge-label="${esc(edge.id)}" text-anchor="middle" dy="-5"><textPath href="#lineage-path-${index}" startOffset="50%">${esc(edge.label)}</textPath></text>` : ""}`;
        })
        .join(
          "",
        )}</svg>${stages.map((label, i) => `<div class="lineage-stage" data-stage="${i}">${label}</div>`).join("")}${ordered
        .map(
          (node) =>
            `<button type="button" class="lineage-node content-card" data-lineage-node="${esc(node.id)}" data-stage="${node.stage}" data-incoming="${data.edges.some((e) => e.target === node.id)}" data-outgoing="${data.edges.some((e) => e.source === node.id)}" aria-pressed="${selected === node.id}"><span class="lineage-node-title">${esc(node.label)}</span><span class="lineage-node-value">${node.value == null ? "—" : num(node.value)} <small>${esc(node.unit)}</small></span><span class="lineage-node-sub">${
              node.states
                ? Object.entries(node.states)
                    .filter(([, v]) => v)
                    .map(([k, v]) => `${stateNames[k] || k} ${num(v)}`)
                    .join(" · ")
                : node.stage === 3
                  ? node.requests == null
                    ? "请求量尚未记录"
                    : `${num(node.requests)} 次 HTTP · ${num(node.http_errors)} 次失败`
                  : node.batch_limit
                    ? `每批最多 ${node.batch_limit} 个兼容目标`
                    : esc(node.tables || "")
            }</span></button>`,
        )
        .join(
          "",
        )}</div></div><aside class="lineage-inspector" aria-label="节点详情" hidden></aside></div>`;
    const viewport = host.querySelector(".lineage-viewport"),
      world = host.querySelector(".lineage-world");
    world.style.width = width + "px";
    world.style.height = height + "px";
    host.querySelectorAll("[data-lineage-node]").forEach((button) => {
      const p = positions.get(button.dataset.lineageNode);
      button.style.left = p.x + "px";
      button.style.top = p.y + "px";
      button.onclick = () => {
        selected = button.dataset.lineageNode;
        highlight();
        inspect();
      };
    });
    host.querySelectorAll(".lineage-stage").forEach((label) => {
      label.style.left = Number(label.dataset.stage) * 330 + 14 + "px";
    });
    let zoom = camera?.zoom ?? 1,
      x = camera?.x ?? 0,
      y = camera?.y ?? 0;
    const dimensions = () => ({
      width: viewport.clientWidth || width + 48,
      height: viewport.clientHeight || height + 208,
    });
    const draw = () => {
      camera = { zoom, x, y };
      world.style.transform = `translate(${x}px, ${y}px) scale(${zoom})`;
      host.querySelector("[data-zoom-label]").textContent =
        Math.round(zoom * 100) + "%";
    };
    const frame = (fit = false) => {
      const size = dimensions();
      zoom = Math.max(
        fit ? 0.1 : 0.75,
        Math.min(
          1,
          (size.width - 48) / width,
          fit ? (size.height - 208) / height : 1,
        ),
      );
      x = fit
        ? (size.width - width * zoom) / 2
        : Math.max(24, (size.width - width * zoom) / 2);
      y =
        112 + (fit ? Math.max(0, (size.height - 208 - height * zoom) / 2) : 0);
      draw();
    };
    const setZoom = (value, anchor = null) => {
      const size = dimensions(),
        center = anchor || { x: size.width / 2, y: size.height / 2 },
        next = Math.max(0.1, Math.min(2, value));
      x = center.x - ((center.x - x) * next) / zoom;
      y = center.y - ((center.y - y) * next) / zoom;
      zoom = next;
      draw();
    };
    if (camera) draw();
    else frame();
    function highlight() {
      const active = related(data, map.get(selected));
      const search = host
        .querySelector("[data-lineage-search]")
        .value.trim()
        .toLowerCase();
      host.querySelectorAll("[data-lineage-node]").forEach((el) => {
        const node = map.get(el.dataset.lineageNode);
        el.classList.toggle("is-dim", !active.nodes.has(node.id));
        el.classList.toggle(
          "is-match",
          Boolean(search) &&
            JSON.stringify([node.label, node.endpoint, node.tables])
              .toLowerCase()
              .includes(search),
        );
        el.setAttribute("aria-pressed", String(node.id === selected));
      });
      host.querySelectorAll("[data-edge-label]").forEach((el) => {
        el.classList.toggle("is-dim", !active.edges.has(el.dataset.edgeLabel));
      });
      host.querySelectorAll("[data-lineage-edge]").forEach((el) => {
        el.classList.toggle(
          "is-dim",
          !active.edges.has(el.dataset.lineageEdge),
        );
        el.classList.toggle(
          "is-active",
          Boolean(selected) && active.edges.has(el.dataset.lineageEdge),
        );
      });
    }
    function inspect() {
      const node = map.get(selected),
        panel = host.querySelector(".lineage-inspector");
      panel.hidden = !node;
      if (!node) {
        panel.replaceChildren();
        return;
      }
      const details = [
        ["节点类型", stages[node.stage]],
        ["数量", `${node.value == null ? "—" : num(node.value)} ${node.unit}`],
        ["可领取", node.eligible == null ? null : num(node.eligible)],
        [
          "分配任务",
          node.allocated_tasks == null ? null : num(node.allocated_tasks),
        ],
        [
          "返回并入库记录",
          node.returned_records == null ? null : num(node.returned_records),
        ],
        ["接口", node.endpoint],
        ["数据表", node.tables],
        [
          "HTTP 请求",
          node.stage === 3
            ? node.requests == null
              ? "未记录"
              : num(node.requests)
            : null,
        ],
        [
          "HTTP 失败",
          node.stage === 3 && node.requests != null
            ? num(node.http_errors)
            : null,
        ],
        ["来源配置", node.configured],
        ["请求统计起点", node.request_since ? stamp(node.request_since) : null],
      ];
      const neighbors = data.edges.filter(
        (e) => e.source === node.id || e.target === node.id,
      );
      panel.innerHTML = `<div class="lineage-inspector-heading"><h3>${esc(node.label)}</h3><button type="button" class="lineage-inspector-close" data-lineage-close aria-label="关闭节点详情" title="关闭节点详情"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg></button></div><p>${esc(node.note || "")}</p><dl>${details
        .filter(([, v]) => v != null)
        .map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`)
        .join("")}</dl>${
        node.states
          ? `<h4>队列状态</h4><dl>${Object.entries(node.states)
              .map(
                ([k, v]) =>
                  `<div><dt>${esc(stateNames[k] || k)}</dt><dd>${num(v)}</dd></div>`,
              )
              .join("")}</dl>`
          : ""
      }${node.records?.length ? `<h4>已提交结果</h4>${node.records.map((r) => `<div class="lineage-result"><strong>${num(r.records)} ${esc(r.unit)}</strong><span>${esc(r.store)} · ${r.attempts == null ? "" : num(r.attempts) + " 次目标观察"} ${r.errors == null ? "" : num(r.errors) + " 次失败"}</span><small>${esc(stamp(r.last_at))}</small></div>`).join("")}` : ""}<h4>关联路径</h4>${neighbors
        .map((e) => {
          const upstream = e.target === node.id;
          const target = map.get(upstream ? e.source : e.target);
          return `<button class="lineage-related content-card" data-related="${esc(target.id)}"><span>${upstream ? "←" : "→"} ${esc(target.label)}</span>${e.label ? `<small>${esc(e.label)}</small>` : ""}</button>`;
        })
        .join(
          "",
        )}${node.link ? `<a class="button secondary" href="${esc(node.link)}">查看对应数据 / 任务</a>` : ""}`;
      panel.querySelector("[data-lineage-close]").onclick = () => {
        selected = null;
        highlight();
        inspect();
      };
      panel.querySelectorAll("[data-related]").forEach(
        (b) =>
          (b.onclick = () => {
            selected = b.dataset.related;
            highlight();
            inspect();
          }),
      );
    }
    host.querySelector("[data-lineage-hours]").onchange = (e) => {
      hours = Number(e.target.value);
      render(host, rev).catch((e) => toast(e.message));
    };
    host.querySelector("[data-lineage-refresh]").onclick = () =>
      render(host, rev, true).catch((e) => toast(e.message));
    host.querySelector("[data-lineage-direction]").onchange = (e) => {
      direction = e.target.value;
      highlight();
    };
    host.querySelector("[data-lineage-search]").oninput = highlight;
    host.querySelector("[data-lineage-clear]").onclick = () => {
      selected = null;
      highlight();
      inspect();
    };
    host.querySelectorAll("[data-zoom]").forEach(
      (b) =>
        (b.onclick = () => {
          if (b.dataset.zoom === "fit") frame(true);
          else if (b.dataset.zoom === "reset") frame();
          else setZoom(zoom + (b.dataset.zoom === "in" ? 0.1 : -0.1));
        }),
    );
    host.querySelector("[data-lineage-flow]").onclick = (event) => {
      const moving = world.classList.toggle("is-flowing");
      event.currentTarget.textContent = moving ? "暂停流动" : "显示流向";
      event.currentTarget.setAttribute("aria-pressed", String(moving));
    };
    let drag,
      suppressClick = false;
    viewport.onpointerdown = (e) => {
      if (e.button !== 0 || drag) return;
      suppressClick = false;
      drag = {
        id: e.pointerId,
        startX: e.clientX,
        startY: e.clientY,
        x,
        y,
        moved: false,
      };
    };
    viewport.onpointermove = (e) => {
      if (!drag || drag.id !== e.pointerId) return;
      const dx = e.clientX - drag.startX,
        dy = e.clientY - drag.startY;
      if (!drag.moved && Math.hypot(dx, dy) < 4) return;
      if (!drag.moved) viewport.setPointerCapture(e.pointerId);
      drag.moved = true;
      viewport.classList.add("is-dragging");
      x = drag.x + dx;
      y = drag.y + dy;
      draw();
    };
    const endDrag = (e) => {
      if (!drag || drag.id !== e.pointerId) return;
      suppressClick = drag.moved;
      if (viewport.hasPointerCapture(e.pointerId))
        viewport.releasePointerCapture(e.pointerId);
      drag = null;
      viewport.classList.remove("is-dragging");
    };
    viewport.onpointerup = endDrag;
    viewport.onpointercancel = endDrag;
    viewport.onlostpointercapture = endDrag;
    viewport.onpointerleave = (e) => {
      if (!drag?.moved) endDrag(e);
    };
    viewport.addEventListener(
      "click",
      (e) => {
        if (suppressClick) {
          e.stopPropagation();
          e.preventDefault();
          suppressClick = false;
        }
      },
      true,
    );
    viewport.addEventListener(
      "wheel",
      (e) => {
        e.preventDefault();
        if (e.ctrlKey || e.metaKey) {
          const rect = viewport.getBoundingClientRect();
          setZoom(zoom * Math.exp(-e.deltaY * 0.002), {
            x: e.clientX - rect.left,
            y: e.clientY - rect.top,
          });
        } else {
          const unit =
            e.deltaMode === 1
              ? 16
              : e.deltaMode === 2
                ? viewport.clientHeight
                : 1;
          x -= (e.shiftKey ? e.deltaY : e.deltaX) * unit;
          y -= (e.shiftKey ? 0 : e.deltaY) * unit;
          draw();
        }
      },
      { passive: false },
    );
    viewport.onkeydown = (e) => {
      if (e.target !== viewport) return;
      const offset = {
        ArrowLeft: [48, 0],
        ArrowRight: [-48, 0],
        ArrowUp: [0, 48],
        ArrowDown: [0, -48],
      }[e.key];
      if (offset) {
        e.preventDefault();
        x += offset[0];
        y += offset[1];
        draw();
      } else if (e.key === "Home") {
        e.preventDefault();
        frame();
      }
    };
    highlight();
    inspect();
  }
  return { render };
}
