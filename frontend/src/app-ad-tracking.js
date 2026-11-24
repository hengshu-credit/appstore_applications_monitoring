import { pagination, bindPagination } from "./pagination.js";
import "./app-ad-tracking.css";

export function createAdTracking(host, m, storeRankings, ctx) {
  const { api, esc, safeURL, stamp, num, table, panel, canCollect } = ctx;
  const base = `/ads/apps/${encodeURIComponent(m.store)}/${encodeURIComponent(m.app_id)}`;
  let channel = "",
    offset = 0,
    limit = 12,
    generation = 0,
    timer,
    galleryKey,
    schedule,
    submitting = false;
  const find = (selector) => host.querySelector(selector);
  const live = () => host.isConnected && !host.hidden;
  const external = (url, label) => {
    const href = safeURL(url);
    return href
      ? `<a href="${href}" target="_blank" rel="noopener noreferrer">${esc(label)} ↗</a>`
      : "";
  };
  const statusLabels = {
    not_checked: "未采集",
    pending: "排队中",
    running: "采集中",
    done: "已完成",
    error: "采集失败",
    paused: "已暂停",
    stopped: "已停用，需重新提交",
    needs_auth: "待配置授权",
    unsupported: "此商店或地区未覆盖",
  };
  const typeLabels = { image: "图片", video: "视频", other: "素材" };

  function media(asset, index) {
    const label = typeLabels[asset.type] || "素材";
    const source = safeURL(asset.url);
    const preview = safeURL(asset.preview_url);
    const picture = preview || (asset.type === "image" ? source : "");
    const caption = source ? `打开${label}` : `${label}预览图`;
    const visual =
      asset.type === "video" &&
      !asset.external_only &&
      source.startsWith("https:")
        ? `<video controls playsinline preload="none" src="${source}" ${preview ? `poster="${preview}"` : ""} aria-label="广告视频 ${index + 1}"></video>`
        : picture
          ? `<img loading="lazy" decoding="async" referrerpolicy="no-referrer" src="${picture}" alt="${label}预览 ${index + 1}">`
          : "";
    return `<figure>${visual}<figcaption>${external(asset.url || asset.preview_url, caption)}</figcaption></figure>`;
  }

  function card(ad) {
    const assets = ad.assets || [];
    const title =
      ad.title ||
      (assets.length
        ? (typeLabels[assets[0].type] || "广告") + "素材"
        : "广告观察");
    const text = ad.body || "";
    const dates = [ad.first_seen, ad.last_seen].filter(Boolean);
    const publishers = ad.publishers || [];
    const links = [
      [ad.evidence_url, "来源证据"],
      [ad.ad_url, "广告原页"],
      [ad.landing_url, "投放落地页"],
      [ad.store_url, "商店页"],
    ];
    return `<article class="ad-creative">
      <div class="ad-creative-meta"><strong>${esc(ad.channel || "渠道未披露")}</strong><span>${esc(ad.country?.toUpperCase() || "地区未披露")}</span></div>
      ${assets.length ? `<div class="ad-assets" tabindex="0" aria-label="广告素材，共 ${assets.length} 项">${assets.map(media).join("")}</div>` : ""}
      <h3>${esc(title)}</h3>
      ${text ? `<p class="ad-copy">${esc(text.length > 180 ? text.slice(0, 180) + "…" : text)}</p>` : ""}
      ${text.length > 180 ? `<details><summary>完整文案</summary><p class="ad-copy">${esc(text)}</p></details>` : ""}
      <p class="sub">${[ad.placement, ad.format, ad.creative_language, ad.keyword].filter(Boolean).map(esc).join(" · ")}</p>
      ${ad.advertiser ? `<div>广告主 · ${esc(ad.advertiser)}</div>` : ""}
      ${dates.length ? `<div>${esc(ad.date_label || (ad.first_seen && ad.last_seen ? "投放观察期" : ad.first_seen ? "首次观察" : "最近观察"))} · ${dates.map(esc).join(" 至 ")}</div>` : ""}
      ${publishers.length ? `<details><summary>展示此广告的应用（${publishers.length}）</summary><p>${publishers.map(esc).join("、")}</p></details>` : ""}
      <div class="sub">${esc(ad.source)} · 采集于 ${esc(stamp(ad.observed_at))}</div>
      <div class="ad-links">${links.map(([url, label]) => external(url, label)).join("")}</div>
      ${ad.version_count > 1 ? `<details><summary data-ad-history="${esc(ad.id)}">已见 ${num(ad.version_count)} 个素材版本</summary><div data-ad-versions></div></details>` : ""}
    </article>`;
  }

  function rankings(sources) {
    const sample = sources.find((s) => s.kind === "ad_rankings");
    const result = sample?.result;
    if (!result)
      return '<p class="sub">等待采集 AppGoblin 月度广告主公开榜单。</p>';
    const stats = [
      ["unique_creatives", "素材数量"],
      ["unique_publishers", "覆盖媒体应用数"],
    ];
    return (
      `<p class="sub">AppGoblin 近一个月的公开样本，共 ${num(result.sample_size)} 个广告主。按各指标从高到低排名，相同数值并列。采集于 ${esc(stamp(sample.success_at))} · ${external(result.evidence_url, "查看原榜")}</p>` +
      (result.listed
        ? table(
            ["指标", "样本内排名", "观测值"],
            stats.map(([key, label]) => {
              const r = result.ranks[key];
              return [
                label,
                r ? `第 ${num(r.position)} / ${num(r.sample)}` : "来源未披露",
                num(r?.value),
              ];
            }),
          )
        : "<p>该应用未出现在本次返回的广告主样本中。</p>")
    );
  }

  function render(data) {
    const activeTasks = data.sources.some(
      (s) =>
        ["pending", "running"].includes(s.status) ||
        (s.status === "error" && s.failures < 3),
    );
    schedule = data.schedule;
    find("[data-ad-schedule]").textContent = schedule?.enabled
      ? `周期任务每 ${num(schedule.refresh_hours)} 小时执行，下一轮计划于 ${stamp(schedule.next_due)}。`
      : schedule
        ? "周期任务已停止。"
        : "当前市场尚未设置周期任务。";
    const stop = find("[data-ad-stop]");
    if (stop) stop.hidden = !schedule?.enabled;
    const total = data.channels.reduce((n, c) => n + c.count, 0);
    find("[data-ad-total]").textContent =
      `${num(total)} 条投放记录 · 已观察到 ${num(data.channels.filter((c) => c.count && c.channel !== "渠道未披露").length)} 个渠道`;
    const channelButton = (name, count, note) =>
      `<button type="button" class="secondary" data-ad-channel="${esc(name)}" aria-pressed="${name === channel}"><strong>${esc(name || "全部渠道")}</strong><span>${count ? num(count) + " 条" : esc(note)}</span></button>`;
    const filters = data.channels.map((c) => {
      const statuses = (c.providers || []).map(
        (k) => data.sources.find((s) => s.kind === k)?.status,
      );
      const status =
        statuses.find((s) => s && s !== "unsupported") || statuses[0];
      const note =
        status === "done" ? "样本未见" : statusLabels[status] || "暂无记录";
      return channelButton(c.channel, c.count, note);
    });
    find("[data-ad-filters]").innerHTML =
      channelButton("", total, "暂无记录") + filters.join("");
    const selected = data.channels.find((c) => c.channel === channel);
    find("[data-ad-channel-status]").textContent = (selected?.providers || [])
      .map((k) => data.sources.find((s) => s.kind === k))
      .filter(Boolean)
      .map((s) => s.name + " · " + (statusLabels[s.status] || s.status))
      .join("　");
    const key = JSON.stringify([
      channel,
      offset,
      limit,
      data.total,
      data.items.map((ad) => [ad.id, ad.observed_at]),
    ]);
    if (key !== galleryKey) {
      galleryKey = key;
      find("[data-ad-items]").innerHTML = data.items.length
        ? `<div class="ad-gallery">${data.items.map(card).join("")}</div>${pagination({ id: "ad-creatives", offset, limit, total: data.total, sizes: [12, 24, 48] })}`
        : '<p class="empty">当前范围暂无广告观察记录。</p>';
      bindPagination(host, "ad-creatives", (start, size) => {
        offset = start;
        limit = size;
        return load();
      });
    }
    find("[data-ad-rankings]").innerHTML = rankings(data.sources);
    const google = data.sources.find((s) => s.kind === "ad_google");
    const leads = google?.result?.candidates || [];
    find("[data-ad-leads]").innerHTML =
      leads.length && (!channel || channel === "Google")
        ? `<details class="ad-source-details"><summary>Google 开发者域名广告线索（${num(leads.length)}）</summary>
           <p class="sub">这些广告关联 ${esc(google.result.domain)}，应用归属待核实。</p>
           <div class="ad-gallery">${leads.map((ad) => card({ ...ad, observed_at: google.success_at })).join("")}</div></details>`
        : "";
    find("[data-ad-sources]").innerHTML =
      table(
        ["来源", "采集状态", "最近成功", "本次来源覆盖"],
        data.sources.map((s) => {
          const r = s.result;
          const status =
            s.status === "error" && s.failures < 3
              ? "等待重试"
              : statusLabels[s.status] || s.status;
          const coverage =
            s.kind === "ad_rankings"
              ? r
                ? `${num(r.sample_size)} 个广告主`
                : "—"
              : r
                ? `${num(r.returned)} 条${r.period_start && r.period_end ? ` · ${esc(r.period_start)} 至 ${esc(r.period_end)}` : ""}`
                : "—";
          const progress = s.progress
            ? ` · 已保存 ${num(s.progress.returned)} 条${s.progress.total != null ? " / 来源共 " + num(s.progress.total) + " 条" : ""}`
            : "";
          return [
            esc(s.name),
            esc(status) +
              progress +
              (s.error ? `<div class="sub">${esc(s.error)}</div>` : ""),
            esc(stamp(s.success_at)),
            r?.coverage ? esc(r.coverage) : coverage,
          ];
        }),
      ) +
      (data.sources.some((s) => s.status === "needs_auth")
        ? `<p class="sub">${ctx.canConfigure ? '<a href="#datasources">前往数据源配置</a>' : "请联系管理员配置广告来源授权。"}</p>`
        : "");
    const reports = find("[data-ad-reports]");
    reports.innerHTML = data.reports.length
      ? panel(
          "自有账户日报",
          '<p class="sub">当前市场最近 30 条已导入记录</p>' +
            table(
              ["日期", "系列 / 关键词", "展示 / 点击 / 安装", "支出", "来源"],
              data.reports.map((r) => [
                esc(r.report_date),
                [r.campaign, r.keyword].map(esc).join(" / "),
                [r.impressions, r.taps, r.installs].map(num).join(" / "),
                esc(r.currency) + " " + esc(r.spend),
                esc(r.source),
              ]),
            ),
        )
      : "";
    if (activeTasks || schedule?.enabled)
      timer = setTimeout(
        () => {
          if (live()) load();
        },
        activeTasks
          ? 5000
          : Math.max(
              5000,
              Math.min(
                2147483647,
                schedule.next_due * 1000 - Date.now() + 1000,
              ),
            ),
      );
  }

  async function load() {
    clearTimeout(timer);
    const ticket = ++generation;
    try {
      const query = new URLSearchParams({
        country: m.country,
        language: m.language,
        channel,
        offset,
        limit,
      });
      const data = await api(base + "?" + query);
      if (!live() || ticket !== generation) return;
      find("[data-ad-status]").textContent = "";
      render(data);
    } catch (error) {
      if (live() && ticket === generation)
        find("[data-ad-status]").textContent = error.message;
    }
  }

  host.innerHTML =
    panel(
      "投放素材",
      `<div class="ad-toolbar"><strong data-ad-total></strong><button type="button" class="secondary" data-ad-refresh>刷新记录</button></div>
    ${
      canCollect
        ? `<form class="ad-task-form" data-ad-task><label>采集方式<select name="mode"><option value="once">一次性</option><option value="periodic">周期性</option></select></label>
      <label data-ad-interval hidden>执行间隔（小时）<input name="refresh_hours" type="number" min="0.25" max="8760" step="any" value="24" disabled></label>
      <button type="submit" data-ad-collect>提交采集任务</button></form>`
        : ""
    }
    <div class="ad-schedule"><span class="sub" data-ad-schedule></span>${canCollect ? '<button type="button" class="link" data-ad-stop hidden>停止周期任务</button>' : ""}</div>
    <p class="sub" role="status" data-ad-task-result></p>
    <div class="ad-channel-filters" role="group" aria-label="投放渠道" data-ad-filters></div><p class="sub" data-ad-channel-status></p><p class="sub">显示 ${esc(m.country.toUpperCase())} 的投放记录和来源未披露地区的素材。</p>
    <details class="ad-source-details"><summary>查看采集来源与进度</summary><div data-ad-sources></div></details>
    <p role="status" data-ad-status>正在读取广告记录…</p><div data-ad-items></div><div data-ad-leads></div>`,
    ) +
    panel("广告投放排名", "<div data-ad-rankings></div>") +
    storeRankings +
    "<div data-ad-reports></div>";
  find("[data-ad-filters]").onclick = (event) => {
    const button = event.target.closest("[data-ad-channel]");
    if (!button || button.dataset.adChannel === channel) return;
    channel = button.dataset.adChannel;
    offset = 0;
    load();
  };
  find("[data-ad-refresh]").onclick = load;
  host.addEventListener("click", async (event) => {
    const toggle = event.target.closest(
      "[data-ad-history], [data-more-versions]",
    );
    if (!toggle) return;
    const details = toggle.closest("details"),
      list = details.querySelector("[data-ad-versions]");
    if (
      list.dataset.loading ||
      (toggle.matches("summary") && (details.open || list.childElementCount))
    )
      return;
    list.dataset.loading = "1";
    const offset = Number(toggle.dataset.moreVersions || 0);
    const id = details.querySelector("summary").dataset.adHistory;
    try {
      const params = new URLSearchParams({
        country: m.country,
        language: m.language,
        offset,
      });
      const data = await api(`${base}/observations/${id}/versions?${params}`);
      if (!list.isConnected) return;
      if (!offset) list.replaceChildren();
      list.querySelector("[data-more-versions]")?.remove();
      for (const v of data.items) {
        list.insertAdjacentHTML(
          "beforeend",
          `<div class="ad-version"><strong>${esc(v.title || v.format || "广告素材")}</strong>
          <p class="sub">采集于 ${esc(stamp(v.first_observed_at))} 至 ${esc(stamp(v.last_observed_at))}</p>
          <p class="ad-copy">${esc(v.body || "")}</p><div class="ad-links">${(v.assets || []).map((a, i) => external(a.url || a.preview_url, "素材 " + (i + 1))).join("")}${external(v.landing_url, "落地页")}</div></div>`,
        );
      }
      if (data.has_more)
        list.insertAdjacentHTML(
          "beforeend",
          `<button class="secondary" data-more-versions="${offset + data.items.length}">更多版本</button>`,
        );
    } catch (e) {
      if (list.isConnected) find("[data-ad-status]").textContent = e.message;
    } finally {
      delete list.dataset.loading;
    }
  });
  host.addEventListener(
    "error",
    (event) => {
      const media = event.target;
      if (!media.matches?.(".ad-assets img, .ad-assets video")) return;
      const note = document.createElement("span");
      note.className = "ad-preview-error";
      note.textContent = "素材预览暂不可用";
      media.replaceWith(note);
    },
    true,
  );
  const form = find("[data-ad-task]");
  if (form) {
    const mode = form.elements.mode,
      interval = form.elements.refresh_hours;
    const collect = find("[data-ad-collect]"),
      stop = find("[data-ad-stop]");
    const feedback = find("[data-ad-task-result]");
    mode.onchange = () => {
      const periodic = mode.value === "periodic";
      find("[data-ad-interval]").hidden = !periodic;
      interval.disabled = !periodic;
      interval.required = periodic;
      if (periodic && schedule?.enabled)
        interval.value = schedule.refresh_hours;
      feedback.textContent = periodic
        ? schedule?.enabled
          ? "修改间隔后提交，即可调整下一轮的执行时间。"
          : "提交后先执行一轮，随后按间隔采集。"
        : "";
    };
    const submit = async (stopping) => {
      if (submitting) return;
      submitting = collect.disabled = stop.disabled = true;
      feedback.textContent = "";
      clearTimeout(timer);
      try {
        const market = { country: m.country, language: m.language };
        if (stopping) {
          await api(
            base + "/schedule?" + new URLSearchParams(market),
            undefined,
            "DELETE",
          );
          feedback.textContent = "已撤销该周期任务尚未完成的来源任务。";
        } else {
          const periodic = mode.value === "periodic";
          const result = await api(base + "/collect", {
            ...market,
            mode: mode.value,
            ...(periodic ? { refresh_hours: Number(interval.value) } : {}),
          });
          feedback.textContent = periodic
            ? result.tasks.length
              ? "周期任务已提交，首轮已入队。"
              : "周期设置已保存。"
            : `一次性任务已提交，共 ${num(result.tasks.length)} 个来源。`;
        }
        if (live()) await load();
      } catch (error) {
        if (live()) feedback.textContent = error.message;
      } finally {
        submitting = collect.disabled = stop.disabled = false;
      }
    };
    form.onsubmit = (event) => {
      event.preventDefault();
      if (form.reportValidity()) submit(false);
    };
    stop.onclick = () => submit(true);
  }
  return {
    show: load,
    hide() {
      clearTimeout(timer);
      generation++;
    },
  };
}
