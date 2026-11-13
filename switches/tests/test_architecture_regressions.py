"""Offline architecture regressions; no bot startup, credentials or real messages."""

import ast
import asyncio
import importlib.util
import json
import os
import sys
import types
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

sys.dont_write_bytecode = True
SOURCE = Path(__file__).resolve().parents[1]
ROOT = Path(os.environ.get("WHALE_TEST_CODE_ROOT", SOURCE))
PLUGINS = Path(
    os.environ.get("WHALE_TEST_PLUGIN_ROOT", SOURCE / "deploy/architecture_candidates")
)
TMP = SOURCE / ".test_tmp" / ("architecture-" + uuid.uuid4().hex)
TMP.mkdir(parents=True)
os.environ["ASTRBOT_ROOT"] = str(TMP)
os.environ.pop("VOICE_DATA_DIR", None)
sys.path.insert(0, str(ROOT))
from whale_core import injectwatch, outbound, voicegate  # noqa: E402


def deny_network(event, args):
    """Block network use except asyncio's internal Windows socketpair.

    Args:
        event: Python audit event.
        args: Audit event arguments.

    Raises:
        RuntimeError: Code attempted a network operation outside the event loop.
    """
    if event not in ("socket.connect", "socket.getaddrinfo"):
        return
    frame = sys._getframe(1)
    while frame is not None:
        if (
            frame.f_code.co_name == "_fallback_socketpair"
            and frame.f_globals.get("__name__") == "socket"
        ):
            return
        frame = frame.f_back
    raise RuntimeError("Offline architecture tests forbid network access")


sys.addaudithook(deny_network)


def load_functions(path, names, namespace=None, class_name=None):
    """Compile actual entry-point bodies without importing or starting AstrBot.

    Args:
        path: Source file to inspect.
        names: Functions or methods to extract unchanged.
        namespace: Controlled dependency globals.
        class_name: Optional containing class.

    Returns:
        Namespace containing the compiled original functions.
    """
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    bodies = tree.body
    if class_name:
        bodies = next(
            n.body
            for n in bodies
            if isinstance(n, ast.ClassDef) and n.name == class_name
        )
    selected = []
    for node in bodies:
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in names
        ):
            node.decorator_list = []
            selected.append(node)
    assert {n.name for n in selected} == set(names)
    module = ast.Module(body=selected, type_ignores=[])
    ns = {"Any": object, "AstrMessageEvent": object, "Path": Path}
    ns.update(namespace or {})
    exec(compile(module, str(path), "exec"), ns)
    return ns


class BudgetTests(unittest.TestCase):
    def part(self, tag="dynamic_context", body="optional", temporary=True, kind="text"):
        return types.SimpleNamespace(
            type=kind, text=f"<{tag}>{body}</{tag}>", _no_save=temporary
        )

    def test_under_cap_preserves_original_objects_and_order(self):
        parts = [self.part(), self.part("memory_index")]
        kept, info = injectwatch.apply_budget(parts, 500)
        self.assertEqual(kept, parts)
        self.assertTrue(all(a is b for a, b in zip(kept, parts)))
        self.assertEqual(info["dropped_parts"], 0)

    def test_priority_selection_does_not_reorder(self):
        parts = [self.part(), self.part("memory_index"), self.part("emotion_state")]
        cap = len(parts[1].text) + len(parts[2].text)
        kept, info = injectwatch.apply_budget(parts, cap)
        self.assertEqual(kept, parts[1:])
        self.assertEqual(info["after_chars"], cap)
        self.assertEqual(info["dropped_tags"], {"dynamic_context": 1})

    def test_zero_and_negative_budget_drop_only_managed_parts(self):
        known, user = self.part(), self.part(temporary=False)
        for cap in (0, -2):
            with self.subTest(cap=cap):
                kept, info = injectwatch.apply_budget([known, user], cap)
                self.assertEqual(kept, [user])
                self.assertEqual(info["after_chars"], 0)
                self.assertEqual(
                    known.text, "<dynamic_context>optional</dynamic_context>"
                )

    def test_unknown_permanent_multimodal_and_plain_content_survive(self):
        parts = [
            self.part("unknown"),
            self.part(temporary=False),
            self.part(kind="image_url"),
            types.SimpleNamespace(text="plain user text", type="text", _no_save=True),
        ]
        self.assertEqual(injectwatch.apply_budget(parts, 0)[0], parts)

    def test_no_save_must_be_exactly_true(self):
        part = self.part()
        part._no_save = 1
        self.assertEqual(injectwatch.apply_budget([part], 0)[0], [part])

    def test_mixed_and_repeated_wrappers_are_not_removed(self):
        for text in (
            "prefix<dynamic_context>x</dynamic_context>",
            "<dynamic_context>x</dynamic_context>suffix",
            "<dynamic_context>a</dynamic_context><dynamic_context>b</dynamic_context>",
            "<dynamic_context><dynamic_context>x</dynamic_context></dynamic_context>",
            "<dynamic_context>x</wrong>",
        ):
            with self.subTest(text=text):
                part = types.SimpleNamespace(text=text, type="text", _no_save=True)
                self.assertEqual(injectwatch.apply_budget([part], 0)[0], [part])

    def test_non_string_payload_does_not_crash_diagnostics(self):
        parts = [types.SimpleNamespace(text={"opaque": 1}, type="image_url")]
        self.assertEqual(
            injectwatch.apply_budget(parts, 0)[1]["unmanaged_text_chars"], 0
        )

    def test_summary_separates_managed_budget_from_user_prompt(self):
        _, budget = injectwatch.apply_budget([self.part()], 100)
        event = injectwatch.measure(["user"], prompt="x" * 5000)
        event["budget"] = budget
        summary = injectwatch.summarize([event])
        self.assertEqual(summary["managed_budget"]["over_cap_turns"], 0)
        self.assertGreater(summary["max_chars"], 5000)

    def test_real_astrbot_textpart_private_attribute(self):
        path = Path(r"%ASTRBOT_HOME%\astrbot\core\agent\message.py")
        spec = importlib.util.spec_from_file_location("offline_astrbot_parts", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        permanent = module.TextPart(text="<dynamic_context>user</dynamic_context>")
        temporary = module.TextPart(
            text="<dynamic_context>optional</dynamic_context>"
        ).mark_as_temp()
        kept, _ = injectwatch.apply_budget([permanent, temporary], 0)
        self.assertEqual(kept, [permanent])


class GateTests(unittest.TestCase):
    def setUp(self):
        self.dir = (
            TMP / uuid.uuid4().hex / "data/plugin_data/astrbot_plugin_whale_voice"
        )
        self.dir.mkdir(parents=True)
        self.gate = {
            "decision": "allow",
            "expires": 200,
            "enforce": True,
            "token": "fixture-token",
        }
        self.write()

    def write(self):
        (self.dir / "voice_gate.json").write_text(
            json.dumps(self.gate), encoding="utf-8"
        )

    def evaluate(self, **kwargs):
        return voicegate.evaluate(
            data_dir=self.dir, now=100, fail_closed=True, **kwargs
        )

    def test_fresh_allow(self):
        self.assertTrue(self.evaluate()["allow"])

    def test_missing_and_corrupt_gate_fail_closed(self):
        path = self.dir / "voice_gate.json"
        path.unlink()
        self.assertFalse(self.evaluate()["allow"])
        for raw in ("{broken", "[]", "null"):
            path.write_text(raw, encoding="utf-8")
            self.assertFalse(self.evaluate()["allow"])

    def test_legacy_observer_can_still_fail_open(self):
        (self.dir / "voice_gate.json").unlink()
        self.assertTrue(voicegate.evaluate(data_dir=self.dir)["allow"])

    def test_expiry_boundary_and_stale_fail_closed(self):
        for expires in (99, 100):
            self.gate["expires"] = expires
            self.write()
            self.assertEqual(self.evaluate()["code"], "gate_stale")
            self.assertFalse(self.evaluate()["allow"])

    def test_nonfinite_and_invalid_clocks_fail_closed(self):
        for expires in (None, "bad", float("nan"), float("inf"), -float("inf")):
            self.gate["expires"] = expires
            self.write()
            self.assertFalse(self.evaluate()["allow"])
        self.gate["expires"] = 200
        self.write()
        for now in ("bad", float("nan"), float("inf")):
            self.assertFalse(
                voicegate.evaluate(data_dir=self.dir, now=now, fail_closed=True)[
                    "allow"
                ]
            )

    def test_unknown_decision_fails_closed(self):
        self.gate["decision"] = "perhaps"
        self.write()
        self.assertFalse(self.evaluate()["allow"])

    def test_enforced_deny(self):
        self.gate["decision"] = "deny"
        self.write()
        self.assertFalse(self.evaluate()["allow"])

    def test_shadow_is_observation_even_with_closed_outlet(self):
        self.gate.update(decision="deny", enforce=False, outlets={"proactive": False})
        self.write()
        self.assertTrue(self.evaluate()["allow"])

    def test_outlet_deny_overrides_general_allow(self):
        self.gate["outlets"] = {"proactive": False}
        self.write()
        self.assertFalse(self.evaluate()["allow"])

    def test_outlet_allow_preserves_existing_reason_pools(self):
        self.gate.update(
            decision="deny",
            code="low_want",
            outlets={"qzone": True, "proactive": False},
        )
        self.write()
        self.assertTrue(self.evaluate(outlet="qzone")["allow"])
        self.assertFalse(self.evaluate()["allow"])

    def test_outlet_cannot_override_quiet_hours_user_limits_or_unknown_deny(self):
        for code in (
            "disabled",
            "quiet_hours",
            "blocked",
            "daily_cap",
            "cold_penalty",
            "cooldown",
            None,
        ):
            with self.subTest(code=code):
                self.gate.update(
                    decision="deny",
                    code=code,
                    outlets={"qzone": True, "proactive": True},
                )
                self.write()
                self.assertFalse(self.evaluate(outlet="qzone")["allow"])
                self.assertFalse(self.evaluate()["allow"])

    def test_duplicate_token_cannot_be_overridden_by_outlet(self):
        self.gate.update(decision="deny", code="low_want", outlets={"qzone": True})
        self.write()
        (self.dir / "consequence.json").write_text(
            json.dumps({"pending": [{"token": "fixture-token"}]})
        )
        self.assertEqual(self.evaluate(outlet="qzone")["code"], "duplicate_token")

    def test_missing_token_blocks_enforced_allow(self):
        for token in (None, "", "   "):
            self.gate["token"] = token
            self.write()
            self.assertFalse(self.evaluate()["allow"])

    def test_corrupt_existing_consequence_fails_closed(self):
        for raw in ("{", "[]", '{"pending":null}', '{"pending":{}}'):
            (self.dir / "consequence.json").write_text(raw)
            self.assertFalse(self.evaluate()["allow"])

    def test_known_other_session_is_isolated(self):
        cfg = self.dir.parent.parent / "config/astrbot_plugin_whale_voice_config.json"
        cfg.parent.mkdir()
        cfg.write_text(json.dumps({"owner_session": "fixture-owner"}))
        self.gate["decision"] = "deny"
        self.write()
        self.assertTrue(self.evaluate(session_id="fixture-other")["allow"])
        self.assertFalse(self.evaluate(session_id="fixture-owner")["allow"])

    def test_missing_owner_config_is_not_a_permission_bypass(self):
        self.gate["decision"] = "deny"
        self.write()
        self.assertFalse(self.evaluate(session_id="fixture-owner")["allow"])


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.queue = outbound.OutboundQueue(
            TMP / (uuid.uuid4().hex + ".sqlite3"),
            outbound.GatePolicy(
                quiet_hours=(0, 0), min_interval_sec=0, per_source_interval=0
            ),
        )
        self.bus = Mock()
        self.bus_patch = patch.object(outbound, "get_bus", return_value=self.bus)
        self.bus_patch.start()
        self.addCleanup(self.bus_patch.stop)
        self.addCleanup(self.queue.store.close)
        self.sender = AsyncMock(return_value=True)
        self.queue.set_sender(self.sender)

    def run_dispatch(self):
        return asyncio.run(self.queue.dispatch_once())

    def request(self, **kwargs):
        return self.queue.request("synthetic fixture", source="offline-test", **kwargs)

    def test_restart_quarantines_sending_without_replay(self):
        ticket = self.request()
        self.queue.store.execute(
            "UPDATE outbox SET status='sending' WHERE id=?", (ticket,)
        )
        reopened = outbound.OutboundQueue(self.queue.store.path)
        row = reopened.store.query_one(
            "SELECT status,last_error FROM outbox WHERE id=?", (ticket,)
        )
        self.assertEqual(row["status"], "unknown")
        self.assertEqual(row["last_error"], "restart_delivery_unknown")
        self.assertEqual(reopened.due(), [])
        self.sender.assert_not_awaited()

    def test_expired_rows_retired_even_when_next_attempt_is_in_future(self):
        ticket = self.request()
        self.queue.store.execute(
            "UPDATE outbox SET expires_at=1, next_attempt=9999999999 WHERE id=?",
            (ticket,),
        )
        self.assertIsNone(self.run_dispatch())
        self.assertEqual(self.queue.stats()["by_status"], {"expired": 1})
        self.assertEqual(self.queue.stats()["session"]["expired"], 1)
        self.sender.assert_not_awaited()
        self.bus.publish.assert_called_once_with(
            "outbound.expired", {"count": 1}, source="whale_core.outbound"
        )
        self.run_dispatch()
        self.assertEqual(self.queue.stats()["session"]["expired"], 1)

    def test_missing_gate_defers_without_attempt_or_send(self):
        self.request()
        with patch.object(voicegate, "gate_dir", return_value=None):
            result = self.run_dispatch()
        self.assertEqual(result["result"], "deferred")
        self.assertEqual(self.queue.store.scalar("SELECT attempts FROM outbox"), 0)
        self.sender.assert_not_awaited()

    def test_gate_exception_defers(self):
        self.request()
        with patch.object(voicegate, "evaluate", side_effect=RuntimeError("fixture")):
            self.assertEqual(self.run_dispatch()["reason"], "voice_gate:unavailable")
        self.sender.assert_not_awaited()

    def test_gate_uses_fail_closed_argument(self):
        self.request(meta={"umo": "fixture-owner"})
        with patch.object(
            voicegate, "evaluate", return_value={"allow": True}
        ) as evaluate:
            self.assertEqual(self.run_dispatch()["result"], "sent")
        evaluate.assert_called_once_with(
            session_id="fixture-owner", outlet="proactive", fail_closed=True
        )

    def test_unknown_verdict_does_not_allow_sending(self):
        self.request()
        with patch.object(voicegate, "evaluate", return_value={}):
            self.assertEqual(self.run_dispatch()["result"], "deferred")
        self.sender.assert_not_awaited()

    def test_timeout_is_unknown_and_not_retried(self):
        self.request()
        self.sender.side_effect = TimeoutError("synthetic timeout")
        with patch.object(voicegate, "evaluate", return_value={"allow": True}):
            self.assertEqual(self.run_dispatch()["result"], "unknown")
            self.assertIsNone(self.run_dispatch())
        self.sender.assert_awaited_once()

    def test_unknown_delivery_remains_in_dedupe_window(self):
        ticket = self.request(dedupe_key="fixture-key")
        self.queue.store.execute(
            "UPDATE outbox SET status='unknown' WHERE id=?", (ticket,)
        )
        self.assertEqual(self.request(dedupe_key="fixture-key"), "")

    def test_explicit_refusal_retries_with_backoff(self):
        self.request()
        self.sender.side_effect = ConnectionRefusedError("synthetic connection refused")
        with patch.object(voicegate, "evaluate", return_value={"allow": True}):
            result = self.run_dispatch()
        self.assertEqual(result["result"], "retry")
        self.assertEqual(self.queue.store.scalar("SELECT attempts FROM outbox"), 1)
        self.assertGreater(
            self.queue.store.scalar("SELECT next_attempt-created_at FROM outbox"), 14
        )

    def test_quiet_hours_precede_sender(self):
        self.request()
        with patch.object(outbound.GatePolicy, "in_quiet_hours", return_value=True):
            self.assertEqual(self.run_dispatch()["reason"], "quiet_hours")
        self.sender.assert_not_awaited()


class EntryPointTests(unittest.TestCase):
    def setUp(self):
        self.sender_path = PLUGINS / "message_sender.py"
        self.soul_path = PLUGINS / "soul_main.py"
        if not self.sender_path.exists():
            self.sender_path = (
                PLUGINS / "astrbot_plugin_proactive_chat/core/message_sender.py"
            )
            self.soul_path = PLUGINS / "astrbot_plugin_dafeiyu_soul/main.py"
        self.shim = load_functions(
            self.sender_path,
            ["_voice_gate_decision", "_voice_gate_allow"],
            {"_voice_gate_dir": lambda: None, "_voice_outlet": lambda: "proactive"},
        )

    def test_shim_missing_gate_blocks_instead_of_fail_open(self):
        self.assertFalse(self.shim["_voice_gate_allow"](session_id="fixture-owner"))

    def test_shim_delegates_same_contract_and_keeps_snapshot(self):
        snapshot = {"stale": 1}
        gate = {
            "enforce": True,
            "decision": "allow",
            "token": "fixture-token",
            "reason_id": "fixture-reason",
        }
        directory = TMP / "fixture-shim"
        with patch.object(
            voicegate,
            "evaluate",
            return_value={"allow": True, "code": "enforced_allow", "gate": gate},
        ) as evaluate:
            allowed = self.shim["_voice_gate_allow"](
                data_dir=directory,
                now=100,
                session_id="fixture-owner",
                snapshot=snapshot,
            )
        self.assertTrue(allowed)
        self.assertEqual(
            snapshot,
            {
                "token": "fixture-token",
                "reason_id": "fixture-reason",
                "data_dir": str(directory),
            },
        )
        evaluate.assert_called_once_with(
            data_dir=directory,
            now=100,
            session_id="fixture-owner",
            outlet="proactive",
            fail_closed=True,
        )

    def test_shim_deny_never_reserves_or_returns_token(self):
        snapshot = {"stale": 1}
        with patch.object(
            voicegate,
            "evaluate",
            return_value={
                "allow": False,
                "code": "duplicate_token",
                "gate": {"token": "fixture-token"},
            },
        ):
            self.assertFalse(
                self.shim["_voice_gate_allow"](data_dir=TMP, snapshot=snapshot)
            )
        self.assertEqual(snapshot, {})

    def test_shim_core_unavailable_fails_closed(self):
        with patch.object(voicegate, "evaluate", side_effect=RuntimeError("fixture")):
            self.assertFalse(self.shim["_voice_gate_allow"](data_dir=TMP))

    def soul(self):
        tree = ast.parse(self.soul_path.read_text(encoding="utf-8"))
        name = next(
            n.name
            for n in tree.body
            if isinstance(n, ast.ClassDef)
            and any(
                isinstance(f, ast.FunctionDef) and f.name == "_proactive_gate_ok"
                for f in n.body
            )
        )
        ns = load_functions(
            self.soul_path,
            ["_send", "_proactive_gate_ok"],
            {"logger": Mock(), "MessageChain": Mock()},
            class_name=name,
        )
        instance = types.SimpleNamespace(
            _umo="fixture-owner",
            _fallback_umo=lambda: "fixture-owner",
            _clean=lambda text: text,
            _proactive_gate_ok=Mock(return_value=False),
            context=types.SimpleNamespace(send_message=AsyncMock(return_value=False)),
        )
        return ns, instance

    def test_soul_autonomous_send_checks_gate_before_transport(self):
        ns, instance = self.soul()
        self.assertFalse(asyncio.run(ns["_send"](instance, "fixture")))
        instance.context.send_message.assert_not_awaited()

    def test_soul_user_requested_send_is_not_blocked_by_proactive_gate(self):
        ns, instance = self.soul()
        self.assertFalse(
            asyncio.run(ns["_send"](instance, "fixture", user_requested=True))
        )
        instance._proactive_gate_ok.assert_not_called()
        instance.context.send_message.assert_awaited_once()

    def test_soul_unconfirmed_transport_is_not_recorded_as_success(self):
        ns, instance = self.soul()
        instance.context.send_message.return_value = None
        self.assertFalse(
            asyncio.run(ns["_send"](instance, "fixture", user_requested=True))
        )

    def test_soul_uses_shared_contract_and_fails_closed(self):
        ns, instance = self.soul()
        with patch.object(
            voicegate, "evaluate", return_value={"allow": True}
        ) as evaluate:
            self.assertTrue(ns["_proactive_gate_ok"](instance, 0, "qzone"))
        evaluate.assert_called_once_with(
            session_id="fixture-owner", outlet="qzone", fail_closed=True
        )
        with patch.object(voicegate, "evaluate", side_effect=RuntimeError("fixture")):
            self.assertFalse(ns["_proactive_gate_ok"](instance))

    def test_only_explicit_user_command_calls_bypass_proactive_gate(self):
        tree = ast.parse(self.soul_path.read_text(encoding="utf-8"))
        calls = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "_send"
        ]
        autonomous = [n for n in calls if not n.keywords]
        explicit = [
            n
            for n in calls
            if any(
                k.arg == "user_requested"
                and isinstance(k.value, ast.Constant)
                and k.value.value is True
                for k in n.keywords
            )
        ]
        self.assertEqual(len(autonomous), 2)
        self.assertEqual(len(explicit), 2)

    def test_hook_does_not_charge_user_prompt_or_mutate_system_prompt(self):
        self.run_hook(True)

    def test_hook_observe_mode_reports_without_removing_blocks(self):
        self.run_hook(False)

    def test_large_user_prompt_does_not_trigger_small_injection_budget(self):
        self.run_hook(True, body_length=1)

    def run_hook(self, enforce, body_length=100):
        path = ROOT / "astrbot_plugin_whale_core/main.py"
        if not path.exists():
            path = PLUGINS / "astrbot_plugin_whale_core/main.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        name = next(
            n.name
            for n in tree.body
            if isinstance(n, ast.ClassDef)
            and any(
                isinstance(f, ast.AsyncFunctionDef) and f.name == "_measure_inject"
                for f in n.body
            )
        )
        ns = load_functions(
            path,
            ["_measure_inject"],
            {
                "CORE_ERROR": None,
                "core_config": types.SimpleNamespace(
                    section=lambda key: {"cap_chars": 60, "enforce_budget": enforce}
                ),
                "injectwatch": injectwatch,
                "time": types.SimpleNamespace(time=lambda: 100),
                "log": Mock(),
            },
            class_name=name,
        )
        instance = types.SimpleNamespace(
            _inject_events=[], _save_inject_stats=Mock(), _last_over_ts=100
        )
        part = types.SimpleNamespace(
            text="<dynamic_context>" + "x" * body_length + "</dynamic_context>",
            _no_save=True,
            type="text",
        )
        original_parts = [part]
        req = types.SimpleNamespace(
            extra_user_content_parts=original_parts,
            prompt="user" * 3000,
            system_prompt="system fixture",
        )
        asyncio.run(
            ns["_measure_inject"](
                instance, types.SimpleNamespace(unified_msg_origin="fixture-owner"), req
            )
        )
        self.assertEqual(req.prompt, "user" * 3000)
        self.assertEqual(req.system_prompt, "system fixture")
        self.assertIs(req.extra_user_content_parts, original_parts)
        dropped = enforce and len(part.text) > 60
        self.assertEqual(len(original_parts), 0 if dropped else 1)
        budget = instance._inject_events[0]["budget"]
        self.assertEqual(budget["enforced"], enforce)
        self.assertLess(budget["before_chars"], 200)
        self.assertEqual(
            budget["after_chars"], 0 if dropped else budget["before_chars"]
        )
        self.assertEqual(instance._inject_events[0]["over_cap"], len(part.text) > 60)


if __name__ == "__main__":
    unittest.main(verbosity=2)

