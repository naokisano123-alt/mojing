"""真实 HTTP 导入回归测试矩阵 (server/tests/import_pipeline/test_import_matrix.py)。

契约与规范依据：
- docs/collaboration/ROUND5.md
- docs/collaboration/R5_GEMINI_LI.md (R5-L 核心交付块 4)
- docs/collaboration/R4_GEMINI_LI.md

特性覆盖：
1. TXT / MD / PDF / DOCX 四种格式真实 201 导入、详情、全文与切片无缝覆盖。
2. 跨重启持久化：同一数据库文件在重新初始化应用后内容完好无损。
3. 结构化告警：普通 PDF (流顺序与提取限制)、普通 DOCX (表格展平与跳过元素)、count 缺省不引发 500。
4. 混合型 PDF 的 PARTIAL_TEXT 真实保留与 count=1。
5. UTF-16 Emoji 边界与文本量超出时的 422 EXTRACTION_LIMIT 拦截。
6. 异常与恶意输入拦截（伪造、损坏、加密、纯空、超限）且确保数据库零残留。
7. 重复上传拦截 409 DUPLICATE_DOCUMENT 与 allow_duplicate 覆盖。
"""

from __future__ import annotations

import io
import unittest
from pathlib import Path

import pypdf

from app.parsers import u16len
from parsers.sample_helpers import create_empty_pdf, create_test_docx, create_test_pdf

from .pipeline_helpers import (
    create_test_world,
    fetch_chunks,
    fetch_raw_text,
    temp_pipeline_client,
    upload_document,
)

SAMPLE_TXT = (
    "北海纪行\n\n"
    "第一章 霜雪之港\n\n"
    "汽笛声在厚重晨雾中回荡，阿勒泰紧了紧身上的羊皮大氅。\n\n"
    "第二章 破冰之航\n\n"
    "开春第一艘灵舟在破冰船的引导下驶入深水港。"
)

SAMPLE_MD = (
    "# 云山志 · 渡云港\n\n"
    "渡云港位于山脚第一重云海旁，是凡间商贾与修道者的交汇之所。\n\n"
    "## 港区地理\n\n"
    "青铜泊位常年停泊着往来各峰的轻型飞舟。\n\n"
    "## 监管势力\n\n"
    "巡云卫负责维护港区秩序与灵石兑换。"
)


class TestImportPipelineMatrix(unittest.TestCase):
    def test_four_formats_201_and_persistence_across_restart(self) -> None:
        """四种受支持格式真实 201 上传、全文与切片提取，且重启应用后持久化完整。"""
        with temp_pipeline_client() as (client, db_path):
            world = create_test_world(client, name="四格式世界")
            world_id = world["id"]

            # 1. TXT
            res_txt = upload_document(client, world_id, "guide.txt", SAMPLE_TXT.encode("utf-8"), "text/plain")
            self.assertEqual(res_txt.status_code, 201, res_txt.text)
            doc_txt = res_txt.json()
            self.assertEqual(doc_txt["media_type"], "text/plain")
            self.assertEqual(doc_txt["status"], "ready")

            # 2. MD
            res_md = upload_document(client, world_id, "guide.md", SAMPLE_MD.encode("utf-8"), "text/markdown")
            self.assertEqual(res_md.status_code, 201, res_md.text)
            doc_md = res_md.json()
            self.assertEqual(doc_md["media_type"], "text/markdown")

            # 3. PDF
            pdf_bytes = create_test_pdf(["渡云港飞舟记录第一卷", "巡云卫巡查守则第二卷"])
            res_pdf = upload_document(client, world_id, "rules.pdf", pdf_bytes, "application/pdf")
            self.assertEqual(res_pdf.status_code, 201, res_pdf.text)
            doc_pdf = res_pdf.json()
            self.assertEqual(doc_pdf["media_type"], "application/pdf")
            self.assertEqual(doc_pdf["encoding_semantics"], "extracted_text")

            # 4. DOCX
            docx_bytes = create_test_docx(
                [
                    ("p", "第一章 听涛阁秘录"),
                    ("table", [["阵法", "消耗灵石"], ["聚风阵", "十枚"]]),
                ]
            )
            res_docx = upload_document(
                client,
                world_id,
                "lore.docx",
                docx_bytes,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
            self.assertEqual(res_docx.status_code, 201, res_docx.text)
            doc_docx = res_docx.json()
            self.assertEqual(doc_docx["media_type"], "application/vnd.openxmlformats-officedocument.wordprocessingml.document")

            # 验证切片覆盖度（首尾与连续性）
            for doc in [doc_txt, doc_md, doc_pdf, doc_docx]:
                chunks = fetch_chunks(client, doc["id"])
                self.assertEqual(len(chunks), doc["chunk_count"])
                self.assertEqual(chunks[0]["start_char"], 0)
                self.assertEqual(chunks[-1]["end_char"], doc["text_length"])
                full_raw = fetch_raw_text(client, doc["id"])
                joined_chunks = "".join(c["text"] for c in chunks)
                self.assertEqual(joined_chunks, full_raw)

            # 5. 跨重启持久化可读性验证：使用相同 db_path 重新构建 app 与 client
            with temp_pipeline_client(existing_db=db_path) as (restarted, _):
                listing = restarted.get(f"/api/v1/worlds/{world_id}/documents").json()
                self.assertEqual(listing["total"], 4)

                for doc in [doc_txt, doc_md, doc_pdf, doc_docx]:
                    persisted_detail = restarted.get(f"/api/v1/documents/{doc['id']}").json()
                    self.assertEqual(persisted_detail["status"], "ready")
                    self.assertEqual(persisted_detail["chunk_count"], doc["chunk_count"])
                    raw = fetch_raw_text(restarted, doc["id"])
                    self.assertGreater(len(raw), 0)

    def test_rich_document_structured_warnings(self) -> None:
        """测试正常 PDF 与 DOCX 的结构化告警，验证 count 缺省不引发 500。"""
        with temp_pipeline_client() as (client, _):
            world = create_test_world(client)
            world_id = world["id"]

            # 普通单页 PDF
            pdf_bytes = create_test_pdf(["纯文本单页正文"])
            res_pdf = upload_document(client, world_id, "normal.pdf", pdf_bytes, "application/pdf")
            self.assertEqual(res_pdf.status_code, 201)
            pdf_warnings = res_pdf.json()["warnings"]

            pdf_codes = [w["code"] for w in pdf_warnings]
            self.assertIn("COLUMN_ORDER_UNCERTAIN", pdf_codes)
            self.assertIn("EXTRACTION_WARNING", pdf_codes)
            self.assertNotIn("HEADERS_FOOTERS_SKIPPED", pdf_codes)
            self.assertTrue(any("count" not in w for w in pdf_warnings))

            # 包含表格与文本框的 DOCX
            docx_bytes = create_test_docx(
                [
                    ("p", "DOCX 正文段落"),
                    ("table", [["属性", "数值"], ["攻击力", "99"]]),
                ]
            )
            res_docx = upload_document(client, world_id, "tables.docx", docx_bytes)
            self.assertEqual(res_docx.status_code, 201)
            docx_warnings = res_docx.json()["warnings"]

            docx_codes = [w["code"] for w in docx_warnings]
            self.assertIn("TABLES_FLATTENED", docx_codes)
            self.assertIn("HEADERS_FOOTERS_SKIPPED", docx_codes)
            self.assertIn("TEXTBOXES_SKIPPED", docx_codes)

            tbl_record = next(w for w in docx_warnings if w["code"] == "TABLES_FLATTENED")
            self.assertEqual(tbl_record["count"], 1)

    def test_mixed_pdf_partial_text_warning(self) -> None:
        """混合页 PDF（有效正文 + 空白页）成功 201 并带有 PARTIAL_TEXT 与 count=1。"""
        with temp_pipeline_client() as (client, _):
            world = create_test_world(client)
            world_id = world["id"]

            pdf_bytes = create_test_pdf(["第一页有效文字", ""])
            res = upload_document(client, world_id, "mixed.pdf", pdf_bytes, "application/pdf")
            self.assertEqual(res.status_code, 201)
            warnings = res.json()["warnings"]

            partial_w = next((w for w in warnings if w["code"] == "PARTIAL_TEXT"), None)
            self.assertIsNotNone(partial_w)
            self.assertEqual(partial_w["count"], 1)

            raw = fetch_raw_text(client, res.json()["id"])
            self.assertIn("第一页有效文字", raw)

    def test_utf16_emoji_boundary_and_limit_enforcement(self) -> None:
        """UTF-16 Emoji 边界准确计算与超限 422 EXTRACTION_LIMIT 拦截。"""
        with temp_pipeline_client() as (client, _):
            world = create_test_world(client)
            world_id = world["id"]

            emojis_text = "云山秘境城堡🏰巨龙守卫🐉灵剑出鞘🗡️神盾守护🛡️光华闪烁✨"
            u16_units = u16len(emojis_text)
            self.assertGreater(u16_units, len(emojis_text))

            res = upload_document(client, world_id, "emoji.txt", emojis_text.encode("utf-8"), "text/plain")
            self.assertEqual(res.status_code, 201)
            doc = res.json()
            self.assertEqual(doc["text_length"], u16_units)
            raw = fetch_raw_text(client, doc["id"])
            self.assertEqual(raw, emojis_text)

        with temp_pipeline_client(overrides={"max_text_units": u16_units - 1}) as (strict_client, _):
            w = create_test_world(strict_client)
            res_limit = upload_document(strict_client, w["id"], "emoji_big.txt", emojis_text.encode("utf-8"), "text/plain")
            self.assertEqual(res_limit.status_code, 422)
            err = res_limit.json()["error"]
            self.assertEqual(err["code"], "EXTRACTION_LIMIT")
            self.assertEqual(err["details"]["limit"], u16_units - 1)
            self.assertEqual(err["details"]["actual"], u16_units)

    def test_corrupt_encrypted_fake_format_zero_residue(self) -> None:
        """伪造、损坏、加密、纯空与超限文档被稳定拦截，且数据库无残留数据。"""
        with temp_pipeline_client() as (client, _):
            world = create_test_world(client)
            world_id = world["id"]

            r_empty = upload_document(client, world_id, "empty.txt", b"   \n\n  ", "text/plain")
            self.assertEqual(r_empty.status_code, 422)
            self.assertEqual(r_empty.json()["error"]["code"], "EMPTY_DOCUMENT")

            r_fake_pdf = upload_document(client, world_id, "fake.pdf", b"This is plain text pretending to be PDF", "application/pdf")
            self.assertEqual(r_fake_pdf.status_code, 422)
            self.assertIn(r_fake_pdf.json()["error"]["code"], {"CORRUPT_DOCUMENT", "PDF_CORRUPTED"})

            r_broken_pdf = upload_document(client, world_id, "broken.pdf", b"%PDF-1.4\n broken trailer data", "application/pdf")
            self.assertEqual(r_broken_pdf.status_code, 422)

            empty_pdf = create_empty_pdf(pages_count=2)
            r_scanned_pdf = upload_document(client, world_id, "scanned.pdf", empty_pdf, "application/pdf")
            self.assertEqual(r_scanned_pdf.status_code, 422)
            self.assertIn(r_scanned_pdf.json()["error"]["code"], {"SCANNED_OR_EMPTY_PDF", "PDF_SCANNED"})

            enc_pdf = create_test_pdf(["机密档案"], encrypted_password="top_secret_pass")
            r_enc = upload_document(client, world_id, "secret.pdf", enc_pdf, "application/pdf")
            self.assertEqual(r_enc.status_code, 422)
            self.assertIn(r_enc.json()["error"]["code"], {"PDF_ENCRYPTED", "ENCRYPTED_DOCUMENT"})

            r_fake_docx = upload_document(client, world_id, "fake.docx", b"PK not really a zip docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
            self.assertEqual(r_fake_docx.status_code, 422)
            self.assertIn(r_fake_docx.json()["error"]["code"], {"CORRUPT_DOCUMENT", "DOCX_INVALID", "DOCX_CORRUPTED"})

            oversize_txt = b"X" * (2 * 1024 * 1024 + 100)
            r_over = upload_document(client, world_id, "huge.txt", oversize_txt, "text/plain")
            self.assertEqual(r_over.status_code, 413)
            self.assertEqual(r_over.json()["error"]["code"], "FILE_TOO_LARGE")

            docs_res = client.get(f"/api/v1/worlds/{world_id}/documents").json()
            self.assertEqual(docs_res["total"], 0, f"Expected 0 documents in world after rejected uploads, got {docs_res['total']}")
            self.assertEqual(len(docs_res["items"]), 0)

    def test_duplicate_document_rejection_and_override(self) -> None:
        """同内容文档上传触发 409 DUPLICATE_DOCUMENT，使用 allow_duplicate 参数允许上传。"""
        with temp_pipeline_client() as (client, _):
            world = create_test_world(client)
            world_id = world["id"]

            first = upload_document(client, world_id, "doc.md", SAMPLE_MD.encode("utf-8"), "text/markdown").json()
            self.assertEqual(first["status"], "ready")

            dup_res = upload_document(client, world_id, "doc_copy.md", SAMPLE_MD.encode("utf-8"), "text/markdown")
            self.assertEqual(dup_res.status_code, 409)
            err = dup_res.json()["error"]
            self.assertEqual(err["code"], "DUPLICATE_DOCUMENT")
            self.assertEqual(err["details"]["existing_document_id"], first["id"])

            allow_res = upload_document(
                client,
                world_id,
                "doc_copy.md",
                SAMPLE_MD.encode("utf-8"),
                "text/markdown",
                allow_duplicate=True,
            )
            self.assertEqual(allow_res.status_code, 201)
            self.assertEqual(client.get(f"/api/v1/worlds/{world_id}/documents").json()["total"], 2)


if __name__ == "__main__":
    unittest.main()
