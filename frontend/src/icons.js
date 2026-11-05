// One 24px / 1.7px outline family; static paths only, no user HTML.
const paths = {
  account: '<circle cx="12" cy="8" r="4"/><path d="M4 21v-2a8 8 0 0 1 16 0v2"/>',
  administration: '<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6Z"/><path d="m8 12 3 3 5-6"/>',
  overview:
    '<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/>',
  events:
    '<rect x="3" y="5" width="18" height="16"/><path d="M7 3v4m10-4v4M3 11h18m-13 5h3m3 0h3"/>',
  apps: '<rect x="4" y="3" width="16" height="18"/><path d="M8 8h2m4 0h2M8 12h2m4 0h2M8 16h2m4 0h2"/>',
  groups:'<rect x="3" y="8" width="18" height="13"/><path d="M3 8V4h7l3 4M7 12h3m4 0h3M7 16h3m4 0h3"/>',
  watchlist:
    '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
  charts: '<path d="M4 3v17h17M8 15l4-5 4 2 5-7"/>',
  globalcharts:
    '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c5 5 5 13 0 18M12 3c-5 5-5 13 0 18"/>',
  featured:
    '<path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9Z"/>',
  keywords:
    '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5m-13-10h5"/>',
  aso: '<path d="m4 16 12-12 4 4-12 12H4ZM13 7l4 4M3 22h18"/>',
  compare:
    '<rect x="3" y="5" width="7" height="14"/><rect x="14" y="5" width="7" height="14"/><path d="M6 9h1m10 6h1"/>',
  reviews: '<path d="M21 4H3v14h5l4 3v-3h9ZM7 9h10M7 13h7"/>',
  market:
    '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c5 5 5 13 0 18-5-5-5-13 0-18Z"/>',
  developers: '<path d="m8 7-5 5 5 5m8-10 5 5-5 5m-3-12-2 18"/>',
  collection:
    '<rect x="3" y="3" width="6" height="6"/><rect x="15" y="15" width="6" height="6"/><path d="M9 6h9v6m-3-3 3 3 3-3M15 18H6v-6m-3 3 3-3 3 3"/>',
  ads: '<path d="m3 10 17-6v16L3 14ZM6 15l2 6h4l-2-5M3 10v4"/>',
  alerts: '<path d="M5 17h14l-2-3V9a5 5 0 0 0-10 0v5ZM10 21h4M12 2v2"/>',
  exports: '<path d="M12 3v12m-5-5 5 5 5-5M4 15v6h16v-6"/>',
  refresh:
    '<path d="M20 8a8 8 0 0 0-14-3L3 8m0-5v5h5M4 16a8 8 0 0 0 14 3l3-3m0 5v-5h-5"/>',
  settings:
    '<path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="3"/><circle cx="15" cy="17" r="3"/>',
  chevron: '<path d="m6 9 6 6 6-6"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
};
export function icon(name) {
  return `<svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.apps}</svg>`;
}
export function storeLogo(store) {
  return `<img class="store-logo" src="/stores/${store === "apple" ? "apple" : "google"}.svg" alt="">`;
}
