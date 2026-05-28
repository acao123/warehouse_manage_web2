import importlib.util
import sys
import types
import unittest
from pathlib import Path

import numpy as np


def _install_stub_modules():
    qgis = types.ModuleType("qgis")
    qgis_analysis = types.ModuleType("qgis.analysis")
    qgis_core = types.ModuleType("qgis.core")
    pyqt5 = types.ModuleType("PyQt5")
    qtcore = types.ModuleType("PyQt5.QtCore")
    osgeo = types.ModuleType("osgeo")
    gdal = types.ModuleType("osgeo.gdal")
    ogr = types.ModuleType("osgeo.ogr")
    osr = types.ModuleType("osgeo.osr")

    qgis_analysis.QgsGridFileWriter = object
    qgis_analysis.QgsIDWInterpolator = object
    qgis_analysis.QgsInterpolator = types.SimpleNamespace(
        LayerData=object,
        ValueSource=types.SimpleNamespace(ValueAttribute=0),
        SourceType=types.SimpleNamespace(SourcePoints=0),
    )
    qgis_analysis.QgsTinInterpolator = types.SimpleNamespace(
        TinInterpolation=types.SimpleNamespace(Linear=0, CloughTocher=1)
    )

    for name in (
        "QgsFeature",
        "QgsField",
        "QgsFields",
        "QgsGeometry",
        "QgsPointXY",
        "QgsRectangle",
        "QgsVectorLayer",
    ):
        setattr(qgis_core, name, object)

    qtcore.QMetaType = types.SimpleNamespace(Type=types.SimpleNamespace(Double=float))
    gdal.UseExceptions = lambda: None
    osr.SpatialReference = object

    sys.modules.setdefault("qgis", qgis)
    sys.modules.setdefault("qgis.analysis", qgis_analysis)
    sys.modules.setdefault("qgis.core", qgis_core)
    sys.modules.setdefault("PyQt5", pyqt5)
    sys.modules.setdefault("PyQt5.QtCore", qtcore)
    sys.modules.setdefault("osgeo", osgeo)
    sys.modules.setdefault("osgeo.gdal", gdal)
    sys.modules.setdefault("osgeo.ogr", ogr)
    sys.modules.setdefault("osgeo.osr", osr)

    osgeo.gdal = gdal
    osgeo.ogr = ogr
    osgeo.osr = osr


_install_stub_modules()

MODULE_PATH = Path(__file__).with_name("kml_to_Ia.py")
SPEC = importlib.util.spec_from_file_location("apps.core.kml_to_Ia", MODULE_PATH)
KML_TO_IA = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(KML_TO_IA)


class ArcgisSectorSearchTests(unittest.TestCase):
    def test_sector_search_limits_neighbors_per_sector(self):
        pts_query = np.array([[0.0, 0.0]])
        pts_train = np.array([
            [1.0, 0.0],    # sector 0
            [2.0, 0.0],    # sector 0
            [0.0, 1.0],    # sector 1
            [-1.0, 0.0],   # sector 2
            [0.0, -1.0],   # sector 3
        ])
        # dists/idxs 模拟 cKDTree.query 的升序返回：前 4 个点距离都为 1，
        # 最后一个点仍在 sector 0，但更远，因此应被 points_per_sector=1 排除。
        dists = np.array([[1.0, 1.0, 1.0, 1.0, 2.0]])
        idxs = np.array([[0, 2, 3, 4, 1]])

        selected_mask, valid_mask = KML_TO_IA._select_arcgis_sector_neighbors(
            dists=dists,
            idxs=idxs,
            pts_query=pts_query,
            pts_train=pts_train,
            n_sectors=4,
            points_per_sector=1,
            min_points=1,
            max_distance=None,
        )

        np.testing.assert_array_equal(valid_mask, np.ones_like(valid_mask, dtype=bool))
        np.testing.assert_array_equal(
            selected_mask,
            np.array([[True, True, True, True, False]]),
        )

    def test_sector_search_skips_underfilled_sector_after_distance_filter(self):
        pts_query = np.array([[0.0, 0.0]])
        pts_train = np.array([
            [1.0, 0.0],     # sector 0
            [2.0, 0.0],     # sector 0
            [0.0, 1.0],     # sector 1
            [0.0, 2.0],     # sector 1
            [-5.0, 0.0],    # sector 2 (too far)
            [0.0, -5.0],    # sector 3 (too far)
        ])
        dists = np.array([[1.0, 1.0, 2.0, 2.0, 5.0, 5.0]])
        idxs = np.array([[0, 2, 1, 3, 4, 5]])

        selected_mask, valid_mask = KML_TO_IA._select_arcgis_sector_neighbors(
            dists=dists,
            idxs=idxs,
            pts_query=pts_query,
            pts_train=pts_train,
            n_sectors=4,
            points_per_sector=2,
            min_points=2,
            max_distance=2.5,
        )

        np.testing.assert_array_equal(
            valid_mask,
            np.array([[True, True, True, True, False, False]]),
        )
        np.testing.assert_array_equal(
            selected_mask,
            np.array([[True, True, True, True, False, False]]),
        )


if __name__ == "__main__":
    unittest.main()
