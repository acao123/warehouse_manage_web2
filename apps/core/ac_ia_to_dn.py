# -*- coding: utf-8 -*-
"""
基于QGIS 3.40.15的Python环境
计算滑动距离Dn并输出Dn.tif

公式: log(Dn) = 1.299 + 1.076*log(Ia) - 12.197*a_c + 5.434*a_c*log(Ia)

注意：
- Ia 单位为 m/s（Arias强度）
- a_c 在 ac.tif 中的存储单位为 m/s²，公式要求以 g 为单位，
  因此计算时需除以 GRAVITY (9.81 m/s²) 进行转换
- Dn 输出单位为 cm（厘米）
- 当 ac 有值但 Ia 超出范围或无值时，Dn 赋值为 0
- 当 ac 本身为 nodata 时，Dn 赋值为 nodata (-9999)
"""

import logging
import os
import math
import re
import xml.etree.ElementTree as ET

from osgeo import gdal, osr
import numpy as np

# ============================================================
# 常量
# ============================================================
GRAVITY = 9.81        # 重力加速度, m/s²
NODATA_VALUE = -9999.0  # 输出 nodata 标记

# ============================================================
# 日志配置
# ============================================================
logger = logging.getLogger('report.core.ac_ia_to_dn')


# ============================================================
# 辅助函数
# ============================================================

def get_search_radius(magnitude):
    """
    根据震级获取搜索半径（单位：km）
    M < 6: 15km
    6 <= M < 7: 50km
    M >= 7: 150km
    """
    if magnitude < 6:
        return 15
    elif magnitude < 7:
        return 50
    else:
        return 150


def km_to_degree(km, latitude):
    """
    将公里转换为度数（考虑纬度影响）
    在赤道附近，1度约等于111km

    返回:
        (lat_degree, lon_degree) 纬度和经度方向的度数
    """
    lat_rad = math.radians(latitude)
    cos_lat = math.cos(lat_rad)
    if cos_lat < 1e-10:
        raise ValueError(f"纬度 {latitude} 过接近极点，无法计算经度转换")
    lat_degree = km / 111.0
    lon_degree = km / (111.0 * cos_lat)
    return lat_degree, lon_degree


def get_pixel_coords(geotransform, lon, lat):
    """
    将地理坐标转换为像素坐标
    geotransform: (origin_x, pixel_width, 0, origin_y, 0, pixel_height)

    返回:
        (col, row) 整数像素坐标
    """
    origin_x = geotransform[0]
    pixel_width = geotransform[1]
    origin_y = geotransform[3]
    pixel_height = geotransform[5]  # 通常为负值

    col = int((lon - origin_x) / pixel_width)
    row = int((lat - origin_y) / pixel_height)

    return col, row


def _validate_epsg4326(dataset, file_path):
    """
    验证栅格数据集是否为 EPSG:4326 (WGS 84) 坐标系。
    如果不是则记录警告但不中断（可能元数据缺失但实际是4326）。
    """
    projection = dataset.GetProjection()
    if not projection:
        logger.warning('文件 %s 没有投影信息，假定为 EPSG:4326', file_path)
        return

    srs = osr.SpatialReference()
    srs.ImportFromWkt(projection)

    srs.AutoIdentifyEPSG()
    auth_name = srs.GetAuthorityName(None)
    auth_code = srs.GetAuthorityCode(None)

    if auth_name == 'EPSG' and auth_code == '4326':
        logger.debug('文件 %s 坐标系验证通过: EPSG:4326', file_path)
    else:
        if srs.IsGeographic():
            datum = srs.GetAttrValue('DATUM', 0) or ''
            if 'WGS' in datum.upper():
                logger.debug('文件 %s 坐标系为 WGS 84 地理坐标系', file_path)
                return
        logger.warning(
            '文件 %s 的坐标系可能不是 EPSG:4326 (检测到: %s:%s)，'
            '计算结果可能不正确',
            file_path, auth_name, auth_code
        )


def _nodata_mask(data, nodata_value, rtol=1e-5):
    """
    生成 nodata 掩码，处理浮点精度问题。

    返回:
        布尔掩码，True 表示该位置是 nodata
    """
    if nodata_value is None:
        return np.zeros(data.shape, dtype=bool)
    if np.isnan(nodata_value):
        return np.isnan(data)
    return np.isclose(data, nodata_value, rtol=rtol, atol=0)


def parse_kml_outermost_bbox(kml_path):
    """
    解析烈度KML文件，找到烈度最小的Placemark（最外圈等值线），
    返回其经纬度 bounding box (min_lon, max_lon, min_lat, max_lat)。

    KML文件格式示例:
        <Placemark><name>4度</name>...<LineString><coordinates>lon,lat,z ...</coordinates></LineString></Placemark>

    参数:
        kml_path: KML文件路径

    返回:
        (min_lon, max_lon, min_lat, max_lat) 或 None（解析失败时）
    """
    try:
        tree = ET.parse(kml_path)
    except ET.ParseError as e:
        logger.error("KML文件解析失败: %s, 错误: %s", kml_path, e)
        return None
    except OSError as e:
        logger.error("KML文件读取失败: %s, 错误: %s", kml_path, e)
        return None

    root = tree.getroot()

    # 处理命名空间 (http://www.opengis.net/kml/2.2 等)
    ns = ''
    if root.tag.startswith('{'):
        ns = root.tag.split('}')[0] + '}'

    placemarks = root.findall(f'.//{ns}Placemark')
    if not placemarks:
        logger.warning("KML文件中未找到Placemark元素: %s", kml_path)
        return None

    # 提取每个Placemark的烈度值和coordinates文本
    intensity_list = []
    for pm in placemarks:
        name_el = pm.find(f'{ns}name')
        if name_el is None or not name_el.text:
            continue
        name_text = name_el.text.strip()
        # 从名称中提取数字，如 "4度" → 4，"5.5度" → 5.5
        match = re.search(r'\d+\.?\d*', name_text)
        if not match:
            logger.debug("Placemark名称中未找到数字: '%s'，跳过", name_text)
            continue
        try:
            intensity_val = float(match.group())
        except ValueError:
            logger.debug("Placemark名称解析烈度失败: '%s'，跳过", name_text)
            continue

        coords_el = pm.find(f'.//{ns}coordinates')
        if coords_el is None or not coords_el.text:
            logger.debug("Placemark '%s' 中未找到coordinates，跳过", name_text)
            continue

        intensity_list.append((intensity_val, coords_el.text.strip()))

    if not intensity_list:
        logger.warning("KML文件中未找到有效的烈度Placemark: %s", kml_path)
        return None

    # 找到烈度最小的Placemark（最外圈）
    intensity_list.sort(key=lambda x: x[0])
    min_intensity, coords_text = intensity_list[0]
    logger.info("KML最外圈烈度: %.0f度，共%d个Placemark", min_intensity, len(intensity_list))

    # 解析坐标字符串，格式: "lon,lat,z lon,lat,z ..."
    lons = []
    lats = []
    for token in coords_text.split():
        token = token.strip()
        if not token:
            continue
        parts = token.split(',')
        if len(parts) < 2:
            continue
        try:
            lons.append(float(parts[0]))
            lats.append(float(parts[1]))
        except ValueError:
            logger.debug("坐标点解析失败: '%s'，跳过", token)
            continue

    if not lons or not lats:
        logger.warning("KML最外圈Placemark中未解析到有效坐标点: %s", kml_path)
        return None

    kml_min_lon = min(lons)
    kml_max_lon = max(lons)
    kml_min_lat = min(lats)
    kml_max_lat = max(lats)

    logger.info(
        "KML最外圈经纬度范围: lon=[%.6f, %.6f], lat=[%.6f, %.6f]",
        kml_min_lon, kml_max_lon, kml_min_lat, kml_max_lat
    )
    return kml_min_lon, kml_max_lon, kml_min_lat, kml_max_lat


def merge_search_bbox_with_kml(base_min_lon, base_max_lon, base_min_lat, base_max_lat,
                               kml_min_lon, kml_max_lon, kml_min_lat, kml_max_lat):
    """
    判断基础搜索范围与KML最外圈范围是否有交集，若有则取并集。

    参数:
        base_min_lon, base_max_lon, base_min_lat, base_max_lat: 基础搜索范围
        kml_min_lon, kml_max_lon, kml_min_lat, kml_max_lat: KML最外圈范围

    返回:
        (min_lon, max_lon, min_lat, max_lat) 合并后（或原始）的范围
    """
    # 判断两个矩形是否有交集
    has_lon_overlap = base_min_lon <= kml_max_lon and kml_min_lon <= base_max_lon
    has_lat_overlap = base_min_lat <= kml_max_lat and kml_min_lat <= base_max_lat
    has_intersection = has_lon_overlap and has_lat_overlap

    if not has_intersection:
        logger.warning(
            "KML范围与原始搜索范围无交集（KML烈度圈数据可能异常），回退使用原始震中搜索范围: lon=[%.6f, %.6f], lat=[%.6f, %.6f]",
            base_min_lon, base_max_lon, base_min_lat, base_max_lat
        )
        return base_min_lon, base_max_lon, base_min_lat, base_max_lat

    # 取并集
    merged_min_lon = min(base_min_lon, kml_min_lon)
    merged_max_lon = max(base_max_lon, kml_max_lon)
    merged_min_lat = min(base_min_lat, kml_min_lat)
    merged_max_lat = max(base_max_lat, kml_max_lat)

    logger.info(
        "KML范围与原始搜索范围有交集，取并集: lon=[%.6f, %.6f], lat=[%.6f, %.6f]",
        merged_min_lon, merged_max_lon, merged_min_lat, merged_max_lat
    )
    return merged_min_lon, merged_max_lon, merged_min_lat, merged_max_lat


def get_utm_epsg(center_lon):
    """
    根据中央经度选择适合中国范围的 UTM 投影带 EPSG 编号。
    优先匹配北半球东经 72°–138° 范围内的 UTM 带；
    超出范围时使用标准公式 32600 + int((lon+180)/6) + 1 兜底并记录 warning。

    参数:
        center_lon: 工作范围中央经度（度）

    返回:
        EPSG 整数，例如 32648
    """
    # UTM 带对照表：(经度下界, 经度上界, EPSG)
    utm_table = [
        (72,  78,  32643),
        (78,  84,  32644),
        (84,  90,  32645),
        (90,  96,  32646),
        (96,  102, 32647),
        (102, 108, 32648),
        (108, 114, 32649),
        (114, 120, 32650),
        (120, 126, 32651),
        (126, 132, 32652),
        (132, 138, 32653),
    ]
    for lon_min, lon_max, epsg in utm_table:
        if lon_min <= center_lon < lon_max:
            logger.info(
                "UTM 带选择：中央经度 %.4f°，匹配 EPSG:%d（经度范围 %d°–%d°E）",
                center_lon, epsg, lon_min, lon_max
            )
            return epsg
    # 兜底：标准 UTM 北半球公式
    epsg = 32600 + int((center_lon + 180) / 6) + 1
    logger.warning(
        "中央经度 %.4f° 超出表格范围 72°–138°E，使用标准公式兜底 EPSG:%d",
        center_lon, epsg
    )
    return epsg


# ============================================================
# 公共接口
# ============================================================

def calculate_dn_optimized(ac_tif_path, ia_tif_path, output_path,
                           epicenter_lon, epicenter_lat, magnitude,
                           intensity_kml_path=None):
    """
    优化版本：使用向量化计算提高效率
    计算滑动距离Dn并输出Dn.tif

    参数:
        ac_tif_path: ac.tif文件路径（a_c 单位为 m/s²）
        ia_tif_path: Ia.tif文件路径（Ia 单位为 m/s）
        output_path: 输出Dn.tif文件路径（Dn 单位为 cm）
        epicenter_lon: 震中经度
        epicenter_lat: 震中纬度
        magnitude: 震级
        intensity_kml_path: （可选）烈度KML文件路径；若提供，则与震中范围取并集确定输出范围

    赋值规则:
        - ac 为 nodata → Dn = nodata (-9999)
        - ac 有值, Ia 超出范围或无值或 Ia<=0 → Dn = 0
        - ac 有值, Ia > 0 → 按公式计算 Dn
    """
    logger.info(
        '开始计算Dn.tif: ac=%s ia=%s output=%s lon=%.4f lat=%.4f M=%.1f kml=%s',
        ac_tif_path, ia_tif_path, output_path,
        epicenter_lon, epicenter_lat, magnitude, intensity_kml_path
    )
    try:
        _calculate_dn_optimized_impl(
            ac_tif_path, ia_tif_path, output_path,
            epicenter_lon, epicenter_lat, magnitude,
            intensity_kml_path=intensity_kml_path
        )
        logger.info('Dn.tif 计算完成: %s', output_path)
    except Exception as exc:
        logger.error('Dn.tif 计算失败: %s', exc, exc_info=True)
        raise


# ============================================================
# 内部实现
# ============================================================

def _calculate_dn_optimized_impl(ac_tif_path, ia_tif_path, output_path,
                                 epicenter_lon, epicenter_lat, magnitude,
                                 intensity_kml_path=None):
    """calculate_dn_optimized 的实际实现。"""

    # ----------------------------------------------------------
    # 1. 参数校验
    # ----------------------------------------------------------
    if not os.path.exists(ac_tif_path):
        raise FileNotFoundError(f"ac.tif 文件不存在: {ac_tif_path}")
    if not os.path.exists(ia_tif_path):
        raise FileNotFoundError(f"Ia.tif 文件不存在: {ia_tif_path}")

    if not (-180 <= epicenter_lon <= 180):
        raise ValueError(f"震中经度超出范围 [-180, 180]: {epicenter_lon}")
    if not (-90 <= epicenter_lat <= 90):
        raise ValueError(f"震中纬度超出范围 [-90, 90]: {epicenter_lat}")
    if magnitude <= 0:
        raise ValueError(f"震级必须大于0: {magnitude}")

    # ----------------------------------------------------------
    # 2. 计算搜索半径与经纬度范围
    # ----------------------------------------------------------
    radius_km = get_search_radius(magnitude)
    logger.info("震级: %.1f, 搜索半径: %d km", magnitude, radius_km)

    try:
        lat_range, lon_range = km_to_degree(radius_km, epicenter_lat)
    except ValueError as e:
        logger.error("经纬度转换失败: %s", e)
        raise

    min_lon = epicenter_lon - lon_range
    max_lon = epicenter_lon + lon_range
    min_lat = epicenter_lat - lat_range
    max_lat = epicenter_lat + lat_range

    # ----------------------------------------------------------
    # 2b. 若提供了烈度KML文件，与原始范围合并
    # ----------------------------------------------------------
    logger.info(
        "原始搜索范围: lon=[%.6f, %.6f], lat=[%.6f, %.6f]",
        min_lon, max_lon, min_lat, max_lat
    )

    if intensity_kml_path is not None:
        if not os.path.exists(intensity_kml_path):
            logger.warning("烈度KML文件不存在，忽略KML范围合并: %s", intensity_kml_path)
        else:
            kml_bbox = parse_kml_outermost_bbox(intensity_kml_path)
            if kml_bbox is not None:
                kml_min_lon, kml_max_lon, kml_min_lat, kml_max_lat = kml_bbox
                logger.info(
                    "KML最外圈范围: lon=[%.6f, %.6f], lat=[%.6f, %.6f]",
                    kml_min_lon, kml_max_lon, kml_min_lat, kml_max_lat
                )
                min_lon, max_lon, min_lat, max_lat = merge_search_bbox_with_kml(
                    min_lon, max_lon, min_lat, max_lat,
                    kml_min_lon, kml_max_lon, kml_min_lat, kml_max_lat
                )
                logger.info(
                    "合并后（裁剪前）经纬度范围: lon=[%.6f, %.6f], lat=[%.6f, %.6f]",
                    min_lon, max_lon, min_lat, max_lat
                )
            else:
                logger.warning("KML文件解析失败，使用原始搜索范围")

    # ----------------------------------------------------------
    # 3. 打开 ac.tif 并确定裁剪窗口
    # ----------------------------------------------------------
    ac_dataset = None
    ia_dataset = None
    out_dataset = None
    # 内存中间数据集（用于 UTM 重投影）
    ac_mem_ds = None
    ia_mem_ds = None
    ac_utm_ds = None
    ia_utm_ds = None

    try:
        ac_dataset = gdal.Open(ac_tif_path, gdal.GA_ReadOnly)
        if ac_dataset is None:
            raise IOError(f"无法打开ac.tif文件: {ac_tif_path}")

        _validate_epsg4326(ac_dataset, ac_tif_path)

        ac_geotransform = ac_dataset.GetGeoTransform()
        ac_band = ac_dataset.GetRasterBand(1)
        ac_nodata = ac_band.GetNoDataValue()

        logger.debug("ac.tif GeoTransform: %s", ac_geotransform)
        logger.debug("ac.tif NoData: %s", ac_nodata)

        # 计算 ac.tif 中的像素范围
        col_min, row_max = get_pixel_coords(ac_geotransform, min_lon, min_lat)
        col_max, row_min = get_pixel_coords(ac_geotransform, max_lon, max_lat)

        # 确保像素坐标在有效范围内
        raw_col_min, raw_row_min, raw_col_max, raw_row_max = col_min, row_min, col_max, row_max
        col_min = max(0, col_min)
        row_min = max(0, row_min)
        col_max = min(ac_dataset.RasterXSize, col_max)
        row_max = min(ac_dataset.RasterYSize, row_max)

        if (col_min != raw_col_min or row_min != raw_row_min
                or col_max != raw_col_max or row_max != raw_row_max):
            logger.info(
                "输出范围已被 ac.tif 边界裁剪: 原始像素区域 col=[%d, %d] row=[%d, %d] "
                "→ 裁剪后 col=[%d, %d] row=[%d, %d]",
                raw_col_min, raw_col_max, raw_row_min, raw_row_max,
                col_min, col_max, row_min, row_max
            )
        else:
            logger.info(
                "输出范围未超出 ac.tif 边界，无需裁剪: col=[%d, %d] row=[%d, %d]",
                col_min, col_max, row_min, row_max
            )

        read_width = col_max - col_min
        read_height = row_max - row_min

        if read_width <= 0 or read_height <= 0:
            raise ValueError(
                f"指定的震中坐标 ({epicenter_lon}, {epicenter_lat}) "
                f"在搜索半径 {radius_km}km 内超出 ac.tif 的范围。"
                f"像素区域: col=[{col_min},{col_max}], row=[{row_min},{row_max}]"
            )

        logger.info(
            "读取ac.tif区域: 起始列=%d, 起始行=%d, 宽度=%d, 高度=%d",
            col_min, row_min, read_width, read_height
        )

        # 计算 WGS84 工作区域的地理边界（用于后续 Ia 裁剪和中央经度计算）
        out_geotransform_4326 = list(ac_geotransform)
        out_geotransform_4326[0] = ac_geotransform[0] + col_min * ac_geotransform[1]
        out_geotransform_4326[3] = ac_geotransform[3] + row_min * ac_geotransform[5]

        work_lon_min = out_geotransform_4326[0]
        work_lon_max = out_geotransform_4326[0] + read_width * out_geotransform_4326[1]
        work_lat_max = out_geotransform_4326[3]
        work_lat_min = out_geotransform_4326[3] + read_height * out_geotransform_4326[5]

        # ----------------------------------------------------------
        # 4. 打开 Ia.tif
        # ----------------------------------------------------------
        ia_dataset = gdal.Open(ia_tif_path, gdal.GA_ReadOnly)
        if ia_dataset is None:
            raise IOError(f"无法打开Ia.tif文件: {ia_tif_path}")

        _validate_epsg4326(ia_dataset, ia_tif_path)

        ia_geotransform = ia_dataset.GetGeoTransform()
        ia_band = ia_dataset.GetRasterBand(1)
        ia_nodata = ia_band.GetNoDataValue()

        logger.debug("Ia.tif GeoTransform: %s", ia_geotransform)
        logger.debug("Ia.tif NoData: %s", ia_nodata)

        # ----------------------------------------------------------
        # 5. 根据工作区域中央经度选择 UTM 投影带
        # ----------------------------------------------------------
        center_lon = (work_lon_min + work_lon_max) / 2.0
        utm_epsg = get_utm_epsg(center_lon)
        utm_srs_str = f'EPSG:{utm_epsg}'

        # nodata 值（若原始为 None 则用 -9999）
        ac_nodata_val = ac_nodata if ac_nodata is not None else -9999.0
        ia_nodata_val = ia_nodata if ia_nodata is not None else -9999.0

        # ----------------------------------------------------------
        # 6. 在内存中裁剪 ac.tif 工作区域（为 Warp 做准备）
        # ----------------------------------------------------------
        logger.info(
            "使用 gdal.Translate 在内存中裁剪 ac.tif: srcWin=[%d, %d, %d, %d]",
            col_min, row_min, read_width, read_height
        )
        ac_mem_ds = gdal.Translate(
            '', ac_dataset,
            format='MEM',
            srcWin=[col_min, row_min, read_width, read_height]
        )
        if ac_mem_ds is None:
            raise IOError("gdal.Translate 创建 ac 内存裁剪数据集失败")

        # ----------------------------------------------------------
        # 7. 在内存中裁剪 Ia.tif 工作区域（与 ac 工作区域地理范围对应）
        # ----------------------------------------------------------
        # 西北角 → (ia_col_clip_min, ia_row_clip_min)
        # 东南角 → (ia_col_clip_max, ia_row_clip_max)
        ia_col_clip_min, ia_row_clip_min = get_pixel_coords(
            ia_geotransform, work_lon_min, work_lat_max
        )
        ia_col_clip_max, ia_row_clip_max = get_pixel_coords(
            ia_geotransform, work_lon_max, work_lat_min
        )
        # 加 1 保证包含边缘像素，并裁剪到有效范围
        ia_col_clip_max += 1
        ia_row_clip_max += 1
        ia_col_clip_min = max(0, ia_col_clip_min)
        ia_row_clip_min = max(0, ia_row_clip_min)
        ia_col_clip_max = min(ia_dataset.RasterXSize, ia_col_clip_max)
        ia_row_clip_max = min(ia_dataset.RasterYSize, ia_row_clip_max)

        ia_clip_width = ia_col_clip_max - ia_col_clip_min
        ia_clip_height = ia_row_clip_max - ia_row_clip_min

        if ia_clip_width > 0 and ia_clip_height > 0:
            logger.info(
                "使用 gdal.Translate 在内存中裁剪 Ia.tif: srcWin=[%d, %d, %d, %d]",
                ia_col_clip_min, ia_row_clip_min, ia_clip_width, ia_clip_height
            )
            ia_mem_ds = gdal.Translate(
                '', ia_dataset,
                format='MEM',
                srcWin=[ia_col_clip_min, ia_row_clip_min, ia_clip_width, ia_clip_height]
            )
            if ia_mem_ds is None:
                logger.warning("gdal.Translate 创建 Ia 内存裁剪数据集失败，Ia 将视为全无效")
        else:
            logger.warning(
                "ac 工作区域与 Ia.tif 无有效重叠（裁剪窗口为空），Ia 将视为全无效"
            )
            ia_mem_ds = None

        # ----------------------------------------------------------
        # 8. 将 ac 重投影并重采样到 UTM/30m（在内存中）
        # ----------------------------------------------------------
        logger.info(
            "开始 warp ac 到 UTM（%s），目标像元 30m，重采样方式：双线性…", utm_srs_str
        )
        ac_utm_ds = gdal.Warp(
            '', ac_mem_ds,
            format='MEM',
            dstSRS=utm_srs_str,
            xRes=30, yRes=30,
            resampleAlg=gdal.GRA_Bilinear,
            dstNodata=ac_nodata_val,
            targetAlignedPixels=True,
            creationOptions=['COMPRESS=LZW', 'TILED=YES', 'BIGTIFF=YES']
        )
        if ac_utm_ds is None:
            raise IOError(f"gdal.Warp 重投影 ac 到 {utm_srs_str} 失败")

        ac_utm_gt = ac_utm_ds.GetGeoTransform()
        utm_width = ac_utm_ds.RasterXSize
        utm_height = ac_utm_ds.RasterYSize

        # UTM 下的输出边界 (minX, minY, maxX, maxY)
        ac_utm_bounds = (
            ac_utm_gt[0],
            ac_utm_gt[3] + utm_height * ac_utm_gt[5],   # minY（南边）
            ac_utm_gt[0] + utm_width * ac_utm_gt[1],    # maxX（东边）
            ac_utm_gt[3]                                 # maxY（北边）
        )

        logger.info(
            "ac UTM/30m 栅格: EPSG:%d, 宽度=%d, 高度=%d, 像元=%.1f m, "
            "范围=(%.2f, %.2f, %.2f, %.2f)",
            utm_epsg, utm_width, utm_height, ac_utm_gt[1],
            ac_utm_bounds[0], ac_utm_bounds[1], ac_utm_bounds[2], ac_utm_bounds[3]
        )

        # ----------------------------------------------------------
        # 9. 将 Ia 重投影并重采样到与 ac 完全相同的 UTM/30m 网格
        # ----------------------------------------------------------
        if ia_mem_ds is not None:
            logger.info(
                "开始 warp Ia 到 UTM（%s），对齐 ac 网格（宽=%d, 高=%d）…",
                utm_srs_str, utm_width, utm_height
            )
            ia_utm_ds = gdal.Warp(
                '', ia_mem_ds,
                format='MEM',
                dstSRS=utm_srs_str,
                outputBounds=ac_utm_bounds,
                width=utm_width,
                height=utm_height,
                xRes=30, yRes=30,
                resampleAlg=gdal.GRA_Bilinear,
                dstNodata=ia_nodata_val,
                creationOptions=['COMPRESS=LZW', 'TILED=YES', 'BIGTIFF=YES']
            )
            if ia_utm_ds is None:
                logger.warning(
                    "gdal.Warp 重投影 Ia 到 %s 失败，Ia 将视为全无效", utm_srs_str
                )
        else:
            ia_utm_ds = None

        if ia_utm_ds is not None:
            logger.info(
                "Ia UTM/30m 栅格: 宽度=%d, 高度=%d（与 ac 网格对齐）",
                ia_utm_ds.RasterXSize, ia_utm_ds.RasterYSize
            )

        # ----------------------------------------------------------
        # 10. 读取 UTM/30m 栅格数据，构建掩码
        # ----------------------------------------------------------
        ac_data_utm = ac_utm_ds.GetRasterBand(1).ReadAsArray().astype(np.float64)

        # ac nodata 掩码与有效掩码
        ac_is_nodata = _nodata_mask(ac_data_utm, ac_nodata_val)
        ac_is_valid = ~ac_is_nodata

        # ★ 关键：将 a_c 从 m/s² 转换为 g 单位
        ac_data_g = np.where(ac_is_valid, ac_data_utm / GRAVITY, np.nan)

        valid_count = int(np.sum(ac_is_valid))
        logger.info(
            "ac UTM 数据统计（转换为 g 后）: 有效像素=%d, min=%.6f, max=%.6f",
            valid_count,
            float(np.nanmin(ac_data_g)) if valid_count > 0 else 0,
            float(np.nanmax(ac_data_g)) if valid_count > 0 else 0
        )

        # 读取 Ia UTM 数据
        if ia_utm_ds is not None:
            ia_data_utm = ia_utm_ds.GetRasterBand(1).ReadAsArray().astype(np.float64)
            ia_is_nodata = _nodata_mask(ia_data_utm, ia_nodata_val)
            # Ia 可用：ac 有效 & Ia 非nodata & Ia > 0
            ia_is_usable = ac_is_valid & (~ia_is_nodata) & (ia_data_utm > 0)

            ia_usable_count = int(np.sum(ia_is_usable))
            logger.info("Ia UTM 数据统计: 可用像素(>0)=%d", ia_usable_count)
            if ia_usable_count > 0:
                logger.info(
                    "Ia 可用值范围: min=%.6f, max=%.6f",
                    float(np.min(ia_data_utm[ia_is_usable])),
                    float(np.max(ia_data_utm[ia_is_usable]))
                )
        else:
            logger.warning("Ia UTM 数据集为空，所有 ac 有值处 Dn 赋值为 0")
            ia_data_utm = np.zeros((utm_height, utm_width), dtype=np.float64)
            ia_is_usable = np.zeros((utm_height, utm_width), dtype=bool)

        # ----------------------------------------------------------
        # 11. 初始化 Dn 数组（UTM/30m 网格）
        # ----------------------------------------------------------
        # ★ 赋值策略:
        #   - 先全部初始化为 NODATA_VALUE
        #   - ac 有值的位置先赋 0（覆盖 nodata，包括 Ia 无值的情况）
        #   - ac 有值 & Ia 有值 & Ia>0 的位置按公式计算后覆盖
        dn_data = np.full((utm_height, utm_width), NODATA_VALUE, dtype=np.float64)
        dn_data[ac_is_valid] = 0.0

        if not np.any(ia_is_usable):
            logger.warning(
                "ac 有效区域内没有可用的 Ia 值，所有 ac 有值处 Dn 赋值为 0"
            )
        else:
            # ----------------------------------------------------------
            # 12. 向量化计算 Dn（在 UTM/30m 网格上，ac 与 Ia 已对齐）
            # ----------------------------------------------------------
            # 公式: log10(Dn) = 1.299 + 1.076*log10(Ia) - 12.197*a_c + 5.434*a_c*log10(Ia)
            #   Ia 单位: m/s
            #   a_c 单位: g (已转换)
            #   Dn 单位: cm
            #
            # 计算掩码: ac 有值 & Ia 可用（非nodata & >0）
            calc_mask = ac_is_valid & ia_is_usable

            if np.any(calc_mask):
                with np.errstate(divide='ignore', invalid='ignore'):
                    log_ia = np.log10(ia_data_utm[calc_mask])
                    ac_g = ac_data_g[calc_mask]

                    log_dn = (
                        1.299
                        + 1.076 * log_ia
                        - 12.197 * ac_g
                        + 5.434 * ac_g * log_ia
                    )

                    dn_values = np.power(10.0, log_dn)

                    # 处理数值异常：NaN / Inf / 负数 → 0
                    bad = np.isnan(dn_values) | np.isinf(dn_values) | (dn_values < 0)
                    if np.any(bad):
                        logger.warning(
                            "Dn 计算中有 %d 个异常值(NaN/Inf/负数)，已置为 0",
                            int(np.sum(bad))
                        )
                        dn_values[bad] = 0.0

                    dn_data[calc_mask] = dn_values

                calc_count = int(np.sum(calc_mask))
                logger.info(
                    "Dn 计算统计: 计算像素=%d, min=%.6f cm, max=%.6f cm, mean=%.6f cm",
                    calc_count,
                    float(np.min(dn_data[calc_mask])),
                    float(np.max(dn_data[calc_mask])),
                    float(np.mean(dn_data[calc_mask]))
                )
            else:
                logger.warning(
                    "没有满足计算条件的像素（需要 ac有值 且 Ia>0），"
                    "所有 ac 有值处 Dn 保持为 0"
                )

        # ★ 此时赋值状态:
        #   ac nodata 位置  → NODATA_VALUE (-9999)
        #   ac有值 & (Ia=nodata | Ia<=0) → 0
        #   ac有值 & Ia>0   → 公式计算值

        # ----------------------------------------------------------
        # 13. 写入输出文件（UTM 投影，30m 像元）
        # ----------------------------------------------------------
        logger.info(
            "开始写入输出文件（%s，30m 像元）: %s", utm_srs_str, output_path
        )

        output_dir = os.path.dirname(output_path)
        if output_dir and not os.path.exists(output_dir):
            try:
                os.makedirs(output_dir, exist_ok=True)
                logger.info("已创建输出目录: %s", output_dir)
            except OSError as e:
                logger.error("无法创建输出目录 %s: %s", output_dir, e)
                raise

        driver = gdal.GetDriverByName('GTiff')
        if driver is None:
            raise RuntimeError("GDAL GTiff 驱动不可用")

        out_dataset = driver.Create(
            output_path,
            utm_width,
            utm_height,
            1,
            gdal.GDT_Float64,
            options=['COMPRESS=LZW', 'TILED=YES', 'BIGTIFF=YES']
        )

        if out_dataset is None:
            raise IOError(f"无法创建输出文件: {output_path}")

        # 输出投影使用 UTM EPSG
        utm_srs_obj = osr.SpatialReference()
        utm_srs_obj.ImportFromEPSG(utm_epsg)
        utm_wkt = utm_srs_obj.ExportToWkt()

        out_dataset.SetGeoTransform(ac_utm_gt)
        out_dataset.SetProjection(utm_wkt)

        out_band = out_dataset.GetRasterBand(1)
        out_band.WriteArray(dn_data)
        out_band.SetNoDataValue(NODATA_VALUE)

        out_band.FlushCache()
        try:
            out_band.ComputeStatistics(False)
        except Exception as stat_exc:
            logger.warning("计算栅格统计信息失败（不影响数据）: %s", stat_exc)

        logger.info(
            "Dn.tif 已成功输出到: %s（UTM EPSG:%d，像元 30m）", output_path, utm_epsg
        )

    finally:
        # ----------------------------------------------------------
        # 14. 确保 GDAL 资源释放
        # ----------------------------------------------------------
        if out_dataset is not None:
            out_dataset = None
        if ia_utm_ds is not None:
            ia_utm_ds = None
        if ac_utm_ds is not None:
            ac_utm_ds = None
        if ia_mem_ds is not None:
            ia_mem_ds = None
        if ac_mem_ds is not None:
            ac_mem_ds = None
        if ia_dataset is not None:
            ia_dataset = None
        if ac_dataset is not None:
            ac_dataset = None


# ============================================================
# 使用示例
# ============================================================
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
    )

    ac_tif_path = r"C:\地质\ac\全国ac分布\ac.tif"
    ia_tif_path = "../../data/geology/ia/Ia.tif"
    output_path = "../../data/geology/ia/Dn.tif"

    epicenter_lon = 103.36
    epicenter_lat = 34.09
    magnitude = 3.0

    # 示例1：不传入烈度KML（与原逻辑完全一致）
    calculate_dn_optimized(
        ac_tif_path=ac_tif_path,
        ia_tif_path=ia_tif_path,
        output_path=output_path,
        epicenter_lon=epicenter_lon,
        epicenter_lat=epicenter_lat,
        magnitude=magnitude
    )

    # 示例2：传入烈度KML文件，与原始范围合并确定输出范围
    intensity_kml_path = "../../data/geology/ia/烈度.kml"
    calculate_dn_optimized(
        ac_tif_path=ac_tif_path,
        ia_tif_path=ia_tif_path,
        output_path=output_path,
        epicenter_lon=epicenter_lon,
        epicenter_lat=epicenter_lat,
        magnitude=magnitude,
        intensity_kml_path=intensity_kml_path
    )