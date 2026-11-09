// Public setup references. Credentials and deployment bindings remain on the server.
const guides = {
  community: {
    vendor: "AppGoblin",
    hosts: ["appgoblin.info"],
    setup:
      "接入地址填写 https://appgoblin.info，允许域名填写 appgoblin.info。在“公开指标接入”区域填写 AppGoblin 账户的邮箱和密码，再保存并授权；邮箱验证或二次验证在来源网站完成。",
    scope:
      "用于应用公开元数据和免费数据目录；网站提供的数据与账户实际权限为准。",
    links: [
      ["官网", "https://appgoblin.info/about"],
      ["注册 / 登录", "https://appgoblin.info/auth/signup"],
      ["免费数据包", "https://appgoblin.info/free-app-datasets"],
    ],
  },
  directory: {
    vendor: "AppBrain",
    hosts: ["www.appbrain.com", "appbrain.com"],
    setup:
      "接入地址填写 https://www.appbrain.com，允许域名填写 www.appbrain.com, developers.appbrain.com, appbrain.com。在“安装指标接入”区域填写 AppBrain 自身的邮箱密码；使用第三方登录的账户需先在官网确认可用的密码登录方式。",
    scope:
      "提供 Android 安装参考指标；可见指标由来源和账户权限决定，不代表全量下载数据。",
    links: [
      ["官网", "https://www.appbrain.com/"],
      ["注册 / 登录", "https://www.appbrain.com/login"],
    ],
  },
  marketdata: {
    vendor: "Appfigures",
    hosts: ["api.appfigures.com"],
    setup:
      "接入地址填写 https://api.appfigures.com/v2，允许域名填写 api.appfigures.com。在 My API Keys 创建具有所需读取权限的 API Client，再进入其详情生成 Personal Access Token，填入页面“市场数据采集”区域的 Personal Access Token 字段。仅保存邮箱密码不能启用 API 采集。",
    scope:
      "用于授权范围内的历史排名、评论、下载与收入估算。套餐权限与 API 配额决定可采范围；估算值与商店实测分开保存。",
    links: [
      ["官网", "https://appfigures.com/"],
      ["创建 Token", "https://appfigures.com/developers/keys"],
      [
        "Token 配置文档",
        "https://docs.appfigures.com/guides/authentication/personal-access-tokens/",
      ],
      ["API 文档", "https://docs.appfigures.com/"],
    ],
  },
  web_feed: {
    vendor: "Google Trends",
    hosts: ["trends.google.com"],
    setup:
      "接入地址填写 https://trends.google.com/trending/rss，允许域名填写 trends.google.com。此来源不需要账户或 Token。",
    scope: "提供网页搜索趋势，可作为关键词线索；不是应用商店排名或应用下载量。",
    links: [["趋势来源", "https://trends.google.com/trending"]],
  },
  apple_ads: {
    setup:
      "按 Apple Ads Platform API 文档完成 OAuth 授权，填写得到的 access_token。广告账户 ID 使用账户访问列表返回的 adAccount.id（adAccountId），不要填组织 orgId。Token 到期后需更新。",
    scope: "此处读取授权的关键词热度；不用于创建广告或自动开通付费服务。",
    links: [
      [
        "账户与 API 接入说明",
        "https://developer.apple.com/documentation/apple-ads-platform-api/access-overview",
      ],
    ],
  },
  apple_lookup: {
    setup:
      "内置 Apple 公开查询接口，不需要 App Store 账号或密码。配置项只控制同市场应用详情的请求批量。",
    links: [
      [
        "Apple 查询接口文档",
        "https://developer.apple.com/library/archive/documentation/AudioVideo/Conceptual/iTuneSearchAPI/index.html",
      ],
    ],
  },
  apple_search: {
    setup:
      "内置 Apple 公开搜索接口，无需账号；按国家、关键词和语言查询公开结果。",
    links: [
      [
        "Apple 搜索接口文档",
        "https://developer.apple.com/library/archive/documentation/AudioVideo/Conceptual/iTuneSearchAPI/index.html",
      ],
    ],
  },
  apple_rss: {
    setup:
      "内置 Apple 公开榜单与评论读取，不需要填写凭据；评论仅代表接口返回的近期范围。",
    links: [["Apple RSS 入口", "https://rss.marketingtools.apple.com/"]],
  },
  apple_today: {
    setup: "读取 App Store 可公开访问的编辑推荐，无需账号。",
    links: [["App Store", "https://apps.apple.com/"]],
  },
  google_play: {
    setup:
      "读取 Google Play 公开商店信息，无需 Google 账号。批量设置用于应用详情，按同一市场与语言组批。",
    links: [["Google Play", "https://play.google.com/store/apps"]],
  },
  store_cdn: {
    setup: "由商店详情中的素材链接自动发现，不需要填写账号或单独配置地址。",
    links: [],
  },
  appgoblin_seed: {
    setup:
      "从 AppGoblin 免费数据目录或公开仓库取得数据包，在下方“公开批量数据包”导入本地文件。目录下载可能需要 AppGoblin 免费账户。",
    scope: "公开历史基线采用 CC BY 4.0 许可，不等同于当前市场的实时观察。",
    links: [
      ["数据包目录", "https://appgoblin.info/free-app-datasets"],
      ["数据仓库与字段说明", "https://github.com/appgoblin-dev/appgoblin-data"],
    ],
  },
};

function safeSourceUrl(value) {
  if (typeof value !== "string" || /\s/.test(value)) return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password
      ? url
      : null;
  } catch {
    return null;
  }
}

export function sourceTitle(source) {
  const guide = guides[source.id];
  if (!guide?.vendor) return source.name || source.id;
  const url = safeSourceUrl(source.origin);
  return url && !guide.hosts.includes(url.hostname)
    ? `${source.name || source.id} · 自定义接入`
    : `${guide.vendor} · ${source.name || source.id}`;
}

export function sourceAddress(esc, origin) {
  const addresses = typeof origin === "string" ? origin.split(/\s+\/\s+/) : [];
  if (addresses.length > 1)
    return addresses.map((address) => sourceAddress(esc, address)).join("<br>");
  const url = safeSourceUrl(origin);
  return url
    ? `<a href="${esc(url.href)}" target="_blank" rel="noopener noreferrer">${esc(origin)}</a>`
    : esc(origin || "尚未配置");
}

export function sourceGuide(esc, source, compact = false) {
  const guide = guides[source.id];
  if (!guide) return "";
  const url = safeSourceUrl(source.origin);
  const custom = guide.vendor && url && !guide.hosts.includes(url.hostname);
  const setup = `<p>${esc(
    custom
      ? `当前为自定义兼容接入，采用 ${guide.vendor} 适配协议。地址、允许域名和账户凭据由该接入服务的管理者提供；下方官方链接仅用于协议参考。`
      : guide.setup,
  )}</p>`;
  return `<div class="source-guide">${compact ? `<details><summary>填写说明</summary>${setup}</details>` : setup}${!compact && guide.scope ? `<p class="sub">${esc(guide.scope)}</p>` : ""}${source.editable_origin && !compact ? `<p class="sub">当前接入：${sourceAddress(esc, source.origin)}。自建兼容服务使用自己的地址和允许域名。</p>` : ""}<div class="source-guide-links">${guide.links.map(([label, url]) => `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)} ↗</a>`).join("")}</div></div>`;
}
