"""
QGIS 3.40.15 Python脚本
功能：读取全国六代图断裂KMZ文件，提取各类断层的颜色和线宽信息
修复版本：支持QgsEmbeddedSymbolRenderer嵌入式符号渲染器
"""

import zipfile
import os
import tempfile
import re
from osgeo import ogr

# 显式设置ogr异常处理，避免FutureWarning
ogr.UseExceptions()

from qgis.core import (
    QgsProject,
    QgsVectorLayer,
    QgsSymbol,
    QgsWkbTypes,
    QgsFeature,
    QgsSingleSymbolRenderer,
    QgsCategorizedSymbolRenderer,
    QgsGraduatedSymbolRenderer
)


def extract_kml_from_kmz(kmz_path):
    """
    从KMZ文件中解压KML文件
    KMZ本质上是一个ZIP压缩包，包含KML文件

    参数:
        kmz_path: KMZ文件路径
    返回:
        解压后的KML文件路径
    """
    if not os.path.exists(kmz_path):
        raise FileNotFoundError(f"KMZ文件不存在: {kmz_path}")

    # 创建临时目录用于解压
    temp_dir = tempfile.mkdtemp()
    kml_path = None

    with zipfile.ZipFile(kmz_path, 'r') as z:
        for file_name in z.namelist():
            if file_name.lower().endswith('.kml'):
                z.extract(file_name, temp_dir)
                kml_path = os.path.join(temp_dir, file_name)
                break

    if kml_path is None:
        raise ValueError("KMZ文件中未找到KML文件")

    return kml_path


def get_layer_style_info(layer):
    """
    获取图层的默认样式信息（颜色和线宽）
    支持多种渲染器类型

    参数:
        layer: QgsVectorLayer对象
    返回:
        包含颜色和线宽的字典
    """
    style_info = {
        'color': None,
        'color_rgba': None,
        'width': None,
        'width_unit': None,
        'renderer_type': None
    }

    renderer = layer.renderer()
    if renderer is None:
        return style_info

    style_info['renderer_type'] = renderer.type()

    # 获取符号 - 根据渲染器类型使用不同方法
    symbol = None

    # 方法1: 尝试 symbols() 方法 (适用于大多数渲染器，包括QgsEmbeddedSymbolRenderer)
    if hasattr(renderer, 'symbols'):
        try:
            # symbols() 需要传入 QgsRenderContext，但可以尝试不传参
            symbols = renderer.symbols(None) if callable(getattr(renderer, 'symbols', None)) else None
            if symbols and len(symbols) > 0:
                symbol = symbols[0]
        except (TypeError, RuntimeError):
            pass

    # 方法2: 尝试 symbol() 方法 (适用于QgsSingleSymbolRenderer)
    if symbol is None and hasattr(renderer, 'symbol'):
        try:
            symbol = renderer.symbol()
        except AttributeError:
            pass

    # 方法3: 对于嵌入式符号渲染器，样式存储在每个要素中
    if symbol is None and renderer.type() == 'embeddedSymbol':
        style_info['note'] = '嵌入式符号渲染器：样式存储在各要素的OGR_STYLE属性中'
        return style_info

    if symbol is None:
        return style_info

    # 检查是否为线类型
    try:
        symbol_type = symbol.type()
        # QGIS 3.40 使用 QgsSymbol.SymbolType 枚举
        if symbol_type == QgsSymbol.SymbolType.Line or symbol_type == 1:  # 1 = Line
            # 获取颜色 (QColor对象)
            color = symbol.color()
            style_info['color'] = color.name()  # 十六进制格式 如 #ff0000
            style_info['color_rgba'] = {
                'red': color.red(),
                'green': color.green(),
                'blue': color.blue(),
                'alpha': color.alpha()
            }

            # 获取线宽
            style_info['width'] = symbol.width()

            # 获取线宽单位
            try:
                width_unit = symbol.widthUnit()
                if hasattr(width_unit, 'name'):
                    style_info['width_unit'] = width_unit.name
                else:
                    style_info['width_unit'] = str(width_unit)
            except Exception:
                style_info['width_unit'] = 'unknown'
    except Exception as e:
        style_info['error'] = str(e)

    return style_info


def parse_ogr_style(ogr_style_str):
    """
    解析OGR_STYLE字符串，提取颜色和线宽
    OGR_STYLE格式示例:
        PEN(c:#FF0000FF,w:2.000000px)
        PEN(c:#AABBGGRR,w:1.5px)

    参数:
        ogr_style_str: OGR_STYLE字符串
    返回:
        解析后的样式字典
    """
    result = {
        'color_hex': None,
        'color_rgb': None,
        'width': None,
        'raw': ogr_style_str
    }

    if ogr_style_str is None or not isinstance(ogr_style_str, str):
        return result

    # 匹配颜色 c:#RRGGBBAA 或 c:#RRGGBB 或 c:#AABBGGRR (KML格式)
    color_match = re.search(r'c:#([0-9A-Fa-f]{6,8})', ogr_style_str)
    if color_match:
        color_hex = color_match.group(1)

        if len(color_hex) == 8:
            # KML颜色格式是 AABBGGRR，需要转换为 RRGGBB
            # AA = Alpha, BB = Blue, GG = Green, RR = Red
            aa = color_hex[0:2]
            bb = color_hex[2:4]
            gg = color_hex[4:6]
            rr = color_hex[6:8]

            result['color_hex'] = f"#{rr}{gg}{bb}"
            result['color_rgb'] = {
                'red': int(rr, 16),
                'green': int(gg, 16),
                'blue': int(bb, 16),
                'alpha': int(aa, 16)
            }
        else:
            # 6位标准格式 RRGGBB
            result['color_hex'] = '#' + color_hex
            result['color_rgb'] = {
                'red': int(color_hex[0:2], 16),
                'green': int(color_hex[2:4], 16),
                'blue': int(color_hex[4:6], 16),
                'alpha': 255
            }

    # 匹配线宽 w:数字px 或 w:数字
    width_match = re.search(r'w:(\d+\.?\d*)(px)?', ogr_style_str)
    if width_match:
        result['width'] = float(width_match.group(1))

    return result


def determine_fault_type(name, description=None):
    """
    根据名称和描述确定断层类型

    参数:
        name: 断层名称
        description: 断层描述
    返回:
        断层类型字符串
    """
    text = str(name or '') + str(description or '')

    # 按优先级匹配断层类型
    fault_patterns = [
        ("全新世", "全新世断层"),
        ("晚更新世", "晚更新世断层"),
        ("早-中更新世", "早中更新世断层"),
        ("早中更新世", "早中更新世断层"),
        ("中更新世", "中更新世断层"),
        ("早更新世", "早更新世断层"),
        ("第四纪", "第四纪断层"),
        ("前第四纪", "前第四纪断层"),
        ("Q4", "第四纪断层"),
        ("Q3", "晚更新世断层"),
        ("Q2", "中更新世断层"),
        ("Q1", "早更新世断层"),
    ]

    for pattern, fault_type in fault_patterns:
        if pattern in text:
            return fault_type

    return "未分类断层"


def analyze_fault_kmz(kmz_path):
    """
    分析断裂KMZ文件，提取各类断层的颜色和线宽信息

    参数:
        kmz_path: KMZ文件路径
    返回:
        断层样式信息列表
    """
    print(f"正在分析KMZ文件: {kmz_path}")
    print("=" * 60)

    # 1. 解压KMZ获取KML
    try:
        kml_path = extract_kml_from_kmz(kmz_path)
        print(f"成功解压KML文件: {kml_path}")
    except Exception as e:
        print(f"解压KMZ失败: {e}")
        return None

    # 2. 使用OGR获取图层信息
    ds = ogr.Open(kml_path)
    if ds is None:
        print("无法打开KML文件")
        return None

    layer_names = []
    for i in range(ds.GetLayerCount()):
        layer_names.append(ds.GetLayerByIndex(i).GetName())

    print(f"\n发现 {len(layer_names)} 个图层:")
    for name in layer_names:
        print(f"  - {name}")

    ds = None  # 关闭数据源

    # 3. 分析每个图层
    all_fault_styles = []

    for layer_name in layer_names:
        print(f"\n{'=' * 60}")
        print(f"分析图层: {layer_name}")
        print("-" * 40)

        # 加载图层
        layer_uri = f"{kml_path}|layername={layer_name}"
        layer = QgsVectorLayer(layer_uri, layer_name, 'ogr')

        if not layer.isValid():
            print(f"  ⚠ 无法加载图层: {layer_name}")
            continue

        # 获取几何类型
        geom_type = QgsWkbTypes.displayString(layer.wkbType())
        feature_count = layer.featureCount()
        print(f"  几何类型: {geom_type}")
        print(f"  要素数量: {feature_count}")

        # 只处理线类型图层
        if layer.geometryType() != QgsWkbTypes.GeometryType.LineGeometry:
            print(f"  跳过非线类型图层")
            continue

        # 获取图层渲染器信息
        renderer = layer.renderer()
        print(f"  渲染器类型: {renderer.type() if renderer else 'None'}")

        # 获取字段列表
        field_names = [field.name() for field in layer.fields()]
        print(f"  字段列表: {field_names}")

        # 用于统计各类断层样式
        fault_type_styles = {}

        # 遍历所有要素
        print(f"\n  正在分析 {feature_count} 条断层要素...")

        for feature in layer.getFeatures():
            # 获取要素属性
            feat_name = None
            feat_description = None
            ogr_style = None

            for field_name in field_names:
                value = feature[field_name]
                if field_name.lower() == 'name':
                    feat_name = value
                elif field_name.lower() == 'description':
                    feat_description = value
                elif field_name.upper() == 'OGR_STYLE':
                    ogr_style = value

            # 解析OGR_STYLE获取颜色和线宽
            style_parsed = parse_ogr_style(ogr_style)

            # 确定断层类型
            fault_type = determine_fault_type(feat_name, feat_description)

            # 统计
            if fault_type not in fault_type_styles:
                fault_type_styles[fault_type] = {
                    'count': 0,
                    'colors': {},  # 统计颜色出现次数
                    'widths': {},  # 统计线宽出现次数
                    'samples': []  # 保存样本
                }

            fault_type_styles[fault_type]['count'] += 1

            # 统计颜色
            color = style_parsed['color_hex']
            if color:
                if color not in fault_type_styles[fault_type]['colors']:
                    fault_type_styles[fault_type]['colors'][color] = 0
                fault_type_styles[fault_type]['colors'][color] += 1

            # 统计线宽
            width = style_parsed['width']
            if width is not None:
                width_str = f"{width:.1f}"
                if width_str not in fault_type_styles[fault_type]['widths']:
                    fault_type_styles[fault_type]['widths'][width_str] = 0
                fault_type_styles[fault_type]['widths'][width_str] += 1

            # 保存前5个样本
            if len(fault_type_styles[fault_type]['samples']) < 5:
                fault_type_styles[fault_type]['samples'].append({
                    'name': feat_name,
                    'color': color,
                    'color_rgb': style_parsed['color_rgb'],
                    'width': width,
                    'ogr_style': ogr_style
                })

        # 输出统计结果
        print(f"\n  {'=' * 50}")
        print(f"  断层样式统计结果:")
        print(f"  {'=' * 50}")

        for fault_type, info in sorted(fault_type_styles.items()):
            print(f"\n  【{fault_type}】")
            print(f"    要素数量: {info['count']} 条")

            # 输出颜色统计
            if info['colors']:
                print(f"    颜色分布:")
                for color, count in sorted(info['colors'].items(), key=lambda x: -x[1]):
                    percentage = count / info['count'] * 100
                    print(f"      {color}: {count} 条 ({percentage:.1f}%)")

            # 输出线宽统计
            if info['widths']:
                print(f"    线宽分布 (px):")
                for width, count in sorted(info['widths'].items(), key=lambda x: -x[1]):
                    percentage = count / info['count'] * 100
                    print(f"      {width}: {count} 条 ({percentage:.1f}%)")

            # 输出样本
            if info['samples']:
                print(f"    样本示例:")
                for sample in info['samples'][:3]:
                    print(f"      - {sample['name']}")
                    print(f"        颜色: {sample['color']}, 线宽: {sample['width']}px")

            # 保存到结果列表
            # 获取最常用的颜色和线宽
            main_color = max(info['colors'].items(), key=lambda x: x[1])[0] if info['colors'] else None
            main_width = max(info['widths'].items(), key=lambda x: x[1])[0] if info['widths'] else None

            all_fault_styles.append({
                'layer_name': layer_name,
                'fault_type': fault_type,
                'count': info['count'],
                'main_color': main_color,
                'main_width': main_width,
                'all_colors': info['colors'],
                'all_widths': info['widths']
            })

    return all_fault_styles


def print_summary(results):
    """
    打印汇总结果
    """
    if not results:
        print("没有分析结果")
        return

    print("\n" + "=" * 70)
    print("【断层样式汇总表】")
    print("=" * 70)
    print(f"{'断层类型':<20} {'数量':>8} {'主要颜色':>12} {'主要线宽':>10}")
    print("-" * 70)

    for item in sorted(results, key=lambda x: -x['count']):
        fault_type = item['fault_type']
        count = item['count']
        color = item['main_color'] or 'N/A'
        width = f"{item['main_width']}px" if item['main_width'] else 'N/A'
        print(f"{fault_type:<20} {count:>8} {color:>12} {width:>10}")

    print("=" * 70)

    # 颜色说明
    print("\n【颜色参考】")
    print("-" * 40)
    color_meanings = {
        '#ff0000': '红色 - 通常表示全新世活动断层',
        '#ff7f00': '橙色 - 通常表示晚更新世断层',
        '#ffff00': '黄色 - 通常表示中更新世断层',
        '#00ff00': '绿色 - 通常表示早更新世断层',
        '#0000ff': '蓝色 - 通常表示前第四纪断层',
    }
    for color, meaning in color_meanings.items():
        print(f"  {color}: {meaning}")


def main():
    """
    主函数 - 在QGIS Python控制台中运行
    """
    # 设置KMZ文件路径 - 请修改为您的实际路径
    kmz_path = r"../../data/geology/断层/全国六代图断裂.KMZ"  # 修改为你的实际路径

    # 分析KMZ文件
    results = analyze_fault_kmz(kmz_path)

    # 打印汇总
    print_summary(results)

    return results


# 如果在QGIS Python控制台中运行
if __name__ == '__main__':
    main()