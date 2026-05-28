import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

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
    def _make_converter(self):
        converter = KML_TO_IA.KmlToIaConverter(
            kml_path="dummy.kml",
            ia_output_path="dummy.tif",
        )
        converter._coord_transform = types.SimpleNamespace(
            TransformPoints=lambda pts: [(lon, lat, 0.0) for lon, lat in pts]
        )
        return converter

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

    def test_sector_search_keeps_nearest_point_per_contour(self):
        pts_query = np.array([[0.0, 0.0]])
        pts_train = np.array([
            [1.0, 0.0],
            [2.0, 0.0],
            [0.0, 3.0],
            [0.0, 4.0],
        ])
        dists = np.array([[1.0, 2.0, 3.0, 4.0]])
        idxs = np.array([[0, 1, 2, 3]])
        contour_ids = np.array([10, 10, 20, 20], dtype=np.int32)

        selected_mask, valid_mask = KML_TO_IA._select_arcgis_sector_neighbors(
            dists=dists,
            idxs=idxs,
            pts_query=pts_query,
            pts_train=pts_train,
            n_sectors=1,
            points_per_sector=4,
            min_points=1,
            max_distance=None,
            contour_ids=contour_ids,
            per_contour_points=1,
            max_contours=None,
        )

        np.testing.assert_array_equal(valid_mask, np.ones_like(valid_mask, dtype=bool))
        np.testing.assert_array_equal(
            selected_mask,
            np.array([[True, False, True, False]]),
        )

    def test_sector_search_limits_distinct_contours_when_requested(self):
        pts_query = np.array([[0.0, 0.0]])
        pts_train = np.array([
            [1.0, 0.0],
            [2.0, 0.0],
            [0.0, 3.0],
            [-4.0, 0.0],
        ])
        dists = np.array([[1.0, 2.0, 3.0, 4.0]])
        idxs = np.array([[0, 1, 2, 3]])
        contour_ids = np.array([10, 20, 30, 40], dtype=np.int32)

        selected_mask, _ = KML_TO_IA._select_arcgis_sector_neighbors(
            dists=dists,
            idxs=idxs,
            pts_query=pts_query,
            pts_train=pts_train,
            n_sectors=1,
            points_per_sector=4,
            min_points=1,
            max_distance=None,
            contour_ids=contour_ids,
            per_contour_points=1,
            max_contours=2,
        )

        np.testing.assert_array_equal(
            selected_mask,
            np.array([[True, True, False, False]]),
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

    def test_iter_sample_points_yields_contour_ids(self):
        converter = self._make_converter()
        converter.sample_interval = 1
        converter._contours = [
            {"coordinates": [(100.0, 20.0), (101.0, 21.0)], "ia": 1.5},
            {"coordinates": [(102.0, 22.0), (103.0, 23.0)], "ia": 2.5},
        ]

        self.assertEqual(
            list(converter._iter_sample_points()),
            [
                (100.0, 20.0, 1.5, 0),
                (101.0, 21.0, 1.5, 0),
                (102.0, 22.0, 2.5, 1),
                (103.0, 23.0, 2.5, 1),
            ],
        )

    def test_prepare_sample_points_keeps_contour_ids_after_dedup(self):
        converter = self._make_converter()
        converter.sample_interval = 1
        converter.max_sample_points = 100
        converter._contours = [
            {"coordinates": [(100.0, 20.0), (101.0, 21.0)], "ia": 1.0},
            {"coordinates": [(100.0, 20.0), (102.0, 22.0)], "ia": 2.0},
        ]

        x_arr, y_arr, ia_arr, contour_ids = converter._prepare_sample_points()

        np.testing.assert_array_equal(x_arr, np.array([100.0, 101.0, 102.0]))
        np.testing.assert_array_equal(y_arr, np.array([20.0, 21.0, 22.0]))
        np.testing.assert_array_equal(ia_arr, np.array([1.0, 1.0, 2.0], dtype=np.float32))
        np.testing.assert_array_equal(contour_ids, np.array([0, 0, 1], dtype=np.int32))

    def _make_arcgis_idw_converter(self, *, radial_assist=True):
        converter = KML_TO_IA.KmlToIaConverter(
            kml_path="dummy.kml",
            ia_output_path="dummy.tif",
            interp_method="qgis_idw",
            resolution=0.1,
            qgis_idw_power=1.0,
            idw_num_neighbors=3,
            chunk_size=1,
            max_interp_workers=1,
            arcgis_idw_radial_assist=radial_assist,
        )
        converter._n_cols = 1
        converter._n_rows = 1
        converter._x_min = 1.5
        converter._y_max = 0.5
        converter._res_lon = 1.0
        converter._res_lat = 1.0
        converter._geo_transform = (0, 1, 0, 0, 0, -1)
        converter._utm_srs = types.SimpleNamespace(ExportToWkt=lambda: "WKT")
        converter._ensure_file_writable = lambda _: None
        return converter

    def _run_arcgis_idw_once(self, converter, pchip_cls):
        class _FakeBand:
            def __init__(self):
                self.arr = None

            def SetNoDataValue(self, _):
                return None

            def WriteArray(self, arr, _xoff, _yoff):
                self.arr = np.array(arr, copy=True)

            def ComputeStatistics(self, _):
                return None

            def FlushCache(self):
                return None

        class _FakeDataset:
            def __init__(self):
                self.band = _FakeBand()

            def SetGeoTransform(self, _):
                return None

            def SetProjection(self, _):
                return None

            def GetRasterBand(self, _):
                return self.band

        class _FakeDriver:
            def __init__(self):
                self.dataset = _FakeDataset()

            def Create(self, *_args, **_kwargs):
                return self.dataset

        class _FakeTree:
            def query(self, pts_query, k):
                d = np.array([1.0, 2.0, 3.0], dtype=np.float64)[:k]
                i = np.array([0, 1, 2], dtype=np.int64)[:k]
                return np.tile(d, (pts_query.shape[0], 1)), np.tile(i, (pts_query.shape[0], 1))

        fake_driver = _FakeDriver()
        fake_gdal = types.SimpleNamespace(
            GDT_Float32=6,
            GetDriverByName=lambda _name: fake_driver,
        )

        with patch.object(KML_TO_IA, "_HAS_SCIPY", True), \
             patch.object(KML_TO_IA, "_cKDTree", lambda _pts: _FakeTree(), create=True), \
             patch.object(KML_TO_IA, "_PchipInterpolator", pchip_cls, create=True), \
             patch.object(KML_TO_IA, "gdal", fake_gdal):
            converter._run_arcgis_idw_interpolation(
                np.array([-1.0, 0.0, 1.0], dtype=np.float64),
                np.array([0.0, 0.0, 0.0], dtype=np.float64),
                np.array([1.0, 0.0, 1.0], dtype=np.float32),
                "/tmp/arcgis_idw_test.tif",
            )

        return float(fake_driver.dataset.band.arr[0, 0])

    def test_arcgis_idw_radial_assist_adds_back_radial_trend(self):
        class _IdentityPchip:
            def __init__(self, *_args, **_kwargs):
                return None

            def __call__(self, r):
                return np.asarray(r, dtype=np.float64)

        converter = self._make_arcgis_idw_converter(radial_assist=True)
        result = self._run_arcgis_idw_once(converter, _IdentityPchip)
        self.assertAlmostEqual(result, 2.0, places=6)

    def test_arcgis_idw_radial_assist_falls_back_when_pchip_fails(self):
        class _FailingPchip:
            def __init__(self, *_args, **_kwargs):
                raise RuntimeError("pchip failed")

        converter = self._make_arcgis_idw_converter(radial_assist=True)
        result = self._run_arcgis_idw_once(converter, _FailingPchip)
        expected = (1.0 + (1.0 / 3.0)) / (1.0 + 0.5 + (1.0 / 3.0))
        self.assertAlmostEqual(result, expected, places=6)

    def test_arcgis_idw_radial_assist_default_enabled(self):
        converter = KML_TO_IA.KmlToIaConverter(
            kml_path="dummy.kml",
            ia_output_path="dummy.tif",
        )
        self.assertTrue(converter.arcgis_idw_radial_assist)


if __name__ == "__main__":
    unittest.main()
