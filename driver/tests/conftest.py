import os
import subprocess

import pytest

HERE = os.path.dirname(__file__)
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
DEFAULT_ANALYZER = os.path.join(REPO, "analyzer", "build", "reachability-analyzer")
TESTDATA = os.path.join(REPO, "analyzer", "test")
FIXTURES = os.path.join(REPO, "fixtures")


@pytest.fixture(scope="session")
def analyzer():
    path = os.environ.get("REACHABILITY_ANALYZER", DEFAULT_ANALYZER)
    if not os.path.exists(path):
        pytest.skip(f"analyzer not built at {path}")
    return path


@pytest.fixture(scope="session")
def svf_analyzer():
    path = os.environ.get("REACHABILITY_ANALYZER_SVF")
    if not path or not os.path.exists(path):
        pytest.skip("SVF analyzer not built (set REACHABILITY_ANALYZER_SVF)")
    return path


@pytest.fixture
def run_analyzer(analyzer):
    def _run(args):
        return subprocess.run([analyzer, *args], capture_output=True, text=True)

    return _run


def ll(name):
    return os.path.join(TESTDATA, name)
