"""samples 风格 CSV 的读写辅助：每行一个十进制整数，# 开头为注释。"""


def iter_csv_ints(path):
    """流式读出 CSV 里的整数，跳过空行与 # 注释行。"""
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                yield int(line)


def write_csv_ints(values, path):
    with open(path, "w", encoding="utf-8") as f:
        for v in values:
            f.write("%d\n" % v)
