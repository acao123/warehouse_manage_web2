"""
QGIS 3.40.15 Python脚本
读取TIF文件的唯一像素值(UniqueValue)及其颜色表中的RGBA颜色
不依赖NumPy
"""

from osgeo import gdal
import struct

# 显式设置GDAL异常处理
gdal.UseExceptions()


def get_unique_values_and_colors(tif_path, sample_step=1):
    """
    从TIF文件读取唯一像素值及其对应的颜色

    参数:
        tif_path: TIF文件路径
        sample_step: 采样步长，1表示读取所有像素
    """

    print(f"数据源: {tif_path}")
    print("=" * 80)

    ds = gdal.Open(tif_path)
    if ds is None:
        print(f"错误：无法打开文件 {tif_path}")
        return None

    width = ds.RasterXSize
    height = ds.RasterYSize
    band_count = ds.RasterCount

    print(f"栅格尺寸: {width} x {height}")
    print(f"波段数量: {band_count}")

    # 获取第一波段
    band = ds.GetRasterBand(1)
    data_type = band.DataType
    nodata = band.GetNoDataValue()

    print(f"数据类型: {gdal.GetDataTypeName(data_type)}")
    print(f"NoData值: {nodata}")

    # 检查是否有颜色表
    color_table = band.GetColorTable()
    has_color_table = color_table is not None

    if has_color_table:
        print(f"颜色表: 有 ({color_table.GetCount()} 个条目)")
    else:
        print("颜色表: 无")

    # 如果没有颜色表但有4个波段，说明颜色存储在RGBA波段中
    rgba_bands = None
    if not has_color_table and band_count >= 3:
        print("检测到RGB/RGBA波段格式")
        rgba_bands = {
            'R': ds.GetRasterBand(1),
            'G': ds.GetRasterBand(2),
            'B': ds.GetRasterBand(3),
            'A': ds.GetRasterBand(4) if band_count >= 4 else None
        }

    print("=" * 80)

    # 第一步：收集所有唯一像素值
    print("\n[步骤1] 扫描所有唯一像素值...")
    unique_values = collect_unique_values(band, width, height, data_type, nodata, sample_step)
    print(f"找到 {len(unique_values)} 个唯一像素值 (UniqueValue)")

    # 第二步：获取每个像素值对应的颜色
    print("\n[步骤2] 获取对应颜色...")
    results = []

    if has_color_table:
        # 从颜色表获取颜色
        for val in sorted(unique_values):
            idx = int(val)
            if 0 <= idx < color_table.GetCount():
                entry = color_table.GetColorEntry(idx)
                r, g, b, a = entry[0], entry[1], entry[2], entry[3] if len(entry) > 3 else 255
            else:
                r, g, b, a = 0, 0, 0, 255

            results.append({
                'value': val,
                'R': r,
                'G': g,
                'B': b,
                'A': a
            })

    elif rgba_bands:
        # 从RGBA波段采样获取颜色（需要找到每个唯一值对应的颜色）
        # 这种情况下，我们需要记录像素值和位置的对应关系
        print("从RGBA波段读取颜色映射...")
        value_color_map = collect_value_color_mapping(ds, width, height, data_type, nodata, sample_step)

        for val in sorted(unique_values):
            if val in value_color_map:
                r, g, b, a = value_color_map[val]
            else:
                r, g, b, a = 0, 0, 0, 255

            results.append({
                'value': val,
                'R': r,
                'G': g,
                'B': b,
                'A': a
            })

    else:
        # 没有颜色信息，只返回像素值
        for val in sorted(unique_values):
            results.append({
                'value': val,
                'R': 0,
                'G': 0,
                'B': 0,
                'A': 255
            })

    ds = None
    return results


def collect_unique_values(band, width, height, data_type, nodata, sample_step):
    """收集唯一像素值"""

    unique_values = set()
    total_rows = height // sample_step

    for y in range(0, height, sample_step):
        row_data = read_row_values(band, width, y, data_type)

        for x in range(0, width, sample_step):
            val = row_data[x]
            if nodata is None or val != nodata:
                unique_values.add(val)

        # 进度显示
        if y % (height // 10 + 1) == 0:
            percent = int(y * 100 / height)
            print(f"  扫描进度: {percent}%", end='\r')

    print(f"  扫描进度: 100%")
    return unique_values


def read_row_values(band, width, y, data_type):
    """读取一行像素值"""

    if data_type == gdal.GDT_Byte:
        row_data = band.ReadRaster(0, y, width, 1, width, 1, gdal.GDT_Byte)
        return list(row_data)

    elif data_type in [gdal.GDT_UInt16]:
        row_data = band.ReadRaster(0, y, width, 1, width, 1, gdal.GDT_UInt16)
        return list(struct.unpack('H' * width, row_data))

    elif data_type in [gdal.GDT_Int16]:
        row_data = band.ReadRaster(0, y, width, 1, width, 1, gdal.GDT_Int16)
        return list(struct.unpack('h' * width, row_data))

    elif data_type in [gdal.GDT_UInt32]:
        row_data = band.ReadRaster(0, y, width, 1, width, 1, gdal.GDT_UInt32)
        return list(struct.unpack('I' * width, row_data))

    elif data_type in [gdal.GDT_Int32]:
        row_data = band.ReadRaster(0, y, width, 1, width, 1, gdal.GDT_Int32)
        return list(struct.unpack('i' * width, row_data))

    elif data_type == gdal.GDT_Float32:
        row_data = band.ReadRaster(0, y, width, 1, width, 1, gdal.GDT_Float32)
        return list(struct.unpack('f' * width, row_data))

    elif data_type == gdal.GDT_Float64:
        row_data = band.ReadRaster(0, y, width, 1, width, 1, gdal.GDT_Float64)
        return list(struct.unpack('d' * width, row_data))

    else:
        # 默认按Byte处理
        row_data = band.ReadRaster(0, y, width, 1, width, 1, gdal.GDT_Byte)
        return list(row_data)


def collect_value_color_mapping(ds, width, height, data_type, nodata, sample_step):
    """
    对于RGBA格式的TIF，建立像素值到颜色的映射
    假设：第一波段存储分类值，后面波段存储RGBA
    或者：需要从渲染后的颜色反推
    """

    band_count = ds.RasterCount

    # 如果是4波段，假设是RGBA直接存储
    # 需要建立位置到颜色的映射，然后找出每个唯一颜色

    value_color_map = {}

    # 读取所有波段
    bands = [ds.GetRasterBand(i+1) for i in range(min(band_count, 4))]

    for y in range(0, height, sample_step):
        rows = []
        for b in bands:
            row_data = b.ReadRaster(0, y, width, 1, width, 1, gdal.GDT_Byte)
            rows.append(list(row_data))

        for x in range(0, width, sample_step):
            if band_count >= 4:
                r, g, b_val, a = rows[0][x], rows[1][x], rows[2][x], rows[3][x]
                # 对于RGBA格式，我们用颜色组合作为key
                color_key = (r, g, b_val, a)
                if color_key not in value_color_map:
                    value_color_map[color_key] = (r, g, b_val, a)
            elif band_count == 3:
                r, g, b_val = rows[0][x], rows[1][x], rows[2][x]
                color_key = (r, g, b_val, 255)
                if color_key not in value_color_map:
                    value_color_map[color_key] = (r, g, b_val, 255)

    return value_color_map


def get_unique_colors_from_rgba_tif(tif_path, sample_step=1):
    """
    专门处理RGBA格式TIF：直接读取唯一颜色组合
    对于你的4波段TIF，每个唯一的RGBA组合就代表一个分类
    """

    print(f"数据源: {tif_path}")
    print("=" * 80)

    ds = gdal.Open(tif_path)
    if ds is None:
        print(f"错误：无法打开文件 {tif_path}")
        return None

    width = ds.RasterXSize
    height = ds.RasterYSize
    band_count = ds.RasterCount

    print(f"栅格尺寸: {width} x {height}")
    print(f"波段数量: {band_count}")
    print("=" * 80)

    if band_count < 3:
        print("错误：此函数需要RGB/RGBA格式的TIF")
        return None

    # 读取波段
    band_r = ds.GetRasterBand(1)
    band_g = ds.GetRasterBand(2)
    band_b = ds.GetRasterBand(3)
    band_a = ds.GetRasterBand(4) if band_count >= 4 else None

    print(f"\n正在扫描唯一颜色组合...")

    # 收集唯一颜色
    unique_colors = {}  # {(R,G,B,A): pixel_count}

    for y in range(0, height, sample_step):
        row_r = list(band_r.ReadRaster(0, y, width, 1, width, 1, gdal.GDT_Byte))
        row_g = list(band_g.ReadRaster(0, y, width, 1, width, 1, gdal.GDT_Byte))
        row_b = list(band_b.ReadRaster(0, y, width, 1, width, 1, gdal.GDT_Byte))
        row_a = list(band_a.ReadRaster(0, y, width, 1, width, 1, gdal.GDT_Byte)) if band_a else [255] * width

        for x in range(0, width, sample_step):
            color = (row_r[x], row_g[x], row_b[x], row_a[x])
            if color in unique_colors:
                unique_colors[color] += 1
            else:
                unique_colors[color] = 1

        if y % (height // 10 + 1) == 0:
            percent = int(y * 100 / height)
            print(f"  进度: {percent}%", end='\r')

    print(f"  进度: 100%")
    print(f"\n找到 {len(unique_colors)} 个唯一颜色 (UniqueValue)")

    ds = None

    # 构建结果，按像素数量排序
    results = []
    for i, ((r, g, b, a), count) in enumerate(sorted(unique_colors.items(), key=lambda x: -x[1]), 1):
        results.append({
            'index': i,
            'value': i,  # 用序号作为UniqueValue
            'R': r,
            'G': g,
            'B': b,
            'A': a,
            'count': count
        })

    return results


def print_results(results):
    """格式化打印结果"""
    if not results:
        print("没有结果")
        return

    print("\n" + "=" * 90)
    print("UniqueValue 像素值及其对应的 RGBA 颜色:")
    print("=" * 90)

    # 判断结果类型
    has_count = 'count' in results[0]

    if has_count:
        print(f"{'序号':<6} | {'UniqueValue':<15} | {'R':<5} | {'G':<5} | {'B':<5} | {'A':<5} | {'HEX颜色':<10} | {'像素数量':<12}")
        print("-" * 90)

        for item in results:
            idx = item.get('index', item['value'])
            val = item['value']
            r, g, b, a = item['R'], item['G'], item['B'], item['A']
            count = item['count']
            hex_color = f"#{r:02x}{g:02x}{b:02x}"

            print(f"{idx:<6} | {val:<15} | {r:<5} | {g:<5} | {b:<5} | {a:<5} | {hex_color:<10} | {count:<12}")
    else:
        print(f"{'序号':<6} | {'UniqueValue.像素值':<20} | {'R':<5} | {'G':<5} | {'B':<5} | {'A':<5} | {'HEX颜色':<10}")
        print("-" * 85)

        for i, item in enumerate(results, 1):
            val = item['value']
            r, g, b, a = item['R'], item['G'], item['B'], item['A']
            hex_color = f"#{r:02x}{g:02x}{b:02x}"

            # 格式化像素值
            if isinstance(val, float):
                val_str = f"{val:.6f}"
            else:
                val_str = str(val)

            print(f"{i:<6} | {val_str:<20} | {r:<5} | {g:<5} | {b:<5} | {a:<5} | {hex_color:<10}")

    print("-" * 90)
    print(f"总计: {len(results)} 个唯一值")


def export_to_csv(results, output_path):
    """导出结果到CSV文件"""
    import csv

    with open(output_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)

        has_count = 'count' in results[0]

        if has_count:
            writer.writerow(['序号', 'UniqueValue', 'R', 'G', 'B', 'A', 'HEX颜色', '像素数量'])
            for item in results:
                idx = item.get('index', item['value'])
                r, g, b, a = item['R'], item['G'], item['B'], item['A']
                hex_color = f"#{r:02x}{g:02x}{b:02x}"
                writer.writerow([idx, item['value'], r, g, b, a, hex_color, item['count']])
        else:
            writer.writerow(['序号', 'UniqueValue.像素值', 'R', 'G', 'B', 'A', 'HEX颜色'])
            for i, item in enumerate(results, 1):
                r, g, b, a = item['R'], item['G'], item['B'], item['A']
                hex_color = f"#{r:02x}{g:02x}{b:02x}"
                writer.writerow([i, item['value'], r, g, b, a, hex_color])

    print(f"\n结果已导出到: {output_path}")


# ==================== 主程序 ====================

if __name__ == "__main__":

    # 修改为你的TIF文件路径
    TIF_PATH = r"../../data/geology/图5/全国土壤利用分类.tif"

    # 你的TIF是4波段RGBA格式，使用这个函数
    print("\n" + "=" * 80)
    print("读取RGBA格式TIF的唯一颜色值")
    print("=" * 80 + "\n")

    results = get_unique_colors_from_rgba_tif(TIF_PATH, sample_step=1)

    if results:
        print_results(results)

        # 导出到CSV（取消注释使用）
        # export_to_csv(results, r"E:\output\unique_values.csv")