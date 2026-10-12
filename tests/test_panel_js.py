"""Execute the actual panel functions with a small DOM stub (Node is optional)."""

import json
from pathlib import Path
import re
import shutil
import subprocess
import unittest


NODE = shutil.which("node")
PANEL = Path(__file__).resolve().parents[1] / "templates" / "panel.html"


@unittest.skipUnless(NODE, "Node.js is required for panel JavaScript checks")
class PanelJavaScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        html = PANEL.read_text(encoding="utf-8")
        # Startup requires the full browser and HTTP server. Exercise the real
        # declared functions directly, with API responses provided by each test.
        cls.full_script = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
        cls.script = cls.full_script.split("\n(async () => {", 1)[0]

    def run_js(self, assertions):
        harness = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, {
    innerHTML: '', textContent: '', value: '', children: [], buttons: [],
    appendChild(child) { this.children.push(child); },
    querySelectorAll(selector) {
      this.buttons = Array.from(this.innerHTML.matchAll(/data-index="(\d+)"/g), match => ({
        dataset: {index: match[1]}, handlers: {},
        addEventListener(event, handler) { this.handlers[event] = handler; },
      }));
      return this.buttons;
    },
    remove() {},
  });
  return elements.get(id);
}
const context = vm.createContext({
  URL, console, setTimeout() {}, setInterval() {},
  document: {getElementById: element, createElement: () => element('created')},
  localStorage: {getItem: () => ''},
});
"""
        code = harness + "\nnew vm.Script(" + json.dumps(self.full_script) + ");\n"
        code += "vm.runInContext(" + json.dumps(self.script) + ", context);\n"
        code += "(async () => {\n" + assertions + "\n})().catch(error => {console.error(error); process.exit(1);});"
        completed = subprocess.run([NODE, "-"], input=code, text=True, encoding="utf-8", capture_output=True, timeout=15)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_logs_and_toasts_render_malicious_input_as_text(self):
        self.run_js(r"""
const payload = '<img src=x onerror="alert(1)">';
context.api = async () => ({ok: true, logs: [{time: payload, level: payload, msg: payload}]});
await context.loadLogs();
assert.ok(!element('log-panel').innerHTML.includes('<img'));
assert.ok(element('log-panel').innerHTML.includes('&lt;img'));
context.toast(payload);
assert.ok(!element('created').innerHTML.includes('<img'));
assert.ok(element('created').innerHTML.includes('&quot;alert(1)&quot;'));
""")

    def test_tag_removal_preserves_original_value_without_inline_javascript(self):
        self.run_js(r"""
const payload = "');alert(1);//<img src=x>";
let removed;
context.renderTags('tags', [payload], 'tag-red', value => { removed = value; });
assert.ok(!element('tags').innerHTML.includes('onclick='));
assert.ok(!element('tags').innerHTML.includes('<img'));
element('tags').buttons[0].handlers.click();
assert.equal(removed, payload);
""")

    def test_team_search_escapes_names_attributes_and_rejects_script_images(self):
        self.run_js(r"""
const payload = '" onclick="alert(1)"><script>alert(1)</script>';
element('team-search-input').value = 'team';
context.api = async () => ({ok: true, teams: [{id: 5, name: payload, slug: payload, image_url: 'javascript:alert(1)', followed: false}]});
await context.searchTeams();
const html = element('team-search-results').innerHTML;
assert.ok(!html.includes('<script>'));
assert.ok(!html.includes('javascript:'));
assert.ok(!html.includes(' onclick="alert(1)"'));
assert.ok(html.includes('&lt;script&gt;'));
context.renderFollowedTeams([{id: 5, name: payload}]);
assert.ok(!element('teams-list').innerHTML.includes('<script>'));
""")

    def test_match_and_stream_fields_cannot_create_html_or_event_attributes(self):
        self.run_js(r"""
const payload = '"><img src=x onerror="alert(1)">';
vm.runInContext('STATE.config = {followed_teams: []}', context);
const card = context.renderMatchCard({id: 5, _t1: payload, _t2: 'Team', league: {name: payload}, tournament: {tier: payload}});
assert.ok(!card.includes('<img'));
assert.ok(card.includes('&lt;img'));
context.streamData = [{name: payload, url: payload}];
vm.runInContext('STATE.streams = streamData', context);
context.renderStreams();
assert.ok(!element('streams-list').innerHTML.includes('<img'));
assert.ok(!element('streams-list').innerHTML.includes(' onerror="alert(1)"'));
assert.equal(context.safeImageUrl('data:text/html,<script>alert(1)</script>'), '');
assert.equal(context.safeImageUrl('https://example.com/image.png'), 'https://example.com/image.png');
""")

    def test_platform_id_is_loaded_and_saved_in_advanced_settings(self):
        self.run_js(r"""
context.applyAdvancedConfig({push_platform_id: 'qq-main'});
assert.equal(element('adv-push-platform').value, 'qq-main');
element('adv-push-platform').value = ' qq-secondary ';
const body = context.getAdvancedConfigBodySafe();
assert.equal(body.push_platform_id, 'qq-secondary');
""")

    def test_save_all_includes_advanced_edits_and_preserves_blank_token(self):
        self.run_js(r"""
let saved;
context.api = async (path, method, body) => { saved = body; return {ok: true}; };
context.loadConfig = async () => {};
element('adv-token').value = ' new-token ';
element('adv-test-mode').checked = true;
element('adv-test-target-type').value = 'group';
element('adv-test-target-id').value = ' 12345 ';
element('adv-push-platform').value = ' qq-main ';
element('adv-notify-all').checked = false;
element('adv-reschedule').checked = false;
element('q-notify-all').checked = true;
element('q-reschedule').checked = true;
vm.runInContext('STATE.config = {}', context);
await context.saveAll();
assert.equal(saved.pandascore_token, 'new-token');
assert.equal(saved.test_mode_enabled, true);
assert.equal(saved.test_target_type, 'group');
assert.equal(saved.test_target_id, '12345');
assert.equal(saved.push_platform_id, 'qq-main');
assert.equal(saved.notify_all_followed, true);
assert.equal(saved.reschedule_notify, true);
assert.equal(element('adv-token').value, '');
element('adv-token').value = '   ';
await context.saveAll();
assert.ok(!Object.hasOwn(saved, 'pandascore_token'));
element('adv-token').value = 'keep-on-failure';
context.api = async () => ({ok: false});
await context.saveAll();
assert.equal(element('adv-token').value, 'keep-on-failure');
""")

    def test_reminder_controls_share_value_and_quick_save_reloads_only_on_success(self):
        self.run_js(r"""
element('q-remind').value = '25';
context.syncRemindInputs('25', 'q-remind');
assert.equal(element('remind-range').value, '25');
assert.equal(element('q-remind-val').textContent, '25min');
element('remind-range').value = '35';
context.syncRemindInputs('35', 'remind-range');
assert.equal(element('q-remind').value, '35');
let reloads = 0;
context.loadConfig = async () => { reloads++; };
context.api = async () => ({ok: true});
assert.equal(await context.quickSave(), true);
assert.equal(reloads, 1);
context.api = async () => ({ok: false});
assert.equal(await context.quickSave(), false);
assert.equal(reloads, 1);
""")

    def test_team_names_use_snapshot_or_real_api_name_then_tbd(self):
        self.run_js(r"""
assert.equal(context.teamName({_t1: ' Saved ', opponents: [{opponent: {name: 'API'}}]}, 0), 'Saved');
assert.equal(context.teamName({_t1: 'TBD', opponents: [{opponent: {name: ' API '}}]}, 0), 'API');
assert.equal(context.teamName({opponents: [{opponent: {name: null}}]}, 0), 'TBD');
assert.equal(context.teamName({}, 1), 'TBD');
vm.runInContext('STATE.config = {followed_teams: []}', context);
const card = context.renderMatchCard({id: 5, _t1: 'TBD', opponents: [{opponent: {name: 'Actual Team'}}]});
assert.ok(card.includes('Actual Team'));
""")


if __name__ == "__main__":
    unittest.main()
