const { test, expect, devices } = require("@playwright/test");
const path = require("node:path");
const clusterDist = path.join(path.dirname(require.resolve("leaflet.markercluster/package.json")), "dist");

test.use({ ...devices["iPhone 13"], browserName: "chromium" });

async function visit(page, url = "/explorer.html") {
  await page.route("https://unpkg.com/leaflet.markercluster@1/dist/**", (route) =>
    route.fulfill({ path: path.join(clusterDist, path.basename(new URL(route.request().url()).pathname)) }),
  );
  await page.goto(url);
  await expect(page.locator('.osm-map-block[lang="zh-Hant"] .osm-explorer-count')).toContainText("顯示");
}

async function narrowToOnePlace(page, root) {
  await root.getByRole("searchbox", { name: "搜尋地點…" }).fill("日光");
  await expect(root.locator(".osm-explorer-count")).toHaveText("顯示 1 / 4 個地點");
  await expect(root.locator(".leaflet-marker-icon")).toHaveCount(1);
}

// A popup taller than the map makes autoPan push the marker out of the viewport;
// mobile markercluster used to remove it on moveend, closing the popup again.
test("tapping a marker keeps its popup open on a phone", async ({ page }) => {
  await visit(page);
  const root = page.locator('.osm-map-block[lang="zh-Hant"]');
  await narrowToOnePlace(page, root);
  // Non-fullscreen maps default to 400px tall (css/osm-map.css:19, --osm-map-height) and
  // .osm-popup has no max-height outside fullscreen (the 35vh cap, css:65/267, is fullscreen-only).
  // Real popups with a photo or many field rows plus the marker offset exceed the map's visible
  // height; the fixture popup is too short, so min-height 340px (vs the 400px map) simulates that.
  await page.addStyleTag({ content: ".osm-map-block .osm-popup{min-height:340px}" });
  await root.locator(".leaflet-marker-icon").first().tap();
  await page.waitForTimeout(800);
  await expect(root.locator(".leaflet-popup")).toBeVisible();
});

// Control: the fullscreen popup is capped at 35vh (css/osm-map.css:65) so it never pushes the
// marker out of the viewport; this guards that the fullscreen path stays unbroken.
test("tapping a marker keeps its popup open in fullscreen on a phone", async ({ page }) => {
  await visit(page);
  await page.evaluate(() => { Element.prototype.requestFullscreen = undefined; });
  const root = page.locator('.osm-map-block[lang="zh-Hant"]');
  await root.getByRole("button", { name: "切換全螢幕" }).click();
  await expect(root).toHaveClass(/osm-map-block--fullscreen/);
  await narrowToOnePlace(page, root);
  await root.locator(".leaflet-marker-icon").first().tap();
  await page.waitForTimeout(800);
  await expect(root.locator(".leaflet-popup")).toBeVisible();
});
