import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from kralyavuz.batch_checker import _run_independent_service_queues
from kralyavuz.main import BatchWorker


class IndependentBatchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.output = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def _result(self, domain, service):
        path = self.output / f"{domain}_{service}.png"
        path.write_bytes(f"{domain}:{service}".encode())
        return SimpleNamespace(screenshot_path=path)

    def _start(self, domains, btk_runner, family_runner, progress=None):
        state = {"results": None, "error": None}

        def target():
            try:
                state["results"] = _run_independent_service_queues(
                    domains,
                    "btk-target",
                    "family-target",
                    progress=progress,
                    btk_runner=btk_runner,
                    family_runner=family_runner,
                )
            except Exception as exc:
                state["error"] = exc

        thread = threading.Thread(target=target)
        thread.start()
        return thread, state

    def _join(self, thread, state):
        thread.join(2)
        self.assertFalse(thread.is_alive(), "Batch testi zamanında tamamlanmadı")
        if state["error"]:
            raise state["error"]
        return state["results"]

    def test_a_btk_advances_while_family_waits_on_site1(self):
        family_site1 = threading.Event()
        release_family = threading.Event()
        btk_site2 = threading.Event()

        def btk_runner(target, domain, captcha_ready, captcha_waiter):
            if domain == "site2.com":
                btk_site2.set()
            return self._result(domain, "BTK")

        def family_runner(target, domain, captcha_ready, captcha_waiter):
            if domain == "site1.com":
                family_site1.set()
                self.assertTrue(release_family.wait(2))
            return self._result(domain, "AileProfili")

        thread, state = self._start(
            ["site1.com", "site2.com"], btk_runner, family_runner
        )
        try:
            self.assertTrue(family_site1.wait(1))
            self.assertTrue(btk_site2.wait(1))
        finally:
            release_family.set()
        self._join(thread, state)

    def test_b_family_advances_to_site2_while_btk_is_on_site2(self):
        btk_site2 = threading.Event()
        release_btk = threading.Event()
        family_site2 = threading.Event()

        def btk_runner(target, domain, captcha_ready, captcha_waiter):
            if domain == "site2.com":
                btk_site2.set()
                self.assertTrue(release_btk.wait(2))
            return self._result(domain, "BTK")

        def family_runner(target, domain, captcha_ready, captcha_waiter):
            if domain == "site1.com":
                self.assertTrue(btk_site2.wait(2))
            else:
                family_site2.set()
            return self._result(domain, "AileProfili")

        thread, state = self._start(
            ["site1.com", "site2.com"], btk_runner, family_runner
        )
        try:
            self.assertTrue(family_site2.wait(1))
            self.assertTrue(btk_site2.is_set())
        finally:
            release_btk.set()
        self._join(thread, state)

    def test_c_wrong_btk_captcha_keeps_btk_on_site1_only(self):
        btk_site1 = threading.Event()
        wrong_code = threading.Event()
        wrong_seen = threading.Event()
        correct_code = threading.Event()
        btk_site2 = threading.Event()
        family_finished = threading.Event()
        family_count = 0
        family_lock = threading.Lock()

        def btk_runner(target, domain, captcha_ready, captcha_waiter):
            if domain == "site1.com":
                btk_site1.set()
                self.assertTrue(wrong_code.wait(2))
                wrong_seen.set()
                self.assertTrue(correct_code.wait(2))
            else:
                btk_site2.set()
            return self._result(domain, "BTK")

        def family_runner(target, domain, captcha_ready, captcha_waiter):
            nonlocal family_count
            result = self._result(domain, "AileProfili")
            with family_lock:
                family_count += 1
                if family_count == 2:
                    family_finished.set()
            return result

        thread, state = self._start(
            ["site1.com", "site2.com"], btk_runner, family_runner
        )
        try:
            self.assertTrue(btk_site1.wait(1))
            wrong_code.set()
            self.assertTrue(wrong_seen.wait(1))
            self.assertFalse(btk_site2.is_set())
            self.assertTrue(family_finished.wait(1))
        finally:
            correct_code.set()
        self.assertTrue(btk_site2.wait(1))
        self._join(thread, state)

    def test_d_finished_btk_queue_does_not_wait_per_domain(self):
        btk_finished = threading.Event()
        release_family = threading.Event()
        btk_count = 0
        btk_lock = threading.Lock()

        def btk_runner(target, domain, captcha_ready, captcha_waiter):
            nonlocal btk_count
            result = self._result(domain, "BTK")
            with btk_lock:
                btk_count += 1
                if btk_count == 3:
                    btk_finished.set()
            return result

        def family_runner(target, domain, captcha_ready, captcha_waiter):
            if domain == "site1.com":
                self.assertTrue(release_family.wait(2))
            return self._result(domain, "AileProfili")

        thread, state = self._start(
            ["site1.com", "site2.com", "site3.com"],
            btk_runner,
            family_runner,
        )
        try:
            self.assertTrue(btk_finished.wait(1))
            self.assertTrue(thread.is_alive())
        finally:
            release_family.set()
        results = self._join(thread, state)
        self.assertTrue(all(item.btk_status == "Tamamlandı" for item in results))

    def test_e_results_and_screenshots_stay_bound_to_domain_and_service(self):
        progress_events = []
        progress_lock = threading.Lock()

        def progress(completed, total, domain, service, stage):
            with progress_lock:
                progress_events.append((domain, service, stage))

        def btk_runner(target, domain, captcha_ready, captcha_waiter):
            return self._result(domain, "BTK")

        def family_runner(target, domain, captcha_ready, captcha_waiter):
            return self._result(domain, "AileProfili")

        results = _run_independent_service_queues(
            ["site1.com", "site2.com", "site3.com"],
            "btk-target",
            "family-target",
            progress=progress,
            btk_runner=btk_runner,
            family_runner=family_runner,
        )

        for item in results:
            self.assertEqual(item.btk_screenshot_path.name, f"{item.domain}_BTK.png")
            self.assertEqual(
                item.family_screenshot_path.name,
                f"{item.domain}_AileProfili.png",
            )
            self.assertEqual(
                item.btk_screenshot_path.read_text(), f"{item.domain}:BTK"
            )
            self.assertEqual(
                item.family_screenshot_path.read_text(),
                f"{item.domain}:AileProfili",
            )
        self.assertIn(("site2.com", "BTK", "Tamamlandı"), progress_events)
        self.assertIn(
            ("site1.com", "Aile Profili", "Tamamlandı"), progress_events
        )

    def test_service_error_does_not_stop_either_queue(self):
        btk_started = []
        family_started = []

        def btk_runner(target, domain, captcha_ready, captcha_waiter):
            btk_started.append(domain)
            if domain == "site1.com":
                raise RuntimeError("BTK timeout")
            return self._result(domain, "BTK")

        def family_runner(target, domain, captcha_ready, captcha_waiter):
            family_started.append(domain)
            return self._result(domain, "AileProfili")

        results = _run_independent_service_queues(
            ["site1.com", "site2.com"],
            "btk-target",
            "family-target",
            btk_runner=btk_runner,
            family_runner=family_runner,
        )

        self.assertEqual(btk_started, ["site1.com", "site2.com"])
        self.assertEqual(family_started, ["site1.com", "site2.com"])
        self.assertTrue(results[0].btk_status.startswith("Hata:"))
        self.assertEqual(results[1].btk_status, "Tamamlandı")
        self.assertTrue(
            all(item.family_status == "Tamamlandı" for item in results)
        )

    def test_captcha_state_reset_is_scoped_to_one_service(self):
        worker = BatchWorker(["site1.com", "site2.com"])
        worker._captcha_codes["Aile Profili"] = "family-code"
        worker._captcha_events["Aile Profili"].set()
        worker._progress(1, 4, "site2.com", "BTK", "Kontrol ediliyor")

        self.assertEqual(worker._captcha_codes["Aile Profili"], "family-code")
        self.assertTrue(worker._captcha_events["Aile Profili"].is_set())
        self.assertEqual(worker._captcha_domains["BTK"], "site2.com")
        self.assertIsNone(worker._captcha_domains["Aile Profili"])
        worker._service_targets["BTK"] = "btk-target"
        with self.assertRaisesRegex(RuntimeError, "yanlış domain"):
            worker.submit_captcha("BTK", "btk-target", "site1.com", "1234")


if __name__ == "__main__":
    unittest.main()
