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
                "/tmp/arcgis_idw_smooth_test.tif",
            )

        arr = fake_driver.dataset.band.arr
        self.assertEqual(arr.shape, (2, 2))
        self.assertGreaterEqual(float(arr.min()), 1.0)
        self.assertLessEqual(float(arr.max()), 3.0)


if __name__ == "__main__":
    unittest.main()
