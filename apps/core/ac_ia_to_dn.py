# -*- coding: utf-8 -*-
"""
基于QGIS 3.40.15的Python环境
计算滑动距离Dn并输出Dn.tif

公式: log(Dn) = 1.299 + 1.076*log(Ia) - 12.197*a_c + 5.434*a_c*log(Ia)
"""

from osgeo import gdal, osr
import numpy as np
import math


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
    """
    # 纬度方向：1度约等于111km
    lat_degree = km / 111.0
    # 经度方向：受纬度影响，1度 = 111 * cos(latitude) km
    lon_degree = km / (111.0 * math.cos(math.radians(latitude)))
    return lat_degree, lon_degree


def get_pixel_coords(geotransform, lon, lat):
    """
    将地理坐标转换为像素坐标
    geotransform: (origin_x, pixel_width, 0, origin_y, 0, pixel_height)
    """
    origin_x = geotransform[0]
    pixel_width = geotransform[1]
    origin_y = geotransform[3]
    pixel_height = geotransform[5]  # 通常为负值

    col = int((lon - origin_x) / pixel_width)
    row = int((lat - origin_y) / pixel_height)

    return col, row


def get_geo_coords(geotransform, col, row):
    """
    将像素坐标转换为地理坐标
    """
    origin_x = geotransform[0]
    pixel_width = geotransform[1]
    origin_y = geotransform[3]
    pixel_height = geotransform[5]

    lon = origin_x + col * pixel_width + pixel_width / 2
    lat = origin_y + row * pixel_height + pixel_height / 2

    return lon, lat


def calculate_dn(ac_tif_path, ia_tif_path, output_path, epicenter_lon, epicenter_lat, magnitude):
    """
    计算滑动距离Dn并输出Dn.tif

    参数:
        ac_tif_path: ac.tif文件路径
        ia_tif_path: Ia.tif文件路径
        output_path: 输出Dn.tif文件路径
        epicenter_lon: 震中经度
        epicenter_lat: 震中纬度
        magnitude: 震级
    """

    # 获取搜索半径
    radius_km = get_search_radius(magnitude)
    print(f"震级: {magnitude}, 搜索半径: {radius_km} km")

    # 计算经纬度范围
    lat_range, lon_range = km_to_degree(radius_km, epicenter_lat)

    min_lon = epicenter_lon - lon_range
    max_lon = epicenter_lon + lon_range
    min_lat = epicenter_lat - lat_range
    max_lat = epicenter_lat + lat_range

    print(f"经度范围: [{min_lon}, {max_lon}]")
    print(f"纬度范围: [{min_lat}, {max_lat}]")

    # 打开ac.tif文件（只读模式）
    ac_dataset = gdal.Open(ac_tif_path, gdal.GA_ReadOnly)
    if ac_dataset is None:
        raise Exception(f"无法打开ac.tif文件: {ac_tif_path}")

    ac_geotransform = ac_dataset.GetGeoTransform()
    ac_projection = ac_dataset.GetProjection()
    ac_band = ac_dataset.GetRasterBand(1)
    ac_nodata = ac_band.GetNoDataValue()

    # 计算ac.tif中的像素范围
    col_min, row_max = get_pixel_coords(ac_geotransform, min_lon, min_lat)
    col_max, row_min = get_pixel_coords(ac_geotransform, max_lon, max_lat)

    # 确保像素坐标在有效范围内
    col_min = max(0, col_min)
    row_min = max(0, row_min)
    col_max = min(ac_dataset.RasterXSize, col_max)
    row_max = min(ac_dataset.RasterYSize, row_max)

    # 计算读取区域的宽度和高度
    read_width = col_max - col_min
    read_height = row_max - row_min

    if read_width <= 0 or read_height <= 0:
        raise Exception("指定的震中坐标超出ac.tif的范围")

    print(f"读取ac.tif区域: 起始列={col_min}, 起始行={row_min}, 宽度={read_width}, 高度={read_height}")

    # 读取ac.tif指定区域的数据
    ac_data = ac_band.ReadAsArray(col_min, row_min, read_width, read_height)
    ac_data = ac_data.astype(np.float64)

    # 打开Ia.tif文件
    ia_dataset = gdal.Open(ia_tif_path, gdal.GA_ReadOnly)
    if ia_dataset is None:
        raise Exception(f"无法打开Ia.tif文件: {ia_tif_path}")

    ia_geotransform = ia_dataset.GetGeoTransform()
    ia_band = ia_dataset.GetRasterBand(1)
    ia_nodata = ia_band.GetNoDataValue()

    # 初始化Dn数组
    dn_data = np.zeros((read_height, read_width), dtype=np.float64)

    # 计算输出数据的地理变换参数
    out_geotransform = list(ac_geotransform)
    out_geotransform[0] = ac_geotransform[0] + col_min * ac_geotransform[1]  # 新的origin_x
    out_geotransform[3] = ac_geotransform[3] + row_min * ac_geotransform[5]  # 新的origin_y

    # 逐像素计算Dn
    print("开始计算Dn...")

    # 使用分块处理以提高效率
    block_size = 256

    for block_row in range(0, read_height, block_size):
        block_height = min(block_size, read_height - block_row)

        for block_col in range(0, read_width, block_size):
            block_width = min(block_size, read_width - block_col)

            # 获取当前块的ac数据
            ac_block = ac_data[block_row:block_row + block_height,
                       block_col:block_col + block_width]

            # 初始化当前块的Dn数据
            dn_block = np.zeros((block_height, block_width), dtype=np.float64)

            # 遍历当前块的每个像素
            for i in range(block_height):
                for j in range(block_width):
                    # 获取当前像素的地理坐标
                    global_col = col_min + block_col + j
                    global_row = row_min + block_row + i

                    lon, lat = get_geo_coords(ac_geotransform, global_col, global_row)

                    # 获取ac值
                    a_c = ac_block[i, j]

                    # 检查ac是否为nodata
                    if ac_nodata is not None and a_c == ac_nodata:
                        dn_block[i, j] = 0
                        continue

                    # 在Ia.tif中查找对应坐标的值
                    ia_col, ia_row = get_pixel_coords(ia_geotransform, lon, lat)

                    # 检查是否在Ia.tif范围内
                    if (ia_col < 0 or ia_col >= ia_dataset.RasterXSize or
                            ia_row < 0 or ia_row >= ia_dataset.RasterYSize):
                        dn_block[i, j] = 0
                        continue

                    # 读取Ia值（单个像素）
                    ia_value = ia_band.ReadAsArray(ia_col, ia_row, 1, 1)

                    if ia_value is None:
                        dn_block[i, j] = 0
                        continue

                    ia = float(ia_value[0, 0])

                    # 检查Ia是否为nodata或无效值
                    if ia_nodata is not None and ia == ia_nodata:
                        dn_block[i, j] = 0
                        continue

                    if ia <= 0:
                        # Ia必须大于0才能计算log
                        dn_block[i, j] = 0
                        continue

                    # 计算Dn
                    # 公式: log(Dn) = 1.299 + 1.076*log(Ia) - 12.197*a_c + 5.434*a_c*log(Ia)
                    try:
                        log_ia = np.log10(ia)
                        log_dn = 1.299 + 1.076 * log_ia - 12.197 * a_c + 5.434 * a_c * log_ia
                        dn = 10 ** log_dn
                        dn_block[i, j] = dn
                    except Exception as e:
                        dn_block[i, j] = 0

            # 将当前块的结果写入dn_data
            dn_data[block_row:block_row + block_height,
            block_col:block_col + block_width] = dn_block

        # 打印进度
        progress = min(100, int((block_row + block_height) / read_height * 100))
        print(f"��算进度: {progress}%")

    print("Dn计算完成，开始写入输出文件...")

    # 创建输出文件
    driver = gdal.GetDriverByName('GTiff')
    out_dataset = driver.Create(
        output_path,
        read_width,
        read_height,
        1,
        gdal.GDT_Float64,
        options=['COMPRESS=LZW', 'TILED=YES', 'BIGTIFF=YES']
    )

    if out_dataset is None:
        raise Exception(f"无法创建输出文件: {output_path}")

    # 设置地理变换参数
    out_dataset.SetGeoTransform(tuple(out_geotransform))

    # 设置投影
    out_dataset.SetProjection(ac_projection)

    # 写入数据
    out_band = out_dataset.GetRasterBand(1)
    out_band.WriteArray(dn_data)
    out_band.SetNoDataValue(0)

    # 计算统计信息
    out_band.FlushCache()
    out_band.ComputeStatistics(False)

    # 关闭数据集
    out_dataset = None
    ac_dataset = None
    ia_dataset = None

    print(f"Dn.tif已成功输出到: {output_path}")


def calculate_dn_optimized(ac_tif_path, ia_tif_path, output_path, epicenter_lon, epicenter_lat, magnitude):
    """
    优化版本：使用向量化计算提高效率
    计算滑动距离Dn并输出Dn.tif

    参数:
        ac_tif_path: ac.tif文件路径
        ia_tif_path: Ia.tif文件路径
        output_path: 输出Dn.tif文件路径
        epicenter_lon: 震中经度
        epicenter_lat: 震中纬度
        magnitude: 震级
    """

    # 获取搜索半径
    radius_km = get_search_radius(magnitude)
    print(f"震级: {magnitude}, 搜索半径: {radius_km} km")

    # 计算经纬度范围
    lat_range, lon_range = km_to_degree(radius_km, epicenter_lat)

    min_lon = epicenter_lon - lon_range
    max_lon = epicenter_lon + lon_range
    min_lat = epicenter_lat - lat_range
    max_lat = epicenter_lat + lat_range

    print(f"经度范围: [{min_lon}, {max_lon}]")
    print(f"纬度范围: [{min_lat}, {max_lat}]")

    # 打开ac.tif文件（只读模式）
    ac_dataset = gdal.Open(ac_tif_path, gdal.GA_ReadOnly)
    if ac_dataset is None:
        raise Exception(f"无法打开ac.tif文件: {ac_tif_path}")

    ac_geotransform = ac_dataset.GetGeoTransform()
    ac_projection = ac_dataset.GetProjection()
    ac_band = ac_dataset.GetRasterBand(1)
    ac_nodata = ac_band.GetNoDataValue()

    # 计算ac.tif中的像素范围
    col_min, row_max = get_pixel_coords(ac_geotransform, min_lon, min_lat)
    col_max, row_min = get_pixel_coords(ac_geotransform, max_lon, max_lat)

    # 确保像素坐标在有效范围内
    col_min = max(0, col_min)
    row_min = max(0, row_min)
    col_max = min(ac_dataset.RasterXSize, col_max)
    row_max = min(ac_dataset.RasterYSize, row_max)

    # 计算读取区域的宽度和高度
    read_width = col_max - col_min
    read_height = row_max - row_min

    if read_width <= 0 or read_height <= 0:
        raise Exception("指定的震中坐标超出ac.tif的范围")

    print(f"读取ac.tif区域: 起始列={col_min}, 起始行={row_min}, 宽度={read_width}, 高度={read_height}")

    # 读取ac.tif指定区域的数据
    ac_data = ac_band.ReadAsArray(col_min, row_min, read_width, read_height)
    ac_data = ac_data.astype(np.float64)

    # 打开Ia.tif文件
    ia_dataset = gdal.Open(ia_tif_path, gdal.GA_ReadOnly)
    if ia_dataset is None:
        raise Exception(f"无法打开Ia.tif文件: {ia_tif_path}")

    ia_geotransform = ia_dataset.GetGeoTransform()
    ia_band = ia_dataset.GetRasterBand(1)
    ia_nodata = ia_band.GetNoDataValue()

    # 计算输出数据的地理变换参数
    out_geotransform = list(ac_geotransform)
    out_geotransform[0] = ac_geotransform[0] + col_min * ac_geotransform[1]  # 新的origin_x
    out_geotransform[3] = ac_geotransform[3] + row_min * ac_geotransform[5]  # 新的origin_y

    # 创建坐标网格
    cols = np.arange(read_width)
    rows = np.arange(read_height)
    col_grid, row_grid = np.meshgrid(cols, rows)

    # 计算每个像素的地理坐标
    lon_grid = out_geotransform[0] + (col_grid + 0.5) * out_geotransform[1]
    lat_grid = out_geotransform[3] + (row_grid + 0.5) * out_geotransform[5]

    # 计算对应的Ia.tif像素坐标
    ia_col_grid = ((lon_grid - ia_geotransform[0]) / ia_geotransform[1]).astype(int)
    ia_row_grid = ((lat_grid - ia_geotransform[3]) / ia_geotransform[5]).astype(int)

    # 创建有效范围掩码
    valid_mask = (
            (ia_col_grid >= 0) &
            (ia_col_grid < ia_dataset.RasterXSize) &
            (ia_row_grid >= 0) &
            (ia_row_grid < ia_dataset.RasterYSize)
    )

    # 计算Ia.tif需要读取的范围
    if not np.any(valid_mask):
        print("警告：没有与Ia.tif重叠的区域，所有Dn值将设为0")
        dn_data = np.zeros((read_height, read_width), dtype=np.float64)
    else:
        ia_col_min = max(0, np.min(ia_col_grid[valid_mask]))
        ia_col_max = min(ia_dataset.RasterXSize, np.max(ia_col_grid[valid_mask]) + 1)
        ia_row_min = max(0, np.min(ia_row_grid[valid_mask]))
        ia_row_max = min(ia_dataset.RasterYSize, np.max(ia_row_grid[valid_mask]) + 1)

        ia_read_width = ia_col_max - ia_col_min
        ia_read_height = ia_row_max - ia_row_min

        print(f"读取Ia.tif区域: 起始列={ia_col_min}, 起始行={ia_row_min}, 宽度={ia_read_width}, 高度={ia_read_height}")

        # 读取Ia.tif对应区域的数据
        ia_data = ia_band.ReadAsArray(ia_col_min, ia_row_min, ia_read_width, ia_read_height)
        ia_data = ia_data.astype(np.float64)

        # 初始化Ia值数组
        ia_values = np.zeros((read_height, read_width), dtype=np.float64)

        # 调整Ia索引到读取区域的局部坐标
        local_ia_col = ia_col_grid - ia_col_min
        local_ia_row = ia_row_grid - ia_row_min

        # 更新有效掩码
        valid_mask = (
                valid_mask &
                (local_ia_col >= 0) &
                (local_ia_col < ia_read_width) &
                (local_ia_row >= 0) &
                (local_ia_row < ia_read_height)
        )

        # 获取有效位置的Ia值
        valid_rows, valid_cols = np.where(valid_mask)
        ia_values[valid_rows, valid_cols] = ia_data[
            local_ia_row[valid_rows, valid_cols],
            local_ia_col[valid_rows, valid_cols]
        ]

        # 处理nodata值
        if ia_nodata is not None:
            ia_values[ia_values == ia_nodata] = 0

        # 处理ac的nodata值
        if ac_nodata is not None:
            ac_data[ac_data == ac_nodata] = np.nan

        # 计算Dn
        # 公式: log(Dn) = 1.299 + 1.076*log(Ia) - 12.197*a_c + 5.434*a_c*log(Ia)
        # Dn = 10^(1.299 + 1.076*log10(Ia) - 12.197*a_c + 5.434*a_c*log10(Ia))

        # 创建有效计算掩码（Ia > 0 且 ac有效）
        calc_mask = (ia_values > 0) & (~np.isnan(ac_data)) & valid_mask

        # 初始化Dn数组
        dn_data = np.zeros((read_height, read_width), dtype=np.float64)

        # 向量化计算
        with np.errstate(divide='ignore', invalid='ignore'):
            log_ia = np.zeros_like(ia_values)
            log_ia[calc_mask] = np.log10(ia_values[calc_mask])

            log_dn = np.zeros_like(ia_values)
            log_dn[calc_mask] = (1.299 +
                                 1.076 * log_ia[calc_mask] -
                                 12.197 * ac_data[calc_mask] +
                                 5.434 * ac_data[calc_mask] * log_ia[calc_mask])

            dn_data[calc_mask] = np.power(10, log_dn[calc_mask])

        # 处理无效值
        dn_data[~calc_mask] = 0
        dn_data[np.isnan(dn_data)] = 0
        dn_data[np.isinf(dn_data)] = 0

    print("Dn计算完成，开始写入输出文件...")

    # 创建输出文件
    driver = gdal.GetDriverByName('GTiff')
    out_dataset = driver.Create(
        output_path,
        read_width,
        read_height,
        1,
        gdal.GDT_Float64,
        options=['COMPRESS=LZW', 'TILED=YES', 'BIGTIFF=YES']
    )

    if out_dataset is None:
        raise Exception(f"无法创建输出文件: {output_path}")

    # 设置地理变换参数
    out_dataset.SetGeoTransform(tuple(out_geotransform))

    # 设置投影
    out_dataset.SetProjection(ac_projection)

    # 写入数据
    out_band = out_dataset.GetRasterBand(1)
    out_band.WriteArray(dn_data)
    out_band.SetNoDataValue(0)

    # 计算统计信息
    out_band.FlushCache()
    out_band.ComputeStatistics(False)

    # 关闭数据集
    out_dataset = None
    ac_dataset = None
    ia_dataset = None

    print(f"Dn.tif已成功输出到: {output_path}")


# 使用示例
if __name__ == "__main__":
    # 输入参数
    ac_tif_path = r"C:\地质\ac\ac分割版\ac2.TIF"  # ac.tif文件路径
    ia_tif_path ="../../data/geology/ia/Ia.tif" # Ia.tif文件路径
    output_path = "../../data/geology/ia/Dn.tif"  # 输出Dn.tif文件路径

    # 震中坐标和震级
    epicenter_lon = 103.36 # 震中经度
    epicenter_lat = 34.09  # 震中纬度
    magnitude = 7.0  # 震级

    # 调用优化版本的计算函数
    calculate_dn_optimized(
        ac_tif_path=ac_tif_path,
        ia_tif_path=ia_tif_path,
        output_path=output_path,
        epicenter_lon=epicenter_lon,
        epicenter_lat=epicenter_lat,
        magnitude=magnitude
    )