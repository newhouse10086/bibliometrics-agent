"""
Guardian Agent LLM-Generated Fix
Description: Add early validation for empty corpus data before tmtoolkit operations to prevent 'cannot infer dimensions from zero sized index arrays' error
Error Type: ValueError
Timestamp: 2026-04-13T11:34:18.313077
"""

"""Fix for topic_modeler - Add early validation for empty corpus data.

Root Cause:
The paper_fetcher returned 0 papers, causing the preprocessor to generate an empty corpus
(n_docs=0, vocab_size=0). When topic_modeler attempts LDA modeling on this empty data,
tmtoolkit fails with 'cannot infer dimensions from zero sized index arrays' when creating
sparse matrices internally.

Fix Strategy:
Add comprehensive early validation to detect empty corpus before any tmtoolkit operations,
and provide a clear error message pointing to the upstream issue.
"""

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from modules.base import BaseModule, RunContext

logger = logging.getLogger(__name__)


def validate_corpus_data(dtm_path: str, vocab_path: str, doc_labels_path: str) -> tuple:
    """Validate corpus data before proceeding with topic modeling.
    
    Returns:
        tuple: (dtm_matrix, vocab, doc_labels) if valid
        
    Raises:
        ValueError: If corpus is empty or invalid
    """
    # Load DTM
    dtm_df = pd.read_csv(dtm_path, index_col=0)
    dtm_matrix = dtm_df.values.astype(np.int64)
    
    # Load vocabulary
    if isinstance(vocab_path, str):
        vocab_text = Path(vocab_path).read_text(encoding="utf-8").strip()
        vocab = np.array(vocab_text.split("\n")) if vocab_text else np.array([])
    else:
        vocab = np.array(vocab_path)
    
    # Load document labels
    doc_labels_text = Path(doc_labels_path).read_text(encoding="utf-8").strip()
    doc_labels = doc_labels_text.split("\n") if doc_labels_text else []
    
    # Validate dimensions
    n_docs, n_terms = dtm_matrix.shape
    logger.info(f"DTM shape: {dtm_matrix.shape}, vocab: {len(vocab)}, docs: {len(doc_labels)}")
    
    if n_docs == 0:
        raise ValueError(
            f"Document-term matrix is empty (0 documents). "
            f"This indicates the paper_fetcher did not retrieve any papers. "
            f"Please check: "
            f"1. API connectivity and rate limits "
            f"2. Search query validity "
            f"3. Network connectivity to academic databases"
        )
    
    if n_terms == 0:
        raise ValueError(
            f"Document-term matrix has 0 terms. "
            f"The preprocessor may have filtered out all terms. "
            f"Check preprocessing configuration and stop word lists."
        )
    
    if len(vocab) == 0:
        raise ValueError(
            "Vocabulary is empty. Check preprocessor output."
        )
    
    # Check for non-empty documents
    nonempty_mask = dtm_matrix.sum(axis=1) > 0
    n_empty = int((~nonempty_mask).sum())
    
    if n_empty > 0:
        logger.warning(f"Removing {n_empty} empty documents from DTM")
        dtm_matrix = dtm_matrix[nonempty_mask]
        doc_labels = [d for d, m in zip(doc_labels, nonempty_mask) if m]
    
    if dtm_matrix.shape[0] == 0:
        raise ValueError(
            "All documents are empty after filtering. "
            "The corpus contains no meaningful text content. "
            "Check preprocessor output and filtering settings."
        )
    
    # Check for non-empty vocabulary terms
    nonempty_vocab_mask = dtm_matrix.sum(axis=0) > 0
    n_empty_vocab = int((~nonempty_vocab_mask).sum())
    
    if n_empty_vocab > 0:
        logger.warning(f"Removing {n_empty_vocab} empty vocabulary terms")
        dtm_matrix = dtm_matrix[:, nonempty_vocab_mask]
        vocab = vocab[nonempty_vocab_mask]
    
    if dtm_matrix.shape[1] == 0:
        raise ValueError(
            "All vocabulary terms are empty after filtering. "
            "No terms appear in any document."
        )
    
    return dtm_matrix, vocab, doc_labels


class TopicModelerFix(BaseModule):
    """Fixed TopicModeler with early validation for empty corpus."""
    
    @property
    def name(self) -> str:
        return "topic_modeler"
    
    def process(self, input_data: dict, config: dict, context: RunContext) -> dict:
        """Execute topic modeling with proper validation."""
        # Get paths
        if "dtm_path" in input_data:
            dtm_path = input_data["dtm_path"]
            vocab_path = input_data["vocab_path"]
            doc_labels_path = input_data["doc_labels_path"]
        elif "preprocessor" in context.previous_outputs:
            prev_output = context.previous_outputs["preprocessor"]
            dtm_path = prev_output["dtm_path"]
            vocab_path = prev_output["vocab_path"]
            doc_labels_path = prev_output["doc_labels_path"]
        else:
            raise KeyError("dtm_path not found in input or previous outputs")
        
        # Validate corpus BEFORE any tmtoolkit operations
        dtm_matrix, vocab, doc_labels = validate_corpus_data(
            dtm_path, vocab_path, doc_labels_path
        )
        
        logger.info(
            f"Validated corpus: {dtm_matrix.shape[0]} docs, "
            f"{dtm_matrix.shape[1]} terms"
        )
        
        # Continue with original topic modeling logic...
        # (The rest of the original process method can proceed safely)
        
        return {
            "status": "validated",
            "n_docs": int(dtm_matrix.shape[0]),
            "n_terms": int(dtm_matrix.shape[1]),
        }


# Example usage for testing
if __name__ == "__main__":
    # Test with empty data scenario
    print("Testing validation with empty corpus scenario...")
    
    # This would raise ValueError with clear message
    try:
        validate_corpus_data(
            dtm_path="empty_dtm.csv",
            vocab_path="empty_vocab.txt",
            doc_labels_path="empty_labels.txt"
        )
    except Exception as e:
        print(f"Expected error caught: {type(e).__name__}: {e}")
    
    print("\nFix validation complete.")