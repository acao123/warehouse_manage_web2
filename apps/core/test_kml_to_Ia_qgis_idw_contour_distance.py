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


class _FakeTree:
    def __init__(self, pts: np.ndarray):
        self.pts = np.asarray(pts, dtype=np.float64)

    def query(self, pts_query, k=1):
        pts_query = np.asarray(pts_query, dtype=np.float64)
        d = np.sqrt(((pts_query[:, None, :] - self.pts[None, :, :]) ** 2).sum(axis=2))
        idx = np.argmin(d, axis=1)
        d_min = d[np.arange(d.shape[0]), idx]
        if k == 1:
            return d_min, idx
        order = np.argsort(d, axis=1)[:, :k]
        d_sorted = np.take_along_axis(d, order, axis=1)
        return d_sorted, order


class ArcgisContourDistanceIdwTests(unittest.TestCase):
    def _make_converter(self, n_cols=5, n_rows=1, x_min=0.0, y_max=0.5):
        converter = KML_TO_IA.KmlToIaConverter(
            kml_path="dummy.kml",
            ia_output_path="dummy.tif",
            interp_method="qgis_idw",
            qgis_idw_power=2.0,
            chunk_size=1,
            max_interp_workers=1,
            arcgis_idw_smooth=False,
        )
        converter._n_cols = n_cols
        converter._n_rows = n_rows
        converter._x_min = x_min
        converter._y_max = y_max
        converter._res_lon = 1.0
        converter._res_lat = 1.0
        converter._geo_transform = (x_min, 1, 0, y_max, 0, -1)
        converter._utm_srs = types.SimpleNamespace(ExportToWkt=lambda: "WKT")
        converter._ensure_file_writable = lambda _: None
        return converter

    def _run_and_get_array(self, converter, x_arr, y_arr, values):
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

        fake_driver = _FakeDriver()
        fake_gdal = types.SimpleNamespace(GDT_Float32=6, GetDriverByName=lambda _name: fake_driver)

        with patch.object(KML_TO_IA, "_HAS_SCIPY", True), \
             patch.object(KML_TO_IA, "_cKDTree", _FakeTree, create=True), \
             patch.object(KML_TO_IA, "gdal", fake_gdal):
            converter._run_arcgis_idw_interpolation(
                np.asarray(x_arr, dtype=np.float64),
                np.asarray(y_arr, dtype=np.float64),
                np.asarray(values, dtype=np.float32),
                "/tmp/arcgis_idw_contour_distance_test.tif",
            )
        return fake_driver.dataset.band.arr

    def test_contour_distance_idw_is_smooth_monotonic_between_contours(self):
        converter = self._make_converter(n_cols=5, n_rows=1, x_min=0.0, y_max=0.5)
        arr = self._run_and_get_array(
            converter,
            x_arr=[0.0, 0.0, 5.0, 5.0],
            y_arr=[-1.0, 1.0, -1.0, 1.0],
            values=[1.0, 1.0, 5.0, 5.0],
        )[0]
        self.assertTrue(np.all(arr[:-1] <= arr[1:]))
        self.assertGreaterEqual(len(np.unique(np.round(arr, 6))), 3)
        self.assertGreater(float(np.ptp(arr)), 0.0)
        self.assertGreaterEqual(float(arr.min()), 1.0)
        self.assertLessEqual(float(arr.max()), 5.0)

    def test_contour_distance_idw_sets_inner_core_to_max_without_overshoot(self):
        converter = self._make_converter(n_cols=1, n_rows=1, x_min=-0.5, y_max=0.5)
        arr = self._run_and_get_array(
            converter,
            x_arr=[1.0, -1.0, 0.0, 0.0, 2.0, -2.0, 0.0, 0.0, 3.0, -3.0, 0.0, 0.0],
            y_arr=[0.0, 0.0, 1.0, -1.0, 0.0, 0.0, 2.0, -2.0, 0.0, 0.0, 3.0, -3.0],
            values=[10.0, 10.0, 10.0, 10.0, 6.0, 6.0, 6.0, 6.0, 2.0, 2.0, 2.0, 2.0],
        )
        self.assertAlmostEqual(float(arr[0, 0]), 10.0, places=6)
        self.assertGreaterEqual(float(arr[0, 0]), 2.0)
        self.assertLessEqual(float(arr[0, 0]), 10.0)


if __name__ == "__main__":
    unittest.main()
