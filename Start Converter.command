#!/bin/bash
# Double-click this file to start the PDF to EPUB web converter.
# It opens http://localhost:5151 in your browser automatically.

cd "$(dirname "$0")"

# Check virtualenv exists
if [ ! -f "books to convert/pdf_env/bin/python3" ]; then
    echo "Setting up Python environment..."
    python3 -m venv "books to convert/pdf_env"
    "books to convert/pdf_env/bin/pip" install pymupdf flask
fi

echo ""
echo "  Starting PDF to EPUB Converter..."
echo "  Close this window to stop the server."
echo ""

"books to convert/pdf_env/bin/python3" web_converter.py
