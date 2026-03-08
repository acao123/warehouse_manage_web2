# -*- coding: utf-8 -*-
"""
地震地质构造图生成脚本（基于QGIS 3.40.15 Python环境）
功能：根据用户输入的震中位置和震级，绘制地质构造图，
      叠加省界、市界、县界、居民地、烈度圈，输出PNG图片。

依赖：QGIS 3.40.15 Python环境
作者：acao123
日期：2026-03-08
"""

import os
import sys
import math
import re
from datetime import datetime

# QGIS相关导入
from qgis.core import (
    QgsApplication,
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsRectangle,
    QgsPointXY,
    QgsPrintLayout,
    QgsLayoutItemMap,
    QgsLayoutItemLegend,
    QgsLayoutItemScaleBar,
    QgsLayoutItemLabel,
    QgsLayoutItemPicture,
    QgsLayoutItemShape,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsUnitTypes,
    QgsTextFormat,
    QgsTextBufferSettings,
    QgsPalLayerSettings,
    QgsVectorLayerSimpleLabeling,
    QgsSymbol,
    QgsSimpleLineSymbolLayer,
    QgsSimpleFillSymbolLayer,
    QgsMarkerSymbol,
    QgsSimpleMarkerSymbolLayer,
    QgsSvgMarkerSymbolLayer,
    QgsFillSymbol,
    QgsLineSymbol,
    QgsRendererCategory,
    QgsCategorizedSymbolRenderer,
    QgsSingleSymbolRenderer,
    QgsLayoutExporter,
    QgsLayoutItemPolyline,
    QgsLayoutItemPolygon,
    QgsFeature,
    QgsGeometry,
    QgsField,
    QgsFields,
    QgsWkbTypes,
    QgsMapSettings,
    QgsMapRendererCustomPainterJob,
    QgsRuleBasedLabeling,
    QgsPalLayerSettings,
    QgsTextFormat,
    QgsPropertyCollection,
)
from qgis.PyQt.QtCore import QVariant, Qt, QPointF, QRectF, QSizeF
from qgis.PyQt.QtGui import QColor, QFont, QPainter, QPen, QBrush, QPolygonF, QImage, QFontMetrics
from qgis.PyQt.QtWidgets import QApplication

from lxml import etree

# ============================================================
# 【配置常量区域】
# ============================================================

# 文件路径常量
TIF_GEOLOGY_PATH = "../../data/geology/图3/group.tif"
SHP_PROVINCE_PATH = "../../data/geology/省市边界/全国行政区划数据最高乡镇级别/全国省份行政区划数据/省级行政区划/省.shp"
SHP_CITY_PATH = "../../data/geology/省市边界/全国行政区划数据最高乡镇级别/全国市级行政区划数据/市级行政区划/市.shp"
SHP_COUNTY_PATH = "../../data/geology/省市边界/全国行政区划数据最高乡镇级别/全国县级行政区划数据/县级行政区划/县.shp"
SHP_RESIDENCE_PATH = "../../data/geology/地市级以上居民地res2_4m/res2_4m.shp"

# 输出图尺寸（毫米）
OUTPUT_WIDTH_MM = 200
OUTPUT_DPI = 300

# 地图边框线宽（毫米）
MAP_BORDER_WIDTH_MM = 0.35

# 行政边界样式
PROVINCE_BORDER_COLOR = QColor(160, 160, 160)
PROVINCE_BORDER_WIDTH_MM = 0.4

CITY_BORDER_COLOR = QColor(160, 160, 160)
CITY_BORDER_WIDTH_MM = 0.24
CITY_BORDER_DASH_INTERVAL = 0.3

COUNTY_BORDER_COLOR = QColor(160, 160, 160)
COUNTY_BORDER_WIDTH_MM = 0.14
COUNTY_BORDER_DASH_INTERVAL = 0.3

# 省名称样式
PROVINCE_LABEL_COLOR = QColor(77, 77, 77)
PROVINCE_LABEL_FONT_SIZE = 8  # pt

# 市名称样式
CITY_LABEL_FONT_SIZE = 9  # pt
CITY_LABEL_COLOR = QColor(0, 0, 0)

# 震中标记样式
EPICENTER_COLOR = QColor(255, 0, 0)
EPICENTER_SIZE_PT = 8 * 2 / 3  # 8pt字体的三分之二

# 比例尺和指北针边框
ELEMENT_BORDER_WIDTH_MM = 0.35
ELEMENT_BORDER_COLOR = QColor(0, 0, 0)
ELEMENT_BG_COLOR = QColor(255, 255, 255)

# 经纬度标注
COORD_LABEL_FONT_SIZE = 8  # pt
MAX_LON_LABELS = 6
MAX_LAT_LABELS = 5

# 烈度圈颜色配置
INTENSITY_COLORS = {
    4: QColor(0, 150, 255),  # IV度 - 蓝色
    5: QColor(0, 200, 100),  # V度 - 绿色
    6: QColor(255, 200, 0),  # VI度 - 黄色
    7: QColor(255, 150, 0),  # VII度 - 橙色
    8: QColor(255, 80, 0),  # VIII度 - 深橙色
    9: QColor(255, 0, 0),  # IX度 - 红色
    10: QColor(200, 0, 50),  # X度 - 深红色
    11: QColor(150, 0, 100),  # XI度 - 紫红色
    12: QColor(100, 0, 150),  # XII度 - 紫色
}
INTENSITY_LINE_WIDTH_MM = 0.5

# 拼音转汉字映射表（常见城市）
PINYIN_TO_CHINESE = {
    "Beijing": "北京",
    "Shanghai": "上海",
    "Tianjin": "天津",
    "Chongqing": "重庆",
    "Shijiazhuang": "石家庄",
    "Taiyuan": "太原",
    "Hohhot": "呼和浩特",
    "Shenyang": "沈阳",
    "Changchun": "长春",
    "Harbin": "哈尔滨",
    "Nanjing": "南京",
    "Hangzhou": "杭州",
    "Hefei": "合肥",
    "Fuzhou": "福州",
    "Nanchang": "南昌",
    "Jinan": "济南",
    "Zhengzhou": "郑州",
    "Wuhan": "武汉",
    "Changsha": "长沙",
    "Guangzhou": "广州",
    "Nanning": "南宁",
    "Haikou": "海口",
    "Chengdu": "成都",
    "Guiyang": "贵阳",
    "Kunming": "昆明",
    "Lhasa": "拉萨",
    "Xi'an": "西安",
    "Lanzhou": "兰州",
    "Xining": "西宁",
    "Yinchuan": "银川",
    "Urumqi": "乌鲁木齐",
    "Tangshan": "唐山",
    "Qinhuangdao": "秦皇岛",
    "Handan": "邯郸",
    "Xingtai": "邢台",
    "Baoding": "保定",
    "Zhangjiakou": "张家口",
    "Chengde": "承德",
    "Cangzhou": "沧州",
    "Langfang": "廊坊",
    "Hengshui": "衡水",
    "Datong": "大同",
    "Yangquan": "阳泉",
    "Changzhi": "长治",
    "Jincheng": "晋城",
    "Shuozhou": "朔州",
    "Jinzhong": "晋中",
    "Yuncheng": "运城",
    "Xinzhou": "忻州",
    "Linfen": "临汾",
    "Lvliang": "吕梁",
    "Baotou": "包头",
    "Wuhai": "乌海",
    "Chifeng": "赤峰",
    "Tongliao": "通辽",
    "Ordos": "鄂尔多斯",
    "Hulunbuir": "呼伦贝尔",
    "Bayannur": "巴彦淖尔",
    "Wulanchabu": "乌兰察布",
    "Dalian": "大连",
    "Anshan": "鞍山",
    "Fushun": "抚顺",
    "Benxi": "本溪",
    "Dandong": "丹东",
    "Jinzhou": "锦州",
    "Yingkou": "营口",
    "Fuxin": "阜新",
    "Liaoyang": "辽阳",
    "Panjin": "盘锦",
    "Tieling": "铁岭",
    "Chaoyang": "朝阳",
    "Huludao": "葫芦岛",
    "Jilin": "吉林",
    "Siping": "四平",
    "Liaoyuan": "辽源",
    "Tonghua": "通化",
    "Baishan": "白山",
    "Songyuan": "松原",
    "Baicheng": "白城",
    "Qiqihar": "齐齐哈尔",
    "Jixi": "鸡西",
    "Hegang": "鹤岗",
    "Shuangyashan": "双鸭山",
    "Daqing": "大庆",
    "Yichun": "伊春",
    "Jiamusi": "佳木斯",
    "Qitaihe": "七台河",
    "Mudanjiang": "牡丹江",
    "Heihe": "黑河",
    "Suihua": "绥化",
    "Wuxi": "无锡",
    "Xuzhou": "徐州",
    "Changzhou": "常州",
    "Suzhou": "苏州",
    "Nantong": "南通",
    "Lianyungang": "连云港",
    "Huai'an": "淮安",
    "Yancheng": "盐城",
    "Yangzhou": "扬州",
    "Zhenjiang": "镇江",
    "Taizhou": "泰州",
    "Suqian": "宿迁",
    "Ningbo": "宁波",
    "Wenzhou": "温州",
    "Jiaxing": "嘉兴",
    "Huzhou": "湖州",
    "Shaoxing": "绍兴",
    "Jinhua": "金华",
    "Quzhou": "衢州",
    "Zhoushan": "舟山",
    "Lishui": "丽水",
    "Wuhu": "芜湖",
    "Bengbu": "蚌埠",
    "Huainan": "淮南",
    "Ma'anshan": "马鞍山",
    "Huaibei": "淮北",
    "Tongling": "铜陵",
    "Anqing": "安庆",
    "Huangshan": "黄山",
    "Chuzhou": "滁州",
    "Fuyang": "阜阳",
    "Suzhou": "宿州",
    "Lu'an": "六安",
    "Bozhou": "亳州",
    "Chizhou": "池州",
    "Xuancheng": "宣城",
    "Putian": "莆田",
    "Sanming": "三明",
    "Quanzhou": "泉州",
    "Zhangzhou": "漳州",
    "Nanping": "南平",
    "Longyan": "龙岩",
    "Ningde": "宁德",
    "Xiamen": "厦门",
    "Jingdezhen": "景德镇",
    "Pingxiang": "萍乡",
    "Jiujiang": "九江",
    "Xinyu": "新余",
    "Yingtan": "鹰潭",
    "Ganzhou": "赣州",
    "Ji'an": "吉安",
    "Yichun": "宜春",
    "Fuzhou": "抚州",
    "Shangrao": "上饶",
    "Qingdao": "青岛",
    "Zibo": "淄博",
    "Zaozhuang": "枣庄",
    "Dongying": "东营",
    "Yantai": "烟台",
    "Weifang": "潍坊",
    "Jining": "济宁",
    "Tai'an": "泰安",
    "Weihai": "威海",
    "Rizhao": "日照",
    "Linyi": "临沂",
    "Dezhou": "德州",
    "Liaocheng": "聊城",
    "Binzhou": "滨州",
    "Heze": "菏泽",
    "Kaifeng": "开封",
    "Luoyang": "洛阳",
    "Pingdingshan": "平顶山",
    "Anyang": "安阳",
    "Hebi": "鹤壁",
    "Xinxiang": "新乡",
    "Jiaozuo": "焦作",
    "Puyang": "濮阳",
    "Xuchang": "许昌",
    "Luohe": "漯河",
    "Sanmenxia": "三门峡",
    "Nanyang": "南阳",
    "Shangqiu": "商丘",
    "Xinyang": "信阳",
    "Zhoukou": "周口",
    "Zhumadian": "驻马店",
    "Huangshi": "黄石",
    "Shiyan": "十堰",
    "Yichang": "宜昌",
    "Xiangyang": "襄阳",
    "Ezhou": "鄂州",
    "Jingmen": "荆门",
    "Xiaogan": "孝感",
    "Jingzhou": "荆州",
    "Huanggang": "黄冈",
    "Xianning": "咸宁",
    "Suizhou": "随州",
    "Zhuzhou": "株洲",
    "Xiangtan": "湘潭",
    "Hengyang": "衡阳",
    "Shaoyang": "邵阳",
    "Yueyang": "岳阳",
    "Changde": "常德",
    "Zhangjiajie": "张家界",
    "Yiyang": "益阳",
    "Chenzhou": "郴州",
    "Yongzhou": "永州",
    "Huaihua": "怀化",
    "Loudi": "娄底",
    "Shaoguan": "韶关",
    "Shenzhen": "深圳",
    "Zhuhai": "珠海",
    "Shantou": "汕头",
    "Foshan": "佛山",
    "Jiangmen": "江门",
    "Zhanjiang": "湛江",
    "Maoming": "茂名",
    "Zhaoqing": "肇庆",
    "Huizhou": "惠州",
    "Meizhou": "梅州",
    "Shanwei": "汕尾",
    "Heyuan": "河源",
    "Yangjiang": "阳江",
    "Qingyuan": "清远",
    "Dongguan": "东莞",
    "Zhongshan": "中山",
    "Chaozhou": "潮州",
    "Jieyang": "揭阳",
    "Yunfu": "云浮",
    "Liuzhou": "柳州",
    "Guilin": "桂林",
    "Wuzhou": "梧州",
    "Beihai": "北海",
    "Fangchenggang": "防城港",
    "Qinzhou": "钦州",
    "Guigang": "贵港",
    "Yulin": "玉林",
    "Baise": "百色",
    "Hezhou": "贺州",
    "Hechi": "河池",
    "Laibin": "来宾",
    "Chongzuo": "崇左",
    "Sanya": "三亚",
    "Sansha": "三沙",
    "Zigong": "自贡",
    "Panzhihua": "攀枝花",
    "Luzhou": "泸州",
    "Deyang": "德阳",
    "Mianyang": "绵阳",
    "Guangyuan": "广元",
    "Suining": "遂宁",
    "Neijiang": "内江",
    "Leshan": "乐山",
    "Nanchong": "南充",
    "Meishan": "眉山",
    "Yibin": "宜宾",
    "Guang'an": "广安",
    "Dazhou": "达州",
    "Ya'an": "雅安",
    "Bazhong": "巴中",
    "Ziyang": "资阳",
    "Liupanshui": "六盘水",
    "Zunyi": "遵义",
    "Anshun": "安顺",
    "Bijie": "毕节",
    "Tongren": "铜仁",
    "Qujing": "曲靖",
    "Yuxi": "玉溪",
    "Baoshan": "保山",
    "Zhaotong": "昭通",
    "Lijiang": "丽江",
    "Pu'er": "普洱",
    "Lincang": "临沧",
    "Tongchuan": "铜川",
    "Baoji": "宝鸡",
    "Xianyang": "咸阳",
    "Weinan": "渭南",
    "Yan'an": "延安",
    "Hanzhong": "汉中",
    "Yulin": "榆林",
    "Ankang": "安康",
    "Shangluo": "商洛",
    "Jiayuguan": "嘉峪关",
    "Jinchang": "金昌",
    "Baiyin": "白银",
    "Tianshui": "天水",
    "Wuwei": "武威",
    "Zhangye": "张掖",
    "Pingliang": "平凉",
    "Jiuquan": "酒泉",
    "Qingyang": "庆阳",
    "Dingxi": "定西",
    "Longnan": "陇南",
    "Haidong": "海东",
    "Shizuishan": "石嘴山",
    "Wuzhong": "吴忠",
    "Guyuan": "固原",
    "Zhongwei": "中卫",
    "Karamay": "克拉玛依",
    "Turpan": "吐鲁番",
    "Hami": "哈密",
    "Tanggu": "塘沽",
    "Binhai": "滨海",
    "Pudong": "浦东",
}


# ============================================================
# 【工具函数】
# ============================================================

def int_to_roman(num):
    """
    将阿拉伯数字转换为罗马数字

    参数:
        num (int): 阿拉伯数字（1-12）
    返回:
        str: 罗马数字字符串
    """
    val = [1000, 900, 500, 400, 100, 90, 50, 40, 10, 9, 5, 4, 1]
    syms = ['M', 'CM', 'D', 'CD', 'C', 'XC', 'L', 'XL', 'X', 'IX', 'V', 'IV', 'I']
    roman_num = ''
    i = 0
    while num > 0:
        for _ in range(num // val[i]):
            roman_num += syms[i]
            num -= val[i]
        i += 1
    return roman_num


def get_map_params_by_magnitude(magnitude):
    """
    根据震级获取地图参数

    参数:
        magnitude (float): 震级
    返回:
        tuple: (半径km, 图幅尺寸km, 比例尺分母)
    说明:
        震级M＜6时，15km半径，30km图幅，比例尺1:150000
        震级6≤M＜7时，50km半径，100km图幅，比例尺1:500000
        震级M≥7时，150km半径，300km图幅，比例尺1:1500000
    """
    if magnitude < 6.0:
        return 15, 30, 150000
    elif magnitude < 7.0:
        return 50, 100, 500000
    else:
        return 150, 300, 1500000


def km_to_degree_lon(km, latitude):
    """
    千米转经度差

    参数:
        km (float): 距离（千米）
        latitude (float): 纬度
    返回:
        float: 经度差
    """
    return km / (111.32 * math.cos(math.radians(latitude)))


def km_to_degree_lat(km):
    """
    千米转纬度差

    参数:
        km (float): 距离（千米）
    返回:
        float: 纬度差
    """
    return km / 110.574


def format_coordinate(value, is_lon=True):
    """
    将十进制度数格式化为 度°分′方向 格式

    参数:
        value (float): 十进制度数
        is_lon (bool): True表示经度，False表示纬度
    返回:
        str: 格式化字符串，如 "118°0′E" 或 "39°0′N"
    """
    abs_val = abs(value)
    degrees = int(abs_val)
    minutes = int((abs_val - degrees) * 60)
    if is_lon:
        suffix = "E" if value >= 0 else "W"
    else:
        suffix = "N" if value >= 0 else "S"
    return f"{degrees}°{minutes}′{suffix}"


def pinyin_to_chinese(pinyin):
    """
    将拼音转换为汉字

    参数:
        pinyin (str): 拼音字符串
    返回:
        str: 汉字字符串，如果没有找到则返回原拼音
    """
    if not pinyin:
        return pinyin

    # 首先尝试完全匹配
    if pinyin in PINYIN_TO_CHINESE:
        return PINYIN_TO_CHINESE[pinyin]

    # 尝试首字母大写匹配
    pinyin_title = pinyin.title()
    if pinyin_title in PINYIN_TO_CHINESE:
        return PINYIN_TO_CHINESE[pinyin_title]

    # 尝试去除空格后匹配
    pinyin_no_space = pinyin.replace(" ", "")
    if pinyin_no_space in PINYIN_TO_CHINESE:
        return PINYIN_TO_CHINESE[pinyin_no_space]

    return pinyin


def calculate_geo_extent(center_lon, center_lat, half_size_km):
    """
    根据中心点和半径计算地理范围

    参数:
        center_lon (float): 中心经度
        center_lat (float): 中心纬度
        half_size_km (float): 半径（千米）
    返回:
        dict: 地理范围字典 {min_lon, max_lon, min_lat, max_lat}
    """
    lon_offset = km_to_degree_lon(half_size_km, center_lat)
    lat_offset = km_to_degree_lat(half_size_km)

    return {
        "min_lon": center_lon - lon_offset,
        "max_lon": center_lon + lon_offset,
        "min_lat": center_lat - lat_offset,
        "max_lat": center_lat + lat_offset,
    }


def choose_tick_step(range_val, target_count):
    """
    选择合适的刻度间隔

    参数:
        range_val (float): 范围大小
        target_count (int): 目标刻度数量
    返回:
        float: 刻度间隔
    """
    candidates = [0.1, 0.2, 0.25, 0.5, 1.0, 2.0, 2.5, 5.0, 10.0]
    for step in candidates:
        n_ticks = range_val / step
        if n_ticks <= target_count + 1:
            return step
    return range_val / target_count

# ============================================================
# 【KML烈度圈解析函数】
# ============================================================

def parse_intensity_kml(kml_path):
    """
    解析KML文件获取烈度圈数据

    参数:
        kml_path (str): KML文件路径
    返回:
        dict: {烈度值: [(lon, lat), ...], ...}
    """
    intensity_data = {}

    if not os.path.exists(kml_path):
        print(f"  *** KML文件不存在: {kml_path} ***")
        return intensity_data

    try:
        with open(kml_path, 'rb') as f:
            kml_content = f.read()

        root = etree.fromstring(kml_content)
        ns = root.nsmap.get(None, 'http://www.opengis.net/kml/2.2')
        nsmap = {'kml': ns}

        # 查找所有Placemark
        placemarks = root.findall('.//kml:Placemark', nsmap)
        if not placemarks:
            placemarks = root.findall('.//{' + ns + '}Placemark')
        if not placemarks:
            placemarks = root.findall('.//Placemark')

        print(f"  找到 {len(placemarks)} 个Placemark")

        for pm in placemarks:
            # 获取烈度名称
            name = _get_kml_element_text(pm, 'name', nsmap, ns)

            # 从名称中提取烈度值
            intensity = _extract_intensity_from_name(name)
            if intensity is None:
                continue

            # 获取坐标
            coords = _extract_linestring_coords(pm, nsmap, ns)
            if coords:
                intensity_data[intensity] = coords
                print(f"    烈度 {intensity}度: {len(coords)} 个坐标点")

    except Exception as e:
        print(f"  *** KML解析失败: {e} ***")

    return intensity_data


def _get_kml_element_text(elem, tag, nsmap, ns):
    """
    获取KML元素的文本内容

    参数:
        elem: XML元素
        tag (str): 标签名
        nsmap (dict): 命名空间映射
        ns (str): 默认命名空间
    返回:
        str: 文本内容
    """
    for pattern in [f'kml:{tag}', f'{{{ns}}}{tag}', tag]:
        try:
            e = elem.find(pattern, nsmap) if 'kml:' in pattern else elem.find(pattern)
        except Exception:
            e = None
        if e is not None and e.text:
            return e.text.strip()
    return ""


def _extract_intensity_from_name(name):
    """
    从名称中提取烈度值

    参数:
        name (str): 名称字符串，如"4度"、"5度"
    返回:
        int: 烈度值，无法解析返回None
    """
    if not name:
        return None

    # 匹配数字+度的模式
    match = re.search(r'(\d+)\s*度', name)
    if match:
        return int(match.group(1))

    # 尝试直接解析数字
    try:
        return int(name.strip())
    except ValueError:
        return None


def _extract_linestring_coords(pm, nsmap, ns):
    """
    从Placemark中提取LineString坐标

    参数:
        pm: Placemark元素
        nsmap (dict): 命名空间映射
        ns (str): 默认命名空间
    返回:
        list: [(lon, lat), ...]
    """
    coords = []

    # 查找LineString元素
    ls_elems = []
    for tag in [f'kml:LineString', f'{{{ns}}}LineString', 'LineString']:
        try:
            found = pm.findall('.//' + tag, nsmap) if 'kml:' in tag else pm.findall('.//' + tag)
            ls_elems.extend(found)
        except Exception:
            pass

    for ls in ls_elems:
        coord_text = ""
        for ctag in [f'kml:coordinates', f'{{{ns}}}coordinates', 'coordinates']:
            try:
                ce = ls.find(ctag, nsmap) if 'kml:' in ctag else ls.find(ctag)
            except Exception:
                ce = None
            if ce is not None and ce.text:
                coord_text = ce.text.strip()
                break

        if coord_text:
            coords = _parse_kml_coordinates(coord_text)

    return coords


def _parse_kml_coordinates(text):
    """
    解析KML coordinates文本

    参数:
        text (str): 坐标文本
    返回:
        list: [(lon, lat), ...]
    """
    coords = []
    for part in text.replace('\n', ' ').replace('\t', ' ').split():
        fields = part.strip().split(',')
        if len(fields) >= 2:
            try:
                lon = float(fields[0])
                lat = float(fields[1])
                coords.append((lon, lat))
            except ValueError:
                continue
    return coords


# ============================================================
# 【QGIS图层加载函数】
# ============================================================

def load_geology_tif_layer(tif_path):
    """
    加载地质构造图TIF图层（不改变原始颜色）

    参数:
        tif_path (str): TIF文件路径
    返回:
        QgsRasterLayer: 栅格图层
    """
    if not os.path.exists(tif_path):
        print(f"  *** TIF文件不存在: {tif_path} ***")
        return None

    layer = QgsRasterLayer(tif_path, "地质构造图")
    if not layer.isValid():
        print(f"  *** TIF图层加载失败: {tif_path} ***")
        return None

    print(f"  加载地质构造图: {tif_path}")
    return layer


def load_province_layer(shp_path, geo_extent):
    """
    加载省界图层并设置样式

    参数:
        shp_path (str): SHP文件路径
        geo_extent (dict): 地理范围
    返回:
        QgsVectorLayer: 矢量图层
    """
    if not os.path.exists(shp_path):
        print(f"  *** 省界SHP文件不存在: {shp_path} ***")
        return None

    layer = QgsVectorLayer(shp_path, "省界", "ogr")
    if not layer.isValid():
        print(f"  *** 省界图层加载失败: {shp_path} ***")
        return None

    # 设置边界线样式：灰色实线，0.4mm
    symbol = QgsLineSymbol.createSimple({
        'color': f'{PROVINCE_BORDER_COLOR.red()},{PROVINCE_BORDER_COLOR.green()},{PROVINCE_BORDER_COLOR.blue()}',
        'width': str(PROVINCE_BORDER_WIDTH_MM),
        'width_unit': 'MM',
    })
    layer.renderer().setSymbol(symbol)

    # 设置标注：省名称，8pt，颜色R=77 G=77 B=77，白边
    _setup_province_labeling(layer)

    layer.triggerRepaint()
    print(f"  加载省界图层: {shp_path}")
    return layer


def _setup_province_labeling(layer):
    """
    设置省界标注样式

    参数:
        layer: 省界图层
    """
    settings = QgsPalLayerSettings()
    settings.fieldName = "省"  # 假设省名称字段为"省"，需根据实际情况调整
    settings.enabled = True

    # 设置字体
    text_format = QgsTextFormat()
    font = QFont("宋体", PROVINCE_LABEL_FONT_SIZE)
    text_format.setFont(font)
    text_format.setSize(PROVINCE_LABEL_FONT_SIZE)
    text_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    text_format.setColor(PROVINCE_LABEL_COLOR)

    # 设置白色缓冲（白边）
    buffer_settings = QgsTextBufferSettings()
    buffer_settings.setEnabled(True)
    buffer_settings.setSize(0.8)
    buffer_settings.setSizeUnit(QgsUnitTypes.RenderMillimeters)
    buffer_settings.setColor(QColor(255, 255, 255))
    text_format.setBuffer(buffer_settings)

    settings.setFormat(text_format)

    labeling = QgsVectorLayerSimpleLabeling(settings)
    layer.setLabeling(labeling)
    layer.setLabelsEnabled(True)


def load_city_layer(shp_path, geo_extent):
    """
    加载市界图层并设置样式

    参数:
        shp_path (str): SHP��件路径
        geo_extent (dict): 地理范围
    返回:
        QgsVectorLayer: 矢量图层
    """
    if not os.path.exists(shp_path):
        print(f"  *** 市界SHP文件不存在: {shp_path} ***")
        return None

    layer = QgsVectorLayer(shp_path, "市界", "ogr")
    if not layer.isValid():
        print(f"  *** 市界图层加载失败: {shp_path} ***")
        return None

    # 设置边界线样式：灰色虚线，0.24mm
    symbol = QgsLineSymbol.createSimple({
        'color': f'{CITY_BORDER_COLOR.red()},{CITY_BORDER_COLOR.green()},{CITY_BORDER_COLOR.blue()}',
        'width': str(CITY_BORDER_WIDTH_MM),
        'width_unit': 'MM',
        'use_custom_dash': '1',
        'customdash': f'{CITY_BORDER_DASH_INTERVAL};{CITY_BORDER_DASH_INTERVAL}',
        'customdash_unit': 'MM',
    })
    layer.renderer().setSymbol(symbol)

    layer.triggerRepaint()
    print(f"  加载市界图层: {shp_path}")
    return layer


def load_county_layer(shp_path, geo_extent):
    """
    加载县界图层并设置样式

    参数:
        shp_path (str): SHP文件路径
        geo_extent (dict): 地理范围
    返回:
        QgsVectorLayer: 矢量图层
    """
    if not os.path.exists(shp_path):
        print(f"  *** 县界SHP文件不存在: {shp_path} ***")
        return None

    layer = QgsVectorLayer(shp_path, "县界", "ogr")
    if not layer.isValid():
        print(f"  *** 县界图层加载失败: {shp_path} ***")
        return None

    # 设置边界线样式：灰色虚线，0.14mm
    symbol = QgsLineSymbol.createSimple({
        'color': f'{COUNTY_BORDER_COLOR.red()},{COUNTY_BORDER_COLOR.green()},{COUNTY_BORDER_COLOR.blue()}',
        'width': str(COUNTY_BORDER_WIDTH_MM),
        'width_unit': 'MM',
        'use_custom_dash': '1',
        'customdash': f'{COUNTY_BORDER_DASH_INTERVAL};{COUNTY_BORDER_DASH_INTERVAL}',
        'customdash_unit': 'MM',
    })
    layer.renderer().setSymbol(symbol)

    layer.triggerRepaint()
    print(f"  加载县界图层: {shp_path}")
    return layer


def load_residence_layer(shp_path, geo_extent):
    """
    加载地市级以上居民地图层并设置样式

    参数:
        shp_path (str): SHP文件路径
        geo_extent (dict): 地理范围
    返回:
        QgsVectorLayer: 矢量图层
    """
    if not os.path.exists(shp_path):
        print(f"  *** 居民地SHP文件不存在: {shp_path} ***")
        return None

    layer = QgsVectorLayer(shp_path, "居民地", "ogr")
    if not layer.isValid():
        print(f"  *** 居民地图层加载失败: {shp_path} ***")
        return None

    # 设置点符号样式：黑色空圈内为实心黑圆，加白色背景
    _setup_residence_symbol(layer)

    # 设置标注：市名称，9pt，黑色，白边，使用汉字
    _setup_residence_labeling(layer)

    layer.triggerRepaint()
    print(f"  加载居民地图层: {shp_path}")
    return layer


def _setup_residence_symbol(layer):
    """
    设置居民地点符号样式

    参数:
        layer: 居民地图层
    """
    # 市名称大小的三分之一
    marker_size = CITY_LABEL_FONT_SIZE / 3.0

    # 创建复合符号：白色背景圆 + 黑色空心圆 + 黑色实心圆
    symbol = QgsMarkerSymbol()
    symbol.deleteSymbolLayer(0)

    # 第一层：白色背景圆
    bg_layer = QgsSimpleMarkerSymbolLayer()
    bg_layer.setShape(QgsSimpleMarkerSymbolLayer.Circle)
    bg_layer.setSize(marker_size * 1.5)
    bg_layer.setSizeUnit(QgsUnitTypes.RenderPoints)
    bg_layer.setColor(QColor(255, 255, 255))
    bg_layer.setStrokeColor(QColor(255, 255, 255))
    symbol.appendSymbolLayer(bg_layer)

    # 第二层：黑色空心圆
    outer_layer = QgsSimpleMarkerSymbolLayer()
    outer_layer.setShape(QgsSimpleMarkerSymbolLayer.Circle)
    outer_layer.setSize(marker_size)
    outer_layer.setSizeUnit(QgsUnitTypes.RenderPoints)
    outer_layer.setColor(QColor(255, 255, 255, 0))  # 透明填充
    outer_layer.setStrokeColor(QColor(0, 0, 0))
    outer_layer.setStrokeWidth(0.3)
    outer_layer.setStrokeWidthUnit(QgsUnitTypes.RenderMillimeters)
    symbol.appendSymbolLayer(outer_layer)

    # 第三层：黑色实心圆（内部）
    inner_layer = QgsSimpleMarkerSymbolLayer()
    inner_layer.setShape(QgsSimpleMarkerSymbolLayer.Circle)
    inner_layer.setSize(marker_size * 0.4)
    inner_layer.setSizeUnit(QgsUnitTypes.RenderPoints)
    inner_layer.setColor(QColor(0, 0, 0))
    inner_layer.setStrokeColor(QColor(0, 0, 0))
    symbol.appendSymbolLayer(inner_layer)

    layer.renderer().setSymbol(symbol)


def _setup_residence_labeling(layer):
    """
    设置居民地标注样式（使用汉字）

    参数:
        layer: 居民地图层
    """
    settings = QgsPalLayerSettings()
    # 使用表达式将PINYIN转换为汉字
    settings.fieldName = "NAME"  # 假设名称字段为NAME，需根据实际情况调整
    settings.enabled = True

    # 设置字体
    text_format = QgsTextFormat()
    font = QFont("宋体", CITY_LABEL_FONT_SIZE)
    text_format.setFont(font)
    text_format.setSize(CITY_LABEL_FONT_SIZE)
    text_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    text_format.setColor(CITY_LABEL_COLOR)

    # 设置白色缓冲（白边）
    buffer_settings = QgsTextBufferSettings()
    buffer_settings.setEnabled(True)
    buffer_settings.setSize(0.8)
    buffer_settings.setSizeUnit(QgsUnitTypes.RenderMillimeters)
    buffer_settings.setColor(QColor(255, 255, 255))
    text_format.setBuffer(buffer_settings)

    settings.setFormat(text_format)

    # 标注位置偏移
    settings.xOffset = 2.0
    settings.yOffset = 0.0

    labeling = QgsVectorLayerSimpleLabeling(settings)
    layer.setLabeling(labeling)
    layer.setLabelsEnabled(True)


def create_intensity_layer(intensity_data, crs):
    """
    根据烈度圈数据创建矢量图层

    参数:
        intensity_data (dict): 烈度圈数据 {���度: [(lon, lat), ...]}
        crs: 坐标参考系统
    返回:
        QgsVectorLayer: 烈度圈图层
    """
    if not intensity_data:
        return None

    # 创建内存图层（直接在URI中定义字段）
    uri = "LineString?crs=EPSG:4326&field=intensity:integer&field=label:string(50)"
    layer = QgsVectorLayer(uri, "烈度圈", "memory")
    provider = layer.dataProvider()

    # 添加要素
    features = []
    for intensity, coords in intensity_data.items():
        if len(coords) < 2:
            continue

        # 创建线几何
        points = [QgsPointXY(lon, lat) for lon, lat in coords]
        # 闭合曲线
        if points[0] != points[-1]:
            points.append(points[0])

        geometry = QgsGeometry.fromPolylineXY(points)

        feature = QgsFeature()
        feature.setGeometry(geometry)
        feature.setAttributes([intensity, f"{int_to_roman(intensity)}"])
        features.append(feature)

    provider.addFeatures(features)

    # 设置分类渲染器
    _setup_intensity_renderer(layer)

    # 设置标注
    _setup_intensity_labeling(layer)

    layer.triggerRepaint()
    print(f"  创建烈度圈图层: {len(features)} 条")
    return layer


def _setup_intensity_renderer(layer):
    """
    设置烈度圈分类渲染器

    参数:
        layer: 烈度圈图层
    """
    categories = []

    for intensity, color in INTENSITY_COLORS.items():
        symbol = QgsLineSymbol.createSimple({
            'color': f'{color.red()},{color.green()},{color.blue()}',
            'width': str(INTENSITY_LINE_WIDTH_MM),
            'width_unit': 'MM',
        })
        category = QgsRendererCategory(intensity, symbol, f"{int_to_roman(intensity)}度")
        categories.append(category)

    renderer = QgsCategorizedSymbolRenderer("intensity", categories)
    layer.setRenderer(renderer)


def _setup_intensity_labeling(layer):
    """
    设置烈度圈标注

    参数:
        layer: 烈度圈图层
    """
    settings = QgsPalLayerSettings()
    settings.fieldName = "label"
    settings.enabled = True

    text_format = QgsTextFormat()
    font = QFont("Times New Roman", 10)
    text_format.setFont(font)
    text_format.setSize(10)
    text_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    text_format.setColor(QColor(0, 0, 0))

    buffer_settings = QgsTextBufferSettings()
    buffer_settings.setEnabled(True)
    buffer_settings.setSize(0.5)
    buffer_settings.setSizeUnit(QgsUnitTypes.RenderMillimeters)
    buffer_settings.setColor(QColor(255, 255, 255))
    text_format.setBuffer(buffer_settings)

    settings.setFormat(text_format)

    labeling = QgsVectorLayerSimpleLabeling(settings)
    layer.setLabeling(labeling)
    layer.setLabelsEnabled(True)


def create_epicenter_layer(center_lon, center_lat):
    """
    创建震中标记图层

    参数:
        center_lon (float): 震中经度
        center_lat (float): 震中纬度
    返回:
        QgsVectorLayer: 震中图层
    """
    # 创建内存图层
    layer = QgsVectorLayer("Point?crs=EPSG:4326", "震中", "memory")
    provider = layer.dataProvider()

    # 添加要素
    feature = QgsFeature()
    feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(center_lon, center_lat)))
    provider.addFeatures([feature])

    # 设置符号：红色五角星，白边
    symbol = QgsMarkerSymbol()
    symbol.deleteSymbolLayer(0)

    # 白色背景五角星
    bg_layer = QgsSimpleMarkerSymbolLayer()
    bg_layer.setShape(QgsSimpleMarkerSymbolLayer.Star)
    bg_layer.setSize(EPICENTER_SIZE_PT * 1.3)
    bg_layer.setSizeUnit(QgsUnitTypes.RenderPoints)
    bg_layer.setColor(QColor(255, 255, 255))
    bg_layer.setStrokeColor(QColor(255, 255, 255))
    symbol.appendSymbolLayer(bg_layer)

    # 红色五角星
    star_layer = QgsSimpleMarkerSymbolLayer()
    star_layer.setShape(QgsSimpleMarkerSymbolLayer.Star)
    star_layer.setSize(EPICENTER_SIZE_PT)
    star_layer.setSizeUnit(QgsUnitTypes.RenderPoints)
    star_layer.setColor(EPICENTER_COLOR)
    star_layer.setStrokeColor(QColor(255, 255, 255))
    star_layer.setStrokeWidth(0.3)
    star_layer.setStrokeWidthUnit(QgsUnitTypes.RenderMillimeters)
    symbol.appendSymbolLayer(star_layer)

    layer.renderer().setSymbol(symbol)
    layer.triggerRepaint()

    print(f"  创建震中图层: ({center_lon:.4f}, {center_lat:.4f})")
    return layer

# ============================================================
# 【布局元素绘制函数】
# ============================================================

def mm_to_pixel(mm, dpi):
    """
    毫米转像素

    参数:
        mm (float): 毫米数
        dpi (int): 分辨率
    返回:
        float: 像素数
    """
    return mm * dpi / 25.4


def draw_map_border(painter, rect, border_width_mm, dpi):
    """
    绘制地图边框

    参数:
        painter: QPainter对象
        rect: 地图矩形区域（像素）
        border_width_mm (float): 边框宽度（毫米）
        dpi (int): 分辨率
    """
    border_width_px = mm_to_pixel(border_width_mm, dpi)

    pen = QPen(QColor(0, 0, 0))
    pen.setWidthF(border_width_px)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    painter.drawRect(rect)


def draw_north_arrow(painter, x, y, width, height, border_width_mm, dpi):
    """
    绘制指北针（白色背景，黑色边框，左黑右白箭头，顶部N）

    参数:
        painter: QPainter对象
        x (int): 左上角X坐标（像素）
        y (int): 左上角Y坐标（像素）
        width (int): 宽度（像素）
        height (int): 高度（像素）
        border_width_mm (float): 边框宽度（毫米）
        dpi (int): 分辨率
    """
    border_width_px = mm_to_pixel(border_width_mm, dpi)

    # 转换为整数
    x = int(x)
    y = int(y)
    width = int(width)
    height = int(height)

    # 白色背景
    painter.setPen(QPen(QColor(0, 0, 0), border_width_px))
    painter.setBrush(QBrush(QColor(255, 255, 255)))
    painter.drawRect(x, y, width, height)

    # 指北针中心
    center_x = x + width / 2
    arrow_top = y + height * 0.25
    arrow_bottom = y + height * 0.85
    arrow_width = width * 0.25

    # 左半边黑色箭头
    left_points = QPolygonF([
        QPointF(center_x, arrow_top),
        QPointF(center_x - arrow_width, arrow_bottom),
        QPointF(center_x, arrow_bottom - (arrow_bottom - arrow_top) * 0.3)
    ])
    painter.setPen(QPen(QColor(0, 0, 0), 1))
    painter.setBrush(QBrush(QColor(0, 0, 0)))
    painter.drawPolygon(left_points)

    # 右半边白色箭头（黑边）
    right_points = QPolygonF([
        QPointF(center_x, arrow_top),
        QPointF(center_x + arrow_width, arrow_bottom),
        QPointF(center_x, arrow_bottom - (arrow_bottom - arrow_top) * 0.3)
    ])
    painter.setPen(QPen(QColor(0, 0, 0), 1))
    painter.setBrush(QBrush(QColor(255, 255, 255)))
    painter.drawPolygon(right_points)

    # N字母
    font = QFont("Times New Roman", int(width * 0.3))
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QPen(QColor(0, 0, 0)))

    fm = QFontMetrics(font)
    n_width = fm.horizontalAdvance("N")
    painter.drawText(int(center_x - n_width / 2), int(y + height * 0.2), "N")


def draw_scale_bar(painter, x, y, width, height, scale_denom, map_size_km, border_width_mm, dpi):
    """
    绘制比例尺

    参数:
        painter: QPainter对象
        x (int): 左上角X坐标（像素）
        y (int): 左上角Y坐标（像素）
        width (int): 宽度（像素）
        height (int): 高度（像素）
        scale_denom (int): 比例尺分母
        map_size_km (float): 地图尺寸（千米）
        border_width_mm (float): 边框宽度（毫米）
        dpi (int): 分辨率
    """
    border_width_px = mm_to_pixel(border_width_mm, dpi)

    # 转换为整数
    x = int(x)
    y = int(y)
    width = int(width)
    height = int(height)

    # 白色背景
    painter.setPen(QPen(QColor(0, 0, 0), border_width_px))
    painter.setBrush(QBrush(QColor(255, 255, 255)))
    painter.drawRect(x, y, width, height)

    # 比例尺数值
    font = QFont("宋体", 8)
    painter.setFont(font)
    painter.setPen(QPen(QColor(0, 0, 0)))

    # 绘制比例尺标题
    scale_text = f"1:{scale_denom:,}"
    fm = QFontMetrics(font)
    text_width = fm.horizontalAdvance(scale_text)
    painter.drawText(int(x + width / 2 - text_width / 2), int(y + height * 0.3), scale_text)

    # 绘制比例尺条
    bar_y = y + height * 0.5
    bar_height = height * 0.15
    bar_width = width * 0.7
    bar_x = x + (width - bar_width) / 2

    # 选择合适的比例尺长度
    nice_values = [1, 2, 5, 10, 20, 50, 100]
    target_km = map_size_km * 0.15
    bar_km = nice_values[0]
    for nv in nice_values:
        if nv <= target_km * 1.5:
            bar_km = nv
        else:
            break

    # 绘制黑白交替的比例尺条
    num_segments = 4
    seg_width = bar_width / num_segments

    for i in range(num_segments):
        color = QColor(0, 0, 0) if i % 2 == 0 else QColor(255, 255, 255)
        painter.setPen(QPen(QColor(0, 0, 0), 1))
        painter.setBrush(QBrush(color))
        painter.drawRect(int(bar_x + i * seg_width), int(bar_y), int(seg_width), int(bar_height))

    # 标注
    small_font = QFont("宋体", 7)
    painter.setFont(small_font)

    painter.drawText(int(bar_x), int(bar_y + bar_height + fm.height()), "0")
    mid_text = str(bar_km // 2)
    mid_width = fm.horizontalAdvance(mid_text)
    painter.drawText(int(bar_x + bar_width / 2 - mid_width / 2), int(bar_y + bar_height + fm.height()), mid_text)

    end_text = f"{bar_km} km"
    end_width = fm.horizontalAdvance(end_text)
    painter.drawText(int(bar_x + bar_width - end_width / 2), int(bar_y + bar_height + fm.height()), end_text)


def draw_legend(painter, x, y, width, height, intensity_data, border_width_mm, dpi):
    """
    绘制图例

    参数:
        painter: QPainter对象
        x (int): 左上角X坐标（像素）
        y (int): 左上角Y坐标（像素）
        width (int): 宽度（像素）
        height (int): 高度（像素）
        intensity_data (dict): 烈度圈数据
        border_width_mm (float): 边框宽度（毫米）
        dpi (int): 分辨率
    """
    border_width_px = mm_to_pixel(border_width_mm, dpi)

    # 转换为整数
    x = int(x)
    y = int(y)
    width = int(width)
    height = int(height)

    # 白色背景
    painter.setPen(QPen(QColor(0, 0, 0), border_width_px))
    painter.setBrush(QBrush(QColor(255, 255, 255)))
    painter.drawRect(x, y, width, height)

    # 图例标题
    title_font = QFont("黑体", 10)
    title_font.setBold(True)
    painter.setFont(title_font)
    painter.setPen(QPen(QColor(0, 0, 0)))

    title = "图  例"
    fm = QFontMetrics(title_font)
    title_width = fm.horizontalAdvance(title)
    painter.drawText(int(x + width / 2 - title_width / 2), int(y + 20), title)

    # 图例项
    item_font = QFont("宋体", 8)
    painter.setFont(item_font)
    item_fm = QFontMetrics(item_font)

    item_y = y + 40
    item_height = 20
    icon_width = 25

    # 第一列图例项
    col1_x = x + 10
    col2_x = x + width // 2

    legend_items = [
        # 第一列
        [
            ("star", "震中位置", EPICENTER_COLOR),
            ("line", "烈度", QColor(0, 0, 0)),
            ("circle", "居民地", QColor(0, 0, 0)),
        ],
        # 第二列
        [
            ("solid_line", "省界", PROVINCE_BORDER_COLOR),
            ("dash_line", "市界", CITY_BORDER_COLOR),
            ("dot_line", "县界", COUNTY_BORDER_COLOR),
        ],
    ]

    for col_idx, col_items in enumerate(legend_items):
        col_x = col1_x if col_idx == 0 else col2_x
        current_y = item_y

        for item_type, label, color in col_items:
            # 绘制图标
            icon_x = col_x
            icon_center_y = current_y + item_height // 2

            if item_type == "star":
                _draw_star(painter, icon_x + icon_width // 2, icon_center_y, 8, color)
            elif item_type == "line":
                painter.setPen(QPen(color, 2))
                painter.drawLine(int(icon_x), int(icon_center_y), int(icon_x + icon_width), int(icon_center_y))
            elif item_type == "circle":
                painter.setPen(QPen(color, 1))
                painter.setBrush(QBrush(QColor(255, 255, 255)))
                painter.drawEllipse(int(icon_x + icon_width // 2 - 5), int(icon_center_y - 5), 10, 10)
                painter.setBrush(QBrush(color))
                painter.drawEllipse(int(icon_x + icon_width // 2 - 2), int(icon_center_y - 2), 4, 4)
            elif item_type == "solid_line":
                painter.setPen(QPen(color, 2))
                painter.drawLine(int(icon_x), int(icon_center_y), int(icon_x + icon_width), int(icon_center_y))
            elif item_type == "dash_line":
                pen = QPen(color, 1)
                pen.setStyle(Qt.DashLine)
                painter.setPen(pen)
                painter.drawLine(int(icon_x), int(icon_center_y), int(icon_x + icon_width), int(icon_center_y))
            elif item_type == "dot_line":
                pen = QPen(color, 1)
                pen.setStyle(Qt.DotLine)
                painter.setPen(pen)
                painter.drawLine(int(icon_x), int(icon_center_y), int(icon_x + icon_width), int(icon_center_y))

            # 绘制文字
            painter.setPen(QPen(QColor(0, 0, 0)))
            painter.setFont(item_font)
            painter.drawText(int(col_x + icon_width + 5), int(icon_center_y + item_fm.height() // 4), label)

            current_y += item_height

    # 绘制地质图例（如果有）
    _draw_geology_legend(painter, x, y + int(height * 0.4), width, int(height * 0.6), item_font)


def _draw_star(painter, cx, cy, size, color):
    """
    绘制五角星

    参数:
        painter: QPainter对象
        cx (float): 中心X坐标
        cy (float): 中心Y坐标
        size (float): 大小
        color: 颜色
    """
    points = []
    for i in range(5):
        # 外顶点
        angle = math.radians(90 + i * 72)
        points.append(QPointF(cx + size * math.cos(angle), cy - size * math.sin(angle)))
        # 内顶点
        angle = math.radians(90 + i * 72 + 36)
        points.append(QPointF(cx + size * 0.4 * math.cos(angle), cy - size * 0.4 * math.sin(angle)))

    polygon = QPolygonF(points)

    # 白边
    painter.setPen(QPen(QColor(255, 255, 255), 2))
    painter.setBrush(QBrush(color))
    painter.drawPolygon(polygon)


def _draw_geology_legend(painter, x, y, width, height, font):
    """
    绘制地质构造图例

    参数:
        painter: QPainter对象
        x (float): 左上角X坐标
        y (float): 左上角Y坐标
        width (float): 宽度
        height (float): 高度
        font: 字体
    """
    # 转换为整数
    x = int(x)
    y = int(y)
    width = int(width)
    height = int(height)

    # 地质图例项（示例，需根据实际TIF文件调整）
    geology_items = [
        (QColor(128, 0, 128), "坚硬岩组", "大型坚硬块状侵入岩；如花岗岩、闪长岩等"),
        (QColor(200, 100, 150), "坚硬岩组", "坚硬块状变质岩；如石英岩、蛇纹岩等"),
        (QColor(255, 200, 200), "坚硬岩组", "巨~厚层沉积岩；如巨~厚层碳酸盐岩、石英砂岩等"),
        (QColor(0, 128, 0), "较坚硬岩组", "坚硬变质岩；如石英片岩、片理化细碧岩、片麻岩等"),
        (QColor(100, 150, 100), "较坚硬岩组", "岩浆岩；如二长岩、凝灰�ite、正长岩等"),
        (QColor(180, 180, 200), "较坚硬岩组", "沉积岩；如白云岩、灰岩、含砾泥夹层石英砂岩"),
        (QColor(150, 200, 255), "较软弱岩组", "砂岩、粗砂岩"),
        (QColor(200, 220, 255), "较软弱岩组", "泥岩、砂质板岩、页岩、粉砂岩夹砂质灰岩"),
        (QColor(180, 255, 180), "较软弱岩组", "绿泥石片岩、绢云片岩、云母��、变粒岩、黑云母岩等"),
        (QColor(255, 255, 150), "软弱岩组", "碎裂岩互层、千枚岩、板岩"),
        (QColor(255, 200, 100), "软弱岩组", "含煤岩系与煤层、含盐（石膏、岩盐）岩层、页岩、泥质砂岩等"),
        (QColor(255, 220, 180), "软弱岩组", "软弱层状黏土为主、砂泥岩互层、泥岩等"),
        (QColor(220, 220, 220), "土体", "黏土"),
        (QColor(255, 240, 200), "土体", "黄土、砂黏土"),
        (QColor(255, 180, 150), "土体", "砂、卵砾石、半胶结砾�ite、砂、粘质砂土"),
        (QColor(255, 255, 255), "其他", "雪、冰等"),
        (QColor(200, 230, 255), "其他", "水体、河流、湖泊等"),
    ]

    item_count = min(len(geology_items), 12)  # 最多显示12项
    item_height = height // item_count if item_count > 0 else 20
    current_y = y

    painter.setFont(font)
    fm = QFontMetrics(font)

    for i, (color, category, desc) in enumerate(geology_items[:item_count]):
        # 绘制颜色块
        painter.setPen(QPen(QColor(0, 0, 0), 1))
        painter.setBrush(QBrush(color))
        painter.drawRect(int(x + 5), int(current_y), 15, int(item_height - 2))

        # 绘制分类名称
        painter.setPen(QPen(QColor(0, 0, 0)))
        painter.drawText(int(x + 25), int(current_y + item_height * 0.7), category)

        # 绘制描述（截断）
        max_desc_width = width - 80
        desc_text = desc
        while fm.horizontalAdvance(desc_text) > max_desc_width and len(desc_text) > 0:
            desc_text = desc_text[:-1]
        if len(desc_text) < len(desc):
            desc_text += "..."

        painter.drawText(int(x + 60), int(current_y + item_height * 0.7), desc_text)

        current_y += item_height


def draw_coordinate_labels(painter, map_rect, geo_extent, dpi):
    """
    绘制经纬度标注（上侧和左侧）

    参数:
        painter: QPainter对象
        map_rect: 地图矩形区域
        geo_extent (dict): 地理范围
        dpi (int): 分辨率
    """
    font = QFont("Times New Roman", COORD_LABEL_FONT_SIZE)
    painter.setFont(font)
    painter.setPen(QPen(QColor(0, 0, 0)))
    fm = QFontMetrics(font)

    # 获取地图区域边界（转换为整数）
    map_left = int(map_rect.x())
    map_top = int(map_rect.y())
    map_width = int(map_rect.width())
    map_height = int(map_rect.height())

    min_lon = geo_extent["min_lon"]
    max_lon = geo_extent["max_lon"]
    min_lat = geo_extent["min_lat"]
    max_lat = geo_extent["max_lat"]

    # 经度标注（上侧）
    lon_step = choose_tick_step(max_lon - min_lon, MAX_LON_LABELS)
    lon_start = math.ceil(min_lon / lon_step) * lon_step

    lon_val = lon_start
    while lon_val <= max_lon:
        frac = (lon_val - min_lon) / (max_lon - min_lon)
        px = int(map_left + frac * map_width)

        label = format_coordinate(lon_val, is_lon=True)
        label_width = fm.horizontalAdvance(label)

        # 绘制刻度线（全部使用int）
        painter.drawLine(px, map_top, px, map_top - 5)

        # 绘制标注
        painter.drawText(int(px - label_width / 2), map_top - 8, label)

        lon_val += lon_step

    # 纬度标注（左侧）
    lat_step = choose_tick_step(max_lat - min_lat, MAX_LAT_LABELS)
    lat_start = math.ceil(min_lat / lat_step) * lat_step

    lat_val = lat_start
    while lat_val <= max_lat:
        frac = (max_lat - lat_val) / (max_lat - min_lat)
        py = int(map_top + frac * map_height)

        label = format_coordinate(lat_val, is_lon=False)
        label_width = fm.horizontalAdvance(label)

        # 绘制刻度线（全部使用int）
        painter.drawLine(map_left, py, map_left - 5, py)

        # 绘制标注
        painter.drawText(int(map_left - label_width - 10), int(py + fm.height() / 4), label)

        lat_val += lat_step


# ============================================================
# 【主函数】
# ============================================================

def generate_earthquake_geology_map(center_lon, center_lat, magnitude, kml_path, output_path):
    """
    生成地震地质构造图

    参数:
        center_lon (float): 震中经度（度）
        center_lat (float): 震中纬度（度）
        magnitude (float): 震级（M）
        kml_path (str): 烈度圈KML文件路径
        output_path (str): 输出PNG文件路径
    返回:
        bool: 成功返回True，失败返回False
    """
    print("=" * 65)
    print("  地 震 地 质 构 造 图 生 成 工 具")
    print("=" * 65)
    print(f"  震中: {center_lon:.4f}°E, {center_lat:.4f}°N")
    print(f"  震级: M{magnitude}")

    # 初始化QGIS应用
    qgs = QgsApplication([], False)
    qgs.initQgis()

    try:
        # [1/8] 获取地图参数
        print("\n[1/8] 计算地图参数...")
        radius_km, map_size_km, scale_denom = get_map_params_by_magnitude(magnitude)
        print(f"  半径: {radius_km}km, 图幅: {map_size_km}km x {map_size_km}km")
        print(f"  比例尺: 1:{scale_denom:,}")

        # 计算地理范围
        geo_extent = calculate_geo_extent(center_lon, center_lat, radius_km)
        print(f"  地理范围: 经度[{geo_extent['min_lon']:.4f}, {geo_extent['max_lon']:.4f}], "
              f"纬度[{geo_extent['min_lat']:.4f}, {geo_extent['max_lat']:.4f}]")

        # [2/8] 创建项目和加载图层
        print("\n[2/8] 创建QGIS项目...")
        project = QgsProject.instance()
        project.setCrs(QgsCoordinateReferenceSystem("EPSG:4326"))

        # [3/8] 加载地质构造图
        print("\n[3/8] 加载地质构造图...")
        geology_layer = load_geology_tif_layer(TIF_GEOLOGY_PATH)
        if geology_layer:
            project.addMapLayer(geology_layer)

        # [4/8] 加载行政边界
        print("\n[4/8] 加载行政边界...")
        county_layer = load_county_layer(SHP_COUNTY_PATH, geo_extent)
        if county_layer:
            project.addMapLayer(county_layer)

        city_layer = load_city_layer(SHP_CITY_PATH, geo_extent)
        if city_layer:
            project.addMapLayer(city_layer)

        province_layer = load_province_layer(SHP_PROVINCE_PATH, geo_extent)
        if province_layer:
            project.addMapLayer(province_layer)

        # [5/8] 加载居民地
        print("\n[5/8] 加载居民地...")
        residence_layer = load_residence_layer(SHP_RESIDENCE_PATH, geo_extent)
        if residence_layer:
            project.addMapLayer(residence_layer)

        # [6/8] 解析烈度圈
        print("\n[6/8] 解析烈度圈...")
        intensity_data = {}
        if kml_path and os.path.exists(kml_path):
            intensity_data = parse_intensity_kml(kml_path)

        intensity_layer = create_intensity_layer(intensity_data, project.crs())
        if intensity_layer:
            project.addMapLayer(intensity_layer)

        # [7/8] 创建震中图层
        print("\n[7/8] 创建震中标记...")
        epicenter_layer = create_epicenter_layer(center_lon, center_lat)
        if epicenter_layer:
            project.addMapLayer(epicenter_layer)

        # [8/8] 渲染输出
        print("\n[8/8] 渲染输出...")

        # 计算输出尺寸
        output_width_px = int(mm_to_pixel(OUTPUT_WIDTH_MM, OUTPUT_DPI))

        # 计算地图区域尺寸（正方形）
        map_size_mm = OUTPUT_WIDTH_MM * 0.65  # 地图占65%宽度
        map_size_px = int(mm_to_pixel(map_size_mm, OUTPUT_DPI))

        # 计算总高度
        legend_height_mm = OUTPUT_WIDTH_MM * 0.35  # 图例区域
        total_height_mm = map_size_mm + legend_height_mm * 0.3
        output_height_px = int(mm_to_pixel(total_height_mm, OUTPUT_DPI))

        # 边距
        margin_mm = 15
        margin_px = int(mm_to_pixel(margin_mm, OUTPUT_DPI))
        coord_label_margin_px = int(mm_to_pixel(10, OUTPUT_DPI))

        # 地图区域
        map_left = margin_px + coord_label_margin_px
        map_top = margin_px + coord_label_margin_px
        map_width = map_size_px
        map_height = map_size_px

        # 创建输出图像
        image = QImage(output_width_px, output_height_px, QImage.Format_ARGB32)
        image.fill(QColor(255, 255, 255))

        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing)

        # 设置地图渲染范围
        map_extent = QgsRectangle(
            geo_extent["min_lon"], geo_extent["min_lat"],
            geo_extent["max_lon"], geo_extent["max_lat"]
        )

        # 渲染地图
        map_settings = QgsMapSettings()
        map_settings.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
        map_settings.setExtent(map_extent)
        map_settings.setOutputSize(QSizeF(map_width, map_height).toSize())
        map_settings.setBackgroundColor(QColor(255, 255, 255))

        # 设置图层顺序（从下到上）
        layers = []
        if geology_layer:
            layers.append(geology_layer)
        if county_layer:
            layers.append(county_layer)
        if city_layer:
            layers.append(city_layer)
        if province_layer:
            layers.append(province_layer)
        if residence_layer:
            layers.append(residence_layer)
        if intensity_layer:
            layers.append(intensity_layer)
        if epicenter_layer:
            layers.append(epicenter_layer)

        map_settings.setLayers(layers)

        # 渲染地图到painter
        job = QgsMapRendererCustomPainterJob(map_settings, painter)
        painter.save()
        painter.translate(map_left, map_top)
        job.start()
        job.waitForFinished()
        painter.restore()

        # 绘制地图边框
        map_rect = QRectF(map_left, map_top, map_width, map_height)
        draw_map_border(painter, map_rect, MAP_BORDER_WIDTH_MM, OUTPUT_DPI)

        # 绘制经纬度标注
        draw_coordinate_labels(painter, map_rect, geo_extent, OUTPUT_DPI)

        # 指北针位置（右上角）
        north_arrow_width = int(mm_to_pixel(12, OUTPUT_DPI))
        north_arrow_height = int(mm_to_pixel(18, OUTPUT_DPI))
        north_arrow_x = map_left + map_width - north_arrow_width
        north_arrow_y = map_top
        draw_north_arrow(painter, north_arrow_x, north_arrow_y,
                         north_arrow_width, north_arrow_height,
                         ELEMENT_BORDER_WIDTH_MM, OUTPUT_DPI)

        # 比例尺位置（右下角）
        scale_bar_width = int(mm_to_pixel(40, OUTPUT_DPI))
        scale_bar_height = int(mm_to_pixel(15, OUTPUT_DPI))
        scale_bar_x = map_left + map_width - scale_bar_width
        scale_bar_y = map_top + map_height - scale_bar_height
        draw_scale_bar(painter, scale_bar_x, scale_bar_y,
                       scale_bar_width, scale_bar_height,
                       scale_denom, map_size_km,
                       ELEMENT_BORDER_WIDTH_MM, OUTPUT_DPI)

        # 图例位置（右侧）
        legend_width = int(mm_to_pixel(OUTPUT_WIDTH_MM * 0.32, OUTPUT_DPI))
        legend_height = map_height
        legend_x = map_left + map_width
        legend_y = map_top
        draw_legend(painter, legend_x, legend_y,
                    legend_width, legend_height,
                    intensity_data,
                    ELEMENT_BORDER_WIDTH_MM, OUTPUT_DPI)

        painter.end()

        # 保存图像
        image.save(output_path, "PNG", 95)

        fsize = os.path.getsize(output_path) / 1024
        print(f"\n  已保存: {output_path}")
        print(f"  大小: {fsize:.1f} KB")
        print(f"  尺寸: {output_width_px}x{output_height_px}px")

        print("\n" + "=" * 65)
        print("【生成完成】")
        print("=" * 65)

        return True

    except Exception as e:
        print(f"\n*** 生成失败: {e} ***")
        import traceback
        traceback.print_exc()
        return False

    finally:
        qgs.exitQgis()


# ============================================================
# 【测试方法】
# ============================================================

def test_generate_earthquake_geology_map():
    """
    测试地震地质构造图生成功能
    """
    print("\n" + "=" * 65)
    print("  开 始 测 试")
    print("=" * 65)

    # 测试参数
    test_cases = [
        {
            "name": "小震级测试 (M5.5)",
            "center_lon": 117.5,
            "center_lat": 39.0,
            "magnitude": 5.5,
            "kml_path": "../../data/geology/test_intensity.kml",
            "output_path": "../../data/geology/output_geology_map_M5.5.png",
        },
        {
            "name": "中震级测试 (M6.5)",
            "center_lon": 103.25,
            "center_lat": 34.06,
            "magnitude": 6.5,
            "kml_path": "../../data/geology/test_intensity.kml",
            "output_path": "../../data/geology/output_geology_map_M6.5.png",
        },
        {
            "name": "大震级测试 (M7.5)",
            "center_lon": 100.0,
            "center_lat": 30.0,
            "magnitude": 7.5,
            "kml_path": "../../data/geology/test_intensity.kml",
            "output_path": "../../data/geology/output_geology_map_M7.5.png",
        },
    ]

    results = []

    for case in test_cases:
        print(f"\n>>> 测试: {case['name']}")
        print("-" * 50)

        # 创建测试KML文件（如果不存在）
        if not os.path.exists(case["kml_path"]):
            _create_test_kml(case["kml_path"], case["center_lon"], case["center_lat"])

        # 确保输出目录存在
        os.makedirs(os.path.dirname(case["output_path"]), exist_ok=True)

        # 执行生成
        success = generate_earthquake_geology_map(
            center_lon=case["center_lon"],
            center_lat=case["center_lat"],
            magnitude=case["magnitude"],
            kml_path=case["kml_path"],
            output_path=case["output_path"],
        )

        results.append({
            "name": case["name"],
            "success": success,
            "output": case["output_path"] if success else None,
        })

    # 输出测试结果汇总
    print("\n" + "=" * 65)
    print("  测 试 结 果 汇 总")
    print("=" * 65)

    for result in results:
        status = "✓ 通过" if result["success"] else "✗ 失败"
        print(f"  {result['name']}: {status}")
        if result["output"]:
            print(f"    输出: {result['output']}")

    # 验证结果
    all_passed = all(r["success"] for r in results)
    if all_passed:
        print("\n【所有测试通过】")
    else:
        print("\n【部分测试失败】")

    return all_passed


def _create_test_kml(kml_path, center_lon, center_lat):
    """
    创建测试用KML文件

    参数:
        kml_path (str): KML文件保存路径
        center_lon (float): 中心经度
        center_lat (float): 中心纬度
    """

    def generate_circle_coords(cx, cy, radius_deg, num_points=36):
        coords = []
        for i in range(num_points):
            angle = 2 * math.pi * i / num_points
            lon = cx + radius_deg * math.cos(angle)
            lat = cy + radius_deg * 0.8 * math.sin(angle)
            coords.append((lon, lat))
        coords.append(coords[0])
        return coords

    kml_content = '''<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Document>
<Placemark><name>6度</name>
<description></description>
<LineString><coordinates>
'''

    coords_6 = generate_circle_coords(center_lon, center_lat, 0.5)
    kml_content += ',0 '.join([f"{lon},{lat}" for lon, lat in coords_6]) + ',0'

    kml_content += '''
</coordinates></LineString>
</Placemark>
<Placemark><name>7度</name>
<description></description>
<LineString><coordinates>
'''

    coords_7 = generate_circle_coords(center_lon, center_lat, 0.3)
    kml_content += ',0 '.join([f"{lon},{lat}" for lon, lat in coords_7]) + ',0'

    kml_content += '''
</coordinates></LineString>
</Placemark>
<Placemark><name>8度</name>
<description></description>
<LineString><coordinates>
'''

    coords_8 = generate_circle_coords(center_lon, center_lat, 0.15)
    kml_content += ',0 '.join([f"{lon},{lat}" for lon, lat in coords_8]) + ',0'

    kml_content += '''
</coordinates></LineString>
</Placemark>
</Document>
</kml>'''

    os.makedirs(os.path.dirname(kml_path), exist_ok=True)

    with open(kml_path, 'w', encoding='utf-8') as f:
        f.write(kml_content)

    print(f"  创建测试KML文件: {kml_path}")


def test_utility_functions():
    """
    测试工具函数
    """
    print("\n>>> 测试工具函数")
    print("-" * 50)

    # 测试罗马数字转换
    assert int_to_roman(4) == "IV", "罗马数字转换失败: 4"
    assert int_to_roman(5) == "V", "罗马数字转换失败: 5"
    assert int_to_roman(9) == "IX", "罗马数字转换失败: 9"
    assert int_to_roman(10) == "X", "罗马数字转换失败: 10"
    print("  ✓ 罗马数字转换测试通过")

    # 测试震级参数
    assert get_map_params_by_magnitude(5.5) == (15, 30, 150000), "震级参数失败: M5.5"
    assert get_map_params_by_magnitude(6.5) == (50, 100, 500000), "震级参数失败: M6.5"
    assert get_map_params_by_magnitude(7.5) == (150, 300, 1500000), "震级参数失败: M7.5"
    print("  ✓ 震级参数测试通过")

    # 测试坐标格式化
    assert format_coordinate(118.5, is_lon=True) == "118°30′E", "坐标格式化失败"
    assert format_coordinate(39.5, is_lon=False) == "39°30′N", "坐标格式化失败"
    print("  ✓ 坐标格式化测试通过")

    # 测试拼音转汉字
    assert pinyin_to_chinese("Beijing") == "北京", "拼音转汉字失败: Beijing"
    assert pinyin_to_chinese("Shanghai") == "上海", "拼音转汉字失败: Shanghai"
    assert pinyin_to_chinese("Unknown") == "Unknown", "拼音转汉字失败: Unknown"
    print("  ✓ 拼音转汉字测试通过")

    print("\n【工具函数测试全部通过】")
    return True


# ============================================================
# 【脚本入口】
# ============================================================

if __name__ == "__main__":
    # 运行工具函数测试
    test_utility_functions()

    # 运行主功能测试
    # test_generate_earthquake_geology_map()

    # 或者直接生成指定地震的地质构造图
    generate_earthquake_geology_map(
        center_lon=114.39,
        center_lat=39.32,
        magnitude=5.5,
        kml_path="../../data/geology/n0432881302350072.kml",
        output_path="../../data/geology/output_earthquake_geology_map.png"
    )