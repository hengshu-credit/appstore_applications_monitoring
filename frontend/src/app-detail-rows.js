import { createAppDetailTable } from "./app-detail-table.js";

export function createAppDetailRows(ctx) {
  const render = createAppDetailTable(ctx);
  return async function appRows(items, options = {}) {
    const scope = options.scope || ctx.market();
    const rows = items.map((item, i) => ({
      ...scope,
      ...item.payload,
      ...item.metadata,
      ...item,
      id: i + 1,
      store: item.store || scope.store,
      country: item.country || scope.country,
      language:
        item.language ||
        ((item.store || scope.store) === scope.store ? scope.language : null) ||
        ctx.market(item.store || scope.store).language,
      _source: item,
    }));
    let note = "";
    if (rows.length) {
      try {
        // Bound each read to the visible rows; shared tables never enqueue collection.
        for (let offset = 0; offset < rows.length; offset += 200) {
          const batch = rows.slice(offset, offset + 200);
          const data = await ctx.api("/apps/detail-rows", {
            targets: batch.map(({ store, app_id, country, language }) => ({
              store,
              app_id,
              country,
              language,
            })),
            days: options.days || 30,
          });
          if (!Array.isArray(data.items)) throw new Error("未返回应用明细");
          const key = (r) =>
            JSON.stringify([r.store, r.app_id, r.country, r.language]);
          const details = new Map(data.items.map((row) => [key(row), row]));
          for (const row of batch) {
            const detail = details.get(key(row));
            if (!detail) continue;
            Object.assign(
              row,
              Object.fromEntries(
                Object.entries(detail).filter(
                  ([k, v]) =>
                    k !== "id" &&
                    (v != null || !["name", "icon", "developer"].includes(k)),
                ),
              ),
            );
          }
        }
      } catch (error) {
        note = `<p class="sub" role="status">补充明细暂不可用：${ctx.esc(error.message)}。已保留当前结果。</p>`;
      }
    }
    return (
      note +
      render(
        { items: rows },
        {
          ...options,
          appCell: (r, developer) =>
            `<div class="member-identity">${ctx.appIconMarkup(r.store, r.app_id, r.name, r.icon, r.country, r.language)}<div><button class="link member-app-name" data-app="${ctx.esc(r.app_id)}" data-store="${ctx.esc(r.store)}" data-country="${ctx.esc(r.country)}" data-language="${ctx.esc(r.language)}">${ctx.esc(r.name || r.app_id)}</button>${developer}</div></div>`,
        },
      )
    );
  };
}
