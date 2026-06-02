import importlib.util
import sys
import tempfile
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


class ArcgisIdwSmoothTests(unittest.TestCase):
    def test_arcgis_idw_smooth_default_and_alias(self):
        converter = KML_TO_IA.KmlToIaConverter("dummy.kml", "dummy.tif")
        self.assertTrue(converter.arcgis_idw_smooth)
        self.assertTrue(converter.arcgis_idw_radial_assist)

        converter_alias = KML_TO_IA.KmlToIaConverter(
            "dummy.kml", "dummy.tif", arcgis_idw_radial_assist=False
        )
        self.assertFalse(converter_alias.arcgis_idw_smooth)
        self.assertFalse(converter_alias.arcgis_idw_radial_assist)

    def test_arcgis_idw_smooth_keeps_original_value_range(self):
        converter = KML_TO_IA.KmlToIaConverter(
            kml_path="dummy.kml",
            ia_output_path="dummy.tif",
            interp_method="qgis_idw",
            idw_num_neighbors=2,
            qgis_idw_power=1.0,
            chunk_size=1,
            max_interp_workers=1,
            arcgis_idw_smooth=True,
            arcgis_idw_smooth_sigma_factor=0.35,
            arcgis_idw_smooth_extra_neighbors=2,
        )
        converter._n_cols = 2
        converter._n_rows = 2
        converter._x_min = 0.0
        converter._y_max = 2.0
        converter._res_lon = 1.0
        converter._res_lat = 1.0
        converter._geo_transform = (0, 1, 0, 0, 0, -1)
        converter._utm_srs = types.SimpleNamespace(ExportToWkt=lambda: "WKT")
        converter._ensure_file_writable = lambda _: None

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
                self.flush_called = False

            def SetGeoTransform(self, _):
                return None

            def SetProjection(self, _):
                return None

            def GetRasterBand(self, _):
                return self.band

            def FlushCache(self):
                self.flush_called = True
                return None

        class _FakeDriver:
            def __init__(self):
                self.dataset = _FakeDataset()

            def Create(self, *_args, **_kwargs):
                return self.dataset

        class _FakeTree:
            def query(self, pts_query, k):
                n = pts_query.shape[0]
                d = np.linspace(1.0, float(k), num=k, dtype=np.float64)
                idx = (np.arange(k, dtype=np.int64) % 3).reshape(1, -1)
                return np.tile(d, (n, 1)), np.tile(idx, (n, 1))

        fake_driver = _FakeDriver()
        fake_gdal = types.SimpleNamespace(
            GDT_Float32=6,
            GetDriverByName=lambda _name: fake_driver,
        )

        with patch.object(KML_TO_IA, "_HAS_SCIPY", True), \
                patch.object(KML_TO_IA, "_cKDTree", lambda _pts: _FakeTree(), create=True), \
                patch.object(KML_TO_IA, "gdal", fake_gdal):
            converter._run_arcgis_idw_interpolation(
                np.array([0.0, 1.0, 2.0], dtype=np.float64),
                np.array([0.0, 1.0, 2.0], dtype=np.float64),
                np.array([1.0, 2.0, 3.0], dtype=np.float32),
                str(Path(tempfile.gettempdir()) / "arcgis_idw_smooth_test.tif"),
            )

        arr = fake_driver.dataset.band.arr
        self.assertEqual(arr.shape, (2, 2))
        self.assertGreaterEqual(float(arr.min()), 1.0)
        self.assertLessEqual(float(arr.max()), 3.0)

    def test_scipy_tin_uses_breakline_points_and_locks_contour_cells(self):
        converter = KML_TO_IA.KmlToIaConverter(
            kml_path="dummy.kml",
            ia_output_path="dummy.tif",
            interp_method="scipy_tin",
            resolution=1.0,
            chunk_size=1,
            max_interp_workers=1,
            scipy_tin_smooth=True,
            scipy_tin_smooth_sigma_factor=0.5,
        )
        converter._n_cols = 1
        converter._n_rows = 1
        converter._x_min = 0.0
        converter._y_max = 1.0
        converter._res_lon = 1.0
        converter._res_lat = 1.0
        converter._geo_transform = (0, 1, 0, 0, 0, -1)
        converter._utm_srs = types.SimpleNamespace(ExportToWkt=lambda: "WKT")
        converter._ensure_file_writable = lambda _: None
        converter._coord_transform = types.SimpleNamespace(
            TransformPoints=lambda pts: [(lon, lat, 0.0) for lon, lat in pts]
        )
        converter._contours = [
            {"coordinates": [(0.0, 0.5), (1.0, 0.5)], "ia": 2.0}
        ]

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
                self.flush_called = False

            def SetGeoTransform(self, _):
                return None

            def SetProjection(self, _):
                return None

            def GetRasterBand(self, _):
                return self.band

            def FlushCache(self):
                self.flush_called = True
                return None

        class _FakeDriver:
            def __init__(self):
                self.dataset = _FakeDataset()

            def Create(self, *_args, **_kwargs):
                return self.dataset

        capture = {}

        class _FakeTin:
            def __init__(self, points, values, **_kwargs):
                capture["points"] = np.array(points, copy=True)
                capture["values"] = np.array(values, copy=True)
                capture["kwargs"] = dict(_kwargs)

            def __call__(self, pts):
                return np.full(pts.shape[0], 10.0, dtype=np.float64)

        class _FakeNN:
            def __init__(self, *_args, **_kwargs):
                pass

            def __call__(self, pts):
                return np.full(pts.shape[0], 10.0, dtype=np.float64)

        fake_driver = _FakeDriver()
        fake_gdal = types.SimpleNamespace(
            GDT_Float32=6,
            GetDriverByName=lambda _name: fake_driver,
        )

        class _FakeHullTri:
            def __init__(self, _points):
                pass

            def find_simplex(self, pts):
                return np.full(pts.shape[0], -1, dtype=np.int64)

        with patch.object(KML_TO_IA, "_HAS_SCIPY", True), \
                patch.object(KML_TO_IA, "_CloughTocher2DInterpolator", _FakeTin, create=True), \
                patch.object(KML_TO_IA, "_NearestNDInterpolator", _FakeNN, create=True), \
                patch("scipy.spatial.Delaunay", _FakeHullTri), \
                patch.object(KML_TO_IA, "gdal", fake_gdal):
            converter._run_scipy_tin_interpolation(
                np.array([0.0, 1.0], dtype=np.float64),
                np.array([0.0, 0.0], dtype=np.float64),
                np.array([1.0, 3.0], dtype=np.float32),
                str(Path(tempfile.gettempdir()) / "scipy_tin_breakline_test.tif"),
            )

        arr = fake_driver.dataset.band.arr
        self.assertEqual(arr.shape, (1, 1))
        self.assertGreater(capture["points"].shape[0], 2)
        self.assertIn(2.0, capture["values"])
        self.assertEqual(capture["kwargs"]["tol"], 1e-4)
        self.assertEqual(capture["kwargs"]["maxiter"], 100)
        self.assertAlmostEqual(float(arr[0, 0]), 2.0, places=6)
        self.assertTrue(fake_driver.dataset.flush_called)

    def test_scipy_tin_warns_for_legacy_compat_params(self):
        converter = KML_TO_IA.KmlToIaConverter(
            kml_path="dummy.kml",
            ia_output_path="dummy.tif",
            interp_method="scipy_tin",
            scipy_tin_smooth=False,
            scipy_tin_smooth_sigma_factor=0.0,
            scipy_tin_idw_neighbors=8,
            scipy_tin_idw_power=2.0,
            scipy_tin_radial_assist=False,
            scipy_tin_blend_safe_dist=5.0,
        )

        with patch.object(KML_TO_IA.logger, "warning") as warning_mock:
            converter._warn_scipy_tin_compat_params()

        warning_mock.assert_called_once()
        warning_args = warning_mock.call_args[0]
        self.assertIn("scipy_tin", warning_args[0])
        self.assertIn("scipy_tin_smooth=False", warning_args[1])
        self.assertIn("scipy_tin_idw_neighbors=8", warning_args[1])
        self.assertIn("scipy_tin_blend_safe_dist=5.0", warning_args[1])

    def test_prepare_breakline_prefers_nearest_contour_for_same_pixel(self):
        converter = KML_TO_IA.KmlToIaConverter(
            "dummy.kml", "dummy.tif", interp_method="scipy_tin", resolution=1.0
        )
        converter._x_min = 0.0
        converter._y_max = 1.0
        converter._res_lon = 1.0
        converter._res_lat = 1.0
        converter._n_cols = 1
        converter._n_rows = 1
        converter._coord_transform = types.SimpleNamespace(
            TransformPoints=lambda pts: [(lon, lat, 0.0) for lon, lat in pts]
        )
        converter._contours = [
            {"coordinates": [(0.0, 0.51), (1.0, 0.51)], "ia": 2.0},
            {"coordinates": [(0.0, 0.99), (1.0, 0.99)], "ia": 5.0},
        ]

        _, _, breakline_cells, _, _ = converter._prepare_scipy_tin_breakline_support(
            np.array([0.0, 1.0], dtype=np.float64),
            np.array([0.0, 0.0], dtype=np.float64),
            np.array([1.0, 3.0], dtype=np.float32),
        )
        self.assertAlmostEqual(float(breakline_cells[0][0]), 2.0, places=6)

    def test_scipy_tin_clamp_uses_interp_values_range(self):
        converter = KML_TO_IA.KmlToIaConverter(
            "dummy.kml", "dummy.tif", interp_method="scipy_tin", resolution=1.0
        )
        converter._n_cols = 1
        converter._n_rows = 1
        converter._x_min = 0.0
        converter._y_max = 1.0
        converter._res_lon = 1.0
        converter._res_lat = 1.0
        converter._geo_transform = (0, 1, 0, 0, 0, -1)
        converter._utm_srs = types.SimpleNamespace(ExportToWkt=lambda: "WKT")
        converter._ensure_file_writable = lambda _: None

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

            def FlushCache(self):
                return None

        class _FakeDriver:
            def __init__(self):
                self.dataset = _FakeDataset()

            def Create(self, *_args, **_kwargs):
                return self.dataset

        class _FakeTin:
            def __init__(self, *_args, **_kwargs):
                pass

            def __call__(self, pts):
                return np.full(pts.shape[0], 4.0, dtype=np.float64)

        class _FakeNN:
            def __init__(self, *_args, **_kwargs):
                pass

            def __call__(self, pts):
                return np.full(pts.shape[0], 4.0, dtype=np.float64)

        fake_driver = _FakeDriver()
        fake_gdal = types.SimpleNamespace(
            GDT_Float32=6,
            GetDriverByName=lambda _name: fake_driver,
        )

        class _FakeHullTri:
            def __init__(self, _points):
                pass

            def find_simplex(self, pts):
                return np.where(pts[:, 0] < 1.0, 0, -1).astype(np.int64)

        with patch.object(KML_TO_IA, "_HAS_SCIPY", True), \
                patch.object(KML_TO_IA, "_CloughTocher2DInterpolator", _FakeTin, create=True), \
                patch.object(KML_TO_IA, "_NearestNDInterpolator", _FakeNN, create=True), \
                patch("scipy.spatial.Delaunay", _FakeHullTri), \
                patch.object(
                    KML_TO_IA.KmlToIaConverter,
                    "_prepare_scipy_tin_breakline_support",
                    return_value=(
                        np.array([[0.0, 0.0], [1.0, 0.0]], dtype=np.float64),
                        np.array([1.0, 5.0], dtype=np.float64),
                        {},
                        0,
                        0,
                    ),
                ), \
                patch.object(KML_TO_IA, "gdal", fake_gdal):
            converter._run_scipy_tin_interpolation(
                np.array([0.0, 1.0], dtype=np.float64),
                np.array([0.0, 0.0], dtype=np.float64),
                np.array([1.0, 3.0], dtype=np.float32),
                str(Path(tempfile.gettempdir()) / "scipy_tin_clamp_range_test.tif"),
            )

        self.assertAlmostEqual(float(fake_driver.dataset.band.arr[0, 0]), 4.0, places=6)

    def test_scipy_tin_keeps_outside_hull_as_nodata(self):
        converter = KML_TO_IA.KmlToIaConverter(
            "dummy.kml", "dummy.tif", interp_method="scipy_tin", resolution=1.0
        )
        converter._n_cols = 1
        converter._n_rows = 1
        converter._x_min = 0.0
        converter._y_max = 1.0
        converter._res_lon = 1.0
        converter._res_lat = 1.0
        converter._geo_transform = (0, 1, 0, 0, 0, -1)
        converter._utm_srs = types.SimpleNamespace(ExportToWkt=lambda: "WKT")
        converter._ensure_file_writable = lambda _: None
        converter._contours = []
        converter._coord_transform = None

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

            def FlushCache(self):
                return None

        class _FakeDriver:
            def __init__(self):
                self.dataset = _FakeDataset()

            def Create(self, *_args, **_kwargs):
                return self.dataset

        class _FakeTri:
            def find_simplex(self, pts):
                return np.full(pts.shape[0], -1, dtype=np.int64)

        class _FakeTin:
            def __init__(self, *_args, **_kwargs):
                self.tri = _FakeTri()

            def __call__(self, pts):
                return np.full(pts.shape[0], np.nan, dtype=np.float64)

        class _FakeNN:
            def __init__(self, *_args, **_kwargs):
                pass

            def __call__(self, pts):
                return np.full(pts.shape[0], 8.0, dtype=np.float64)

        class _FakeHullTri:
            def __init__(self, _points):
                pass

            def find_simplex(self, pts):
                return np.full(pts.shape[0], -1, dtype=np.int64)

        fake_driver = _FakeDriver()
        fake_gdal = types.SimpleNamespace(
            GDT_Float32=6,
            GetDriverByName=lambda _name: fake_driver,
        )

        # v3.19: 使用真实 ConvexHull，提供三个明确落在像素 (0.5,0.5) 下方的输入点，
        # 使凸包三角形完全在 y<=0 区域内，从而令唯一栅格像素落在凸包外 → 结果应为 NoData。
        with patch.object(KML_TO_IA, "_HAS_SCIPY", True), \
                patch.object(KML_TO_IA, "_CloughTocher2DInterpolator", _FakeTin, create=True), \
                patch.object(KML_TO_IA, "_NearestNDInterpolator", _FakeNN, create=True), \
                patch.object(KML_TO_IA, "gdal", fake_gdal):
            converter._run_scipy_tin_interpolation(
                np.array([0.0, 1.0, 0.5], dtype=np.float64),   # 三点：x
                np.array([-2.0, -2.0, -3.0], dtype=np.float64), # 三点：y（全在像素下方）
                np.array([1.0, 3.0, 2.0], dtype=np.float32),    # 三点的值
                str(Path(tempfile.gettempdir()) / "scipy_tin_outside_hull_test.tif"),
            )

        self.assertAlmostEqual(float(fake_driver.dataset.band.arr[0, 0]), -9999.0, places=6)

    def test_scipy_tin_prefilters_outside_hull_before_interp(self):
        converter = KML_TO_IA.KmlToIaConverter(
            "dummy.kml", "dummy.tif", interp_method="scipy_tin", resolution=1.0, chunk_size=1
        )
        converter._n_cols = 2
        converter._n_rows = 1
        converter._x_min = 0.0
        converter._y_max = 1.0
        converter._res_lon = 1.0
        converter._res_lat = 1.0
        converter._geo_transform = (0, 1, 0, 0, 0, -1)
        converter._utm_srs = types.SimpleNamespace(ExportToWkt=lambda: "WKT")
        converter._ensure_file_writable = lambda _: None

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

            def FlushCache(self):
                return None

        class _FakeDriver:
            def __init__(self):
                self.dataset = _FakeDataset()

            def Create(self, *_args, **_kwargs):
                return self.dataset

        capture = {"call_sizes": []}

        class _FakeTin:
            def __init__(self, *_args, **_kwargs):
                pass

            def __call__(self, pts):
                capture["call_sizes"].append(int(pts.shape[0]))
                return np.full(pts.shape[0], 2.0, dtype=np.float64)

        class _FakeNN:
            def __init__(self, *_args, **_kwargs):
                pass

            def __call__(self, pts):
                raise AssertionError("outside-hull pixels should not use nearest-neighbor fill")

        fake_driver = _FakeDriver()
        fake_gdal = types.SimpleNamespace(
            GDT_Float32=6,
            GetDriverByName=lambda _name: fake_driver,
        )

        # v3.19: 使用真实 ConvexHull。三个非共线点 (0,0)、(0.8,0)、(0,1.5) 形成凸包三角形，
        # 使得 x=0.5 像素（中心 (0.5,0.5)）在凸包内，x=1.5 像素（中心 (1.5,0.5)）在凸包外。
        with patch.object(KML_TO_IA, "_HAS_SCIPY", True), \
                patch.object(KML_TO_IA, "_CloughTocher2DInterpolator", _FakeTin, create=True), \
                patch.object(KML_TO_IA, "_NearestNDInterpolator", _FakeNN, create=True), \
                patch.object(
                    KML_TO_IA.KmlToIaConverter,
                    "_prepare_scipy_tin_breakline_support",
                    return_value=(
                        np.array([[0.0, 0.0], [0.8, 0.0], [0.0, 1.5]], dtype=np.float64),
                        np.array([1.0, 3.0, 2.0], dtype=np.float64),
                        {},
                        0,
                        0,
                    ),
                ), \
                patch.object(KML_TO_IA, "gdal", fake_gdal):
            converter._run_scipy_tin_interpolation(
                np.array([0.0, 1.0], dtype=np.float64),
                np.array([0.0, 0.0], dtype=np.float64),
                np.array([1.0, 3.0], dtype=np.float32),
                str(Path(tempfile.gettempdir()) / "scipy_tin_chunk_prefilter_test.tif"),
            )

        np.testing.assert_array_equal(fake_driver.dataset.band.arr, np.array([[2.0, -9999.0]], dtype=np.float32))
        self.assertEqual(capture["call_sizes"], [1, 1])

    def test_scipy_tin_fills_inside_hull_nan_with_nearest(self):
        converter = KML_TO_IA.KmlToIaConverter(
            "dummy.kml", "dummy.tif", interp_method="scipy_tin", resolution=1.0, chunk_size=1
        )
        converter._n_cols = 2
        converter._n_rows = 1
        converter._x_min = 0.0
        converter._y_max = 1.0
        converter._res_lon = 1.0
        converter._res_lat = 1.0
        converter._geo_transform = (0, 1, 0, 0, 0, -1)
        converter._utm_srs = types.SimpleNamespace(ExportToWkt=lambda: "WKT")
        converter._ensure_file_writable = lambda _: None

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

            def FlushCache(self):
                return None

        class _FakeDriver:
            def __init__(self):
                self.dataset = _FakeDataset()

            def Create(self, *_args, **_kwargs):
                return self.dataset

        class _FakeHullTri:
            def __init__(self, _points):
                pass

            def find_simplex(self, pts):
                return np.where(pts[:, 0] < 1.0, 0, -1).astype(np.int64)

        class _FakeTin:
            def __init__(self, *_args, **_kwargs):
                self.tri = None

            def __call__(self, pts):
                return np.full(pts.shape[0], np.nan, dtype=np.float64)

        class _FakeNN:
            def __init__(self, *_args, **_kwargs):
                pass

            def __call__(self, pts):
                return np.full(pts.shape[0], 9.0, dtype=np.float64)

        fake_driver = _FakeDriver()
        fake_gdal = types.SimpleNamespace(
            GDT_Float32=6,
            GetDriverByName=lambda _name: fake_driver,
        )

        with patch.object(KML_TO_IA, "_HAS_SCIPY", True), \
                patch.object(KML_TO_IA, "_CloughTocher2DInterpolator", _FakeTin, create=True), \
                patch.object(KML_TO_IA, "_NearestNDInterpolator", _FakeNN, create=True), \
                patch("scipy.spatial.Delaunay", _FakeHullTri), \
                patch.object(
                    KML_TO_IA.KmlToIaConverter,
                    "_prepare_scipy_tin_breakline_support",
                    return_value=(
                        np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]], dtype=np.float64),
                        np.array([1.0, 10.0, 5.0], dtype=np.float64),
                        {},
                        0,
                        0,
                    ),
                ), \
                patch.object(KML_TO_IA, "gdal", fake_gdal):
            converter._run_scipy_tin_interpolation(
                np.array([0.0, 1.0], dtype=np.float64),
                np.array([0.0, 0.0], dtype=np.float64),
                np.array([1.0, 10.0], dtype=np.float32),
                str(Path(tempfile.gettempdir()) / "scipy_tin_inside_nn_fill_test.tif"),
            )

        np.testing.assert_array_equal(fake_driver.dataset.band.arr, np.array([[9.0, -9999.0]], dtype=np.float32))

    def test_scipy_tin_falls_back_to_linear_when_self_check_fails(self):
        converter = KML_TO_IA.KmlToIaConverter(
            "dummy.kml", "dummy.tif", interp_method="scipy_tin", resolution=1.0
        )
        converter._n_cols = 1
        converter._n_rows = 1
        converter._x_min = 0.0
        converter._y_max = 1.0
        converter._res_lon = 1.0
        converter._res_lat = 1.0
        converter._geo_transform = (0, 1, 0, 0, 0, -1)
        converter._utm_srs = types.SimpleNamespace(ExportToWkt=lambda: "WKT")
        converter._ensure_file_writable = lambda _: None

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

            def FlushCache(self):
                return None

        class _FakeDriver:
            def __init__(self):
                self.dataset = _FakeDataset()

            def Create(self, *_args, **_kwargs):
                return self.dataset

        class _FakeTri:
            def find_simplex(self, pts):
                return np.zeros(pts.shape[0], dtype=np.int64)

        capture = {"linear_builds": 0}

        class _FakeTin:
            def __init__(self, *_args, **_kwargs):
                self.tri = _FakeTri()

            def __call__(self, _pts):
                raise RuntimeError("self-check failed")

        class _FakeLinear:
            def __init__(self, *_args, **_kwargs):
                capture["linear_builds"] += 1
                self.tri = _FakeTri()

            def __call__(self, pts):
                return np.full(pts.shape[0], 2.5, dtype=np.float64)

        class _FakeNN:
            def __init__(self, *_args, **_kwargs):
                pass

            def __call__(self, pts):
                return np.full(pts.shape[0], 2.5, dtype=np.float64)

        fake_driver = _FakeDriver()
        fake_gdal = types.SimpleNamespace(
            GDT_Float32=6,
            GetDriverByName=lambda _name: fake_driver,
        )

        with patch.object(KML_TO_IA, "_HAS_SCIPY", True), \
                patch.object(KML_TO_IA, "_CloughTocher2DInterpolator", _FakeTin, create=True), \
                patch.object(KML_TO_IA, "_LinearNDInterpolator", _FakeLinear, create=True), \
                patch.object(KML_TO_IA, "_NearestNDInterpolator", _FakeNN, create=True), \
                patch.object(
                    KML_TO_IA.KmlToIaConverter,
                    "_prepare_scipy_tin_breakline_support",
                    return_value=(
                        np.array([[0.0, 0.0], [1.0, 0.0]], dtype=np.float64),
                        np.array([1.0, 3.0], dtype=np.float64),
                        {},
                        0,
                        0,
                    ),
                ), \
                patch.object(KML_TO_IA, "gdal", fake_gdal):
            converter._run_scipy_tin_interpolation(
                np.array([0.0, 1.0], dtype=np.float64),
                np.array([0.0, 0.0], dtype=np.float64),
                np.array([1.0, 3.0], dtype=np.float32),
                str(Path(tempfile.gettempdir()) / "scipy_tin_self_check_fallback_test.tif"),
            )

        self.assertEqual(capture["linear_builds"], 1)
        self.assertAlmostEqual(float(fake_driver.dataset.band.arr[0, 0]), 2.5, places=6)

    def test_points_in_convex_hull_accepts_cw_vertices(self):
        points = np.array(
            [[0.5, 0.5], [1.5, 0.5], [0.0, 0.0]],
            dtype=np.float64,
        )
        hull_cw = np.array(
            [[0.0, 0.0], [0.0, 1.0], [1.0, 1.0], [1.0, 0.0]],
            dtype=np.float64,
        )
        inside = KML_TO_IA.KmlToIaConverter._points_in_convex_hull(points, hull_cw, eps=1e-9)
        np.testing.assert_array_equal(inside, np.array([True, False, True], dtype=bool))

    def test_points_in_convex_hull_batches_large_inputs(self):
        # Slightly above the implementation batch threshold to exercise auto-batching path.
        n_points = 2_000_100
        points = np.empty((n_points, 2), dtype=np.float64)
        points[:, 0] = 0.25
        points[:, 1] = 0.25
        points[-1] = np.array([1.5, 0.25], dtype=np.float64)
        hull_ccw = np.array(
            [[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]],
            dtype=np.float64,
        )
        inside = KML_TO_IA.KmlToIaConverter._points_in_convex_hull(points, hull_ccw, eps=1e-9)
        self.assertEqual(inside.shape[0], n_points)
        self.assertTrue(bool(np.all(inside[:-1])))
        self.assertFalse(bool(inside[-1]))

    def test_simplify_convex_polygon_removes_collinear_vertices(self):
        hull = np.array(
            [
                [0.0, 0.0],
                [1.0, 0.0],
                [2.0, 0.0],
                [3.0, 0.0],
                [3.0, 2.0],
                [0.0, 2.0],
            ],
            dtype=np.float64,
        )
        simplified = KML_TO_IA.KmlToIaConverter._simplify_convex_polygon(hull, collinear_tol=0.5)
        self.assertEqual(simplified.shape[0], 4)
        np.testing.assert_allclose(
            simplified,
            np.array(
                [
                    [0.0, 0.0],
                    [3.0, 0.0],
                    [3.0, 2.0],
                    [0.0, 2.0],
                ],
                dtype=np.float64,
            ),
            atol=1e-9,
        )

    def test_simplify_convex_polygon_handles_foldback_ac_overlap(self):
        hull = np.array(
            [
                [0.0, 0.0],
                [1.0, 0.0],
                [0.0, 0.0],
                [0.0, 1.0],
                [-1.0, 0.0],
            ],
            dtype=np.float64,
        )
        simplified = KML_TO_IA.KmlToIaConverter._simplify_convex_polygon(hull, collinear_tol=0.01)
        self.assertGreaterEqual(simplified.shape[0], 3)
        np.testing.assert_allclose(
            simplified,
            np.array(
                [
                    [0.0, 0.0],
                    [0.0, 1.0],
                    [-1.0, 0.0],
                ],
                dtype=np.float64,
            ),
            atol=1e-9,
        )


if __name__ == "__main__":
    unittest.main()
