# Contributing to OmniCache

Thank you for your interest in contributing to OmniCache.

---

## Development Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/13manmayarai-hash/omnicache-proxy.git
   cd omnicache-proxy
   ```

2. **Install in editable mode with development dependencies:**
   ```bash
   pip install -e .
   pip install pytest
   ```

3. **Run the test suite:**
   ```bash
   pytest tests/ -v
   ```

---

## Guidelines

* Follow standard PEP 8 styling conventions.
* Add unit tests in `tests/` for any new endpoints or features.
* Avoid introducing heavy binary dependencies to preserve cross-platform compatibility across macOS, Linux, Windows, and ARM architectures.
