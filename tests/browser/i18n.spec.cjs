const { test, expect } = require("@playwright/test");

// Migrated from Tabular 0.10.2: map controls belong to OSM and evolve with its UI.
test("OSM maps, searchable layers and shared lightbox use the triggering locale", async ({ page }) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/unpkg.com/**", (route) => route.abort());
  await page.goto("/i18n.html#place-0");
  for (const [locale, label, all, close, name] of [
    ["en", "Work", "All (11)", "Close (Esc)", "Source 0"],
    ["ja", "作品", "すべて（11）", "閉じる（Esc）", "場所 0"],
  ]) {
    const map = page.locator(`#osm-${locale}`);
    await expect(map.locator(".osm-popup-name")).toHaveText(name);
    await map.locator(".osm-explorer-filter-toggle").click();
    const layer = map.locator(".osm-map-layer-bar");
    await expect(layer.locator("legend")).toHaveText(label);
    await layer.getByRole("button").click();
    const input = layer.getByRole("combobox", { name: label });
    await expect(input).toBeFocused();
    await expect(layer.getByRole("option").first()).toHaveText(all);
    await input.press("Escape");
    await map.locator(".osm-explorer-filter-toggle").click();
    await map.locator(".osm-popup-photo-button").click();
    await expect(page.locator("#osm-photo-lightbox")).toHaveAttribute("lang", locale);
    await expect(page.locator(".osm-lightbox-close")).toHaveAttribute("aria-label", close);
    await page.keyboard.press("Escape");
  }
  await page.locator("#osm-ja .osm-list-photo-button").first().click();
  await expect(page.locator(".osm-lightbox-image")).toHaveAttribute("alt", "場所の写真");
  await page.keyboard.press("Escape");
  expect(errors).toEqual([]);
});
