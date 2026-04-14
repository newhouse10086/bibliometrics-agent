"""编码错误修复模板.

自动检测文件编码并处理各种编码问题。
"""
from pathlib import Path


def load_documents_with_encoding_fix(file_path, fallback_encoding="utf-8"):
    """自动检测并处理文件编码错误.

    三层策略:
    1. chardet 自动检测编码
    2. 尝试常见编码列表
    3. 容错回退

    Args:
        file_path: 输入文件路径
        fallback_encoding: 回退编码

    Returns:
        解码后的文本内容
    """
    import chardet
    import logging

    logger = logging.getLogger(__name__)
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    raw_data = path.read_bytes()

    # 无 BOM 则直接尝试 utf-8
    if not raw_data[:3] == b"\xef\xbb\xbf" and not raw_data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return raw_data.decode("utf-8")
        except UnicodeDecodeError:
            pass

    # Strategy 1: chardet
    try:
        detected = chardet.detect(raw_data)
        encoding = detected.get("encoding", fallback_encoding)
        if encoding and detected.get("confidence", 0) > 0.6:
            logger.info(f"chardet detected: {encoding} (confidence={detected['confidence']:.2f})")
            return raw_data.decode(encoding)
    except Exception:
        pass

    # Strategy 2: 常见编码
    for enc in ["utf-8-sig", "utf-8", "latin1", "iso-8859-1", "cp1252", "gbk", "gb2312", "big5"]:
        try:
            return raw_data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue

    # Strategy 3: 容错回退
    logger.warning(f"All decodings failed, using {fallback_encoding} with errors='replace'")
    return raw_data.decode(fallback_encoding, errors="replace")
