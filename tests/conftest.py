import pytest

from asicre import paths


def _have_inputs() -> bool:
    return paths.PUZZLE_GDS.exists() and paths.LIBERTY.exists()


needs_inputs = pytest.mark.skipif(
    not _have_inputs(),
    reason="needs the puzzle submodule and the SKY130 library (git submodule update --init; make pdk)",
)


@pytest.fixture(scope="session")
def ctx():
    if not _have_inputs():
        pytest.skip("puzzle files or PDK missing")
    from asicre.pipeline import Context
    return Context()
