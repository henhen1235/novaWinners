from pathlib import Path

import ezdxf
import pytest

from ingest_blueprint.ifc_loader import load_ifc
from ingest_blueprint.pipeline import run


def _office_dxf(path: Path) -> None:
    document = ezdxf.new()
    model = document.modelspace()
    walls = [
        ((0, 0), (3.5, 0)),
        ((4.4, 0), (8, 0)),
        ((8, 0), (8, 6)),
        ((8, 6), (0, 6)),
        ((0, 6), (0, 0)),
    ]
    for start, end in walls:
        model.add_line(start, end, dxfattribs={"layer": "A-WALL"})
    model.add_text("EXIT", dxfattribs={"height": 0.3, "insert": (3.7, -0.4)})
    document.saveas(path)


def test_dxf_office_validates(tmp_path: Path):
    path = tmp_path / "office.dxf"
    _office_dxf(path)
    scene, code = run(path, tmp_path / "out", no_llm=True)
    assert code == 0
    assert len(scene["rooms"]) >= 1
    assert scene["exits"]


def test_ifc_import_explains_the_optional_install():
    try:
        import ifcopenshell  # noqa: F401
    except ImportError:
        with pytest.raises(ImportError, match="requirements-ifc"):
            load_ifc("missing.ifc")
        return
    pytest.skip("ifcopenshell is installed; geometry projection is covered when a sample file is present")
