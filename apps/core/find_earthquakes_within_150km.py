"""
在 QGIS 中查找某次地震 150KM 范围内的历史地震
使用 PyQGIS 的 QgsDistanceArea 进行椭球体精确计算
"""
from qgis.core import (
    QgsPointXY,
    QgsDistanceArea,
    QgsCoordinateReferenceSystem,
    QgsProject,
    QgsVectorLayer,
    QgsFeature,
    QgsGeometry,
)


def find_nearby_earthquakes(
    earthquake_layer_name: str,
    center_lon: float,
    center_lat: float,
    radius_km: float = 150.0,
    lon_field: str = "longitude",
    lat_field: str = "latitude",
    magnitude_field: str = "magnitude",
    time_field: str = "eq_time",
):
    """
    查找指定震中 radius_km 范围内的所有地震记录

    参数:
        earthquake_layer_name: QGIS 中地震图层的名称
        center_lon: 本次地震的经度
        center_lat: 本次地震的纬度
        radius_km: 搜索半径（公里），默认 150
        lon_field: 经度字段名
        lat_field: 纬度字段名
        magnitude_field: 震级字段名
        time_field: 地震时间字段名

    返回:
        list[dict]: 符合条件的地震记录列表
    """
    # 获取图层
    layers = QgsProject.instance().mapLayersByName(earthquake_layer_name)
    if not layers:
        print(f"错误：未找到名为 '{earthquake_layer_name}' 的图层")
        return []

    layer = layers[0]

    # 初始化距离计算器（使用 WGS84 椭球体，结果精确）
    distance_calc = QgsDistanceArea()
    distance_calc.setSourceCrs(
        QgsCoordinateReferenceSystem("EPSG:4326"),  # WGS84
        QgsProject.instance().transformContext(),
    )
    distance_calc.setEllipsoid("WGS84")

    # 本次地震的震中
    center_point = QgsPointXY(center_lon, center_lat)
    radius_m = radius_km * 1000  # 转换为米

    # 遍历所有地震记录，筛选在范围内的
    nearby_earthquakes = []

    for feature in layer.getFeatures():
        try:
            feat_lon = float(feature[lon_field])
            feat_lat = float(feature[lat_field])
        except (TypeError, ValueError):
            continue

        target_point = QgsPointXY(feat_lon, feat_lat)

        # 计算椭球体上的真实距离（单位：米）
        dist_m = distance_calc.measureLine(center_point, target_point)

        if dist_m <= radius_m:
            nearby_earthquakes.append(
                {
                    "经度": feat_lon,
                    "纬度": feat_lat,
                    "震级": feature[magnitude_field],
                    "时间": str(feature[time_field]),
                    "距离(km)": round(dist_m / 1000, 2),
                }
            )

    # 按距离排序
    nearby_earthquakes.sort(key=lambda x: x["距离(km)"])

    return nearby_earthquakes


# ============ 使用示例 ============
if __name__ == "__main__":
    # 本次地震的震中经纬度（示例：四川汶川）
    current_eq_lon = 103.42
    current_eq_lat = 31.01

    results = find_nearby_earthquakes(
        earthquake_layer_name="地震记录",  # 你的图层名称
        center_lon=current_eq_lon,
        center_lat=current_eq_lat,
        radius_km=150.0,
        lon_field="longitude",   # 根据实际字段名修改
        lat_field="latitude",
        magnitude_field="magnitude",
        time_field="eq_time",
    )

    # 打印结果
    print(f"\n本次地震位置: ({current_eq_lon}, {current_eq_lat})")
    print(f"150KM 范围内共有 {len(results)} 次地震:\n")
    print(f"{'序号':<5}{'经度':<12}{'纬度':<12}{'震级':<8}{'距离(km)':<12}{'时间'}")
    print("-" * 70)
    for i, eq in enumerate(results, 1):
        print(
            f"{i:<5}{eq['经度']:<12}{eq['纬度']:<12}{eq['震级']:<8}"
            f"{eq['距离(km)']:<12}{eq['时间']}"
        )