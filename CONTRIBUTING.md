# 🤝 Contributing to OmniCache AI Proxy

Thank you for your interest in contributing to OmniCache! We welcome contributions from developers worldwide.

---

## 🚀 Getting Started

1. **Fork and Clone the Repository:**
   ```bash
   git clone https://github.com/13manmayarai-hash/omnicache-proxy.git
   cd omnicache-proxy
   ```
2. **Install Development Dependencies:**
   ```bash
   pip install starlette uvicorn httpx
   ```
3. **Run the Test Suite:**
   ```bash
   python3 -m unittest discover -s tests
   ```

---

## 📋 Pull Request (PR) Guidelines
* Ensure all 27 unit tests pass with 100% pass rate before submitting.
* Write clean, type-annotated code with minimal external dependencies.
* Keep core lookups strictly under **< 1.0 ms**.
