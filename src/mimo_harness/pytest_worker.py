"""Private subprocess runner. Pytest is optional for the main CLI process.

Records stable node IDs and phases rather than interpreting terminal output.
This is an evidence recorder for trusted local tests, not a security sandbox.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def run_unittest(data, output):
    """Retain the stdlib-only path, with real case IDs and zero-test detection."""
    import inspect
    import unittest
    from xml.etree.ElementTree import Element, SubElement, ElementTree

    class Recorder(unittest.TextTestResult):
        def record(self, test, status, message=""):
            try:
                file = inspect.getsourcefile(type(test)) or ""
            except TypeError:
                file = ""
            old = data["cases"].get(test.id(), {})
            if old.get("status") in {"error", "failed"} and status == "passed":
                return
            data["cases"][test.id()] = {
                "nodeid": test.id(), "status": status, "file": file,
                "assertion_failure": status == "failed", "message": message,
                "phases": {"call": status},
            }

        def addSuccess(self, test):
            super().addSuccess(test)
            self.record(test, "passed")

        def addFailure(self, test, err):
            super().addFailure(test, err)
            self.record(test, "failed", self._exc_info_to_string(err, test))

        def addError(self, test, err):
            super().addError(test, err)
            self.record(test, "error", self._exc_info_to_string(err, test))

        def addSkip(self, test, reason):
            super().addSkip(test, reason)
            self.record(test, "skipped", reason)

        def addExpectedFailure(self, test, err):
            super().addExpectedFailure(test, err)
            self.record(test, "xfail")

        def addUnexpectedSuccess(self, test):
            super().addUnexpectedSuccess(test)
            self.record(test, "xpass")

        def addSubTest(self, test, subtest, err):
            super().addSubTest(test, subtest, err)
            if err is not None:
                self.record(test, "failed" if issubclass(err[0], AssertionError) else "error",
                            self._exc_info_to_string(err, test))

    def ids(suite):
        for test in suite:
            if isinstance(test, unittest.TestSuite):
                yield from ids(test)
            else:
                yield test.id()

    sys.path.insert(0, str(Path.cwd()))
    suite = unittest.defaultTestLoader.discover(".")
    data["collected"] = list(ids(suite))
    result = unittest.TextTestRunner(verbosity=2, resultclass=Recorder).run(suite)
    code = 5 if result.testsRun == 0 else 0 if result.wasSuccessful() else 1
    data["exit_code"] = code
    data["runner_version"] = sys.version.split()[0]
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    xml = Element("testsuite", tests=str(result.testsRun))
    for nodeid, case in data["cases"].items():
        element = SubElement(xml, "testcase", name=nodeid)
        if case["status"] in {"failed", "error", "skipped", "xfail", "xpass"}:
            tag = "failure" if case["status"] in {"failed", "xpass"} else "error" if case["status"] == "error" else "skipped"
            SubElement(element, tag).text = case.get("message", "")
    ElementTree(xml).write(sys.argv[4], encoding="utf-8", xml_declaration=True)
    return code


def main() -> int:
    output = Path(sys.argv[1])
    args = json.loads(sys.argv[2])
    data = {
        "schema_version": 1, "cases": {}, "collected": [],
        "collection_errors": [], "collection_skips": [], "deselected": [], "exit_code": None,
    }
    if len(sys.argv) > 3 and sys.argv[3] == "unittest":
        return run_unittest(data, output)
    try:
        import pytest
    except ImportError:
        data["runner_error"] = "PYTEST_NOT_INSTALLED"
        output.write_text(json.dumps(data), encoding="utf-8")
        return 4

    class Recorder:
        def pytest_configure(self, config):
            if config.inipath is not None:
                try:
                    Path(config.inipath).resolve().relative_to(Path(config.rootpath).resolve())
                except ValueError as exc:
                    raise pytest.UsageError(
                        "Pytest configuration is outside the captured workspace. "
                        "Add a project pytest.ini (even an empty [pytest] file), "
                        "or select a configuration inside the project explicitly."
                    ) from exc

        @pytest.hookimpl(hookwrapper=True)
        def pytest_runtest_makereport(self, item, call):
            outcome = yield
            report = outcome.get_result()
            case = data["cases"].setdefault(item.nodeid, {
                "nodeid": item.nodeid, "status": "not_run", "phases": {},
                "assertion_failure": False, "file": str(item.path),
            })
            case["phases"][report.when] = report.outcome
            if report.failed:
                assertion = (
                    report.when == "call" and call.excinfo is not None
                    and issubclass(call.excinfo.type, AssertionError)
                )
                case["status"] = "failed" if assertion else "error"
                case["assertion_failure"] = assertion
                case["message"] = str(report.longrepr)
            elif case["status"] not in {"failed", "error"}:
                if report.skipped:
                    case["status"] = "xfail" if hasattr(report, "wasxfail") else "skipped"
                elif report.when == "call":
                    case["status"] = "xpass" if hasattr(report, "wasxfail") else "passed"

        def pytest_collection_finish(self, session):
            data["collected"] = [item.nodeid for item in session.items]

        def pytest_collectreport(self, report):
            if report.failed:
                data["collection_errors"].append({
                    "nodeid": report.nodeid, "message": str(report.longrepr),
                })
            elif report.skipped:
                data["collection_skips"].append({"nodeid": report.nodeid, "message": str(report.longrepr)})

        def pytest_deselected(self, items):
            data["deselected"].extend(item.nodeid for item in items)

    # Keep target imports independent of where this installed worker resides.
    sys.path.insert(0, str(Path.cwd()))
    try:
        code = int(pytest.main(args, plugins=[Recorder()]))
        data["exit_code"] = code
    except BaseException as exc:
        code = 4
        data["runner_error"] = f"{type(exc).__name__}: {exc}"
    data["pytest_version"] = pytest.__version__
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
