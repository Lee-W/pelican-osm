const { test, expect } = require("@playwright/test");

test("nested sections collapse and expand together, with subtotals beside titles", async ({ page }) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/groups.html");
  const root = page.locator(".osm-section-groups");
  const headers = root.locator(".osm-group-header");
  const rows = root.locator(".osm-place-row:visible");
  await expect(headers).toHaveCount(9);
  await expect(rows).toHaveCount(6);
  await expect(root.getByRole("button", { name: "全部展開", exact: true })).toBeDisabled();
  await root.getByRole("button", { name: "全部收合", exact: true }).click();
  await expect(rows).toHaveCount(0);
  await expect(root.locator(".osm-group-header:visible")).toHaveCount(2);
  await expect(root.locator(".osm-list-no-results")).toBeHidden();
  await expect(root.getByRole("button", { name: "全部收合", exact: true })).toBeDisabled();
  await root.getByRole("button", { name: "全部展開", exact: true }).click();
  await expect(rows).toHaveCount(6);
  const header = headers.first();
  const title = await header.locator(".osm-group-header-title").boundingBox();
  const count = await header.locator(".osm-group-count").boundingBox();
  expect(Math.abs(count.y - title.y)).toBeLessThan(10);
  await expect(header.locator(".osm-group-count")).toHaveText("5 個地點");
  await header.focus();
  await page.keyboard.press("Space");
  await expect(header).toHaveAttribute("aria-expanded", "false");
  await expect(rows).toHaveCount(1);
  await page.keyboard.press("Enter");
  await expect(rows).toHaveCount(6);
  expect(errors).toEqual([]);
});

test("search finds hoisted country, city and theater names after collapse", async ({ page }) => {
  await page.goto("/groups.html");
  const root = page.locator(".osm-section-groups");
  const search = root.getByRole("searchbox");
  await root.getByRole("button", { name: "全部收合", exact: true }).click();
  await search.fill("臺北");
  await expect(root.locator(".osm-place-row:visible")).toHaveCount(3);
  await expect(root.locator(".osm-place-list-count")).toHaveText("3 個地點");
  await expect(root.locator(".osm-group-header:visible")).toHaveCount(4);
  await expect(root.locator(".osm-group-header--depth-0 .osm-group-count").first()).toHaveText("3 個地點");
  await search.fill("信義影城");
  await expect(root.locator(".osm-place-row:visible")).toHaveCount(2);
  await search.fill("日本");
  await expect(root.locator(".osm-place-row:visible")).toHaveCount(1);
  await root.getByRole("button", { name: "清除篩選", exact: true }).click();
  await expect(root.locator(".osm-place-row:visible")).toHaveCount(6);
});

test("deep links expand ancestors; sorting remains inside each theater", async ({ page }) => {
  await page.goto("/groups.html");
  const root = page.locator(".osm-section-groups");
  await root.getByRole("button", { name: "全部收合", exact: true }).click();
  const target = root.locator('.osm-group-header--depth-2').first();
  const anchor = await target.getAttribute("id");
  await page.evaluate((anchor) => { location.hash = anchor; }, anchor);
  await expect(target).toBeVisible();
  await expect(root.locator(".osm-place-row:visible")).toHaveCount(2);
  await root.getByRole("button", { name: "全部展開", exact: true }).click();
  const originalHeaders = await root.locator(".osm-group-header-title").allTextContents();
  await root.getByRole("button", { name: "影廳", exact: false }).first().click();
  await expect(root.locator(".osm-place-row [data-field=hall]")).toHaveText(
    ["1 廳", "2 廳", "7 廳", "3 廳", "5 廳", "6 廳"], { useInnerText: true },
  );
  expect(await root.locator(".osm-group-header-title").allTextContents()).toEqual(originalHeaders);
});

test("map links in headings do not collapse their group", async ({ page }) => {
  await page.goto("/groups.html");
  const heading = page.locator(".osm-group-header--depth-2").first();
  const link = heading.getByRole("link", { name: "OpenStreetMap" });
  // Replace the navigation destination locally to isolate the group interaction.
  await link.evaluate((link) => { link.href = "#map-link-test"; link.removeAttribute("target"); });
  await link.click();
  await expect(heading).toHaveAttribute("aria-expanded", "true");
  await expect(page.locator(".osm-place-row:visible")).toHaveCount(6);
  await link.focus();
  await page.keyboard.press("Enter");
  await expect(heading).toHaveAttribute("aria-expanded", "true");
});

for (const width of [390, 1200]) {
  test(`section hierarchy and palette stay readable at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/groups.html");
    const paddings = await page.locator(".osm-group-header td").evaluateAll((cells) =>
      cells.slice(0, 3).map((cell) => parseFloat(getComputedStyle(cell).paddingInlineStart)),
    );
    expect(paddings[0]).toBeLessThan(paddings[1]);
    expect(paddings[1]).toBeLessThan(paddings[2]);
    const title = await page.locator('.osm-group-header--depth-2 .osm-group-header-title').first().boundingBox();
    const rowTextStart = await page.locator('.osm-place-row td').first().evaluate((cell) =>
      cell.getBoundingClientRect().x + parseFloat(getComputedStyle(cell).paddingInlineStart),
    );
    expect(Math.abs(rowTextStart - title.x)).toBeLessThan(2);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    for (const theme of ["light", "dark"]) {
      await page.evaluate((theme) => document.documentElement.className = `theme-${theme}`, theme);
      const palette = await page.locator('.osm-group-header td').evaluateAll((cells) => {
        const context = document.createElement('canvas').getContext('2d');
        function color(css) {
          context.fillStyle = css;
          context.fillRect(0, 0, 1, 1);
          return [...context.getImageData(0, 0, 1, 1).data].slice(0, 3);
        }
        return cells.slice(0, 3).map((cell) => {
          const style = getComputedStyle(cell);
          const stops = [...style.backgroundImage.matchAll(/(?:rgba?|color)\([^)]+\)/g)];
          return { foreground: color(style.color), background: color(stops.at(-1)[0]) };
        });
      });
      function luminance(rgb) {
        return rgb.map((v) => v / 255).map((v) => v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4)
          .reduce((sum, v, i) => sum + v * [0.2126, 0.7152, 0.0722][i], 0);
      }
      function contrast(a, b) {
        const values = [luminance(a), luminance(b)].sort((a, b) => b - a);
        return (values[0] + 0.05) / (values[1] + 0.05);
      }
      palette.forEach(({ foreground, background }) => expect(contrast(foreground, background)).toBeGreaterThanOrEqual(4.5));
      expect(contrast(palette[0].background, palette[1].background)).toBeGreaterThan(3);
      expect(contrast(palette[1].background, palette[2].background)).toBeGreaterThan(1.15);
      await page.screenshot({ path: testInfo.outputPath(`${theme}.png`), fullPage: true });
    }
  });
}
