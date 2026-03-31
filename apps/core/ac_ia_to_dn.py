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


# ============================================================
# 公共接口
# ============================================================

def calculate_dn_optimized(ac_tif_path, ia_tif_path, output_path,
                           epicenter_lon, epicenter_lat, magnitude):
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

    赋值规则:
        - ac 为 nodata → Dn = nodata (-9999)
        - ac 有值, Ia 超出范围或无值或 Ia<=0 → Dn = 0
        - ac 有值, Ia > 0 → 按公式计算 Dn
    """
    logger.info(
        '开始计算Dn.tif: ac=%s ia=%s output=%s lon=%.4f lat=%.4f M=%.1f',
        ac_tif_path, ia_tif_path, output_path,
        epicenter_lon, epicenter_lat, magnitude
    )
    try:
        _calculate_dn_optimized_impl(
            ac_tif_path, ia_tif_path, output_path,
            epicenter_lon, epicenter_lat, magnitude
        )
        logger.info('Dn.tif 计算完成: %s', output_path)
    except Exception as exc:
        logger.error('Dn.tif 计算失败: %s', exc, exc_info=True)
        raise


# ============================================================
# 内部实现
# ============================================================

def _calculate_dn_optimized_impl(ac_tif_path, ia_tif_path, output_path,
                                 epicenter_lon, epicenter_lat, magnitude):
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

    logger.info("经度范围: [%.6f, %.6f]", min_lon, max_lon)
    logger.info("纬度范围: [%.6f, %.6f]", min_lat, max_lat)

    # ----------------------------------------------------------
    # 3. 打开 ac.tif 并读取数据
    # ----------------------------------------------------------
    ac_dataset = None
    ia_dataset = None
    out_dataset = None

    try:
        ac_dataset = gdal.Open(ac_tif_path, gdal.GA_ReadOnly)
        if ac_dataset is None:
            raise IOError(f"无法打开ac.tif文件: {ac_tif_path}")

        _validate_epsg4326(ac_dataset, ac_tif_path)

        ac_geotransform = ac_dataset.GetGeoTransform()
        ac_projection = ac_dataset.GetProjection()
        ac_band = ac_dataset.GetRasterBand(1)
        ac_nodata = ac_band.GetNoDataValue()

        logger.debug("ac.tif GeoTransform: %s", ac_geotransform)
        logger.debug("ac.tif NoData: %s", ac_nodata)

        # 计算 ac.tif 中的像素范围
        col_min, row_max = get_pixel_coords(ac_geotransform, min_lon, min_lat)
        col_max, row_min = get_pixel_coords(ac_geotransform, max_lon, max_lat)

        # 确保像素坐标在有效范围内
        col_min = max(0, col_min)
        row_min = max(0, row_min)
        col_max = min(ac_dataset.RasterXSize, col_max)
        row_max = min(ac_dataset.RasterYSize, row_max)

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

        ac_data_raw = ac_band.ReadAsArray(col_min, row_min, read_width, read_height)
        if ac_data_raw is None:
            raise IOError(
                f"读取 ac.tif 数据失败: "
                f"offset=({col_min},{row_min}), size=({read_width},{read_height})"
            )
        ac_data = ac_data_raw.astype(np.float64)

        # 构建 ac nodata 掩码 (True = 该像素是 nodata)
        ac_is_nodata = _nodata_mask(ac_data, ac_nodata)
        # ac 有效值掩码 (True = 该像素有有效的 ac 值)
        ac_is_valid = ~ac_is_nodata

        # ★ 关键：将 a_c 从 m/s² 转换为 g 单位
        ac_data_g = np.where(ac_is_valid, ac_data / GRAVITY, np.nan)

        valid_count = int(np.sum(ac_is_valid))
        logger.info(
            "ac 数据统计(转换为g后): 有效像素=%d, min=%.6f, max=%.6f",
            valid_count,
            float(np.nanmin(ac_data_g)) if valid_count > 0 else 0,
            float(np.nanmax(ac_data_g)) if valid_count > 0 else 0
        )

        # ----------------------------------------------------------
        # 4. 打开 Ia.tif 并读取数据
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
        # 5. 计算输出的地理变换参数
        # ----------------------------------------------------------
        out_geotransform = list(ac_geotransform)
        out_geotransform[0] = ac_geotransform[0] + col_min * ac_geotransform[1]
        out_geotransform[3] = ac_geotransform[3] + row_min * ac_geotransform[5]

        # ----------------------------------------------------------
        # 6. 创建坐标网格，映射 ac 像素 -> Ia 像素
        # ----------------------------------------------------------
        cols = np.arange(read_width)
        rows = np.arange(read_height)
        col_grid, row_grid = np.meshgrid(cols, rows)

        # 每个像素中心的地理坐标
        lon_grid = out_geotransform[0] + (col_grid + 0.5) * out_geotransform[1]
        lat_grid = out_geotransform[3] + (row_grid + 0.5) * out_geotransform[5]

        # 对应的 Ia.tif 像素坐标
        ia_col_grid = ((lon_grid - ia_geotransform[0]) / ia_geotransform[1]).astype(int)
        ia_row_grid = ((lat_grid - ia_geotransform[3]) / ia_geotransform[5]).astype(int)

        # Ia 坐标在有效像素范围内的掩码
        in_ia_bounds = (
            (ia_col_grid >= 0) &
            (ia_col_grid < ia_dataset.RasterXSize) &
            (ia_row_grid >= 0) &
            (ia_row_grid < ia_dataset.RasterYSize)
        )

        # ----------------------------------------------------------
        # 7. 初始化 Dn 数组 + 读取 Ia 数据并映射
        # ----------------------------------------------------------
        # ★ 赋值策略:
        #   - 先全部初始化为 NODATA_VALUE
        #   - ac 有值的位置先赋 0（覆盖 nodata，包括 Ia 无值的情况）
        #   - ac 有值 & Ia 有值 & Ia>0 的位置按公式计算后覆盖
        dn_data = np.full((read_height, read_width), NODATA_VALUE, dtype=np.float64)
        dn_data[ac_is_valid] = 0.0

        if not np.any(in_ia_bounds & ac_is_valid):
            # ac 有效区域与 Ia 没有任何重叠，ac 有值处已经是 0，完成
            logger.warning(
                "ac 有效区域与 Ia.tif 没有重叠，所有 ac 有值处 Dn 赋值为 0"
            )
        else:
            # 计算 Ia.tif 需要读取的最小包围矩形
            need_read = in_ia_bounds & ac_is_valid
            ia_col_min = int(np.min(ia_col_grid[need_read]))
            ia_col_max = int(np.max(ia_col_grid[need_read])) + 1
            ia_row_min = int(np.min(ia_row_grid[need_read]))
            ia_row_max = int(np.max(ia_row_grid[need_read])) + 1

            ia_col_min = max(0, ia_col_min)
            ia_col_max = min(ia_dataset.RasterXSize, ia_col_max)
            ia_row_min = max(0, ia_row_min)
            ia_row_max = min(ia_dataset.RasterYSize, ia_row_max)

            ia_read_width = ia_col_max - ia_col_min
            ia_read_height = ia_row_max - ia_row_min

            if ia_read_width <= 0 or ia_read_height <= 0:
                raise ValueError("Ia.tif 读取区域计算异常，宽度或高度 <= 0")

            logger.info(
                "读取Ia.tif区域: 起始列=%d, 起始行=%d, 宽度=%d, 高度=%d",
                ia_col_min, ia_row_min, ia_read_width, ia_read_height
            )

            ia_data_raw = ia_band.ReadAsArray(
                ia_col_min, ia_row_min, ia_read_width, ia_read_height
            )
            if ia_data_raw is None:
                raise IOError(
                    f"读取 Ia.tif 数据失败: "
                    f"offset=({ia_col_min},{ia_row_min}), "
                    f"size=({ia_read_width},{ia_read_height})"
                )
            ia_data = ia_data_raw.astype(np.float64)

            # 调整 Ia 索引到局部坐标
            local_ia_col = ia_col_grid - ia_col_min
            local_ia_row = ia_row_grid - ia_row_min

            # 有效映射掩码：在 Ia 边界内 & 局部坐标合法 & ac 有效
            mapped_mask = (
                in_ia_bounds &
                ac_is_valid &
                (local_ia_col >= 0) &
                (local_ia_col < ia_read_width) &
                (local_ia_row >= 0) &
                (local_ia_row < ia_read_height)
            )

            # 从 Ia 数据中取值（仅对 mapped_mask 为 True 的位置）
            ia_values = np.zeros((read_height, read_width), dtype=np.float64)
            mapped_rows, mapped_cols = np.where(mapped_mask)
            if mapped_rows.size > 0:
                ia_values[mapped_rows, mapped_cols] = ia_data[
                    local_ia_row[mapped_rows, mapped_cols],
                    local_ia_col[mapped_rows, mapped_cols]
                ]

            # 处理 Ia 的 nodata → 当作无值，对应 Dn 保持 0
            ia_is_nodata = _nodata_mask(ia_values, ia_nodata)
            # Ia 有效 = 在映射范围内 & 不是 nodata & 值 > 0
            ia_is_usable = mapped_mask & (~ia_is_nodata) & (ia_values > 0)

            ia_usable_count = int(np.sum(ia_is_usable))
            logger.info(
                "Ia 数据统计: 映射像素=%d, 可用像素(>0)=%d",
                int(np.sum(mapped_mask)), ia_usable_count
            )
            if ia_usable_count > 0:
                logger.info(
                    "Ia 可用值范围: min=%.6f, max=%.6f",
                    float(np.min(ia_values[ia_is_usable])),
                    float(np.max(ia_values[ia_is_usable]))
                )

            # ----------------------------------------------------------
            # 8. 向量化计算 Dn
            # ----------------------------------------------------------
            # 公式: log10(Dn) = 1.299 + 1.076*log10(Ia) - 12.197*a_c + 5.434*a_c*log10(Ia)
            #   Ia 单位: m/s
            #   a_c 单位: g (已转换)
            #   Dn 单位: cm
            #
            # 计算掩码: ac 有值 & Ia 可用 (在范围内 & 非nodata & >0)
            calc_mask = ac_is_valid & ia_is_usable

            if np.any(calc_mask):
                with np.errstate(divide='ignore', invalid='ignore'):
                    log_ia = np.log10(ia_values[calc_mask])
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
            #   ac有值 & (Ia超范围 | Ia=nodata | Ia<=0) → 0
            #   ac有值 & Ia>0 → 公式计算值

        # ----------------------------------------------------------
        # 9. 写入输出文件
        # ----------------------------------------------------------
        logger.info("开始写入输出文件: %s", output_path)

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
            read_width,
            read_height,
            1,
            gdal.GDT_Float64,
            options=['COMPRESS=LZW', 'TILED=YES', 'BIGTIFF=YES']
        )

        if out_dataset is None:
            raise IOError(f"无法创建输出文件: {output_path}")

        out_dataset.SetGeoTransform(tuple(out_geotransform))
        out_dataset.SetProjection(ac_projection)

        out_band = out_dataset.GetRasterBand(1)
        out_band.WriteArray(dn_data)
        out_band.SetNoDataValue(NODATA_VALUE)

        out_band.FlushCache()
        try:
            out_band.ComputeStatistics(False)
        except Exception as stat_exc:
            logger.warning("计算栅格统计信息失败（不影响数据）: %s", stat_exc)

        logger.info("Dn.tif 已成功输出到: %s", output_path)

    finally:
        # ----------------------------------------------------------
        # 10. 确保 GDAL 资源释放
        # ----------------------------------------------------------
        if out_dataset is not None:
            out_dataset = None
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

    calculate_dn_optimized(
        ac_tif_path=ac_tif_path,
        ia_tif_path=ia_tif_path,
        output_path=output_path,
        epicenter_lon=epicenter_lon,
        epicenter_lat=epicenter_lat,
        magnitude=magnitude
    )