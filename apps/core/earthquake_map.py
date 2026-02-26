# -*- coding: utf-8 -*-
"""
基于 QGIS 3.40 的地震分级示意图绘制脚本
输入：震中经度、纬度、震级M、历史地震 CSV 文件(GBK)，输出 PNG（可插入 Word）
"""

import os
import math
import csv
from datetime import datetime
from qgis.core import (
    QgsApplication,
    QgsProject,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsVectorLayer,
    QgsRasterLayer,
    QgsField,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsMarkerSymbol,
    QgsSimpleMarkerSymbolLayer,
    QgsSymbol,
    QgsRendererCategory,
    QgsCategorizedSymbolRenderer,
    QgsLayout,
    QgsLayoutItemMap,
    QgsLayoutItemScaleBar,
    QgsLayoutItemPicture,
    QgsLayoutItemLegend,
    QgsLayoutItemLabel,
    QgsLayoutSize,
    QgsLayoutPoint,
    QgsUnitTypes,
    QgsRectangle,
    QgsLayoutExporter
)
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtCore import QVariant

# ------------------ 工具函数 ------------------

def compute_range_and_scale(mag):
    """根据震级返回绘图半径(km)和比例尺"""
    if mag < 6:
        return 15, 150000  # 半径15 km，图幅30 km，比例尺1:150000
    elif 6 <= mag < 7:
        return 50, 500000  # 半径50 km，图幅100 km，比例尺1:500000
    else:
        return 150, 1500000  # 半径150 km，图幅300 km，比例尺1:1500000

def haversine(lon1, lat1, lon2, lat2):
    """计算两点间球面距离（千米）"""
    R = 6371.0
    lon1, lat1, lon2, lat2 = map(math.radians, [lon1, lat1, lon2, lat2])
    dlon, dlat = lon2 - lon1, lat2 - lat1
    a = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return 2 * R * math.asin(math.sqrt(a))

def deg_buffer(lon, lat, radius_km):
    """将半径(千米)转换为经纬度包络框增量"""
    # 近似转换：纬度 1° ≈ 110.574 km，经度 1° ≈ 111.320*cos(lat) km
    dlat = radius_km / 110.574
    dlon = radius_km / (111.320 * math.cos(math.radians(lat)))
    return dlon, dlat

def load_quake_csv(csv_path):
    """读取 GBK CSV，返回记录列表"""
    rows = []
    with open(csv_path, "r", encoding="gbk", errors="ignore") as f:
        reader = csv.DictReader(f, delimiter=",")
        for r in reader:
            try:
                lon = float(r.get("经度", "").strip())
                lat = float(r.get("纬度", "").strip())
                mag = float(r.get("震级", "").strip())
            except Exception:
                continue
            rows.append({
                "time": r.get("发震时刻", "").strip(),
                "lon": lon,
                "lat": lat,
                "depth": r.get("深度（千米）", "").strip(),
                "place": r.get("参考位置", "").strip(),
                "mag": mag
            })
    return rows

def filter_by_distance(rows, center_lon, center_lat, radius_km):
    """筛选指定半径内且震级≥4.7的历史地震"""
    results = []
    for r in rows:
        if r["mag"] < 4.7:
            continue
        dist = haversine(center_lon, center_lat, r["lon"], r["lat"])
        if dist <= radius_km:
            r["dist_km"] = dist
            results.append(r)
    return results

def stats_text(filtered_rows, radius_km):
    """统计文本"""
    total = len(filtered_rows)
    c47_59 = sum(1 for r in filtered_rows if 4.7 <= r["mag"] < 6)
    c60_69 = sum(1 for r in filtered_rows if 6.0 <= r["mag"] < 7)
    c70_79 = sum(1 for r in filtered_rows if 7.0 <= r["mag"] < 8)
    c80_up = sum(1 for r in filtered_rows if r["mag"] >= 8)
    if filtered_rows:
        max_quake = max(filtered_rows, key=lambda r: r["mag"])
        max_txt = f"{max_quake['time']} {max_quake['place']} {max_quake['mag']}级"
    else:
        max_txt = "无记录"
    return (
        f"自1900年以来，本次地震震中{radius_km}km范围内曾发生{total}次4.7级以上地震，"
        f"其中4.7~5.9级{c47_59}次，6.0~6.9级{c60_69}次，7.0~7.9级{c70_79}次，"
        f"8.0级以上{c80_up}次，最大地震为：{max_txt}"
    )

def build_quake_layer(filtered_rows):
    """构建内存点图层（历史地震）"""
    vl = QgsVectorLayer("Point?crs=EPSG:4326", "历史地震", "memory")
    pr = vl.dataProvider()
    pr.addAttributes([
        QgsField("time", QVariant.String),
        QgsField("mag", QVariant.Double),
        QgsField("place", QVariant.String),
        QgsField("depth", QVariant.String),
        QgsField("dist_km", QVariant.Double)
    ])
    vl.updateFields()

    feats = []
    for r in filtered_rows:
        feat = QgsFeature()
        feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(r["lon"], r["lat"])))
        feat.setAttributes([r["time"], r["mag"], r["place"], r["depth"], r["dist_km"]])
        feats.append(feat)
    pr.addFeatures(feats)
    vl.updateExtents()

    # 颜色（RGB 注释：#RRGGBB 二进制形式在括号中）
    cat_styles = [
        # 4.7~5.9 浅绿色，小点
        (4.7, 6, QColor(144, 238, 144), 4),   # #90EE90 (10010000 11101110 10010000)
        # 6.0~6.9 黄色，中点
        (6, 7, QColor(255, 215, 0), 6),       # #FFD700 (11111111 11010111 00000000)
        # 7.0~7.9 橙色，大点
        (7, 8, QColor(255, 140, 0), 8),       # #FF8C00 (11111111 10001100 00000000)
        # ≥8.0 红色，最大
        (8, 11, QColor(220, 20, 60), 10)      # #DC143C (11011100 00010100 00111100)
    ]

    categories = []
    for idx, (lo, hi, color, size) in enumerate(cat_styles):
        sym = QgsSymbol.defaultSymbol(vl.geometryType())
        sym.setColor(color)
        sym.setSize(size)
        label = f"{lo:.1f}–{hi-0.1:.1f}级" if hi < 10 else "≥8.0级"
        cat = QgsRendererCategory(idx, sym, label)
        cat.range = (lo, hi)
        categories.append(cat)

    # 使用表达式分段
    renderer = QgsCategorizedSymbolRenderer("mag", categories)
    vl.setRenderer(renderer)
    return vl

def build_epicenter_layer(lon, lat):
    """构建震中五角星图层"""
    vl = QgsVectorLayer("Point?crs=EPSG:4326", "震中", "memory")
    pr = vl.dataProvider()
    pr.addAttributes([QgsField("name", QVariant.String)])
    vl.updateFields()
    feat = QgsFeature()
    feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(lon, lat)))
    feat.setAttributes(["震中"])
    pr.addFeature(feat)
    vl.updateExtents()

    star = QgsMarkerSymbol.createSimple({
        "name": "star",
        "color": "#FF0000",  # 红色
        "outline_color": "#8B0000",
        "size": "12"
    })
    vl.renderer().setSymbol(star)
    return vl

def add_tianditu_layer(token):
    """添加天地图影像 XYZ 图层"""
    # 注意：需满足天地图使用条款与密钥有效性
    url = (
        f"type=xyz&zmin=0&zmax=18&url="
        f"http://t0.tianditu.gov.cn/img_c/wmts?"
        f"SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=img&STYLE=default&"
        f"TILEMATRIXSET=c&FORMAT=tiles&TILEMATRIX=%7Bz%7D&TILEROW=%7By%7D&TILECOL=%7Bx%7D&tk={token}"
    )
    rl = QgsRasterLayer(url, "TianDiTu_影像", "wms")
    if not rl.isValid():
        raise RuntimeError("天地图底图加载失败，请检查 token 或网络。")
    return rl

def build_layout(project, base_layer, quake_layer, epic_layer,
                 extent_rect, scale_value, stat_txt, output_png):
    """构建并导出版式"""
    lyt = QgsLayout(project)
    lyt.initializeDefaults()

    # 地图项
    map_item = QgsLayoutItemMap(lyt)
    map_item.setRect(20, 20, 200, 200)
    map_item.setLayers([base_layer, quake_layer, epic_layer])
    map_item.setExtent(extent_rect)
    map_item.setScale(scale_value)
    map_item.attemptMove(QgsLayoutPoint(10, 10, QgsUnitTypes.LayoutMillimeters))
    map_item.attemptResize(QgsLayoutSize(190, 190, QgsUnitTypes.LayoutMillimeters))
    lyt.addLayoutItem(map_item)

    # 指北针（使用内置 SVG）
    north = QgsLayoutItemPicture(lyt)
    north.setPicturePath(QgsApplication.iconPath("north_arrow.svg"))
    north.attemptMove(QgsLayoutPoint(175, 12, QgsUnitTypes.LayoutMillimeters))
    north.attemptResize(QgsLayoutSize(20, 20, QgsUnitTypes.LayoutMillimeters))
    lyt.addLayoutItem(north)

    # 比例尺（线段比例尺）
    scale_bar = QgsLayoutItemScaleBar(lyt)
    scale_bar.setStyle("Line Ticks Middle")
    scale_bar.setLinkedMap(map_item)
    scale_bar.applyDefaultSize()
    scale_bar.setNumberOfSegmentsLeft(0)
    scale_bar.setNumberOfSegments(2)
    scale_bar.setUnits(QgsUnitTypes.DistanceKilometers)
    scale_bar.setUnitsPerSegment(scale_value / 100000)  # 近似段长（km）
    scale_bar.attemptMove(QgsLayoutPoint(140, 195, QgsUnitTypes.LayoutMillimeters))
    lyt.addLayoutItem(scale_bar)

    # 图例
    legend = QgsLayoutItemLegend(lyt)
    legend.setLinkedMap(map_item)
    legend.setTitle("图例")
    legend.model().setRootGroup(legend.model().rootGroup())  # 保持默认
    legend.attemptMove(QgsLayoutPoint(10, 195, QgsUnitTypes.LayoutMillimeters))
    legend.attemptResize(QgsLayoutSize(60, 30, QgsUnitTypes.LayoutMillimeters))
    lyt.addLayoutItem(legend)

    # 统计文字
    label = QgsLayoutItemLabel(lyt)
    label.setText(stat_txt)
    label.adjustSizeToText()
    label.attemptMove(QgsLayoutPoint(10, 170, QgsUnitTypes.LayoutMillimeters))
    lyt.addLayoutItem(label)

    # 导出
    exporter = QgsLayoutExporter(lyt)
    res = exporter.exportToImage(output_png, QgsLayoutExporter.ImageExportSettings())
    if res != QgsLayoutExporter.Success:
        raise RuntimeError(f"导出失败，返回码：{res}")

def run_plot(center_lon, center_lat, mag, csv_path, output_png, tianditu_token):
    """主流程：读取、筛选、绘制并输出 PNG"""
    radius_km, scale_val = compute_range_and_scale(mag)
    rows = load_quake_csv(csv_path)
    filtered = filter_by_distance(rows, center_lon, center_lat, radius_km)

    # 项目与底图
    project = QgsProject.instance()
    base = add_tianditu_layer(tianditu_token)
    project.addMapLayer(base)

    # 震中与历史点图层
    epic = build_epicenter_layer(center_lon, center_lat)
    quake = build_quake_layer(filtered)
    project.addMapLayer(epic)
    project.addMapLayer(quake)

    # 计算绘图范围
    dlon, dlat = deg_buffer(center_lon, center_lat, radius_km)
    rect = QgsRectangle(
        center_lon - dlon, center_lat - dlat,
        center_lon + dlon, center_lat + dlat
    )

    # 统计文本
    stat_txt = stats_text(filtered, radius_km)

    # 版式导出
    build_layout(project, base, quake, epic, rect, scale_val, stat_txt, output_png)
    print(f"输出完成：{output_png}")

# ------------------ 测试入口 ------------------
if __name__ == "__main__":
    # 若在独立 Python 环境运行，需要设置 QGIS 环境路径
    # os.environ["QGIS_PREFIX_PATH"] = r"/path/to/your/QGIS"  # 修改为本机 QGIS 安装路径
    qgs = QgsApplication([], False)
    qgs.initQgis()

    # 示例参数（请按需替换）
    center_lon = 103.25     # 震中经度
    center_lat = 34.06      # 震中纬度
    mag = 5.5               # 震级
    csv_path = r"../../data/geology/历史地震CSV文件.csv"  # 历史地震 CSV (GBK)
    output_png = r"../../data/geology/quake_map.png"     # 输出图片
    tianditu_token = "7652448d9b3e8d4c294df6001dce72a4"  # 天地图密钥，按需替换

    run_plot(center_lon, center_lat, mag, csv_path, output_png, tianditu_token)

    qgs.exitQgis()