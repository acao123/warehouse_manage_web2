        # 以组合方式居中显示"GDP(万元/km²)",其中单位部分使用 Times New Roman
        # 布局宽度分配：GDP( = 8.0mm, 万元/km² = 18.0mm, ) = 2.0mm
        # 总宽度 28.0mm，确保单位文本有足够空间显示
        gdp_left_part_width = 8.0   # "GDP(" 部分宽度
        gdp_unit_part_width = 18.0  # "万元/km²" 部分宽度（增加以容纳完整文本）
        gdp_right_part_width = 2.0  # ")" 部分宽度
        title_group_width = gdp_left_part_width + gdp_unit_part_width + gdp_right_part_width
        title_group_x = legend_x + (legend_width - title_group_width) / 2.0

        gdp_title_cn_left = QgsLayoutItemLabel(layout)
        gdp_title_cn_left.setText("GDP(")
        gdp_title_cn_left.setTextFormat(gdp_title_cn_format)
        gdp_title_cn_left.attemptMove(QgsLayoutPoint(title_group_x, gdp_title_y, QgsUnitTypes.LayoutMillimeters))
        gdp_title_cn_left.attemptResize(QgsLayoutSize(gdp_left_part_width, GDP_LEGEND_TITLE_HEIGHT_MM, QgsUnitTypes.LayoutMillimeters))
        gdp_title_cn_left.setHAlign(Qt.AlignRight)
        gdp_title_cn_left.setVAlign(Qt.AlignVCenter)
        gdp_title_cn_left.setFrameEnabled(False)
        gdp_title_cn_left.setBackgroundEnabled(False)
        layout.addLayoutItem(gdp_title_cn_left)

        gdp_title_tnr = QgsLayoutItemLabel(layout)
        gdp_title_tnr.setText("万元/km²")
        gdp_title_tnr.setTextFormat(gdp_title_tnr_format)
        # 轻微上移，修正 Times New Roman 字体的视觉基线偏低问题
        gdp_title_tnr.attemptMove(QgsLayoutPoint(title_group_x + gdp_left_part_width, gdp_title_y - 0.4, QgsUnitTypes.LayoutMillimeters))
        gdp_title_tnr.attemptResize(QgsLayoutSize(gdp_unit_part_width, GDP_LEGEND_TITLE_HEIGHT_MM, QgsUnitTypes.LayoutMillimeters))
        gdp_title_tnr.setHAlign(Qt.AlignHCenter)
        gdp_title_tnr.setVAlign(Qt.AlignVCenter)
        gdp_title_tnr.setFrameEnabled(False)
        gdp_title_tnr.setBackgroundEnabled(False)
        layout.addLayoutItem(gdp_title_tnr)

        gdp_title_cn_right = QgsLayoutItemLabel(layout)
        gdp_title_cn_right.setText(")")
        gdp_title_cn_right.setTextFormat(gdp_title_cn_format)
        gdp_title_cn_right.attemptMove(QgsLayoutPoint(title_group_x + gdp_left_part_width + gdp_unit_part_width, gdp_title_y, QgsUnitTypes.LayoutMillimeters))
        gdp_title_cn_right.attemptResize(QgsLayoutSize(gdp_right_part_width, GDP_LEGEND_TITLE_HEIGHT_MM, QgsUnitTypes.LayoutMillimeters))
        gdp_title_cn_right.setHAlign(Qt.AlignLeft)
        gdp_title_cn_right.setVAlign(Qt.AlignVCenter)
        gdp_title_cn_right.setFrameEnabled(False)
        gdp_title_cn_right.setBackgroundEnabled(False)
        layout.addLayoutItem(gdp_title_cn_right)
