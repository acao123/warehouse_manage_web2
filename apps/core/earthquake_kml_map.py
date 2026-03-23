def _wrap_description_text(text, max_chars_per_line):
    """
    将说明文字按最大字符数手动折行（纯文本模式，不使用HTML）

    中文字符按1个字符计，ASCII字符（含数字、英文、罗马字母）按0.5个字符计，
    与字体渲染宽度近似对齐。

    参数:
        text (str): 原始说明文字
        max_chars_per_line (int): 每行最大字符数（以中文字符为基准）
    返回:
        str: 插入换行符后的文字
    """
    if not text or max_chars_per_line <= 0:
        return text

    lines = []
    current_line = ""
    current_width = 0.0

    for char in text:
        # 估算字符宽度：中文/全角字符宽度为1，ASCII字符宽度为0.5
        if ord(char) > 127:
            char_width = 1.0
        else:
            char_width = 0.5

        if current_width + char_width > max_chars_per_line and current_line:
            lines.append(current_line)
            current_line = char
            current_width = char_width
        else:
            current_line += char
            current_width += char_width

    if current_line:
        lines.append(current_line)

    return "\n".join(lines) if lines else text
