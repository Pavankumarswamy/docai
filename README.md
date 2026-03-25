# 📄 DOCAI – LangGraph Multi-Agent Document Processor
<p align="center">
  <img src="https://img.shields.io/badge/Version-2.0.0-gold?style=for-the-badge" alt="Version">
  <img src="https://img.shields.io/badge/Purpose-Multi--Agent%20Document%20Healing-green?style=for-the-badge" alt="Purpose">
  <img src="https://img.shields.io/badge/Platform-Windows%20x64-blue?style=for-the-badge&logo=windows" alt="Platform">
  <img src="https://img.shields.io/badge/Python-3.12+-yellow?style=for-the-badge&logo=python" alt="Python">
</p>

> **Transform manual document editing into an automated, AI-driven workflow.** 
> DOCAI is a specialized suite powered by **LangGraph** that parses Excel problem lists, analyzes Word documents section-by-section, and applies precise text/table edits entirely autonomously using a double-pass LLM pipeline.

---

## 🔥 Key Features

### 🤖 Multi-Agent LangGraph Pipeline
- **Section Extraction**: Intelligently groups Word (`.docx`) content into logical sections based on headings, entirely ignoring the first few header pages and forcefully skipping restricted sections (e.g., "Scope of Process Note").
- **Editor (Pass 1)**: Analyzes the target section against the entire CSV context to determine exact target edits.
- **Reviewer (Pass 1.5)**: Strictly validates LLM output to ensure zero structural corruption and enforces strict **Business Language Rules** (never outputting terms like "bug fixed").
- **Refiner (Pass 2)**: Re-evaluates Pass 1 edits for redundancy and clarity before final application.

### ⚡ Pure HTML/JS Frontend + FastAPI
- **Zero Build Tools**: The frontend is built entirely in Vanilla JS/HTML/CSS and served natively straight out of the FastAPI backend. No React, no Webpack, no `npm install`.
- **Fast UI**: Select your target folder and target Excel context file securely through a robust local web interface.
- **Change Tracking**: Automatically outputs a `change_report_YYYY-MM-DD.docx` file detailing the original content, exactly formatted edit JSON payloads, and context used.

---

## 🚀 Quick Start (Development Mode)

### Start the Application
Since the frontend is served statically alongside the backend, you only need to run one single command:

```bash
cd backend
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
python run_backend.py
```
**Access DOCAI at:** `http://localhost:8000`

---

## 🏁 Performance Benchmarks
We tested DOCAI version 2.0 with a standard 10-section SRS Document (approx. 200 paragraphs) and a complex bug context list:
- **Total Time Taken**: 158.85 seconds (~2.6 minutes)
- **Sections Processed**: 10
- **Double-Pass Efficiency**: ~15 seconds per logical section
- **Model**: Local Ollama (qwen2.5:3b) / NVIDIA Mixtral fallback

---

## 📜 Details
- **Architecture**: FastAPI, LangGraph, Python-Docx
- **LLM Support**: Configured to connect simultaneously to NVIDIA Inference APIs and fallback seamlessly to Local Ollama instances.
- **License**: MIT
