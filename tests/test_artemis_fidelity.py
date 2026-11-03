"""Interaction regressions for the reference reconstruction, with local test data."""
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright
from sabra_api.main import app


@pytest.fixture()
def fidelity_page(prepared, monkeypatch):
    monkeypatch.setattr('sabra_engine.context.get_settings', lambda: prepared.ctx.settings)
    client = TestClient(app)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 390, 'height': 844})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))

        def serve(route):
            request = route.request
            url = urlsplit(request.url)
            assert url.hostname == 'artemis.test'
            response = client.request(request.method, url.path, content=request.post_data,
                                      headers={'content-type': 'application/json'} if request.post_data else {})
            route.fulfill(status=response.status_code, body=response.content,
                          content_type=response.headers.get('content-type', 'application/octet-stream'))

        page.route('**/*', serve)
        yield page, prepared
        assert not errors
        browser.close()


def test_search_filters_and_saved_opportunities_follow_candidate_across_routes(fidelity_page):
    page, prepared = fidelity_page
    page.goto('http://artemis.test/opportunities', wait_until='networkidle')
    query = page.get_by_role('searchbox', name='Search opportunities')
    query.fill('no matching company')
    assert page.get_by_text('No jobs match those filters.').is_visible()
    query.fill('revance')
    assert page.locator('.ranked-opportunity').count() == 1
    page.get_by_role('button', name='High confidence', exact=True).click()
    assert page.locator('.ranked-opportunity').count() == 0  # unassessed is not high confidence
    page.get_by_role('button', name='All', exact=True).click()
    page.get_by_role('button', name='More filters').click()
    page.get_by_role('spinbutton', name='Minimum salary').fill('999999')
    page.get_by_role('button', name='Filter jobs', exact=True).click()
    assert page.locator('.ranked-opportunity').count() == 0
    page.get_by_role('button', name='Clear', exact=True).click()
    save = page.locator('[data-save]')
    assert save.is_enabled()
    save.click()
    assert save.get_attribute('aria-pressed') == 'true'
    page.reload(wait_until='networkidle')
    assert page.locator('[data-save]').get_attribute('aria-pressed') == 'true'
    page.goto('http://artemis.test/', wait_until='networkidle')
    assert page.locator('[data-save]').get_attribute('aria-pressed') == 'true'
    page.goto('http://artemis.test/opportunities/' + prepared.opportunity_id, wait_until='networkidle')
    assert page.locator('[data-save]').get_attribute('aria-pressed') == 'true'
    page.locator('[data-save]').click()
    assert page.locator('[data-save]').get_attribute('aria-pressed') == 'false'
    page.evaluate("artemisInitSaves('different-candidate')")
    assert page.locator('[data-save]').get_attribute('aria-pressed') == 'false'
    page.locator('[data-save]').click()
    page.evaluate("artemisInitSaves('third-candidate')")
    assert page.locator('[data-save]').get_attribute('aria-pressed') == 'false'


def test_home_failure_exits_loading_and_keeps_metric_geometry(fidelity_page):
    page, _ = fidelity_page
    page.route('**/api/home', lambda route: route.fulfill(status=503, json={'detail': 'Brief temporarily unavailable.'}))
    page.goto('http://artemis.test/', wait_until='networkidle')
    assert page.locator('#home-summary').inner_text() == 'Brief temporarily unavailable.'
    assert 'Loading' not in page.locator('.home-panels').inner_text()
    assert page.locator('.reference-metric').count() == 4
    assert all(value == '—' for value in page.locator('[data-reference-metric]').all_text_contents())
    assert page.evaluate('document.documentElement.scrollWidth') == 390


def test_saved_storage_unavailable_and_long_titles_do_not_break_mobile(fidelity_page):
    page, _ = fidelity_page
    rows = [{'id': 'long', 'company': 'A Very Long Company Name', 'title': 'Principal Product Strategy and Infrastructure Leadership ' * 4,
             'location': 'Remote', 'status': 'SHORTLISTED', 'rank_position': 1,
             'fit_score': None, 'confidence': None, 'salary_max': None, 'salary_min': None}]
    page.route('**/api/opportunities', lambda route: route.fulfill(json={'opportunities': rows}))
    page.goto('http://artemis.test/opportunities', wait_until='networkidle')
    assert page.evaluate('document.documentElement.scrollWidth') == 390
    assert page.locator('[data-save]').is_visible()
    page.get_by_role('button', name='Remote', exact=True).click()
    assert page.locator('.ranked-opportunity').count() == 1
    page.get_by_role('button', name='More filters').click()
    page.get_by_role('spinbutton', name='Minimum salary').fill('1')
    page.get_by_role('button', name='Filter jobs').click()
    assert page.locator('.ranked-opportunity').count() == 0  # unknown pay is not a salary match
    page.get_by_role('button', name='Clear', exact=True).click()
    page.evaluate("() => { Storage.prototype.setItem=()=>{throw new Error('storage blocked')} }")
    page.locator('[data-save]').click()
    assert page.locator('[data-save]').get_attribute('aria-pressed') == 'false'
    assert 'unavailable' in page.locator('[data-save]').get_attribute('aria-label')


def test_keyboard_menu_focus_and_reduced_motion(fidelity_page):
    page, _ = fidelity_page
    page.emulate_media(reduced_motion='reduce')
    page.goto('http://artemis.test/', wait_until='networkidle')
    page.keyboard.press('Tab')
    assert page.locator('.skip-link').evaluate('el=>el===document.activeElement')
    page.keyboard.press('Enter')
    assert page.locator('main').evaluate('el=>el===document.activeElement')
    page.locator('.navtoggle summary').focus()
    page.keyboard.press('Enter')
    assert page.locator('.mobilemenu').is_visible()
    assert page.locator('html').evaluate('el=>getComputedStyle(el).scrollBehavior') == 'auto'


def test_remote_chip_uses_recorded_work_arrangement(fidelity_page):
    page, _ = fidelity_page
    rows = [
        {'id': 'remote-city', 'company': 'Remote Team', 'title': 'Engineer', 'location': 'London', 'remote_status': 'REMOTE'},
        {'id': 'onsite-text', 'company': 'Onsite Team', 'title': 'Engineer', 'location': 'Remote Office', 'remote_status': 'ONSITE'},
        {'id': 'unknown-city', 'company': 'Unknown Team', 'title': 'Engineer', 'location': 'London', 'remote_status': 'UNKNOWN'},
    ]
    page.route('**/api/opportunities', lambda route: route.fulfill(json={'opportunities': rows}))
    page.goto('http://artemis.test/opportunities', wait_until='networkidle')
    assert page.locator('.ranked-opportunity').count() == 3
    page.get_by_role('button', name='Remote', exact=True).click()
    assert page.locator('.ranked-opportunity').count() == 1
    assert page.locator('button[data-id="remote-city"]').is_visible()


def test_opportunity_api_preserves_recorded_work_arrangement(prepared, monkeypatch):
    monkeypatch.setattr('sabra_engine.context.get_settings', lambda: prepared.ctx.settings)
    rows = TestClient(app).get('/api/opportunities').json()['opportunities']
    assert len(rows) == 1
    assert rows[0]['remote_status'] == 'ONSITE'


@pytest.mark.parametrize('width', [1280, 1440, 1920])
def test_desktop_uses_readable_scale_and_one_aligned_application_canvas(fidelity_page, width):
    page, _ = fidelity_page
    page.set_viewport_size({'width': width, 'height': 1080})
    page.goto('http://artemis.test/', wait_until='networkidle')
    geometry = page.evaluate('''() => {
      const box=s=>document.querySelector(s).getBoundingClientRect();
      const size=s=>parseFloat(getComputedStyle(document.querySelector(s)).fontSize);
      return {hero:box('.home-hero').toJSON(),panels:box('.home-panels').toJSON(),
        header:box('.topnav').toJSON(),nav:size('nav.primary a'),label:size('.reference-metric-label'),
        title:size('.brief-item-title'),headline:size('.home-hero h1')};
    }''')
    assert geometry['hero']['width'] >= width*.9
    assert abs(geometry['hero']['x']-geometry['panels']['x']) <= 1
    assert abs(geometry['hero']['width']-geometry['panels']['width']) <= 1
    assert geometry['header']['height'] >= 48
    assert geometry['hero']['height'] >= 300
    assert geometry['nav'] >= 16 and geometry['label'] >= 10
    assert geometry['title'] >= 11 and geometry['headline'] >= 50
    assert page.evaluate('document.documentElement.scrollWidth') == width


def test_desktop_scale_does_not_jump_at_1500_pixels(fidelity_page):
    page, _ = fidelity_page
    sizes=[]
    for width in (1499, 1500):
        page.set_viewport_size({'width':width,'height':1000})
        page.goto('http://artemis.test/',wait_until='networkidle')
        sizes.append(page.locator('.home-hero').evaluate("el=>({height:el.getBoundingClientRect().height,font:parseFloat(getComputedStyle(el.querySelector('h1')).fontSize)})"))
    assert abs(sizes[0]['height']-sizes[1]['height']) < 2
    assert abs(sizes[0]['font']-sizes[1]['font']) < 1


def test_latest_home_reference_landmarks_and_destination_links(fidelity_page):
    page, _ = fidelity_page
    page.set_viewport_size({'width':1280,'height':853})
    page.goto('http://artemis.test/',wait_until='networkidle')
    expected={'.topnav':(0,50),'.home-hero':(50,305),'.reference-metrics':(355,71),'.home-panels':(446,262),'.home-destinations':(725,120)}
    for selector,(top,height) in expected.items():
        box=page.locator(selector).bounding_box()
        assert abs(box['y']-top)<=4,(selector,box)
        assert abs(box['height']-height)<=4,(selector,box)
    destinations=page.get_by_role('navigation',name='Explore Artemis')
    assert destinations.get_by_role('link').count()==4
    for path in ('/opportunities','/research','/career-plan','/applications'):
        page.goto('http://artemis.test/',wait_until='networkidle')
        page.locator('.home-destinations a[href="'+path+'"]').click()
        assert page.url.endswith(path)
        assert page.locator('main').is_visible()


@pytest.mark.parametrize('width,height',[(1280,800),(1440,900),(1920,1080)])
def test_latest_desktop_destination_tiles_fit_standard_viewports(fidelity_page,width,height):
    page,_=fidelity_page
    page.set_viewport_size({'width':width,'height':height})
    page.goto('http://artemis.test/',wait_until='networkidle')
    box=page.locator('.home-destinations').bounding_box()
    assert box['y']+box['height'] <= height,(width,height,box)
    assert page.locator('.home-hero').bounding_box()['width'] >= width*.9
