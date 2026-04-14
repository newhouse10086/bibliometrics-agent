"""内存分块处理模板.

当文档集过大时，分块处理以避免内存溢出。
"""
import gc
import logging

logger = logging.getLogger(__name__)


def preprocess_with_chunking(documents, process_func, chunk_size=500):
    """分块处理文档.

    Args:
        documents: 文档字典 {id: text}
        process_func: 单块处理函数 (chunk_dict) -> processed_dict
        chunk_size: 每块文档数

    Returns:
        全部处理结果
    """
    all_processed = {}
    doc_items = list(documents.items())
    total_chunks = max(1, (len(doc_items) + chunk_size - 1) // chunk_size)

    for chunk_idx in range(total_chunks):
        start = chunk_idx * chunk_size
        end = min(start + chunk_size, len(doc_items))
        chunk = dict(doc_items[start:end])

        logger.info(f"Processing chunk {chunk_idx + 1}/{total_chunks} ({len(chunk)} docs)")

        try:
            processed = process_func(chunk)
            all_processed.update(processed)
            del chunk, processed
            gc.collect()
        except MemoryError:
            # 自动缩小块大小
            if chunk_size > 50:
                logger.warning(f"MemoryError at chunk_size={chunk_size}, retrying with {chunk_size // 2}")
                # 处理剩余文档
                remaining = dict(doc_items[start:])
                return {
                    **all_processed,
                    **preprocess_with_chunking(remaining, process_func, chunk_size // 2),
                }
            raise RuntimeError("Cannot process even with minimal chunk size (50)")

    return all_processed


def reduce_vocabulary(vocab, max_size=10000, frequency=None):
    """缩减词汇表.

    Args:
        vocab: 词汇列表或字典
        max_size: 最大词汇数
        frequency: 词频字典（可选）

    Returns:
        缩减后的词汇表
    """
    if len(vocab) <= max_size:
        return vocab

    if frequency and isinstance(frequency, dict):
        # 按频率排序
        sorted_vocab = sorted(vocab, key=lambda w: frequency.get(w, 0), reverse=True)
        return sorted_vocab[:max_size]

    # 无频率信息，直接截断
    return vocab[:max_size]
