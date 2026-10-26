"""Browser QA for newly imported organizer-only Public cases."""

import json
from pathlib import Path

from playwright.sync_api import sync_playwright


BASE = 'http://127.0.0.1:8502'
OUTPUT = Path('/opt/aiops-competition-visualization/reports/import-20261016')
CASES = {'SF-002': '84,982', 'SF-003': '41,160', 'SF-010': '41,581'}


def select_case(page, case_id):
    page.get_by_role('combobox', name='Case').click()
    page.get_by_role('option', name=case_id + ' · ready').click()
    page.get_by_text(CASES[case_id]).first.wait_for(timeout=30000)


def tab(page, name):
    page.get_by_role('tab', name=name, exact=True).click()
    page.wait_for_timeout(900)


def main():
    if OUTPUT.exists():
        raise RuntimeError('Browser QA output already exists')
    OUTPUT.mkdir(parents=True, mode=0o700)
    results = {}
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
        page = browser.new_page(viewport={'width': 1512, 'height': 960},
                                device_scale_factor=1)
        page.on('pageerror', lambda error: errors.append(str(error)))
        for case_id in CASES:
            page.goto(BASE, wait_until='domcontentloaded', timeout=30000)
            page.get_by_role('combobox', name='Case').wait_for(timeout=45000)
            select_case(page, case_id)
            page.screenshot(path=str(OUTPUT / f'{case_id}-overview.png'))
            tab(page, 'Metrics')
            page.get_by_text('Available metric families').first.wait_for(timeout=30000)
            page.screenshot(path=str(OUTPUT / f'{case_id}-metrics.png'))
            tab(page, 'Logs')
            page.get_by_text('Logs per minute').first.wait_for(timeout=30000)
            page.screenshot(path=str(OUTPUT / f'{case_id}-logs.png'))
            tab(page, 'Traces')
            page.get_by_text('real spans').first.wait_for(timeout=30000)
            page.screenshot(path=str(OUTPUT / f'{case_id}-waterfall.png'))
            tab(page, 'Topology')
            page.get_by_text('Public placement snapshot').first.wait_for(timeout=30000)
            page.screenshot(path=str(OUTPUT / f'{case_id}-topology.png'))
            results[case_id] = {'status': 'PASS', 'screenshots': [
                f'{case_id}-{name}.png' for name in
                ('overview', 'metrics', 'logs', 'waterfall', 'topology')]}
        browser.close()
    result = {'status': 'BROWSER_IMPORTED_CASES_PASS' if not errors else 'BROWSER_IMPORTED_CASES_FAIL',
              'cases': results, 'page_errors': errors,
              'server': BASE, 'team_api_opened': False}
    (OUTPUT / 'browser-qa-results.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'status': result['status'], 'case_ids': list(results),
                      'screenshots': sum(len(row['screenshots']) for row in results.values()),
                      'page_errors': errors}))
    if errors:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
