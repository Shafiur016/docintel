from io import BytesIO
from docx import Document as WordDocument
from app.ingest import extract_content, chunk_text


def test_extracts_docx_and_text_with_source_boundaries():
    doc = WordDocument()
    doc.add_paragraph('A PostgreSQL database supports transactions.')
    memory = BytesIO()
    doc.save(memory)
    result = extract_content(memory.getvalue(), 'manual.docx')
    assert 'PostgreSQL' in result
    assert extract_content(b'Redis caches data.', 'manual.txt') == 'Redis caches data.'


def test_chunking_has_nonempty_overlapping_windows():
    parts = chunk_text(' '.join('word' + str(i) for i in range(150)), chunk_size=75, overlap=15)
    assert len(parts) >= 2
    assert all(parts)
    assert len(parts[0]) <= 75
