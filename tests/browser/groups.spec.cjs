const { test, expect } = require("@playwright/test");

// Generic hierarchy, controls and palette tests live in pelican-tabular.
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
