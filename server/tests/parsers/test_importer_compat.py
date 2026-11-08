"""与既有 importer.py 的端到端兼容性回归测试。

契约与不变式验证（docs/contracts/M1_AB_API.md §3.4）：
1. 覆盖不变式（Coverage Invariant）：提取文本经分块后拼接，必须完整复原原文，字符无增减；
2. UTF-16 偏移单调连续性：chunks 的 [start_char, end_char) 必须在 UTF-16 code unit 意义下无缝首尾相接；
3. 包含 Emoji（如 🏮, ⚓, 📜）时，代理对（Surrogate pairs）不得损坏，u16len 长度与偏移精准一致；
4. DOCX 提取的 Markdown 标题在 chunk_text 中能被准确捕获为 heading_path。
"""

from __future__ import annotations

import unittest

from server.app.importer import chunk_text, normalize, u16len
from server.app.parsers import extract_document
from server.app.parsers.types import ParseLimits

from .sample_helpers import create_test_docx, create_test_pdf


class TestImporterCompatibility(unittest.TestCase):
    def setUp(self) -> None:
        self.limits = ParseLimits(max_bytes=2 * 1024 * 1024)

    def test_pdf_extraction_to_chunking_invariants(self) -> None:
        """验证 PDF 提取的中文正文与现有分块系统完全兼容并满足覆盖不变式。"""
        page1 = "第一章 雾港来信 ⚓\n北方群岛的晨雾弥漫在旧码头上。旧街的杂货铺刚刚开门。"
        page2 = "第二章 锈蚀钟塔\n老钟在正午敲响了三下。镇上的工匠在修补铜扣。"
        pdf_bytes = create_test_pdf([page1, page2])

        parsed = extract_document(pdf_bytes, "world_lore.pdf", limits=self.limits)
        norm_text = normalize(parsed.text)
        chunks = chunk_text(norm_text)

        self.assertGreater(len(chunks), 0)

        # 1. 覆盖不变式：拼接所有块内容等于规范化后的全文
        reconstructed = "".join(c.text for c in chunks)
        self.assertEqual(reconstructed, norm_text)

        # 2. UTF-16 偏移连续性不变式
        self.assertEqual(chunks[0].start_char, 0)
        self.assertEqual(chunks[-1].end_char, u16len(norm_text))
        for i in range(1, len(chunks)):
            self.assertEqual(
                chunks[i].start_char,
                chunks[i - 1].end_char,
                f"Chunk {i} start != chunk {i-1} end",
            )
            self.assertEqual(chunks[i].text_length, u16len(chunks[i].text))

    def test_docx_extraction_heading_path_detection(self) -> None:
        """验证 DOCX 提取的标题语法能被既有 chunk_text 准确识别并放入 heading_path。"""
        elements = [
            ("heading", "第一章 蒸汽公会", 1),
            ("p", "公会位于城北机械区，拥有多座熔炉。"),
            ("heading", "公会主要人物", 2),
            ("p", "会长奥斯汀是一名精通机械学的老矮人。"),
        ]
        docx_bytes = create_test_docx(elements)

        parsed = extract_document(docx_bytes, "guild.docx", limits=self.limits)
        norm_text = normalize(parsed.text)
        chunks = chunk_text(norm_text)

        self.assertGreater(len(chunks), 0)

        # 验证覆盖不变式
        self.assertEqual("".join(c.text for c in chunks), norm_text)

        # 验证第二段人物描述继承了正确的层级路径 ['第一章 蒸汽公会', '公会主要人物']
        austin_chunks = [c for c in chunks if "奥斯汀" in c.text]
        self.assertTrue(len(austin_chunks) > 0)
        heading_path = austin_chunks[0].heading_path
        self.assertIn("第一章 蒸汽公会", heading_path)
        self.assertIn("公会主要人物", heading_path)

    def test_emoji_and_surrogate_pair_u16_safety(self) -> None:
        """验证包含 4 字节 Emoji 时 UTF-16 偏移量准确无损。"""
        elements = [
            ("heading", "神话图谱 📜", 1),
            ("p", "古神遗留下的三件圣物：深海罗盘 🧭、守夜火炬 🏮 与破晓之剑 ⚔️。"),
        ]
        docx_bytes = create_test_docx(elements)

        parsed = extract_document(docx_bytes, "myth.docx", limits=self.limits)
        norm_text = normalize(parsed.text)
        chunks = chunk_text(norm_text)

        self.assertEqual("".join(c.text for c in chunks), norm_text)
        total_u16 = u16len(norm_text)
        self.assertEqual(chunks[-1].end_char, total_u16)


if __name__ == "__main__":
    unittest.main()
