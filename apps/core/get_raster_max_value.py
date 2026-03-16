"""
从 a.tif 随机获取1000个经纬度点，获取 a.tif 和 b.tif 对应位置的值
适用于 QGIS 3.40.15 Python 环境
坐标系统: EPSG:4326 - WGS 84
"""

import random
from qgis.core import (
    QgsRasterLayer,
    QgsPointXY,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProject,
    QgsRaster
)


def get_valid_random_points_from_raster(raster_path, num_points=1000, band_number=1):
    """
    从栅格文件中随机获取有效（非NoData）的点坐标及其值

    参数:
        raster_path: str - 栅格文件路径
        num_points: int - 需要获取的点数量
        band_number: int - 波段编号

    返回:
        list - 包含 (经度, 纬度, 值) 的列表
    """

    # 加载栅格图层
    raster_layer = QgsRasterLayer(raster_path, 'raster_a')

    if not raster_layer.isValid():
        print(f"错误: 无法加载栅格文件: {raster_path}")
        return None

    # 获取数据提供者
    provider = raster_layer.dataProvider()

    # 获取栅格范围和尺寸
    extent = raster_layer.extent()
    width = raster_layer.width()
    height = raster_layer.height()

    # 计算像素分辨率
    pixel_width = extent.width() / width
    pixel_height = extent.height() / height

    # 读取栅格数据块
    block = provider.block(band_number, extent, width, height)

    # 获取栅格的坐标参考系统
    raster_crs = raster_layer.crs()
    target_crs = QgsCoordinateReferenceSystem("EPSG:4326")

    # 创建坐标转换器（如果需要）
    need_transform = raster_crs != target_crs
    if need_transform:
        transform = QgsCoordinateTransform(raster_crs, target_crs, QgsProject.instance())
        print(f"a.tif 坐标系: {raster_crs.authid()}, 将转换到 EPSG:4326")

    # 收集所有有效像素的位置
    print("正在扫描 a.tif 中的有效像素...")
    valid_pixels = []
    for row in range(height):
        for col in range(width):
            if not block.isNoData(col, row):
                valid_pixels.append((col, row))

    total_valid = len(valid_pixels)
    print(f"a.tif 中有效像素总数: {total_valid}")

    if total_valid == 0:
        print("错误: 栅格中没有有效数据")
        return None

    # 确定实际采样数量
    actual_num_points = min(num_points, total_valid)
    if actual_num_points < num_points:
        print(f"警告: 有效像素不足 {num_points} 个，将采样 {actual_num_points} 个点")

    # 随机采样
    sampled_pixels = random.sample(valid_pixels, actual_num_points)

    # 转换为经纬度坐标并获取值
    result_points = []
    for col, row in sampled_pixels:
        # 获取像素值
        value = block.value(col, row)

        # 计算像素中心点的地理坐标
        x_coord = extent.xMinimum() + (col + 0.5) * pixel_width
        y_coord = extent.yMaximum() - (row + 0.5) * pixel_height

        point = QgsPointXY(x_coord, y_coord)

        # 坐标转换
        if need_transform:
            point = transform.transform(point)

        longitude = point.x()
        latitude = point.y()

        result_points.append((longitude, latitude, value))

    print(f"成功从 a.tif 采样 {len(result_points)} 个点")
    return result_points


def get_raster_value_at_point(raster_layer, provider, longitude, latitude,
                               band_number=1, transform=None):
    """
    获取栅格在指定经纬度处的值

    参数:
        raster_layer: QgsRasterLayer - 栅格图层
        provider: QgsRasterDataProvider - 数据提供者
        longitude: float - 经度
        latitude: float - 纬度
        band_number: int - 波段编号
        transform: QgsCoordinateTransform - 坐标转换器（从4326到栅格坐标系）

    返回:
        float or None - 栅格值，如果无效则返回 None
    """

    # 创建点坐标 (EPSG:4326)
    point = QgsPointXY(longitude, latitude)

    # 如果需要坐标转换
    if transform is not None:
        point = transform.transform(point)

    # 检查点是否在栅格范围内
    extent = raster_layer.extent()
    if not extent.contains(point):
        return None

    # 使用 identify 方法获取值 - 修复：使用正确的枚举类型
    ident = provider.identify(point, QgsRaster.IdentifyFormatValue)

    if ident.isValid():
        results = ident.results()
        if band_number in results:
            value = results[band_number]
            # 检查是否为 None 或 NoData
            if value is not None:
                return value

    return None


def sample_and_compare_rasters(raster_a_path, raster_b_path, num_points=1000, band_number=1):
    """
    主函数：从 a.tif 随机采样点，获取 a 和 b 对应的值

    参数:
        raster_a_path: str - a.tif 文件路径
        raster_b_path: str - b.tif 文件路径
        num_points: int - 采样点数量
        band_number: int - 波段编号

    返回:
        list - 包含结果字典的列表
    """

    print("=" * 60)
    print("开始处理栅格数据...")
    print("=" * 60)

    # 步骤1: 从 a.tif 随机获取点
    print("\n[步骤1] 从 a.tif 随机采样点...")
    sample_points = get_valid_random_points_from_raster(
        raster_a_path, num_points, band_number
    )

    if sample_points is None:
        return None

    # 步骤2: 加载 b.tif
    print("\n[步骤2] 加载 b.tif...")
    raster_b = QgsRasterLayer(raster_b_path, 'raster_b')

    if not raster_b.isValid():
        print(f"错误: 无法加载栅格文件: {raster_b_path}")
        return None

    provider_b = raster_b.dataProvider()

    # 检查 b.tif 的坐标系，创建坐标转换器
    raster_b_crs = raster_b.crs()
    target_crs = QgsCoordinateReferenceSystem("EPSG:4326")

    transform_b = None
    if raster_b_crs != target_crs:
        # 从 EPSG:4326 转换到 b.tif 的坐标系
        transform_b = QgsCoordinateTransform(target_crs, raster_b_crs, QgsProject.instance())
        print(f"b.tif 坐标系: {raster_b_crs.authid()}, 将从 EPSG:4326 转换")
    else:
        print(f"b.tif 坐标系: EPSG:4326")

    # 步骤3: 获取 b.tif 对应位置的值
    print("\n[步骤3] 获取 b.tif 对应位置的值...")
    results = []
    valid_count = 0
    invalid_count = 0

    for i, (lon, lat, value_a) in enumerate(sample_points):
        # 获取 b.tif 在该位置的值
        value_b = get_raster_value_at_point(
            raster_b, provider_b, lon, lat, band_number, transform_b
        )

        result = {
            'index': i + 1,
            'longitude': lon,
            'latitude': lat,
            'value_a': value_a,
            'value_b': value_b
        }
        results.append(result)

        if value_b is not None:
            valid_count += 1
        else:
            invalid_count += 1

        # 显示进度
        if (i + 1) % 200 == 0:
            print(f"  已处理 {i + 1}/{len(sample_points)} 个点...")

    print(f"\n处理完成!")
    print(f"  - 在 b.tif 中有值的点: {valid_count}")
    print(f"  - 在 b.tif 中无值的点 (超出范围或NoData): {invalid_count}")

    return results


def print_results(results, max_display=50):
    """
    打印结果

    参数:
        results: list - 结果列表
        max_display: int - 最大显示行数
    """

    if results is None:
        return

    print("\n" + "=" * 90)
    print("采样结果")
    print("=" * 90)
    print(f"{'序号':<6} {'经度':<15} {'纬度':<15} {'a.tif值':<15} {'b.tif值':<15}")
    print("-" * 90)

    display_count = min(len(results), max_display)

    for i in range(display_count):
        r = results[i]
        value_b_str = f"{r['value_b']:.6f}" if r['value_b'] is not None else "N/A"
        print(f"{r['index']:<6} {r['longitude']:<15.6f} {r['latitude']:<15.6f} "
              f"{r['value_a']:<15.6f} {value_b_str:<15}")

    if len(results) > max_display:
        print(f"... 共 {len(results)} 条记录，仅显示前 {max_display} 条 ...")

    print("-" * 90)


def export_to_csv(results, output_path):
    """
    将结果导出为 CSV 文件

    参数:
        results: list - 结果列表
        output_path: str - 输出文件路径
    """

    if results is None:
        return

    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            # 写入表头
            f.write("序号,经度,纬度,a_value,b_value\n")

            # 写入数据
            for r in results:
                value_b_str = str(r['value_b']) if r['value_b'] is not None else ""
                f.write(f"{r['index']},{r['longitude']:.8f},{r['latitude']:.8f},"
                        f"{r['value_a']},{value_b_str}\n")

        print(f"\n结果已导出到: {output_path}")
    except Exception as e:
        print(f"导出CSV时出错: {str(e)}")


# ==================== 使用示例 ====================
if __name__ == '__console__' or __name__ == '__main__':

    # ========== 请修改以下路径为您的实际文件路径 ==========
    # 注意：使用原始字符串 r"..." 或正斜杠 "/" 避免转义问题
    raster_a_path = "../../data/geology/ia/dn.tif"  # a.tif 文件路径
    raster_b_path = r"E:\code\python\地质\计算结果\dnn.tif"  # b.tif 文件路径
    output_csv_path = "../../data/geology/ia/sample_results.csv"  # 输出 CSV 文件路径

    # 采样点数量
    sample_count = 1000

    # 波段编号（默认为1）
    band = 1
    # ==================================================

    # 执行采样和对比
    results = sample_and_compare_rasters(
        raster_a_path,
        raster_b_path,
        num_points=sample_count,
        band_number=band
    )

    # 打印结果（默认显示前50条）
    print_results(results, max_display=50)

    # 导出完整结果到 CSV 文件
    export_to_csv(results, output_csv_path)

    print("\n" + "=" * 60)
    print("全部处理完成!")
    print("=" * 60)