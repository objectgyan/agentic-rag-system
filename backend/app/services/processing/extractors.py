"""Content extractors for all supported document types."""

import io
from dataclasses import dataclass
from typing import List, Optional

from app.core.llm_clients import openai_client


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
        import pytesseract
        from PIL import Image

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


        b64 = base64.b64encode(data).decode()
        client = openai_client()

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

        client = openai_client()
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


class VideoExtractor:
    """Extract text from video by transcribing audio track."""

    async def extract(self, data: bytes, filename: str = "video.mp4") -> ExtractedContent:
        import subprocess
        import tempfile
        from pathlib import Path


        # Save video to temp file
        with tempfile.NamedTemporaryFile(suffix=Path(filename).suffix, delete=False) as video_file:
            video_file.write(data)
            video_path = video_file.name

        # Extract audio using ffmpeg
        audio_path = video_path.replace(Path(filename).suffix, ".mp3")
        try:
            subprocess.run(
                ["ffmpeg", "-i", video_path, "-vn", "-acodec", "mp3", "-ab", "128k", "-ar", "44100", "-y", audio_path],
                check=True,
                capture_output=True,
            )

            # Transcribe audio with Whisper
            client = openai_client()
            with open(audio_path, "rb") as audio_file:
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
                    "extraction_method": "video_audio_transcription",
                },
            )
        finally:
            # Cleanup temp files
            Path(video_path).unlink(missing_ok=True)
            Path(audio_path).unlink(missing_ok=True)


class URLExtractor:
    """Extract content from web URLs."""

    _HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }

    async def extract(self, url: str, recursive: bool = False, max_pages: int = 10) -> ExtractedContent:
        import httpx

        max_pages = max(1, min(max_pages, 50))  # hard cap so a crawl can't run away
        async with httpx.AsyncClient(follow_redirects=True, timeout=30, headers=self._HEADERS) as client:
            if url.rstrip("/").lower().endswith("sitemap.xml"):
                return await self._crawl_sitemap(client, url, max_pages)
            if recursive:
                return await self._crawl_recursive(client, url, max_pages)
            text, title, _ = await self._fetch_page(client, url)
            return ExtractedContent(text=text, metadata={"url": url, "title": title})

    async def _fetch_page(self, client, url: str):
        """Fetch one page; return (clean_text, title, outbound_links)."""
        import re

        from bs4 import BeautifulSoup

        response = await client.get(url)
        response.raise_for_status()

        html_text = None
        for encoding in [response.encoding, "utf-8", "iso-8859-1", "windows-1252", "latin-1"]:
            if encoding:
                try:
                    html_text = response.content.decode(encoding, errors="strict")
                    break
                except (UnicodeDecodeError, LookupError):
                    continue
        if html_text is None:
            html_text = response.content.decode("utf-8", errors="ignore")

        soup = BeautifulSoup(html_text, "html.parser")
        # Collect links before stripping chrome (some live in nav).
        links = [a.get("href") for a in soup.find_all("a", href=True)]
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
        text = re.sub(r" +", " ", text).strip()
        title = soup.title.string if soup.title else url
        return text, title, links

    async def _crawl_recursive(self, client, start_url: str, max_pages: int) -> ExtractedContent:
        from urllib.parse import urlparse

        domain = urlparse(start_url).netloc
        seen = {start_url}
        queue = [start_url]
        pages = []
        while queue and len(pages) < max_pages:
            current = queue.pop(0)
            try:
                text, title, links = await self._fetch_page(client, current)
            except Exception:
                continue
            pages.append(f"# {title}\n{current}\n\n{text}")
            for nxt in self._same_domain_links(current, links, domain):
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        return ExtractedContent(
            text="\n\n---\n\n".join(pages),
            metadata={"url": start_url, "pages_crawled": len(pages), "mode": "recursive"},
        )

    async def _crawl_sitemap(self, client, sitemap_url: str, max_pages: int) -> ExtractedContent:
        response = await client.get(sitemap_url)
        response.raise_for_status()
        locs = self._parse_sitemap_locs(response.text)[:max_pages]
        pages = []
        for loc in locs:
            try:
                text, title, _ = await self._fetch_page(client, loc)
                pages.append(f"# {title}\n{loc}\n\n{text}")
            except Exception:
                continue
        return ExtractedContent(
            text="\n\n---\n\n".join(pages),
            metadata={"url": sitemap_url, "pages_crawled": len(pages), "mode": "sitemap"},
        )

    @staticmethod
    def _same_domain_links(base_url: str, links, domain: str):
        """Resolve links to absolute URLs and keep same-domain http(s) pages."""
        from urllib.parse import urljoin, urlparse

        out = []
        for href in links:
            if not href or href.startswith(("#", "mailto:", "javascript:")):
                continue
            absolute = urljoin(base_url, href).split("#")[0]
            parsed = urlparse(absolute)
            if parsed.scheme in ("http", "https") and parsed.netloc == domain:
                out.append(absolute)
        return out

    @staticmethod
    def _parse_sitemap_locs(xml_text: str):
        import re

        return [m.strip() for m in re.findall(r"<loc>\s*(.*?)\s*</loc>", xml_text, re.IGNORECASE)]


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
