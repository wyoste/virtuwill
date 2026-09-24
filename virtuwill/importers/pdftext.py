"""Text from PDF pages, one string per page (pdfplumber keeps lines in reading order)."""
import io


def pages(content):
    import pdfplumber
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        return [page.extract_text() or "" for page in pdf.pages]
