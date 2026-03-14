"""
@author: acao
@date: 2026-01-30
计算安全系数 Fs 的栅格结果，计算临界加速度a_c的栅格结果，计算位移量log(Dn)的栅格结果 （QGIS 3.40 兼容）。
使用的包尽量是 QGIS 自带的包，避免额外安装包。

1、安全系数 Fs计算公式
Fs = c'/(gamma * t * sin(alpha)) + tan(phi')/tan(alpha)

2、临界加速度计算公式：
a_c = (Fs - 1) * g * sin(alpha)
3、滑动距离计算公式：
log(Dn) = 1.299 + 1.076*log(Ia) - 12.197*a_c + 5.434*a_c*log(Ia)

输入：
文件所在路径
../data/paperdata/c/c.tif: 有效黏聚力c'
../data/paperdata/gamma/gamma.tif: 总重度 gamma
../data/paperdata/slope/slope.tif: 坡面倾角（弧度）alpha
- /data/paperdata/phi/phi.tif: 有效内摩擦角（弧度）phi
- /data/paperdata/Ia/阿里亚斯强度.tif: 地震动强度参数 Ia

说明：
- t 固定为常数（默认 3）
- alpha/phi 单位已经是弧度了
- g = 9.81 m/s² (重力加速度)
-- 需要列出重要代码的中文注释
-- 在计算前需要检验输入文件是否符合计算 不符合输出原因
- 输出fs.tif文件到../data/paperdata/fs/fs2.tif
- 输出ac.tif文件到../data/paperdata/ac/ac.tif
- 输出dn.tif文件到../data/paperdata/dn/dn.tif

计算fs的代码已经有了 请添加生成ac.tif和dn.tif的代码

"""

import os
import numpy as np
from osgeo import gdal

# 固定参数
t = 3  # 固定厚度

# 输入文件路径
c_path = "../data/paperdata/c/c.tif"
gamma_path = "../data/paperdata/gamma/gamma.tif"
slope_path = "../data/paperdata/slope/slope.tif"
phi_path = "../data/paperdata/phi/phi.tif"
output_path = "../data/paperdata/fs/fs2.tif"
ac_output_path = "../data/paperdata/ac/ac.tif"
dn_output_path = "../data/paperdata/dn/dn.tif"
# 增加危险程度分类和输出
risk_output_path = "../data/paperdata/dn/risk_level.tif"

# 检查输入文件是否存在
def check_file_exists(file_path):
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"文件不存在: {file_path}")


for path in [c_path, gamma_path, slope_path, phi_path]:
    check_file_exists(path)


# 读取栅格数据
def read_raster(file_path):
    dataset = gdal.Open(file_path)
    if not dataset:
        raise ValueError(f"无法打开文件: {file_path}")
    band = dataset.GetRasterBand(1)
    data = band.ReadAsArray()
    return data, dataset


c_data, c_ds = read_raster(c_path)
gamma_data, gamma_ds = read_raster(gamma_path)
slope_data, slope_ds = read_raster(slope_path)
phi_data, phi_ds = read_raster(phi_path)


# 检查数据一致性
def check_data_consistency(*arrays):
    shape = arrays[0].shape
    for array in arrays:
        if array.shape != shape:
            raise ValueError("输入栅格数据的尺寸不一致")


check_data_consistency(c_data, gamma_data, slope_data, phi_data)

# 计算安全系数 Fs
slope_sin = np.sin(slope_data)
slope_tan = np.tan(slope_data)
phi_tan = np.tan(phi_data)

# 避免除以零或无效值
with np.errstate(divide='ignore', invalid='ignore'):
    fs_data = np.where(
        (gamma_data * t * slope_sin != 0) & (slope_tan != 0),
        c_data / (gamma_data * t * slope_sin) + phi_tan / slope_tan,
        np.nan
    )

# 输出计算结果的范围
valid_fs_data = fs_data[~np.isnan(fs_data)]  # 排除无效值
if valid_fs_data.size > 0:
    print(f"Fs 值的范围: 最小值 = {valid_fs_data.min()}, 最大值 = {valid_fs_data.max()}")
else:
    print("Fs 值全部为无效值，无法计算范围")


# 保存结果
def save_raster(output_path, data, reference_ds):
    driver = gdal.GetDriverByName('GTiff')
    out_ds = driver.Create(
        output_path,
        reference_ds.RasterXSize,
        reference_ds.RasterYSize,
        1,
        gdal.GDT_Float32
    )
    out_ds.SetGeoTransform(reference_ds.GetGeoTransform())
    out_ds.SetProjection(reference_ds.GetProjection())
    out_band = out_ds.GetRasterBand(1)
    out_band.WriteArray(data)
    out_band.SetNoDataValue(np.nan)
    out_band.FlushCache()
    out_ds = None


save_raster(output_path, fs_data, c_ds)

print(f"安全系数栅格已保存到 {output_path}")

# 计算临界加速度 a_c
g = 9.81  # 重力加速度 (m/s²)
ac_data = (fs_data - 1) * g * slope_sin

# 输出计算结果的范围
valid_ac_data = ac_data[~np.isnan(ac_data)]  # 排除无效值
if valid_ac_data.size > 0:
    print(f"a_c 值的范围: 最小值 = {valid_ac_data.min()}, 最大值 = {valid_ac_data.max()}")
else:
    print("a_c 值全部为无效值，无法计算范围")

# 保存临界加速度结果
save_raster(ac_output_path, ac_data, c_ds)
print(f"临界加速度栅格已保存到 {ac_output_path}")

# 计算滑动距离 log(Dn)
Ia_path = "../data/paperdata/Ia/阿里亚斯强度.tif"
check_file_exists(Ia_path)
Ia_data, Ia_ds = read_raster(Ia_path)
check_data_consistency(ac_data, Ia_data)

# 在计算 log(Dn) 前，将 Ia 中的 nodata 值替换为 0
Ia_data[np.isnan(Ia_data)] = 0

with np.errstate(divide='ignore', invalid='ignore'):
    log_dn_data = np.where(
        (Ia_data > 0) & (~np.isnan(ac_data)),
        1.299 + 1.076 * np.log10(Ia_data) - 12.197 * ac_data + 5.434 * ac_data * np.log10(Ia_data),
        np.nan
    )

# 输出计算结果的范围
valid_log_dn_data = log_dn_data[~np.isnan(log_dn_data)]  # 排除无效值
if valid_log_dn_data.size > 0:
    print(f"log(Dn) 值的范围: 最小值 = {valid_log_dn_data.min()}, 最大值 = {valid_log_dn_data.max()}")
else:
    print("log(Dn) 值全部为无效值，无法计算范围")

# 保存滑动距离结果
save_raster(dn_output_path, log_dn_data, Ia_ds)
print(f"滑动距离栅格已保存到 {dn_output_path}")

# 定义危险程度的阈值和对应等级
risk_thresholds = [0.5, 1.0, 1.5]  # 示例阈值，可根据实际需求调整
risk_levels = [1, 2, 3, 4]  # 危险等级，1 表示最低，4 表示最高

# 计算危险等级
risk_data = np.zeros_like(log_dn_data, dtype=np.int32)
for i, threshold in enumerate(risk_thresholds):
    risk_data[log_dn_data > threshold] = risk_levels[i + 1]

# 保存危险等级栅格
save_raster(risk_output_path, risk_data, Ia_ds)
print(f"危险等级栅格已保存到 {risk_output_path}")
