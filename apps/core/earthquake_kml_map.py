'''
你好，你是一名优秀的程序员和地质专家。
基于QGIS 3.40.15使用python将信息系统GIS中的.kml添加底图+省、市、县界+断裂，然后根据要求输出png图
说明：
	实现步骤如下：
    (1).kml文件的xml格式打开是这样的：
	<?xml version="1.0" encoding="UTF-8"?>
	<kml xmlns="http://www.opengis.net/kml/2.2">
	<Document>
	<Placemark><name>4度</name>
	<description></description>
	<LineString><coordinates>
	114.78551111594,39.444015151372,0 114.78440837926,39.4460985825,0 114.78327632395,39.448172529053,0 114.78211508894,...
	</coordinates></LineString>
	</Placemark>
	<Placemark><name>5度</name>
	<description></description>
	<LineString><coordinates>
	114.54659841049,39.369129104779,0 114.54620255623,39.369877072372,...
	</coordinates></LineString>
	</Placemark>
	<Placemark><name>6度</name>
	<description></description>
	<LineString><coordinates>
	114.42167259312,39.329939431831,0 114.42160044682,39.330076196429,0 114.421526587,...
	</coordinates></LineString>
	</Placemark>
	</Document>
	</kml>
    从该xml中可以获取到烈度 比如说4度，5度，6度(在qgis中显示为烈度圈)，
    需要获取所有的烈度(烈度圈一定是一圈套一圈，越外层烈度依次递减)
	2）底图(使用天地图矢量底图+矢量注记)：
		TIANDITU_TK = "1ef76ef90c6eb961cb49618f9b1a399d"
		# 矢量底图URL
		TIANDITU_VEC_URL = (
			"http://t{s}.tianditu.gov.cn/vec_c/wmts?"
			"SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
			"&LAYER=vec&STYLE=default&TILEMATRIXSET=c"
			"&FORMAT=tiles&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}"
			"&tk=" + TIANDITU_TK
		)

		# 矢量注记URL
		TIANDITU_CVA_URL = (
			"http://t{s}.tianditu.gov.cn/cva_c/wmts?"
			"SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
			"&LAYER=cva&STYLE=default&TILEMATRIXSET=c"
			"&FORMAT=tiles&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}"
			"&tk=" + TIANDITU_TK
		)
	3）指北针放在底图的右上角，白色背景，指针左侧是黑色右侧是白色，上边和底图对齐，右侧和底图对齐，参考制图布局参考图2.png
	4）烈度使用罗马数字(I（1）、V（5）、X（10）...)
	5）字体字号(所有字体，英文用times New Roman，中文用宋体，图例两个字用黑体)
	6）右上角说明说明文字为用户输出+分析得出(文字不会超过450字)，注意说明文字字号可以使用常量设置，左右缩进 文字不能超过输出的画布，首字缩进2个字符
		用户输入：据中国地震台网正式测定:2026年01月26日14时56分甘隶甘南州选部县(103.25”,34.06’)发生5.5级地震,震源深度10千米。综合考虑震中附近地质构造背景、地震波衰减特性，估计了本次地震的地震动预测图。预计极震区地震烈度可达X度，极震区面积估算为X平方千米,地震烈度VI度以上区域面积达X平方千米。

		需要分析的是极震区地震烈度可达X度为最大烈度，极震区面积估算为X平方千米为最大烈度面积，
		地震烈度VI度以上区域面积达X平方千米为烈度VI度以上区域的面积
	7）比例尺使用线段比例尺，放在图右下位置，下面为制图时间：XX年XX月XX日(当前时间)
	说明：省界shp文件位置：../../data/geology/省市边界/全国行政区划数据最高乡镇级别/全国省份行政区划数据/省级行政区划/省.shp
	      市界shp文件位置：../../data/geology/省市边界/全国行政区划数据最高乡镇级别/全国市级行政区划数据/市级行政区划/市.shp
	      县界shp文件位置：../../data/geology/省市边界/全国行政区划数据最高乡镇级别/全国县级行政区划数据/县级行政区划/县.shp
		  全国六代图断裂位置:../../data/geology/断层/全国六代图断裂.KMZ
	8）比例尺使用线段比例尺，根据用户传入的震级动态调整比例尺比值。
	说明：震级M＜6时，比例尺设置为1：150000，震级6≤M＜7时，比例尺设置为1：500000；震级M≥7时，比例尺设置为1：1500000

	省界、市界、县界、全国六代图断裂位置使用常量，kml文件、说明文字通过传参
	注释是中文注释，要求方法和参数需要有中文注释
	代码需要无bug可运行，并写出测试方法，代码分四部分输出。
	图例参考：代码earthquake_map.py，但是在该制图图例在图正下方，图例标题 三行四列布局 超过12个图例不展示，烈度图例用线段表示
	布局参考图：制图布局参考图2.png

┌─────────────────────────────────┐─────────────────┤
│                         N(指北针)│  说明文字（9pt宋体│
│                                 │  首行2字缩进      │
│          地图框                  │   总字数≤450字）
│  (含天地图底图+省市县界+           │                  │
│   断裂+烈度圈+震中)               │                  │
│                                 │                  │
│                                 │  比例尺（动态档位） │
│                                 │  制图日期         │
├─────────────────────────────────┴──────────────────┤
│           图例（三行四列，黑体标题"图  例"）            │
└────────────────────────────────────────────────────┤


'''