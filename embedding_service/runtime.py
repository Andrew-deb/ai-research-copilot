"""One bounded CPU encoder; independent of dashboard, database and MCP tools."""

import math
import threading
import time

MODEL = "nomic-ai/modernbert-embed-base"
DIMENSION = 768
PREFIXES = {"query": "search_query: ", "document": "search_document: "}


class Encoder:
    def __init__(self):
        self.model = None
        self.state = "loading"
        self.load_seconds = None
        self.lock = threading.Lock()

    def load(self, path):
        from sentence_transformers import SentenceTransformer
        import torch

        torch.set_num_threads(2)
        started = time.perf_counter()
        # Never download an unpinned Hub model silently.
        self.model = SentenceTransformer(path, device="cpu", local_files_only=True)
        if self.model.get_sentence_embedding_dimension() != DIMENSION:
            raise ValueError("Incompatible embedding dimension")
        self.load_seconds = time.perf_counter() - started
        self.state = "ready"

    def encode(self, text, kind):
        prefixed = PREFIXES[kind] + text.strip()
        # Reject rather than silently truncate. Preserve the saved tokenizer limit.
        limit = min(self.model.max_seq_length, 8192)
        tokens = self.model.tokenizer(prefixed, truncation=False)["input_ids"]
        if len(tokens) > limit:
            raise ValueError("Input exceeds the model token limit")
        vector = self.model.encode(prefixed, normalize_embeddings=True).tolist()
        if len(vector) != DIMENSION or not all(math.isfinite(x) for x in vector):
            raise ValueError("Invalid embedding output")
        if not math.isclose(sum(x * x for x in vector), 1.0, abs_tol=1e-4):
            raise ValueError("Invalid embedding normalization")
        return vector
