export function selectedStores(value) {
  return value === "all" ? ["google", "apple"] : [value];
}
export function recommendedLanguage(preferences, store, country) {
  return preferences?.countries?.[country]?.[store]?.default || "en";
}
export function effectiveLanguage(preferences, store, country, value = "auto") {
  if (value !== "auto") return value;
  return store === "all"
    ? "auto"
    : recommendedLanguage(preferences, store, country);
}
export function languageName(preferences, code) {
  return (
    preferences?.languages?.[code] ||
    preferences?.languages?.[code.split("-")[0]] ||
    code
  );
}
export function autoCaption(preferences, store, country) {
  const codes = [
    ...new Set(
      selectedStores(store).map((s) =>
        recommendedLanguage(preferences, s, country),
      ),
    ),
  ];
  return "自动 · " + codes.map((c) => languageName(preferences, c)).join(" / ");
}
export function languageChoices(preferences, store, country, common) {
  const suggested = [
    ...new Set(
      selectedStores(store).flatMap(
        (s) => preferences?.countries?.[country]?.[s]?.options || ["en"],
      ),
    ),
  ];
  const rows = [
    ["auto", "自动匹配国家和商店"],
    ...suggested.map((code) => [
      code,
      languageName(preferences, code) + "（推荐）",
    ]),
    ...common,
  ];
  const seen = new Set();
  return rows.filter(([code]) => {
    if (seen.has(code)) return false;
    seen.add(code);
    return true;
  });
}
