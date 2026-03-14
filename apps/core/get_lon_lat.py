# 导入QGIS核心模块
from qgis.core import (
    QgsApplication,
    QgsRasterLayer,
    QgsCoordinateTransform,
    QgsCoordinateReferenceSystem,
    QgsPointXY,
QgsProject
)

# 初始化QGIS应用（无GUI模式）
# QgsApplication.setPrefixPath("/usr/bin/qgis", True)  # 根据你的QGIS安装路径调整
qgs = QgsApplication([], False)
qgs.initQgis()


def get_tif_center_lonlat(tif_path):
    """
    获取TIFF文件中心点的经纬度

    参数:
        tif_path: TIFF文件的完整路径

    返回:
        (longitude, latitude): 中心点的经纬度坐标
    """
    try:
        # 1. 加载栅格图层
        raster_layer = QgsRasterLayer(tif_path, "Ia_tif")

        if not raster_layer.isValid():
            raise Exception(f"无法加载栅格文件: {tif_path}")

        # 2. 获取栅格的范围信息
        extent = raster_layer.extent()

        # 3. 计算几何中心点（图层坐标系）
        center_x = (extent.xMinimum() + extent.xMaximum()) / 2
        center_y = (extent.yMinimum() + extent.yMaximum()) / 2
        center_point = QgsPointXY(center_x, center_y)

        # 4. 获取图层的坐标系
        src_crs = raster_layer.crs()

        # 5. 定义目标坐标系（WGS84，EPSG:4326，即经纬度）
        dest_crs = QgsCoordinateReferenceSystem("EPSG:4326")

        # 6. 创建坐标转换器
        transform = QgsCoordinateTransform(src_crs, dest_crs, QgsProject.instance())

        # 7. 转换坐标到经纬度
        lonlat_point = transform.transform(center_point)

        # 8. 返回经纬度
        longitude = lonlat_point.x()
        latitude = lonlat_point.y()

        return longitude, latitude

    except Exception as e:
        print(f"处理过程中出错: {str(e)}")
        return None, None
    finally:
        # 清理QGIS资源
        qgs.exitQgis()


# 主程序
if __name__ == "__main__":
    # 替换为你的Ia.tif文件路径
    tif_file_path = "../../data/geology/ia/Ia.tif"

    # 获取中心点经纬度
    lon, lat = get_tif_center_lonlat(tif_file_path)

    if lon and lat:
        print(f"Ia.tif 文件中心点坐标：")
        print(f"经度 (Longitude): {lon:.6f}")
        print(f"纬度 (Latitude): {lat:.6f}")
    else:
        print("获取中心点坐标失败！")