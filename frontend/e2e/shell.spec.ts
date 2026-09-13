import { expect, test } from '@playwright/test';

// Phase 1 smoke E2E — boots the Vite dev server and asserts the
// design tokens, fonts, and procedural Heraldry all render. Catches
// the regression where a missing CSS / JS module breaks the whole
// shell with a blank page.

test.describe('Phase 1 React shell', () => {
  test('renders the appbar brand + ribbon heading', async ({ page }) => {
    await page.goto('/');

    await expect(page).toHaveTitle('The Chronicler');
    await expect(page.getByText('The Chronicler', { exact: true })).toBeVisible();
    await expect(page.getByText('Volumes upon the Shelf')).toBeVisible();
  });

  test('renders procedural shields for sample seeds', async ({ page }) => {
    await page.goto('/');

    // Each Heraldry component renders an SVG with an aria-label
    // ("shield-12267", "shield-Toirrdelbach", etc.). Visibility check
    // on the SVG itself, not the inner <path> (which has 0 bounding
    // box per Playwright's heuristic).
    const shields = page.locator('svg[aria-label^="shield-"]');
    await expect(shields).toHaveCount(6);
    await expect(shields.first()).toBeVisible();

    // ring=true → gilded ring path in addition to the outer rim. So
    // each shield's SVG has at least 2 top-level <path> children.
    const pathCount = await shields.first().evaluate((svg) => {
      return svg.querySelectorAll(':scope > path').length;
    });
    expect(pathCount).toBeGreaterThanOrEqual(2);
  });

  test('applies the parchment theme by default', async ({ page }) => {
    await page.goto('/');
    const html = page.locator('html');
    await expect(html).toHaveAttribute('data-theme', 'light');
    // The body background should be the deep-paper token. We don't
    // assert the exact RGB (it's a custom property) — just that the
    // page paints something non-default.
    const bgColor = await page.locator('body').evaluate((el) =>
      window.getComputedStyle(el).backgroundColor,
    );
    expect(bgColor).not.toBe('rgba(0, 0, 0, 0)');
    expect(bgColor).not.toBe('rgb(255, 255, 255)');
  });
});
