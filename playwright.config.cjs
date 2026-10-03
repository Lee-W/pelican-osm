const { defineConfig } = require("@playwright/test");
const port = process.env.OSM_TEST_PORT || "8766";
const baseURL = `http://127.0.0.1:${port}`;

module.exports = defineConfig({
  testDir: "tests/browser",
  fullyParallel: true,
  workers: 2,
  use: { baseURL, browserName: "chromium" },
  webServer: {
    command: `uv run --no-sync python scripts/serve_browser_fixtures.py --port ${port}`,
    url: `${baseURL}/explorer.html`,
    reuseExistingServer: !process.env.CI,
  },
});
