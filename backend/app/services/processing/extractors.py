"""Content extractors for all supported document types."""

from typing import Optional, List, Tuple
from dataclasses import dataclass
import io


@dataclass
class ExtractedContent:
    text: str
    pages: Optional[List[str]] = None
    metadata: Optional[dict] = None


class PDFExtractor:
    """Extract text from PDF files."""

    def extract(self, data: bytes) -> ExtractedContent:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        pages = []
        full_text = []

        for page in reader.pages:
            text = page.extract_text() or ""
            pages.append(text)
            full_text.append(text)

        return ExtractedContent(
            text="\n\n".join(full_text),
            pages=pages,
            metadata={"page_count": len(reader.pages)},
        )


class DOCXExtractor:
    """Extract text from DOCX files."""

    def extract(self, data: bytes) -> ExtractedContent:
        from docx import Document

        doc = Document(io.BytesIO(data))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        return ExtractedContent(text="\n\n".join(paragraphs))


class TextExtractor:
    """Extract text from plain text files."""

    def extract(self, data: bytes) -> ExtractedContent:
        text = data.decode("utf-8", errors="replace")
        return ExtractedContent(text=text)


class CSVExtractor:
    """Extract content from CSV files."""

    def extract(self, data: bytes) -> ExtractedContent:
        import pandas as pd

        df = pd.read_csv(io.BytesIO(data))
        # Convert to readable text format
        lines = []
        for _, row in df.iterrows():
            parts = [f"{col}: {val}" for col, val in row.items() if pd.notna(val)]
            lines.append(" | ".join(parts))

        return ExtractedContent(
            text="\n".join(lines),
            metadata={"rows": len(df), "columns": list(df.columns)},
        )


class XLSXExtractor:
    """Extract content from Excel files."""

    def extract(self, data: bytes) -> ExtractedContent:
        import pandas as pd

        sheets = pd.read_excel(io.BytesIO(data), sheet_name=None)
        all_text = []
        for sheet_name, df in sheets.items():
            all_text.append(f"## Sheet: {sheet_name}\n")
            for _, row in df.iterrows():
                parts = [f"{col}: {val}" for col, val in row.items() if pd.notna(val)]
                all_text.append(" | ".join(parts))

        return ExtractedContent(text="\n".join(all_text))


class HTMLExtractor:
    """Extract text from HTML."""

    def extract(self, data: bytes) -> ExtractedContent:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(data, "html.parser")
        for script in soup(["script", "style", "nav", "footer", "header"]):
            script.decompose()

        text = soup.get_text(separator="\n", strip=True)
        title = soup.title.string if soup.title else None

        return ExtractedContent(
            text=text,
            metadata={"title": title},
        )


class ImageExtractor:
    """Extract text from images using OCR and optional vision models."""

    def extract(self, data: bytes) -> ExtractedContent:
        from PIL import Image
        import pytesseract

        image = Image.open(io.BytesIO(data))
        text = pytesseract.image_to_string(image)

        return ExtractedContent(
            text=text.strip(),
            metadata={
                "width": image.width,
                "height": image.height,
                "format": image.format,
            },
        )

    async def extract_with_vision(self, data: bytes) -> ExtractedContent:
        """Use GPT-4V or Claude for rich image understanding."""
        import base64
        import openai
        from app.core.config import settings

        b64 = base64.b64encode(data).decode()
        client = openai.AsyncOpenAI(api_key=settings.openai_api_key)

        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this image in detail. Extract any text, data, charts, diagrams, or important visual information."},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ],
            }],
            max_tokens=1000,
        )

        # Also run OCR
        ocr_text = self.extract(data).text
        vision_text = response.choices[0].message.content

        return ExtractedContent(
            text=f"[Vision Description]\n{vision_text}\n\n[OCR Text]\n{ocr_text}",
            metadata={"extraction_method": "vision+ocr"},
        )


class AudioExtractor:
    """Extract text from audio using Whisper transcription."""

    async def extract(self, data: bytes, filename: str = "audio.mp3") -> ExtractedContent:
        import openai
        from app.core.config import settings

        client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
        audio_file = io.BytesIO(data)
        audio_file.name = filename

        response = await client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            response_format="verbose_json",
        )

        return ExtractedContent(
            text=response.text,
            metadata={
                "duration": getattr(response, "duration", None),
                "language": getattr(response, "language", None),
            },
        )


class URLExtractor:
    """Extract content from web URLs."""

    async def extract(self, url: str, recursive: bool = False, max_pages: int = 10) -> ExtractedContent:
        import httpx
        from bs4 import BeautifulSoup

        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            response = await client.get(url)
            response.raise_for_status()

        soup = BeautifulSoup(response.content, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        text = soup.get_text(separator="\n", strip=True)
        title = soup.title.string if soup.title else url

        return ExtractedContent(
            text=text,
            metadata={"url": url, "title": title, "status_code": response.status_code},
        )


def get_extractor(doc_type: str):
    """Factory function to get the appropriate extractor."""
    extractors = {
        "pdf": PDFExtractor(),
        "docx": DOCXExtractor(),
        "txt": TextExtractor(),
        "markdown": TextExtractor(),
        "csv": CSVExtractor(),
        "xlsx": XLSXExtractor(),
        "html": HTMLExtractor(),
        "image": ImageExtractor(),
    }
    return extractors.get(doc_type)
