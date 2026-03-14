#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""详细检查 QGIS QgsGridFileWriter API"""

from qgis.analysis import QgsGridFileWriter, QgsIDWInterpolator, QgsInterpolator
from qgis.core import QgsRectangle, QgsVectorLayer, QgsFeature, QgsGeometry, QgsPointXY, QgsField
from PyQt5.QtCore import QMetaType

# 创建一个简单的测试图层
layer = QgsVectorLayer("Point?crs=EPSG:32648", "test", "memory")
provider = layer.dataProvider()
provider.addAttributes([QgsField("value", QMetaType.Type.Double)])
layer.updateFields()

# 添加一个测试点
feat = QgsFeature()
feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(350000, 3770000)))
feat.setAttributes([1.0])
provider.addFeatures([feat])

# 创建插值器
layer_data = QgsInterpolator.LayerData()
layer_data.source = layer
layer_data.valueSource = QgsInterpolator.ValueSource.ValueAttribute
layer_data.interpolationAttribute = 0
layer_data.sourceType = QgsInterpolator.SourceType.SourcePoints

interpolator = QgsIDWInterpolator([layer_data])
extent = QgsRectangle(340000, 3760000, 360000, 3780000)

# 测试不同的构造函数调用方式
import tempfile
import os

tmp_dir = tempfile.gettempdir()
test_path = os.path.join(tmp_dir, "test_qgis_writer.asc")

print("测试不同的 QgsGridFileWriter 构造函数调用方式:\n")

# 方式1: 5参数 (interpolator, path, extent, ncols, nrows)
try:
    writer = QgsGridFileWriter(interpolator, test_path, extent, 10, 10)
    print("✅ 方式1成功: QgsGridFileWriter(interpolator, path, extent, ncols, nrows)")
    result = writer.writeFile()
    print(f"   writeFile() 返回: {result}")
    if os.path.exists(test_path):
        os.remove(test_path)
        print(f"   文件创建成功并已删除")
except Exception as e:
    print(f"❌ 方式1失败: {e}")

# 方式2: 7参数 (interpolator, path, extent, ncols, nrows, cellsizeX, cellsizeY)
try:
    writer = QgsGridFileWriter(interpolator, test_path, extent, 10, 10, 100.0, 100.0)
    print("\n✅ 方式2成功: QgsGridFileWriter(interpolator, path, extent, ncols, nrows, cellX, cellY)")
    result = writer.writeFile()
    print(f"   writeFile() 返回: {result}")
    if os.path.exists(test_path):
        os.remove(test_path)
        print(f"   文件创建成功并已删除")
except Exception as e:
    print(f"\n❌ 方式2失败: {e}")

# 方式3: 使用 setCellSizeX/Y 方法
try:
    writer = QgsGridFileWriter(interpolator, test_path, extent, 10, 10)
    if hasattr(writer, 'setCellSizeX'):
        writer.setCellSizeX(100.0)
        writer.setCellSizeY(100.0)
        print("\n✅ 方式3: 存在 setCellSizeX/Y 方法")
    else:
        print("\n❌ 方式3: 不存在 setCellSizeX/Y 方法")
except Exception as e:
    print(f"\n❌ 方式3失败: {e}")

# 列出 QgsGridFileWriter 的所有方法
print("\n" + "="*50)
print("QgsGridFileWriter 可用方法和属性:")
print("="*50)
for attr in dir(QgsGridFileWriter):
    if not attr.startswith('_'):
        print(f"  {attr}")