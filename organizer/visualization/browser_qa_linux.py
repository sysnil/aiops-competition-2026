"""Real browser checks and SF-001 screenshots on the 51 host's loopback port."""

import json
from pathlib import Path

from playwright.sync_api import sync_playwright
from PIL import Image


BASE = 'http://127.0.0.1:8502'
SHOTS = Path('/opt/aiops-competition-visualization/reports/screenshots')


def choose(page, tab):
    page.get_by_role('tab', name=tab, exact=True).click()
    page.wait_for_timeout(800)


def main():
    SHOTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True,
                                              args=['--no-sandbox', '--disable-dev-shm-usage'])
        page = browser.new_page(viewport={'width': 1512, 'height': 960},
                                device_scale_factor=1)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(BASE, wait_until='domcontentloaded', timeout=30000)
        page.get_by_text('80,358').first.wait_for(timeout=45000)
        page.screenshot(path=str(SHOTS / 'SF-001-overview.png'))

        choose(page, 'Metrics')
        page.get_by_text('container_cpu_usage_seconds_total').first.wait_for(timeout=30000)
        page.screenshot(path=str(SHOTS / 'SF-001-metrics.png'))

        choose(page, 'Logs')
        page.get_by_text('Logs per minute').first.wait_for(timeout=30000)
        page.screenshot(path=str(SHOTS / 'SF-001-logs.png'))

        choose(page, 'Traces')
        page.get_by_text('real spans').first.wait_for(timeout=30000)
        row_boxes = page.get_by_role('checkbox')
        row_box_count = row_boxes.count()
        selected_from_table = False
        bounds = None
        canvas_bounds = [canvas.bounding_box() for canvas in page.locator('canvas').all()]
        if row_box_count > 1:
            row_boxes.nth(1).click()
            page.get_by_text('Selected from Trace list:').wait_for(timeout=30000)
            selected_from_table = True
        else:
            bounds = next((box for box in canvas_bounds if box and box['width'] > 700
                           and 300 < box['y'] < 800), None)
            if bounds:
                page.mouse.click(bounds['x'] + 25, bounds['y'] + 54)
                try:
                    page.get_by_text('Selected from Trace list:').wait_for(timeout=5000)
                    selected_from_table = True
                except Exception:
                    pass
        page.screenshot(path=str(SHOTS / 'SF-001-waterfall.png'))

        choose(page, 'Topology')
        page.get_by_text('Public placement snapshot').first.wait_for(timeout=30000)
        page.screenshot(path=str(SHOTS / 'SF-001-topology.png'))

        page.get_by_text('Review candidate · session only').click()
        page.get_by_label('Candidate type').click()
        page.get_by_role('option', name='REQUEST_LATENCY_INCREASE · 请求延迟升高').click()
        page.get_by_label('Candidate entity').fill('checkout')
        choose(page, 'Review')
        page.get_by_text('Candidate-window call edges').first.wait_for(timeout=30000)
        page.screenshot(path=str(SHOTS / 'SF-001-review.png'))

        page.get_by_role('combobox', name='Case').click()
        page.get_by_role('option', name='SF-002 · 数据未就绪').click()
        page.get_by_text('SF-002：数据未就绪').first.wait_for(timeout=30000)

        image = Image.open(SHOTS / 'SF-001-metrics.png').convert('RGB')
        chart = image.crop((400, 470, 1200, 780))
        distinct_chart_colors = len(chart.getcolors(chart.width * chart.height) or [])
        sf002_not_ready = page.get_by_text('SF-002：数据未就绪').count() > 0
        passed = not errors and selected_from_table and sf002_not_ready and \
            distinct_chart_colors > 100
        result = {'status': 'BROWSER_QA_PASS' if passed else 'BROWSER_QA_FAIL',
                  'page_errors': errors,
                  'sf002_not_ready_visible': sf002_not_ready,
                  'waterfall_row_checkbox_count': row_box_count,
                  'waterfall_grid_bounds': bounds,
                  'waterfall_selected_from_table': selected_from_table,
                  'metrics_chart_distinct_colors': distinct_chart_colors,
                  'screenshots': sorted(path.name for path in SHOTS.glob('SF-001-*.png'))}
        (SHOTS.parent / 'browser-qa-results.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result))
        browser.close()
        if not passed:
            raise SystemExit(1)


if __name__ == '__main__':
    main()
