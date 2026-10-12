"""Screenshot / screen-recording scanner that builds a planner inventory."""

OCR_READERS = ("auto", "rapidocr", "tesseract")
LLM_READERS = ("llm", "ollama", "claude")  # "llm" is the default LLM: Ollama, local and free
READERS = OCR_READERS + LLM_READERS
