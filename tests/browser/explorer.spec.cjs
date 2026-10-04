const { test, expect } = require("@playwright/test");
const path = require("node:path");
const clusterDist = path.join(path.dirname(require.resolve("leaflet.markercluster/package.json")), "dist");

async function visit(page, url = "/explorer.html", clustered = true) {
  await page.route("https://unpkg.com/leaflet.markercluster@1/dist/**", (route) =>
    clustered
      ? route.fulfill({ path: path.join(clusterDist, path.basename(new URL(route.request().url()).pathname)) })
      : route.abort(),
  );
  await page.goto(url);
  await expect(page.locator('.osm-map-block[lang="zh-Hant"] .osm-explorer-count')).toContainText("顯示");
}

for (const clustered of [true, false]) {
  test(`map search, facets, empty state and reset compose (clustered=${clustered})`, async ({ page }) => {
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await visit(page, "/explorer.html", clustered);
    const root = page.locator('.osm-map-block[lang="zh-Hant"]');
    const count = root.locator(".osm-explorer-count");
    const search = root.getByRole("searchbox", { name: "搜尋地點…" });
    await expect(count).toHaveText("顯示 4 / 4 個地點");
    await root.getByRole("button", { name: "篩選", exact: true }).click();
    await root.getByRole("button", { name: "東京", exact: true }).click();
    await root.getByRole("button", { name: "散步", exact: true }).click();
    await expect(count).toHaveText("顯示 1 / 4 個地點");
    // Another city stays available even while the current city is selected.
    await root.getByRole("button", { name: "京都", exact: true }).click();
    await expect(root.getByRole("button", { name: "京都", exact: true })).toHaveAttribute("aria-pressed", "true");
    await expect(root.getByRole("button", { name: "篩選 (2)", exact: true })).toBeVisible();
    await root.getByRole("button", { name: "清除篩選", exact: true }).click();
    await expect(count).toHaveText("顯示 4 / 4 個地點");
    await expect(root.locator('[aria-pressed="true"]')).toHaveCount(0);
    await search.fill("cafe\u0301");
    await expect(count).toHaveText("顯示 1 / 4 個地點");
    await search.fill("休息");
    await expect(count).toHaveText("顯示 1 / 4 個地點");
    await search.fill("no-such-place");
    await expect(count).toHaveText("顯示 0 / 4 個地點");
    await expect(root.locator(".osm-map-no-results")).toBeVisible();
    await root.getByRole("button", { name: "清除篩選", exact: true }).click();
    await expect(root.locator(".osm-map-no-results")).toBeHidden();
    await expect(count).toHaveText("顯示 4 / 4 個地點");
    await expect(search).toHaveValue("");
    expect(errors).toEqual([]);
  });
}

test("map components keep separate queries and languages; deep links restore filtered markers", async ({ page }) => {
  await visit(page);
  const chinese = page.locator('.osm-map-block[lang="zh-Hant"]');
  const english = page.locator('.osm-map-block[lang="en"]');
  await english.scrollIntoViewIfNeeded();
  await expect(english.locator(".osm-explorer-count")).toHaveText("Showing 4 / 4 places");
  await english.getByRole("searchbox", { name: "Search places…" }).fill("東京");
  await expect(english.locator(".osm-explorer-count")).toHaveText("Showing 1 / 4 places");
  await expect(chinese.locator(".osm-explorer-count")).toHaveText("顯示 4 / 4 個地點");
  await chinese.getByRole("searchbox").fill("no-such-place");
  await page.evaluate(() => { location.hash = "cafe"; });
  await expect(chinese.getByRole("searchbox")).toHaveValue("");
  await expect(chinese.locator(".leaflet-popup")).toContainText("Ｃａｆé 日光");
  await expect(english.locator(".leaflet-popup")).toContainText("Ｃａｆé 日光");
});

for (const clustered of [true, false]) {
  test(`cinema search includes hall formats, rows and notes (clustered=${clustered})`, async ({ page }) => {
    await visit(page, "/groups.html", clustered);
    const map = page.locator(".osm-map-block");
    const list = page.locator(".osm-place-list-wrapper");
    const count = map.locator(".osm-explorer-count");
    await expect(count).toHaveText("顯示 4 / 4 個地點");
    for (const [query, places, rows] of [
      ["IMAX", 2, 2], ["音效清楚", 1, 1], ["H–J", 1, 1], ["臺北", 2, 3],
      ["新影廳欄位", 1, 1], ["97", 1, 1],
    ]) {
      await map.getByRole("searchbox").fill(query);
      await expect(count).toHaveText(`顯示 ${places} / 4 個地點`);
      await list.getByRole("searchbox").fill(query);
      await expect(list.locator(".osm-place-row:visible")).toHaveCount(rows);
    }
    await map.getByRole("searchbox").fill("IMAX");
    await map.getByRole("button", { name: "篩選", exact: true }).click();
    await map.getByRole("button", { name: "臺北", exact: true }).click();
    await expect(count).toHaveText("顯示 1 / 4 個地點");
    await list.getByRole("searchbox").fill("missing");
    await expect(count).toHaveText("顯示 1 / 4 個地點");
    await map.getByRole("button", { name: "清除篩選", exact: true }).click();
    await expect(count).toHaveText("顯示 4 / 4 個地點");
    await map.getByRole("searchbox").fill("音效清楚");
    await expect(count).toHaveText("顯示 1 / 4 個地點");
    await map.getByRole("button", { name: "松仁影城", exact: true }).click();
    await expect(map.locator(".osm-popup")).toContainText("松仁影城");
    await expect(map.locator(".osm-popup")).not.toContainText("_osm_search");
    await expect(map.getByRole("link", { name: "在表格中檢視" })).toHaveAttribute("href", /osm-place-song-ren/);
  });
}

test("map and list search all public values without a field allowlist", async ({ page }) => {
  await visit(page);
  const map = page.locator('.osm-map-block[lang="zh-Hant"]');
  const list = page.locator(".osm-place-list-wrapper");
  for (const [query, matches] of [
    ["新欄位也能找到", 1], ["巢狀文字", 1], ["73", 1], ["false", 1],
    ["park.example/guide", 1], ["公園資訊", 1], ["sample2.svg", 1],
    ["35.726", 1], ["private-only-value", 0], ["unused-translation", 0],
  ]) {
    await map.getByRole("searchbox").fill(query);
    await expect(map.locator(".osm-explorer-count")).toHaveText(`顯示 ${matches} / 4 個地點`);
    await list.getByRole("searchbox").fill(query);
    await expect(list.locator(".osm-place-row:visible")).toHaveCount(matches);
  }
  await list.getByRole("searchbox").fill("咖啡");
  await list.locator(".osm-place-row:visible .osm-badge--tag").filter({ hasText: /^散步$/ }).first().click();
  await list.getByRole("searchbox").fill("巢狀文字");
  await expect(list.locator(".osm-place-row:visible")).toHaveCount(1);
  await expect(list.locator(".osm-place-row:visible")).toContainText("南池袋公園");
});

test("large layer dropdown composes with search and clearing", async ({ page }) => {
  await visit(page, "/many-layers.html");
  const root = page.locator('.osm-map-block[lang="zh-Hant"]');
  await root.getByRole("button", { name: "篩選", exact: true }).click();
  const select = root.getByRole("combobox", { name: "圖層" });
  await select.selectOption("City 3");
  await expect(root.locator(".osm-explorer-count")).toHaveText("顯示 1 / 12 個地點");
  await select.selectOption("City 5");
  await root.getByRole("searchbox").fill("missing");
  await expect(root.locator(".osm-explorer-count")).toHaveText("顯示 0 / 12 個地點");
  await root.getByRole("button", { name: "清除篩選", exact: true }).first().click();
  await expect(select).toHaveValue("");
  await expect(root.locator(".osm-explorer-count")).toHaveText("顯示 12 / 12 個地點");
});

for (const width of [390, 1200]) {
  test(`map filters start collapsed and keep many tags compact at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    await visit(page, "/many-tags.html");
    const root = page.locator('.osm-map-block[lang="zh-Hant"]');
    const panel = root.locator(".osm-map-filters");
    const toggle = root.locator(".osm-explorer-filter-toggle");
    await expect(panel).toBeHidden();
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await toggle.click();
    const mapBox = await root.locator(".osm-map").boundingBox();
    const panelBox = await panel.boundingBox();
    expect(panelBox.y).toBeGreaterThanOrEqual(mapBox.y + mapBox.height);
    expect(panelBox.height).toBeLessThanOrEqual(240);
    expect(mapBox.height).toBe(360);
    const chips = panel.locator(".osm-map-tag-chip");
    await expect(chips).toHaveCount(20);
    const boxes = await chips.evaluateAll(elements => elements.map(el => {
      const {x, y, width, height} = el.getBoundingClientRect();
      return {x, y, width, height};
    }));
    for (let i = 0; i < boxes.length; i++) {
      expect(boxes[i].height).toBeGreaterThanOrEqual(44);
      if (!i) continue;
      const previous = boxes[i - 1], current = boxes[i];
      if (current.y === previous.y) expect(current.x - previous.x - previous.width).toBeGreaterThanOrEqual(8);
      else expect(current.y - previous.y - previous.height).toBeGreaterThanOrEqual(8);
    }
    await chips.first().focus();
    await page.keyboard.press("Enter");
    await expect(chips.first()).toHaveAttribute("aria-pressed", "true");
    await expect(root.locator(".osm-explorer-count")).toHaveText("顯示 1 / 20 個地點");
    await toggle.click();
    await expect(panel).toBeHidden();
    await expect(toggle).toHaveText("篩選 (1)");
    await expect(root.locator(".osm-explorer-count")).toHaveText("顯示 1 / 20 個地點");
    await toggle.click();
    await expect(chips.first()).toHaveAttribute("aria-pressed", "true");
    await chips.first().focus();
    await page.keyboard.press("Space");
    await expect(chips.first()).toHaveAttribute("aria-pressed", "false");
    await expect(root.locator(".osm-explorer-count")).toHaveText("顯示 20 / 20 個地點");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await root.screenshot({ path: testInfo.outputPath("filters-below-map.png") });
  });
}

test("list search composes with tags, grouping and sorting through Tabular", async ({ page }) => {
  await visit(page);
  const root = page.locator(".osm-place-list-wrapper");
  const rows = root.locator(".osm-place-row:visible");
  await root.getByRole("searchbox").fill("咖啡");
  await expect(rows).toHaveCount(1);
  await expect(root.locator(".osm-place-list-count")).toHaveText("1 個地點");
  await rows.locator(".osm-badge--tag").filter({ hasText: /^散步$/ }).first().click();
  await root.getByRole("searchbox").fill("");
  await expect(rows).toHaveCount(3);
  await root.getByRole("button", { name: "名稱" }).click();
  await expect(root.locator("th").filter({ hasText: "名稱" })).toHaveAttribute("aria-sort", "ascending");
  await root.getByRole("button", { name: "清除篩選", exact: true }).click();
  await expect(rows).toHaveCount(4);
  await expect(root.locator(".osm-tag-filter-chip")).toHaveCount(0);
  await root.getByRole("searchbox").fill("unknown");
  await expect(rows).toHaveCount(0);
  await expect(root.locator(".osm-list-no-results")).toBeVisible();
  await expect(page.locator('.osm-map-block[lang="zh-Hant"] .osm-explorer-count')).toHaveText("顯示 4 / 4 個地點");
});

test("photo viewer opens from keyboard, traps focus, and returns it on Escape", async ({ page }) => {
  await visit(page);
  const opener = page.locator(".osm-list-photo-button").first();
  await opener.focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog", { name: "地點照片" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("button", { name: "關閉（Esc）" })).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(dialog.getByRole("button", { name: "下一張（→）" })).toBeFocused();
  await page.keyboard.press("ArrowRight");
  await expect(dialog.locator(".osm-lightbox-info")).toHaveText("2 / 2");
  await expect(dialog.getByRole("button", { name: "下一張（→）" })).toBeDisabled();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(opener).toBeFocused();
});

test("mobile defaults, reordered labels, readable text and dark palette", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await visit(page);
  const root = page.locator('.osm-map-block[lang="zh-Hant"]');
  await expect(root.locator(".osm-map-filters")).toBeHidden();
  await root.getByRole("button", { name: "篩選", exact: true }).click();
  await expect(root.locator(".osm-map-filters")).toBeVisible();
  const photo = page.locator(".osm-list-photo-button").first();
  await photo.scrollIntoViewIfNeeded();
  const first = page.locator(".osm-place-row").first();
  const nameBox = await first.locator('[data-field="name"]').boundingBox();
  const categoryBox = await first.locator('[data-field="category"]').boundingBox();
  expect(nameBox.y).toBeLessThan(categoryBox.y);
  expect(await first.locator('[data-field="name"]').evaluate((el) => getComputedStyle(el).fontSize)).toBe("18px");
  expect(await first.locator('[data-field="notes"]').evaluate((el) => getComputedStyle(el, "::before").content)).toContain("備註");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.evaluate(() => document.documentElement.className = "theme-dark");
  expect(await root.evaluate((el) => getComputedStyle(el).backgroundColor)).toBe("rgb(24, 32, 45)");
  expect(await page.locator(".osm-place-list-wrapper").evaluate((el) => getComputedStyle(el).color)).toBe("rgb(228, 233, 242)");
});

test("CSS fullscreen keeps controls usable and resizes the map on exit", async ({ page }) => {
  await visit(page);
  await page.evaluate(() => { Element.prototype.requestFullscreen = undefined; });
  const root = page.locator('.osm-map-block[lang="zh-Hant"]');
  const originalHeight = (await root.locator(".osm-map").boundingBox()).height;
  await root.getByRole("button", { name: "切換全螢幕" }).click();
  await expect(root).toHaveClass(/osm-map-block--fullscreen/);
  await root.getByRole("searchbox").fill("京都");
  await expect(root.locator(".osm-explorer-count")).toHaveText("顯示 1 / 4 個地點");
  const mapBox = await root.locator(".osm-map").boundingBox();
  expect(mapBox.y).toBeGreaterThan(0);
  expect(mapBox.y + mapBox.height).toBeLessThanOrEqual((await page.viewportSize()).height + 1);
  await page.keyboard.press("Escape");
  await expect(root).not.toHaveClass(/osm-map-block--fullscreen/);
  await expect(root.getByRole("button", { name: "切換全螢幕" })).toHaveAttribute("aria-pressed", "false");
  expect((await root.locator(".osm-map").boundingBox()).height).toBe(originalHeight);
});

test("native fullscreen keeps the photo dialog inside the map and restores its toggle", async ({ page }) => {
  await visit(page, "/explorer.html#cafe");
  const root = page.locator('.osm-map-block[lang="zh-Hant"]');
  const fullscreen = root.getByRole("button", { name: "切換全螢幕" });
  await fullscreen.click();
  await expect(fullscreen).toHaveAttribute("aria-pressed", "true");
  await expect(root.locator(".osm-popup-photo-button").first()).toBeVisible();
  await root.locator(".osm-popup-photo-button").first().click();
  await expect(root.getByRole("dialog", { name: "地點照片" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(root.getByRole("dialog")).toBeHidden();
  await fullscreen.click();
  await expect(fullscreen).toHaveAttribute("aria-pressed", "false");
});

test("static lists remain readable and labeled without JavaScript", async ({ browser, baseURL }) => {
  const context = await browser.newContext({ javaScriptEnabled: false, viewport: { width: 390, height: 844 } });
  const page = await context.newPage();
  await page.goto(`${baseURL}/explorer.html`);
  await expect(page.locator(".osm-place-row")).toHaveCount(4);
  await expect(page.locator(".osm-explorer-controls")).toHaveCount(0);
  await expect(page.locator('[data-field="notes"]').first()).toHaveAttribute("data-label", "備註");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await context.close();
});
