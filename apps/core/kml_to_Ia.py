# -*- coding: utf-8 -*-
"""
KML格式PGA等值线转换为Ia栅格文件工具
基于QGIS 3.40.15 Python环境

功能：
    1. 解析KML文件获取PGA等值线（LineString）
    2. 将PGA值(g单位)转换为实际加速度值(m/s²)
    3. 根据公式 log10(Ia) = 0.797 + 1.837 * log10(PGA) 计算Ia(阿里亚斯强度)值
    4. 使用插值算法进行插值计算（支持IDW、TIN、Kriging）
    5. ���选输出PGA.tif和Ia.tif
    6. 分辨率固定为30米×30米

作者: AI Assistant
日期: 2026-03-14
版本: 1.1
"""

import os
import gc
import time
import math
import traceback
from xml.etree import ElementTree as ET
from typing import List, Tuple, Dict, Optional, Union
from dataclasses import dataclass, field

# QGIS核心库导入
from qgis.core import (
    QgsApplication,
    QgsProject,
    QgsVectorLayer,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsField,
    QgsFields,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsRectangle,
    QgsRasterFileWriter,
    QgsRasterLayer,
    QgsVectorFileWriter,
    QgsWkbTypes,
    Qgis
)

# QGIS分析库导入 - 注意大小写: QgsTinInterpolator (不是 QgsTINInterpolator)
from qgis.analysis import (
    QgsInterpolator,
    QgsIDWInterpolator,  # 注意: 小写 Idw
    QgsTinInterpolator,  # 注意: 小写 Tin
    QgsGridFileWriter
)

from qgis.PyQt.QtCore import QVariant

# 尝试导入处理模块（用于克里金插值）
try:
    import processing
    from processing.core.Processing import Processing

    PROCESSING_AVAILABLE = True
except ImportError:
    PROCESSING_AVAILABLE = False


# ============================================================================
#                              参数配置类
# ============================================================================

@dataclass
class IDWParams:
    """
    IDW（反距离加权）插值算法参数配置

    属性说明:
        coefficient (float): 距离权重系数（幂次）
            - 默认值: 2.0
            - 取值范围: 通常 0.5 - 5.0
            - 说明: 控制距离对权重的影响程度，权重 = 1 / (距离 ^ coefficient)
            - 值越大，近距离点的权重越大，远距离点权重衰减越快
            - 优点:
                * coefficient=1: 距离影响较小，结果更平滑，适合渐变数据
                * coefficient=2: 标准设置，平衡局部和全局特征（推荐）
                * coefficient=3+: 强调局部特征，适合突变明显的数据
            - 缺点:
                * 值过大(>3)可能导致"牛眼效应"（数据点周围出现圆形等值线）
                * 值过小(<1)可能过度平滑，丢失局部细节
    """
    coefficient: float = 2.0

    def validate(self) -> None:
        """验证参数有效性"""
        if self.coefficient <= 0:
            raise ValueError(f"coefficient必须大于0，当前值: {self.coefficient}")
        if self.coefficient > 10:
            print(f"[WARNING] coefficient值({self.coefficient})较大，可能产生牛眼效应")


@dataclass
class TINParams:
    """
    TIN（不规则三角网）插值算法参数配置

    属性说明:
        method (int): 插值方法
            - 默认值: 0
            - 取值:
                * 0 - 线性插值 (Linear)
                    说明: 在三角形内使用线性函数（平面）进行插值
                    优点: 计算速度快；保留原始数据点的精确值；结果连续
                    缺点: 一阶导数不连续；等值线可能出现折角；三角形边界处不平滑
                    适用: 快速预览；数据点密集；对平滑度要求不高

                * 1 - 克劳德-杜尚插值 (Clough-Tocher)
                    说明: 使用三次多项式插值，将每个三角形细分为3个子三角形
                    优点: 结果更平滑；等值线更圆润；一阶导数连续(C1连续)
                    缺点: 计算复杂度更高(约3倍)；内存使用更多；处理速度较慢
                    适用: 最终出图；需要平滑��值线；数据点稀疏
    """
    method: int = 0

    def validate(self) -> None:
        """验证参数有效性"""
        if self.method not in [0, 1]:
            raise ValueError(f"method必须为0(线性)或1(克劳德-杜尚)，当前值: {self.method}")

    @property
    def method_name(self) -> str:
        """获取方法名称"""
        return "线性插值(Linear)" if self.method == 0 else "克劳德-杜尚插值(Clough-Tocher)"


@dataclass
class KrigingParams:
    """
    克里金（Kriging）插值算法参数配置

    克里金是地统计学中的最优线性无偏估计方法(BLUE)，考虑数据的空间自相关性。

    属性说明:
        variogram_model (str): 变异函数模型类型
            - 默认值: 'Spherical'
            - 可选值:
                * 'Spherical' - 球状模型（最常用，推荐）
                    数学形式: γ(h) = C0 + C*[1.5*(h/a) - 0.5*(h/a)³], h≤a; γ(h)=C0+C, h>a
                    特点: 在变程处达到基台，有明确的影响范围
                    适用: 大多数地学数据，是最通用的选择
                    优点: 适应性强，计算稳定，物理意义明确

                * 'Exponential' - 指数模型
                    数学形式: γ(h) = C0 + C*[1 - exp(-3h/a)]
                    特点: 渐近趋近基台，理论变程为无穷大，实际变程约为a
                    适用: 空间相关性缓慢衰减的数据
                    优点: 在变程附近过渡更平滑
                    缺点: 没有明确的影响范围边界

                * 'Gaussian' - 高斯模型
                    数学形式: γ(h) = C0 + C*[1 - exp(-(3h/a)²)]
                    特点: 在原点附近曲率为0，抛物线起始
                    适用: 变化非常平缓、连续性极好的数据
                    优点: 产生最平滑的插值结果
                    缺点: 可能导致数值不稳定；对噪声敏感

                * 'Linear' - 线性模型
                    数学形式: γ(h) = C0 + b*h
                    特点: 最简单的模型，没有基台
                    适用: 数据范围小于空间相关范围时
                    优点: 计算快速，参数少
                    缺点: 不适合有明显空间结构的数据

        nugget (float): 块金效应值（C0）
            - 默认值: 0.0
            - 取值范围: >= 0
            - 说明: 表示微尺度变异（小于采样间距的变异）或测量误差
            - 当nugget=0时，插值结果精确通过数据点（精确插值）
            - 当nugget>0时，插值结果不一定通过数据点（平滑插值）
            - 建议: 通常设为基台值(sill)的5%-20%，或根据测量误差估计
            - 优点: 考虑测量误差，避免过拟合
            - 缺点: 设置过大会过度平滑

        sill (float): 基台值（C0 + C，总方差）
            - 默认值: 1.0
            - 取值范围: > nugget
            - 说明: 变异函数的最大值，表示数据在大距离上的总方差
            - 可通过计算数据方差来估计
            - 优点: 控制插值的整体变异程度

        range_val (float): 变程值（a，单位：度）
            - 默认值: 1.0
            - 取值范围: > 0
            - 说明: 空间相关性消失的距离，超过此距离的数据点被认为不相关
            - 建议: 根据数据的空间分布特征设置，通常为数据范围的1/3到1/2
            - 优点: 控制插值的平滑程度和影响范围
            - 注意: 单位是度（EPSG:4326），1度约111公里

        search_radius (float): 搜索半径（单位：度）
            - 默认值: -1（使用所有点）
            - 取值范围: > 0 或 -1
            - 说明: 限制参与插值计算的点的搜索范围
            - 当设为-1时，使用所有数据点
            - 建议: 设为变程的2-3倍，或数据范围的1/2
            - 优点: 减少计算量，提高效率；避免远距离噪声影响
            - 缺点: 设置过小可能导致某些区域无足够点进行插值

        min_points (int): 最小搜索点数
            - 默认值: 4
            - 取值范围: >= 1
            - 说明: 参与插值的最小数据点数量
            - 建议: 通常设为3-5
            - 优点: 确保插值的统计可靠性

        max_points (int): 最大搜索点数
            - 默认值: 12
            - 取值范围: >= min_points
            - 说明: 参与插值的最大数据点数量
            - 建议: 通常设为8-20
            - 优点: 控制计算量，提高效率
            - 缺点: 设置过小可能遗漏重要信息
    """
    variogram_model: str = 'Spherical'
    nugget: float = 0.0
    sill: float = 1.0
    range_val: float = 1.0
    search_radius: float = -1.0
    min_points: int = 4
    max_points: int = 12

    def validate(self) -> None:
        """验证参数有效性"""
        valid_models = ['Spherical', 'Exponential', 'Gaussian', 'Linear']
        if self.variogram_model not in valid_models:
            raise ValueError(
                f"variogram_model必须为 {valid_models} 之一，"
                f"当前值: {self.variogram_model}"
            )
        if self.nugget < 0:
            raise ValueError(f"nugget必须 >= 0，当前值: {self.nugget}")
        if self.sill <= self.nugget:
            raise ValueError(f"sill必须 > nugget，当前sill={self.sill}, nugget={self.nugget}")
        if self.range_val <= 0:
            raise ValueError(f"range_val必须 > 0，当前值: {self.range_val}")
        if self.search_radius != -1 and self.search_radius <= 0:
            raise ValueError(f"search_radius必须 > 0 或为 -1，当前值: {self.search_radius}")
        if self.min_points < 1:
            raise ValueError(f"min_points必须 >= 1，当前值: {self.min_points}")
        if self.max_points < self.min_points:
            raise ValueError(
                f"max_points必须 >= min_points，当前max_points={self.max_points}, "
                f"min_points={self.min_points}"
            )


# ============================================================================
#                              主转换器类
# ============================================================================

class KmlToPgaIaConverter:
    """
    KML格式PGA等值线转换器

    将地震局提供的KML格式PGA等值线文件转换为PGA.tif和Ia.tif栅格文件

    特性：
        - 固定分辨率：30米×30米
        - 投影：EPSG:4326（WGS 84）
        - 最大范围：300km×300km
        - 内存限制：10GB
        - 支持IDW、TIN、Kriging三种插值算法

    属性:
        kml_file_path (str): 输入的KML文件路径
        output_dir (str): 输出目录路径
        max_extent_km (float): 最大插值范围（公里），默认300公里
        max_memory_gb (float): 最大内存使用限制（GB），默认10GB

    使用示例:
        >>> converter = KmlToPgaIaConverter(
        ...     kml_file_path="path/to/pga.kml",
        ...     output_dir="path/to/output/"
        ... )
        >>> # 使用IDW插值
        >>> idw_params = IDWParams(coefficient=2.0)
        >>> output_files = converter.convert(
        ...     method='idw',
        ...     params=idw_params,
        ...     output_pga=True,
        ...     output_ia=True
        ... )
    """

    # ========================= 常量定义 =========================

    # 重力加速度常量 (m/s²)
    GRAVITY: float = 9.8

    # 阿里亚斯强度计算公式常量
    # log10(Ia) = IA_CONST_A + IA_CONST_B * log10(PGA)
    IA_CONST_A: float = 0.797
    IA_CONST_B: float = 1.837

    # 固定分辨率：30米
    RESOLUTION: float = 30.0

    # EPSG:4326下1度约等于的公里数（赤道附近）
    DEGREE_TO_KM: float = 111.0

    # KML命名空间
    KML_NAMESPACE: Dict[str, str] = {'kml': 'http://www.opengis.net/kml/2.2'}

    def __init__(
            self,
            kml_file_path: str,
            output_dir: str,
            max_extent_km: float = 300.0,
            max_memory_gb: float = 10.0
    ):
        """
        初始化转换器

        参数:
            kml_file_path (str): 输入的KML文件路径
            output_dir (str): 输出目录路径
            max_extent_km (float): 最大插值范围（公里），默认300公里
            max_memory_gb (float): 最大内存使用限制（GB），默认10GB

        异常:
            FileNotFoundError: 当KML文件不存在时
            ValueError: 当参数值无效时
        """
        # 验证KML文件存在
        if not os.path.exists(kml_file_path):
            raise FileNotFoundError(f"KML文件不存在: {kml_file_path}")

        # 验证参数
        if max_extent_km <= 0:
            raise ValueError(f"最大插值范围必须大于0，当前值: {max_extent_km}")
        if max_memory_gb <= 0:
            raise ValueError(f"最大内存限制必须大于0，当前值: {max_memory_gb}")

        self.kml_file_path = kml_file_path
        self.output_dir = output_dir
        self.max_extent_km = max_extent_km
        self.max_memory_gb = max_memory_gb

        # 设置坐标参考系统为WGS84 (EPSG:4326)
        self.crs = QgsCoordinateReferenceSystem("EPSG:4326")

        # 存储解析后的点数据: [(经度, 纬度, PGA_m/s², Ia)]
        self._points_data: List[Tuple[float, float, float, float]] = []

        # 临时图层引用，用于资源清理
        self._temp_layers: List[QgsVectorLayer] = []

        # 临时文件路径列表，用于清理
        self._temp_files: List[str] = []

        # 确保输出目录存在
        os.makedirs(output_dir, exist_ok=True)

        print(f"[INFO] 转换器初始化完成")
        print(f"  - KML文件: {kml_file_path}")
        print(f"  - 输出目录: {output_dir}")
        print(f"  - 固定分辨率: {self.RESOLUTION}米 × {self.RESOLUTION}米")
        print(f"  - 最大范围: {max_extent_km}km × {max_extent_km}km")
        print(f"  - 内存限制: {max_memory_gb}GB")

    # ========================= KML解析方法 =========================

    def parse_kml(self) -> List[Tuple[float, float, float]]:
        """
        解析KML文件，提取PGA等值线坐标和对应的PGA值

        KML文件格式示例:
            <Placemark>
                <name>0.01g</name>
                <LineString>
                    <coordinates>102.89,34.29,0 102.88,34.28,0</coordinates>
                </LineString>
            </Placemark>

        返回:
            List[Tuple[float, float, float]]: 包含(经度, 纬度, PGA值(m/s²))的列表

        异常:
            ET.ParseError: 当XML解析失败时
            ValueError: 当KML格式不正确时
        """
        print(f"\n[INFO] 开始解析KML文件: {os.path.basename(self.kml_file_path)}")

        try:
            tree = ET.parse(self.kml_file_path)
            root = tree.getroot()
        except ET.ParseError as e:
            raise ET.ParseError(f"KML文件XML解析失败: {e}")

        points = []
        placemark_count = 0
        skipped_count = 0

        # 查找所有Placemark元素（同时尝试有命名空间和无命名空间的情况）
        placemarks = root.findall('.//kml:Placemark', self.KML_NAMESPACE)
        if not placemarks:
            # 尝试不带命名空间的查找
            placemarks = root.findall('.//Placemark')

        for placemark in placemarks:
            placemark_count += 1

            # 获取name元素，格式如 "0.01g"
            name_elem = placemark.find('kml:name', self.KML_NAMESPACE)
            if name_elem is None:
                name_elem = placemark.find('name')

            if name_elem is None or name_elem.text is None:
                skipped_count += 1
                continue

            # 解析PGA值（单位：g）
            name_text = name_elem.text.strip()
            try:
                # 移除'g'后缀并转换为浮点数
                pga_g = float(name_text.lower().replace('g', '').strip())
            except ValueError:
                print(f"  [WARNING] 无法解析PGA值: '{name_text}'，跳过")
                skipped_count += 1
                continue

            # 将PGA从g单位转换为m/s²
            pga_ms2 = pga_g * self.GRAVITY

            # 获取LineString坐标
            linestring = placemark.find('.//kml:LineString/kml:coordinates', self.KML_NAMESPACE)
            if linestring is None:
                linestring = placemark.find('.//LineString/coordinates')

            if linestring is None or linestring.text is None:
                print(f"  [WARNING] Placemark '{name_text}'缺少坐标数据，跳过")
                skipped_count += 1
                continue

            # 解析坐标字符串
            # 格式: "lon1,lat1,alt1 lon2,lat2,alt2 ..."
            coords_text = linestring.text.strip()
            coord_pairs = coords_text.split()

            for coord_pair in coord_pairs:
                parts = coord_pair.split(',')
                if len(parts) >= 2:
                    try:
                        lon = float(parts[0])
                        lat = float(parts[1])
                        # 验证坐标范围
                        if -180 <= lon <= 180 and -90 <= lat <= 90:
                            points.append((lon, lat, pga_ms2))
                        else:
                            print(f"  [WARNING] 坐标超出范围: ({lon}, {lat})")
                    except ValueError:
                        continue

        print(f"  - 处理Placemark数: {placemark_count}")
        print(f"  - 跳过数: {skipped_count}")
        print(f"  - 提取坐标点数: {len(points)}")

        if not points:
            raise ValueError("KML文件中未找到有效的坐标数据")

        # 统计PGA值分布
        pga_values = sorted(set([p[2] for p in points]))
        print(f"  - PGA等级数: {len(pga_values)}")
        print(f"  - PGA范围: {min(pga_values) / self.GRAVITY:.4f}g - {max(pga_values) / self.GRAVITY:.4f}g")

        return points

    # ========================= 计算方法 =========================

    def calculate_ia(self, pga_ms2: float) -> float:
        """
        根据PGA计算阿里亚斯强度(Ia)

        公式: log10(Ia) = 0.797 + 1.837 * log10(PGA)

        其中:
            - Ia: 阿里亚斯强度，单位 m/s
            - PGA: 峰值地面加速度，单位 m/s²

        参数:
            pga_ms2 (float): PGA值，单位m/s²

        返回:
            float: 阿里亚斯强度(Ia)值，单位m/s

        注意:
            当PGA小于等于0时，返回一个极小值(1e-10)以避免log计算错误
        """
        if pga_ms2 <= 0:
            return 1e-10

        # log10(Ia) = 0.797 + 1.837 * log10(PGA)
        log_ia = self.IA_CONST_A + self.IA_CONST_B * math.log10(pga_ms2)
        ia = math.pow(10, log_ia)

        return ia

    def prepare_point_data(self) -> None:
        """
        准备插值用的点数据

        解析KML文件并计算每个点的PGA和Ia值

        处理流程:
            1. 调用parse_kml()解析KML文件
            2. 对每个坐标点计算Ia值
            3. 存储到内部数据结构
        """
        print("\n[INFO] 准备点数据...")

        # 解析KML获取原始点
        raw_points = self.parse_kml()

        # 计算Ia值并存储
        self._points_data = []
        for lon, lat, pga_ms2 in raw_points:
            ia = self.calculate_ia(pga_ms2)
            self._points_data.append((lon, lat, pga_ms2, ia))

        print(f"[INFO] 点数据准备完成，共 {len(self._points_data)} 个点")

        # 打印统计信息
        pga_values = [p[2] for p in self._points_data]
        ia_values = [p[3] for p in self._points_data]

        print(f"  - PGA范围: {min(pga_values):.4f} - {max(pga_values):.4f} m/s²")
        print(f"  - Ia范围: {min(ia_values):.6f} - {max(ia_values):.6f} m/s")

    # ========================= 图层创建方法 =========================

    def create_point_layer(self, value_type: str = 'pga') -> QgsVectorLayer:
        """
        创建内存中的点图层用于插值

        参数:
            value_type (str): 值类型，'pga'或'ia'

        返回:
            QgsVectorLayer: 包含点数据的矢量图层

        异常:
            RuntimeError: 当创建图层失败时
            ValueError: 当value_type无效时
        """
        if value_type not in ['pga', 'ia']:
            raise ValueError(f"value_type必须为'pga'或'ia'，当前值: {value_type}")

        print(f"\n[INFO] 创建{value_type.upper()}点图层...")

        # 创建内存图层
        # 格式: "Point?crs=EPSG:4326&field=fieldname:fieldtype"
        layer = QgsVectorLayer(
            f"Point?crs=EPSG:4326&field={value_type}:double",
            f"points_{value_type}",
            "memory"
        )

        if not layer.isValid():
            raise RuntimeError(f"创建内存图层失败: points_{value_type}")

        # 获取数据提供者
        provider = layer.dataProvider()

        # 添加要素
        features = []
        # pga在索引2，ia在索引3
        value_index = 2 if value_type == 'pga' else 3

        for point_data in self._points_data:
            lon, lat = point_data[0], point_data[1]
            value = point_data[value_index]

            feature = QgsFeature()
            feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(lon, lat)))
            feature.setAttributes([value])
            features.append(feature)

        # 批量添加要素
        success, _ = provider.addFeatures(features)
        if not success:
            raise RuntimeError("添加要素到图层失败")

        layer.updateExtents()

        # 保存临时图层引用
        self._temp_layers.append(layer)

        print(f"  - 要素数量: {layer.featureCount()}")
        print(f"  - 范围: {layer.extent().toString()}")

        return layer

    # ========================= 范围和尺寸计算方法 =========================

    def calculate_extent(self, layer: QgsVectorLayer) -> QgsRectangle:
        """
        计算插值范围，确保不超过最大范围限制

        参数:
            layer (QgsVectorLayer): 输入点图层

        返回:
            QgsRectangle: 计算后的插值范围
        """
        extent = layer.extent()

        # 添加10%的缓冲区，确保边缘点也能正确插值
        buffer_ratio = 0.1
        width = extent.width()
        height = extent.height()

        buffered_extent = QgsRectangle(
            extent.xMinimum() - width * buffer_ratio,
            extent.yMinimum() - height * buffer_ratio,
            extent.xMaximum() + width * buffer_ratio,
            extent.yMaximum() + height * buffer_ratio
        )

        # 计算当前范围的大小（公里）
        width_km = buffered_extent.width() * self.DEGREE_TO_KM
        height_km = buffered_extent.height() * self.DEGREE_TO_KM

        print(f"\n[INFO] 计算插值范围")
        print(f"  - 原始范围: {width_km:.2f} km × {height_km:.2f} km")

        # 检查是否超过最大范围
        if width_km > self.max_extent_km or height_km > self.max_extent_km:
            print(f"  [WARNING] 范围超过最大限制 {self.max_extent_km} km")

            # 计算中心点
            center_x = buffered_extent.center().x()
            center_y = buffered_extent.center().y()

            # 计算新的范围（度）
            half_extent_deg = (self.max_extent_km / 2) / self.DEGREE_TO_KM

            buffered_extent = QgsRectangle(
                center_x - half_extent_deg,
                center_y - half_extent_deg,
                center_x + half_extent_deg,
                center_y + half_extent_deg
            )

            print(f"  - 调整后范围: {self.max_extent_km} km × {self.max_extent_km} km")

        return buffered_extent

    def calculate_grid_dimensions(self, extent: QgsRectangle) -> Tuple[int, int]:
        """
        根据固定���辨率(30米)计算栅格维度

        同时检查内存使用是否超限，如超限则抛出异常

        参数:
            extent (QgsRectangle): 插值范围（度）

        返回:
            Tuple[int, int]: (列数, 行数)

        异常:
            MemoryError: 当估算内存超过限制时
        """
        # 计算范围中心点的纬度，用于更精确的距离计算
        center_lat = extent.center().y()

        # 在该纬度下，1度经度对应的公里数
        lon_km_per_degree = self.DEGREE_TO_KM * math.cos(math.radians(center_lat))
        lat_km_per_degree = self.DEGREE_TO_KM

        # 计算范围的实际距离（米）
        width_m = extent.width() * lon_km_per_degree * 1000
        height_m = extent.height() * lat_km_per_degree * 1000

        # 计算栅格尺寸（固定30米分辨率）
        ncols = int(math.ceil(width_m / self.RESOLUTION))
        nrows = int(math.ceil(height_m / self.RESOLUTION))

        # 确保至少有1个像素
        ncols = max(1, ncols)
        nrows = max(1, nrows)

        # 估算内存使用（每像素8字节double + 处理开销约2倍）
        memory_bytes = ncols * nrows * 8 * 2
        memory_gb = memory_bytes / (1024 ** 3)

        print(f"\n[INFO] 栅格尺寸计算")
        print(f"  - 范围: {width_m / 1000:.2f} km × {height_m / 1000:.2f} km")
        print(f"  - 分辨率: {self.RESOLUTION} 米 × {self.RESOLUTION} 米（固定）")
        print(f"  - 栅格尺寸: {ncols} × {nrows} 像素")
        print(f"  - 估算内存: {memory_gb:.2f} GB")

        # 检查内存限制
        if memory_gb > self.max_memory_gb:
            raise MemoryError(
                f"估算内存使用 ({memory_gb:.2f} GB) 超过限制 ({self.max_memory_gb} GB)。"
                f"请减小插值范围或使用更小的数据集。"
            )

        return ncols, nrows

    # ========================= IDW插值方法 =========================

    def interpolate_idw(
            self,
            layer: QgsVectorLayer,
            output_path: str,
            params: IDWParams
    ) -> Tuple[str, float]:
        """
        使用IDW（反距离加权）算法进行插值

        IDW原理:
            未知点的值 = Σ(wi * vi) / Σ(wi)
            其中 wi = 1 / di^p
            di: 未知点到第i个已知点的距离
            vi: 第i个已知点的值
            p: 距离权重系数(coefficient)

        参数:
            layer (QgsVectorLayer): 输入点图层
            output_path (str): 输出栅格文件路径
            params (IDWParams): IDW算法参数

        返回:
            Tuple[str, float]: (输出文件路径, 插值耗时秒数)

        异常:
            RuntimeError: 当插值失败时
        """
        # 验证参数
        params.validate()

        print(f"\n{'=' * 60}")
        print(f"IDW插值")
        print(f"{'=' * 60}")
        print(f"参数:")
        print(f"  - 距离权重系数(coefficient): {params.coefficient}")

        start_time = time.time()

        try:
            # 准备插值数据源
            layer_data = QgsInterpolator.LayerData()
            layer_data.source = layer
            layer_data.valueSource = QgsInterpolator.ValueAttribute
            layer_data.interpolationAttribute = 0  # 第一个属性字段
            layer_data.sourceType = QgsInterpolator.SourcePoints

            # 创建IDW插值器 (注意: QgsIdwInterpolator 小写)
            interpolator = QgsIDWInterpolator([layer_data])
            interpolator.setDistanceCoefficient(params.coefficient)

            # 计算范围和栅格尺寸
            extent = self.calculate_extent(layer)
            ncols, nrows = self.calculate_grid_dimensions(extent)

            # 执行插值并写入文件
            print(f"\n[INFO] 开始写入栅格文件...")
            grid_writer = QgsGridFileWriter(
                interpolator,
                output_path,
                extent,
                ncols,
                nrows
            )

            result = grid_writer.writeFile()

            if result != 0:
                raise RuntimeError(f"IDW插值写入失败，错误码: {result}")

            elapsed_time = time.time() - start_time

            print(f"\n[SUCCESS] IDW插值完成")
            print(f"  - 耗时: {elapsed_time:.2f} 秒")
            print(f"  - 输出: {output_path}")

            return output_path, elapsed_time

        except Exception as e:
            print(f"\n[ERROR] IDW插值失败: {e}")
            traceback.print_exc()
            raise
        finally:
            # 强制垃圾回收释放内存
            gc.collect()

    # ========================= TIN插值方法 =========================

    def interpolate_tin(
            self,
            layer: QgsVectorLayer,
            output_path: str,
            params: TINParams
    ) -> Tuple[str, float]:
        """
        使用TIN（不规则三角网）算法进行插值

        TIN原理:
            1. 使用Delaunay三角剖分将点连接成三角网
            2. 对于每个待插值点，找到其所在的三角形
            3. 使用三角形顶点的值进行插值:
               - 线性: 平面插值
               - Clough-Tocher: 三次多项式插值

        参数:
            layer (QgsVectorLayer): 输入点图层
            output_path (str): 输出栅格文件路径
            params (TINParams): TIN算法参数

        返回:
            Tuple[str, float]: (输出文件路径, 插值耗时秒数)

        异常:
            RuntimeError: 当插值失败时
        """
        # 验证参数
        params.validate()

        print(f"\n{'=' * 60}")
        print(f"TIN插值")
        print(f"{'=' * 60}")
        print(f"参数:")
        print(f"  - 插值方法(method): {params.method} ({params.method_name})")

        start_time = time.time()

        try:
            # 准备插值数据源
            layer_data = QgsInterpolator.LayerData()
            layer_data.source = layer
            layer_data.valueSource = QgsInterpolator.ValueAttribute
            layer_data.interpolationAttribute = 0
            layer_data.sourceType = QgsInterpolator.SourcePoints

            # 创建TIN插值器 (注意: QgsTinInterpolator 小写)
            tin_method = (QgsTinInterpolator.Linear if params.method == 0
                          else QgsTinInterpolator.CloughTocher)
            interpolator = QgsTinInterpolator([layer_data], tin_method)

            # 计算范围和栅格尺寸
            extent = self.calculate_extent(layer)
            ncols, nrows = self.calculate_grid_dimensions(extent)

            # 执行插值并写入文件
            print(f"\n[INFO] 开始写入栅格文件...")
            grid_writer = QgsGridFileWriter(
                interpolator,
                output_path,
                extent,
                ncols,
                nrows
            )

            result = grid_writer.writeFile()

            if result != 0:
                raise RuntimeError(f"TIN插值写入失败，错误码: {result}")

            elapsed_time = time.time() - start_time

            print(f"\n[SUCCESS] TIN插值完成")
            print(f"  - 耗时: {elapsed_time:.2f} 秒")
            print(f"  - 输出: {output_path}")

            return output_path, elapsed_time

        except Exception as e:
            print(f"\n[ERROR] TIN插值失败: {e}")
            traceback.print_exc()
            raise
        finally:
            gc.collect()

    # ========================= Kriging插值方法 =========================

    def interpolate_kriging(
            self,
            layer: QgsVectorLayer,
            output_path: str,
            params: KrigingParams
    ) -> Tuple[str, float]:
        """
        使用克里金算法进行插值（通过SAGA Processing工具）

        克里金原理:
            克里金是最优线性无偏估计(BLUE)，通过变异函数描述空间自相关性:
            Z*(x0) = Σ(λi * Z(xi))
            其中权重λi通过最小化估计方差并满足无偏条件求解

        参数:
            layer (QgsVectorLayer): 输入点图层
            output_path (str): 输出栅格文件路径
            params (KrigingParams): 克里金算法参数

        返回:
            Tuple[str, float]: (输出文件路径, 插值耗时秒数)

        异常:
            RuntimeError: 当SAGA工具不可用或插值失败时
        """
        # 验证参数
        params.validate()

        if not PROCESSING_AVAILABLE:
            raise RuntimeError(
                "QGIS Processing模块不可用，无法执行克里金插值。"
                "请确保SAGA工具已正确安装。"
            )

        print(f"\n{'=' * 60}")
        print(f"克里金插值 (Kriging)")
        print(f"{'=' * 60}")
        print(f"参数:")
        print(f"  - 变异函数模型(variogram_model): {params.variogram_model}")
        print(f"  - 块金值(nugget): {params.nugget}")
        print(f"  - 基台值(sill): {params.sill}")
        print(f"  - 变程(range_val): {params.range_val}")
        print(f"  - 搜索半径(search_radius): {params.search_radius}")
        print(f"  - 最小搜索点数(min_points): {params.min_points}")
        print(f"  - 最大搜索点数(max_points): {params.max_points}")

        start_time = time.time()

        # 临时shapefile路径
        temp_shp = os.path.join(self.output_dir, '_temp_kriging_points.shp')
        self._temp_files.append(temp_shp)

        try:
            # 计算范围
            extent = self.calculate_extent(layer)
            ncols, nrows = self.calculate_grid_dimensions(extent)

            # 变异函数模型映射到SAGA参数
            model_map = {
                'Spherical': 1,
                'Exponential': 2,
                'Gaussian': 3,
                'Linear': 4
            }
            model_type = model_map.get(params.variogram_model, 1)

            # 将点图层保存为临时shapefile（SAGA需要文件输入）
            print(f"\n[INFO] 保存临时shapefile...")

            options = QgsVectorFileWriter.SaveVectorOptions()
            options.driverName = "ESRI Shapefile"
            options.fileEncoding = "UTF-8"

            error = QgsVectorFileWriter.writeAsVectorFormatV3(
                layer,
                temp_shp,
                QgsProject.instance().transformContext(),
                options
            )

            if error[0] != QgsVectorFileWriter.NoError:
                raise RuntimeError(f"保存临时shapefile失败: {error[1]}")

            # 计算分辨率（度）
            center_lat = extent.center().y()
            lon_km_per_degree = self.DEGREE_TO_KM * math.cos(math.radians(center_lat))
            resolution_deg = self.RESOLUTION / (lon_km_per_degree * 1000)

            # 获取字段名
            field_name = layer.fields().at(0).name()

            print(f"\n[INFO] 执行SAGA克里金插值...")
            print(f"  - 分辨率: {resolution_deg:.8f} 度 (约{self.RESOLUTION}米)")

            # 确定搜索范围设置
            search_range_mode = 0 if params.search_radius <= 0 else 1
            actual_search_radius = max(params.search_radius,
                                       extent.width()) if params.search_radius > 0 else extent.width()

            # 执行SAGA普通克里金插值
            result = processing.run(
                "saga:ordinarykriging",
                {
                    'POINTS': temp_shp,
                    'FIELD': field_name,
                    'TARGET_USER_XMIN': extent.xMinimum(),
                    'TARGET_USER_XMAX': extent.xMaximum(),
                    'TARGET_USER_YMIN': extent.yMinimum(),
                    'TARGET_USER_YMAX': extent.yMaximum(),
                    'TARGET_USER_SIZE': resolution_deg,
                    'TARGET_USER_FITS': 0,  # 适应范围
                    'SEARCH_RANGE': search_range_mode,
                    'SEARCH_RADIUS': actual_search_radius,
                    'SEARCH_POINTS_ALL': 0,  # 使用点数限制
                    'SEARCH_POINTS_MIN': params.min_points,
                    'SEARCH_POINTS_MAX': params.max_points,
                    'SEARCH_DIRECTION': 0,  # 所有方向
                    'MODEL': model_type,
                    'BLOCK': 0,  # 点克里金
                    'DBLOCK': 1,
                    'VAR_MAXDIST': -1,  # 自动
                    'VAR_NCLASSES': 100,
                    'VAR_NSKIP': 1,
                    'VAR_MODEL': f'{params.nugget} + {params.sill - params.nugget} * '
                                 f'{"Sph" if params.variogram_model == "Spherical" else params.variogram_model[:3]}'
                                 f'({params.range_val})',
                    'PREDICTION': output_path,
                    'VARIANCE': 'TEMPORARY_OUTPUT'
                }
            )

            elapsed_time = time.time() - start_time

            # 检查输出文件
            actual_output = output_path
            if not os.path.exists(output_path):
                # 尝试从结果获取输出路径
                if result and 'PREDICTION' in result:
                    actual_output = result['PREDICTION']

            print(f"\n[SUCCESS] 克里金插值完成")
            print(f"  - 耗时: {elapsed_time:.2f} 秒")
            print(f"  - 输出: {actual_output}")

            return actual_output, elapsed_time

        except Exception as e:
            print(f"\n[ERROR] 克里金插值失败: {e}")
            traceback.print_exc()
            raise
        finally:
            # 清理临时文件
            self._cleanup_temp_files()
            gc.collect()

    # ========================= 主转换方法 =========================

    def convert(
            self,
            method: str = 'idw',
            params: Union[IDWParams, TINParams, KrigingParams, None] = None,
            output_pga: bool = True,
            output_ia: bool = True
    ) -> Dict[str, Dict[str, Union[str, float]]]:
        """
        执行转换，生成PGA和/或Ia栅格文件

        参数:
            method (str): 插值方法，可选 'idw', 'tin', 'kriging'
            params: 对应插值方法的参数对象
                - IDW: IDWParams
                - TIN: TINParams
                - Kriging: KrigingParams
                - 如果为None，使用默认参数
            output_pga (bool): 是否输出PGA.tif，默认True
            output_ia (bool): 是否输出Ia.tif，默认True

        返回:
            Dict[str, Dict[str, Union[str, float]]]: 输出结果字典
            {
                'pga': {'path': 'xxx/PGA.tif', 'time': 12.34},
                'ia': {'path': 'xxx/Ia.tif', 'time': 15.67}
            }

        异常:
            ValueError: 当method参数无效或未指定任何输出时
        """
        # 验证参数
        if not output_pga and not output_ia:
            raise ValueError("至少需要指定一个输出（output_pga或output_ia）")

        method = method.lower()
        valid_methods = ['idw', 'tin', 'kriging']
        if method not in valid_methods:
            raise ValueError(f"无效的插值方法: '{method}'，可选: {valid_methods}")

        # 设置默认参数
        if params is None:
            if method == 'idw':
                params = IDWParams()
            elif method == 'tin':
                params = TINParams()
            else:
                params = KrigingParams()
            print(f"[INFO] 使用默认参数")

        # 验证参数类型匹配
        expected_types = {
            'idw': IDWParams,
            'tin': TINParams,
            'kriging': KrigingParams
        }
        if not isinstance(params, expected_types[method]):
            raise TypeError(
                f"参数类型不匹配: 方法'{method}'需要{expected_types[method].__name__}，"
                f"但收到{type(params).__name__}"
            )

        print("\n" + "=" * 70)
        print("           KML → PGA/Ia 栅格转换器")
        print("=" * 70)
        print(f"插值方法: {method.upper()}")
        print(f"输出PGA: {'是' if output_pga else '否'}")
        print(f"输出Ia: {'是' if output_ia else '否'}")
        print(f"固定分辨率: {self.RESOLUTION}米 × {self.RESOLUTION}米")
        print("=" * 70)

        total_start_time = time.time()
        output_results = {}

        try:
            # 准备点数据
            self.prepare_point_data()

            # 选择插值方法
            interpolate_func = {
                'idw': self.interpolate_idw,
                'tin': self.interpolate_tin,
                'kriging': self.interpolate_kriging
            }[method]

            # 输出PGA
            if output_pga:
                print("\n" + "-" * 50)
                print("生成 PGA.tif")
                print("-" * 50)

                pga_layer = self.create_point_layer('pga')
                pga_output = os.path.join(self.output_dir, 'PGA.tif')

                path, elapsed = interpolate_func(pga_layer, pga_output, params)
                output_results['pga'] = {'path': path, 'time': elapsed}

            # 输出Ia
            if output_ia:
                print("\n" + "-" * 50)
                print("生成 Ia.tif")
                print("-" * 50)

                ia_layer = self.create_point_layer('ia')
                ia_output = os.path.join(self.output_dir, 'Ia.tif')

                path, elapsed = interpolate_func(ia_layer, ia_output, params)
                output_results['ia'] = {'path': path, 'time': elapsed}

            total_time = time.time() - total_start_time

            # 打印总结
            print("\n" + "=" * 70)
            print("                    转换完成!")
            print("=" * 70)
            print(f"总耗时: {total_time:.2f} 秒")
            print("\n输出文件:")
            for key, info in output_results.items():
                file_size = os.path.getsize(info['path']) / 1024 / 1024
                print(f"  - {key.upper()}.tif")
                print(f"      路径: {info['path']}")
                print(f"      插值耗时: {info['time']:.2f} 秒")
                print(f"      文件大小: {file_size:.2f} MB")
            print("=" * 70)

            return output_results

        except Exception as e:
            print(f"\n[ERROR] 转换失败: {e}")
            traceback.print_exc()
            raise
        finally:
            # 清理资源
            self.cleanup()

    # ========================= 资源清理方法 =========================

    def _cleanup_temp_files(self) -> None:
        """清理临时文件"""
        for temp_file in self._temp_files:
            try:
                # 删除shapefile相关的所有文件
                base = temp_file.replace('.shp', '')
                for ext in ['.shp', '.shx', '.dbf', '.prj', '.cpg', '.sbn', '.sbx']:
                    f = base + ext
                    if os.path.exists(f):
                        os.remove(f)
            except Exception as e:
                print(f"[WARNING] 清理临时文件失败: {e}")
        self._temp_files.clear()

    def cleanup(self) -> None:
        """
        清理所有临时资源，释放内存

        在转换完成或发生错误后调用此方法确保资源被释放
        """
        print("\n[INFO] 清理临时资源...")

        # 清理临时图层
        for layer in self._temp_layers:
            try:
                del layer
            except:
                pass
        self._temp_layers.clear()

        # 清理临时文件
        self._cleanup_temp_files()

        # 清理点数据
        self._points_data.clear()

        # 强制垃圾回收
        gc.collect()

        print("[INFO] 资源清理完成")


# ============================================================================
#                              参数说明函数
# ============================================================================

def print_params_help() -> None:
    """
    打印所有插值算法参数的详细说明
    """
    help_text = """
╔══════════════════════════════════════════════════════════════════════════════╗
║                         插值算法参数详细说明                                   ║
╚══════════════════════════════════════════════════════════════════════════════╝

┌──────────────────────────────────────────────────────────────────────────────┐
│  一、IDW（反距离加权）算法 - IDWParams                                        │
└──────────────────────────────────────────────────────────────────────────────┘

  参数: coefficient (float)
  ════════════════════════════════════════════════════════════════════════════

  【含义】距离权重系数（幂次），控制距离对权重的影响程度

  【公式】权重 wi = 1 / (距离di ^ coefficient)
         插值值 = Σ(wi × vi) / Σ(wi)

  【默认值】2.0

  【取值范围】0.5 - 5.0（推荐），理论上 > 0

  【参数调节指南】

    ┌─────────────┬───────────────────────────────────────────────────────────┐
    │ 值          │ 效果说明                                                   │
    ├─────────────┼───────────────────────────────────────────────────────────┤
    │ 0.5 - 1.0   │ 距离影响小，结果非常平滑，适合大范围渐变趋势               │
    │             │ 优点: 平滑过渡，减少局部波动                               │
    │             │ 缺点: 可能丢失局部细节和极值                               │
    ├─────────────┼───────────────────────────────────────────────────────────┤
    │ 1.5 - 2.5   │ 【推荐】平衡局部特征和全局趋势                            │
    │             │ 2.0是最常用的标准设置                                      │
    │             │ 优点: 适用于大多数场景                                     │
    ├─────────────┼───────────────────────────────────────────────────────────┤
    │ 3.0 - 5.0   │ 距离影响大，强调局部特征，近点权重显著增加                │
    │             │ 优点: 保留局部变化细节                                     │
    │             │ 缺点: 可能产生"牛眼效应"（数据点周围出现同心圆等值线）     │
    └─────────────┴───────────────────────────────────────────────────────────┘

  【使用示例】
    idw_params = IDWParams(coefficient=2.0)


┌──────────────────────────────────────────────────────────────────────────────┐
│  二、TIN（不规则三角网）算法 - TINParams                                      │
└──────────────────────────────────────────────────────────────────────────────┘

  参数: method (int)
  ════════════════════════════════════════════════════════════════════════════

  【含义】三角形内部的插值计算方法

  【默认值】0

  【可选值】

    ┌───────┬─────────────────────────────────────────────────────────────────┐
    │ 值    │ 说明                                                             │
    ├───────┼─────────────────────────────────────────────────────────────────┤
    │   0   │ 线性插值 (Linear)                                                │
    │       │                                                                  │
    │       │ 【原理】在三角形内使用线性函数（平面方程）进行插值              │
    │       │         z = ax + by + c                                          │
    │       │                                                                  │
    │       │ 【优点】                                                         │
    │       │   • 计算速度快（约为Clough-Tocher的3倍）                        │
    │       │   • 保留原始数据点的精确值                                       │
    │       │   • 结果连续（C0连续）                                           │
    │       │   • 内存占用小                                                   │
    │       │                                                                  │
    │       │ 【缺点】                                                         │
    │       │   • 一阶导数不连续，等值线可能出现折角                          │
    │       │   • 三角形边界处可能有明显棱角                                   │
    │       │                                                                  │
    │       │ 【适用场景】                                                     │
    │       │   • 快速预览和测试                                               │
    │       │   • 数据点非常密集                                               │
    │       │   • 对平滑度要求不高                                             │
    │       │   • 数据本身有突变特征                                           │
    ├───────┼─────────────────────────────────────────────────────────────────┤
    │   1   │ 克劳德-杜尚插值 (Clough-Tocher)                                  │
    │       │                                                                  │
    │       │ 【原理】将每个三角形细分为3个子三角形，使用三次多项式插值       │
    │       │         保证相邻三角形边界处一阶导数连续                        │
    │       │                                                                  │
    │       │ 【优点】                                                         │
    │       │   • 结果更平滑（C1连续）                                         │
    │       │   • 等值线更圆润自然                                             │
    │       │   • 视觉效果更好                                                 │
    │       │                                                                  │
    │       │ 【缺点】                                                         │
    │       │   • 计算复杂度更高（约为Linear的3倍）                           │
    │       │   • 内存使用更多                                                 │
    │       │   • 处理速度较慢                                                 │
    │       │                                                                  │
    │       │ 【适用场景】                                                     │
    │       │   • 最终出图和报告                                               │
    │       │   • 需要平滑等值线                                               │
    │       │   • 数据点相对稀疏                                               │
    │       │   • 展示用途                                                     │
    └───────┴─────────────────────────────────────────────────────────────────┘

  【使用示例】
    tin_params = TINParams(method=0)  # 线性插值
    tin_params = TINParams(method=1)  # 克劳德-杜尚插值


┌──────────────────────────────────────────────────────────────────────────────┐
│  三、克里金（Kriging）算法 - KrigingParams                                    │
└──────────────────────────────────────────────────────────────────────────────┘

  克里金是地统计学中的最优线性无偏估计方法(BLUE)，考虑数据的空间自相关性。
  需要SAGA工具支持。

  ──────────────────────────────────────────────────────────────────────────────
  参数1: variogram_model (str) - 变异函数模型
  ──────────────────────────────────────────────────────────────────────────────

  【含义】描述空间自相关性随距离变化的数学模型

  【默认值】'Spherical'

  【可选值】

    ┌──────────────┬──────────────────────────────────────────────────────────┐
    │ 模型          │ 说明                                                      ���
    ├──────────────┼──────────────────────────────────────────────────────────┤
    │ 'Spherical'  │ 球状模型（最常用，推荐）                                  │
    │              │                                                           │
    │              │ 【数学形式】                                              │
    │              │   γ(h) = C0 + C×[1.5×(h/a) - 0.5×(h/a)³]  当 h ≤ a       │
    │              │   γ(h) = C0 + C                            当 h > a       │
    │              │                                                           │
    │              │ 【特点】在变程处精确达到基台，有明确的影响范围            │
    │              │ 【优点】适应性强，计算稳定，物理意义明确                  │
    │              │ 【适用】大多数地学数据，最通用的选择                      │
    ├──────────────┼──────────────────────────────────────────────────────────┤
    │ 'Exponential'│ 指数模型                                                  │
    │              │                                                           │
    │              │ 【数学形式】γ(h) = C0 + C×[1 - exp(-3h/a)]                │
    │              │                                                           │
    │              │ 【特点】渐近趋近基台，理论变程为无穷大                    │
    │              │ 【优点】在变程附近过渡更平滑                              │
    │              │ 【适用】空间相关性缓慢衰减的数据                          │
    ├──────────────┼──────────────────────────────────────────────────────────┤
    │ 'Gaussian'   │ 高斯模型                                                  │
    │              │                                                           │
    │              │ 【数学形式】γ(h) = C0 + C×[1 - exp(-(3h/a)²)]             │
    │              │                                                           │
    │              │ 【特点】在原点附近曲率为0，抛物线起始                     │
    │              │ 【优点】产生最平滑的插值结果                              │
    │              │ 【缺点】可能导致数值不稳定，对噪声敏感                    │
    │              │ 【适用】变化非常平缓、连续性极好的数据                    │
    ├──────────────┼──────────────────────────────────────────────────────────┤
    │ 'Linear'     │ 线性模型                                                  │
    │              │                                                           │
    │              │ 【数学形式】γ(h) = C0 + b×h                               │
    │              │                                                           │
    │              │ 【特点】最简单的模型，没有基台                            │
    │              │ 【优点】计算快速，参数少                                  │
    │              │ 【缺点】不适合有明显空间结构的数据                        │
    │              │ 【适用】数据范围小于空间相关范围时                        │
    └──────────────┴──────────────────────────────────────────────────────────┘

  其他参数: nugget(块金值), sill(基台值), range_val(变程), 
           search_radius(搜索半径), min_points(最小点数), max_points(最大点数)

  【使用示例】
    kriging_params = KrigingParams(
        variogram_model='Spherical',
        nugget=0.0,
        sill=1.0,
        range_val=0.5,
        search_radius=-1,
        min_points=4,
        max_points=12
    )


┌──────────────────────────────────────────────────────────────────────────────┐
│  四、算法选择建议                                                             │
└──────────────────────────────────────────────────────────────────────────────┘

  ┌─────────────────────────┬──────────────┬──────────────────────────────────┐
  │ 场景                    │ 推荐算法      │ 推荐参数                          │
  ├─────────────────────────┼──────────────┼──────────────────────────────────┤
  │ 快速预览/测试           │ IDW          │ coefficient=2.0                  │
  │ 数据点分布均匀          │ IDW          │ coefficient=2.0                  │
  │ 数据点分布不规则        │ TIN          │ method=0 (Linear)                │
  │ 需要平滑结果            │ TIN          │ method=1 (Clough-Tocher)         │
  │ 需要最佳估计精度        │ Kriging      │ variogram_model='Spherical'      │
  │ 考虑空间相关性          │ Kriging      │ 根据数据特征选择模型              │
  │ 内存受限                │ IDW或TIN     │ -                                │
  │ 计算时间受限            │ IDW          │ -                                │
  │ 最终出图/报告           │ TIN或Kriging │ method=1 或 Spherical模型        │
  └─────────────────────────┴──────────────┴──────────────────────────────────┘

╚══════════════════════════════════════════════════════════════════════════════╝
"""
    print(help_text)


# ============================================================================
#                              测试函数
# ============================================================================

def create_test_kml(output_path: str, num_contours: int = 5) -> str:
    """
    创建测试用的KML文件

    参数:
        output_path (str): 输出KML文件路径
        num_contours (int): 等值线数量，默认5

    返回:
        str: 创建的KML文件路径
    """
    # 中心点（中国某地）
    center_lon, center_lat = 103.0, 34.0

    # PGA值列表（从外到内递增）
    pga_values = [0.01, 0.02, 0.03, 0.05, 0.1, 0.2, 0.3, 0.5][:num_contours]

    kml_content = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Document>
<name>Test PGA Contours</name>
"""

    for i, pga in enumerate(pga_values):
        # 计算等值线半径（PGA越大，圈越小）
        radius = 0.5 * (len(pga_values) - i) / len(pga_values) + 0.1

        # 生成圆形等值线的坐标点（每个圆36个点）
        coords = []
        for angle in range(0, 361, 10):
            rad = math.radians(angle)
            lon = center_lon + radius * math.cos(rad)
            lat = center_lat + radius * math.sin(rad) * 0.8  # 稍微压扁成椭圆
            coords.append(f"{lon:.6f},{lat:.6f},0")

        coords_str = " ".join(coords)

        kml_content += f"""<Placemark>
<name>{pga}g</name>
<description>PGA等值线 {pga}g</description>
<LineString>
<coordinates>
{coords_str}
</coordinates>
</LineString>
</Placemark>
"""

    kml_content += """</Document>
</kml>"""

    # 确保目录存在
    os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else '.', exist_ok=True)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(kml_content)

    print(f"[INFO] 测试KML文件已创建: {output_path}")
    return output_path


def test_parse_kml():
    """
    测试KML解析功能
    """
    print("\n" + "=" * 70)
    print("测试: KML解析功能")
    print("=" * 70)

    test_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'test_output')
    os.makedirs(test_dir, exist_ok=True)

    test_kml_path = "../../data/geology/kml/source.kml"
    converter = None

    try:
        # 创建测试KML
        # create_test_kml(test_kml_path, num_contours=4)

        # 创建转换器并解析
        converter = KmlToPgaIaConverter(
            kml_file_path=test_kml_path,
            output_dir=test_dir
        )

        points = converter.parse_kml()

        # 验证结果
        assert len(points) > 0, "解析结果为空"

        print(f"\n[测试结果]")
        print(f"  - 解析点数: {len(points)}")
        print(f"  - PGA等级数: {len(set([p[2] for p in points]))}")

        # 验证PGA值转换
        for lon, lat, pga_ms2 in points[:3]:
            pga_g = pga_ms2 / 9.8
            print(f"  - 示例点: ({lon:.4f}, {lat:.4f}), PGA={pga_g:.4f}g = {pga_ms2:.4f}m/s²")

        print("\n✅ KML解析测试通过!")
        return True

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        traceback.print_exc()
        return False
    finally:
        # 清理
        if converter:
            converter.cleanup()
        if os.path.exists(test_kml_path):
            os.remove(test_kml_path)


def test_ia_calculation():
    """
    测试Ia计算功能
    """
    print("\n" + "=" * 70)
    print("测试: Ia计算功能")
    print("=" * 70)

    test_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'test_output')
    os.makedirs(test_dir, exist_ok=True)

    test_kml_path = os.path.join(test_dir, 'test_ia.kml')
    converter = None

    try:
        # 创建测试KML
        create_test_kml(test_kml_path, num_contours=1)

        converter = KmlToPgaIaConverter(
            kml_file_path=test_kml_path,
            output_dir=test_dir
        )

        # 测试不同PGA值的Ia计算
        test_cases = [
            (0.098, "0.01g"),  # 0.01g × 9.8
            (0.196, "0.02g"),  # 0.02g × 9.8
            (0.294, "0.03g"),  # 0.03g × 9.8
            (0.49, "0.05g"),  # 0.05g × 9.8
            (0.98, "0.1g"),  # 0.1g × 9.8
            (4.9, "0.5g"),  # 0.5g × 9.8
        ]

        print("\n[Ia计算验证] 公式: log10(Ia) = 0.797 + 1.837 × log10(PGA)")
        print("-" * 60)
        print(f"{'PGA(g)':<12} {'PGA(m/s²)':<15} {'Ia(m/s)':<15} {'验证':<10}")
        print("-" * 60)

        all_passed = True
        for pga_ms2, pga_label in test_cases:
            ia = converter.calculate_ia(pga_ms2)

            # 手动验证公式
            expected_log_ia = 0.797 + 1.837 * math.log10(pga_ms2)
            expected_ia = math.pow(10, expected_log_ia)

            passed = abs(ia - expected_ia) < 1e-10
            all_passed = all_passed and passed
            status = "✓" if passed else "✗"

            print(f"{pga_label:<12} {pga_ms2:<15.4f} {ia:<15.6f} {status:<10}")

        # 测试边界情况
        assert converter.calculate_ia(0) == 1e-10, "PGA=0时应返回1e-10"
        assert converter.calculate_ia(-1) == 1e-10, "PGA<0时应返回1e-10"

        print("-" * 60)

        if all_passed:
            print("\n✅ Ia计算测试通过!")
            return True
        else:
            print("\n❌ Ia计算测试失败!")
            return False

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        traceback.print_exc()
        return False
    finally:
        if converter:
            converter.cleanup()
        if os.path.exists(test_kml_path):
            os.remove(test_kml_path)


def test_idw_interpolation():
    """
    测试IDW插值功能
    """
    print("\n" + "=" * 70)
    print("测试: IDW插值功能")
    print("=" * 70)

    test_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'test_output')
    os.makedirs(test_dir, exist_ok=True)

    test_kml_path = os.path.join(test_dir, 'test_idw.kml')
    output_subdir = os.path.join(test_dir, 'idw_output')
    os.makedirs(output_subdir, exist_ok=True)

    try:
        # 创建测试KML
        create_test_kml(test_kml_path, num_contours=4)

        # 创建转换器（使用较小范围限制以加快测试）
        converter = KmlToPgaIaConverter(
            kml_file_path=test_kml_path,
            output_dir=output_subdir,
            max_extent_km=50.0  # 限制范围以加快测试
        )

        # 测试不同coefficient参数
        test_coefficients = [1.0, 2.0, 3.0]

        for coef in test_coefficients:
            print(f"\n--- 测试 coefficient={coef} ---")

            params = IDWParams(coefficient=coef)

            results = converter.convert(
                method='idw',
                params=params,
                output_pga=True,
                output_ia=True
            )

            # 验证输出
            for key, info in results.items():
                assert os.path.exists(info['path']), f"输出文件不存在: {info['path']}"
                file_size = os.path.getsize(info['path'])
                assert file_size > 0, f"输出文件为空: {info['path']}"
                print(f"  {key.upper()}: {file_size / 1024:.2f} KB, 耗时: {info['time']:.2f}s")

        print("\n✅ IDW插值测试通过!")
        return True

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        traceback.print_exc()
        return False
    finally:
        # 清理
        if os.path.exists(test_kml_path):
            os.remove(test_kml_path)
        # 清理输出文件
        import shutil
        if os.path.exists(output_subdir):
            shutil.rmtree(output_subdir)


def test_tin_interpolation():
    """
    测试TIN插值功能
    """
    print("\n" + "=" * 70)
    print("测试: TIN插值功能")
    print("=" * 70)

    test_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'test_output')
    os.makedirs(test_dir, exist_ok=True)

    test_kml_path = os.path.join(test_dir, 'test_tin.kml')
    output_subdir = os.path.join(test_dir, 'tin_output')
    os.makedirs(output_subdir, exist_ok=True)

    try:
        # 创建测试KML
        create_test_kml(test_kml_path, num_contours=4)

        converter = KmlToPgaIaConverter(
            kml_file_path=test_kml_path,
            output_dir=output_subdir,
            max_extent_km=50.0
        )

        # 测试两种方法
        test_methods = [
            (0, "线性插值(Linear)"),
            (1, "克劳德-杜尚插值(Clough-Tocher)")
        ]

        for method_id, method_name in test_methods:
            print(f"\n--- 测试 {method_name} ---")

            params = TINParams(method=method_id)

            results = converter.convert(
                method='tin',
                params=params,
                output_pga=True,
                output_ia=True
            )

            # 验证输出
            for key, info in results.items():
                assert os.path.exists(info['path']), f"输出文件不存在: {info['path']}"
                file_size = os.path.getsize(info['path'])
                assert file_size > 0, f"输出文件为空: {info['path']}"
                print(f"  {key.upper()}: {file_size / 1024:.2f} KB, 耗时: {info['time']:.2f}s")

        print("\n✅ TIN插值测试通过!")
        return True

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        traceback.print_exc()
        return False
    finally:
        if os.path.exists(test_kml_path):
            os.remove(test_kml_path)
        import shutil
        if os.path.exists(output_subdir):
            shutil.rmtree(output_subdir)


def test_kriging_interpolation():
    """
    测试克里金插值功能（需要SAGA支持）
    """
    print("\n" + "=" * 70)
    print("测试: 克里金插值功能")
    print("=" * 70)

    if not PROCESSING_AVAILABLE:
        print("[跳过] QGIS Processing模块不可用，无法测试克里金插值")
        return True

    test_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'test_output')
    os.makedirs(test_dir, exist_ok=True)

    test_kml_path = os.path.join(test_dir, 'test_kriging.kml')
    output_subdir = os.path.join(test_dir, 'kriging_output')
    os.makedirs(output_subdir, exist_ok=True)

    try:
        # 创建测试KML
        create_test_kml(test_kml_path, num_contours=4)

        converter = KmlToPgaIaConverter(
            kml_file_path=test_kml_path,
            output_dir=output_subdir,
            max_extent_km=50.0
        )

        # 测试不同变异函数模型
        test_models = ['Spherical', 'Exponential']

        for model in test_models:
            print(f"\n--- 测试 {model}模型 ---")

            params = KrigingParams(
                variogram_model=model,
                nugget=0.0,
                sill=1.0,
                range_val=0.3,
                search_radius=-1,
                min_points=4,
                max_points=12
            )

            results = converter.convert(
                method='kriging',
                params=params,
                output_pga=True,
                output_ia=False  # 只测试PGA以加快速度
            )

            # 验证输出
            for key, info in results.items():
                assert os.path.exists(info['path']), f"输出文件不存在: {info['path']}"
                file_size = os.path.getsize(info['path'])
                assert file_size > 0, f"输出文件为空: {info['path']}"
                print(f"  {key.upper()}: {file_size / 1024:.2f} KB, 耗时: {info['time']:.2f}s")

        print("\n✅ 克里金插值测试通过!")
        return True

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        traceback.print_exc()
        return False
    finally:
        if os.path.exists(test_kml_path):
            os.remove(test_kml_path)
        import shutil
        if os.path.exists(output_subdir):
            shutil.rmtree(output_subdir)


def run_all_tests():
    """
    运行所有测试

    返回:
        bool: 所有测试是否通过
    """
    print("\n" + "╔" + "═" * 68 + "╗")
    print("║" + " " * 20 + "KML → PGA/Ia 转换器测试套件" + " " * 20 + "║")
    print("╚" + "═" * 68 + "╝")

    tests = [
        ("KML解析测试", test_parse_kml)
    ]

    results = []
    for test_name, test_func in tests:
        try:
            passed = test_func()
            results.append((test_name, passed))
        except Exception as e:
            results.append((test_name, False))
            print(f"[错误] {test_name}: {e}")

    # 打印总结
    print("\n" + "=" * 70)
    print("测试结果汇总")
    print("=" * 70)

    passed_count = 0
    for test_name, passed in results:
        status = "✅ 通过" if passed else "❌ 失败"
        print(f"  {test_name}: {status}")
        if passed:
            passed_count += 1

    print("-" * 70)
    print(f"总计: {passed_count}/{len(results)} 通过")
    print("=" * 70)

    return passed_count == len(results)


# ============================================================================
#                              使用示例
# ============================================================================

def example_usage():
    """
    完整使用示例
    """
    example_code = '''
# ============================================================================
#                           使用示例代码
# ============================================================================

# 1. 导入模块
from kml_to_ia_converter import (
    KmlToPgaIaConverter,
    IDWParams,
    TINParams,
    KrigingParams,
    print_params_help
)

# 2. 查看参数说明（可选）
print_params_help()

# 3. 创建转换器实例
converter = KmlToPgaIaConverter(
    kml_file_path="D:/data/earthquake_pga.kml",  # 输入KML文件路径
    output_dir="D:/output/",                      # 输出目录
    max_extent_km=300.0,                          # 最大范围300km
    max_memory_gb=10.0                            # 内存限制10GB
)

# ============================================================================
# 方式一：使用IDW插值
# ============================================================================
idw_params = IDWParams(
    coefficient=2.0  # 距离权重系数，推荐1.5-2.5
)

results = converter.convert(
    method='idw',
    params=idw_params,
    output_pga=True,   # 输出PGA.tif
    output_ia=True     # 输出Ia.tif
)

print(f"PGA文件: {results['pga']['path']}, 耗时: {results['pga']['time']:.2f}秒")
print(f"Ia文件: {results['ia']['path']}, 耗时: {results['ia']['time']:.2f}秒")

# ============================================================================
# 方式二：使用TIN插值
# ============================================================================
tin_params = TINParams(
    method=1  # 0=线性插值, 1=克劳德-杜尚插值（更平滑）
)

results = converter.convert(
    method='tin',
    params=tin_params,
    output_pga=True,
    output_ia=True
)

# ============================================================================
# 方式三：使用克里金插值（需要SAGA工具）
# ============================================================================
kriging_params = KrigingParams(
    variogram_model='Spherical',  # 变异函数模型
    nugget=0.0,                   # 块金值
    sill=1.0,                     # 基台值
    range_val=0.5,                # 变程（度），约55公里
    search_radius=-1,             # 搜索半径，-1表示使用所有点
    min_points=4,                 # 最小搜索点数
    max_points=12                 # 最大搜索点数
)

results = converter.convert(
    method='kriging',
    params=kriging_params,
    output_pga=True,
    output_ia=True
)

# ============================================================================
# 只输出Ia.tif（不输出PGA.tif）
# ============================================================================
results = converter.convert(
    method='idw',
    params=IDWParams(coefficient=2.0),
    output_pga=False,  # 不输出PGA
    output_ia=True     # 只输出Ia
)
'''
    print(example_code)


# ============================================================================
#                              主程序入口
# ============================================================================

if __name__ == '__main__':
    """
    主程序入口

    运行方式:

    1. 在QGIS Python控制台中:
       >>> exec(open('kml_to_ia_converter.py').read())
       >>> run_all_tests()  # 运行测试
       >>> print_params_help()  # 查看参数说明
       >>> example_usage()  # 查看使用示例

    2. 直接运行脚本（需要QGIS环境）:
       python kml_to_ia_converter.py
    """
    print("\n" + "=" * 70)
    print("KML → PGA/Ia 栅格转换工具")
    print("基于 QGIS 3.40.15")
    print("分辨率: 30米 × 30米（固定）")
    print("=" * 70)

    # 运行测试
    # all_passed = run_all_tests()

    # 打印使用示例
    print("\n\n")
    # example_usage()

    # 打印参数说明
    print("\n\n")
    print_params_help()

    kml_path = "../../data/geology/kml/source.kml",  # 输入KML文件路径
    pga_output_path = "../../data/geology/kml/PGA.tif",  # PGA输出路径（不需要可设为None）
    converter = KmlToPgaIaConverter(
        kml_file_path="../../data/geology/kml/source.kml",
        output_dir="./../data/geology/",  # 输出目录
        max_extent_km=300.0,  # 最大范围300km
        max_memory_gb=10.0  # 内存限制10GB
    )

    # ============================================================================
    # 方式一：使用IDW插值
    # ============================================================================
    idw_params = IDWParams(
        coefficient=2.0  # 距离权重系数，推荐1.5-2.5
    )

    results = converter.convert(
        method='idw',
        params=idw_params,
        output_pga=True,  # 输出PGA.tif
        output_ia=True  # 输出Ia.tif
    )

    # if not all_passed:
    #     exit(1)