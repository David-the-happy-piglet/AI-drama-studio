def contains_chinese(s):
    for ch in s:
        if '\u4e00' <= ch <= '\u9fff':  # 常用汉字范围
            return True
    return False
