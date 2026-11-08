"""导入限额与格式策略单元测试套件。

覆盖要求（依据 R3_GEMINI_LI.md 与 M1_R3_API.md §2.1）：
1. 默认限额检查：TXT/MD 为 2 MiB，PDF/DOCX 为 8 MiB。
2. 扩展名格式容错：大小写无关（.PDF, docx, .TxT）、去除前后空白、前导点自动补全。
3. 未知格式回退与判断。
4. 传入配置注入覆盖（不读 os.environ）。
5. 上传读取限量辅助函数（limit + 1）。
6. Capabilities 结构格式符合 R3 契约。
"""

from __future__ import annotations

import unittest

from app.import_policy import (
    DEFAULT_FALLBACK_MAX_BYTES,
    DEFAULT_MAX_BYTES_BY_FORMAT,
    ImportPolicyConfig,
    get_capabilities_max_bytes,
    get_max_bytes_for_suffix,
    get_upload_read_limit,
    is_supported_suffix,
    normalize_suffix,
)


class TestImportPolicy(unittest.TestCase):
    def test_normalize_suffix(self) -> None:
        """测试扩展名规范化。"""
        self.assertEqual(normalize_suffix(".pdf"), ".pdf")
        self.assertEqual(normalize_suffix("PDF"), ".pdf")
        self.assertEqual(normalize_suffix("  .DOCX  "), ".docx")
        self.assertEqual(normalize_suffix("txt"), ".txt")
        self.assertEqual(normalize_suffix(""), "")
        self.assertEqual(normalize_suffix(None), "")

    def test_default_format_limits(self) -> None:
        """验证默认 2 MiB (TXT/MD) 与 8 MiB (PDF/DOCX) 逐格式限额。"""
        two_mib = 2 * 1024 * 1024
        eight_mib = 8 * 1024 * 1024

        self.assertEqual(get_max_bytes_for_suffix(".txt"), two_mib)
        self.assertEqual(get_max_bytes_for_suffix(".md"), two_mib)
        self.assertEqual(get_max_bytes_for_suffix("TXT"), two_mib)
        self.assertEqual(get_max_bytes_for_suffix("md"), two_mib)

        self.assertEqual(get_max_bytes_for_suffix(".pdf"), eight_mib)
        self.assertEqual(get_max_bytes_for_suffix(".docx"), eight_mib)
        self.assertEqual(get_max_bytes_for_suffix("PDF"), eight_mib)
        self.assertEqual(get_max_bytes_for_suffix("DOCX"), eight_mib)

    def test_unknown_format_fallback(self) -> None:
        """未知格式使用安全的 fallback 限额。"""
        self.assertEqual(get_max_bytes_for_suffix(".exe"), DEFAULT_FALLBACK_MAX_BYTES)
        self.assertEqual(get_max_bytes_for_suffix(".bin"), DEFAULT_FALLBACK_MAX_BYTES)
        self.assertEqual(get_max_bytes_for_suffix(None), DEFAULT_FALLBACK_MAX_BYTES)

    def test_upload_read_limit_plus_one(self) -> None:
        """验证上传读取上限为 limit + 1。"""
        eight_mib = 8 * 1024 * 1024
        two_mib = 2 * 1024 * 1024

        self.assertEqual(get_upload_read_limit(".pdf"), eight_mib + 1)
        self.assertEqual(get_upload_read_limit(".docx"), eight_mib + 1)
        self.assertEqual(get_upload_read_limit(".txt"), two_mib + 1)
        self.assertEqual(get_upload_read_limit(".md"), two_mib + 1)

    def test_is_supported_suffix(self) -> None:
        """验证支持格式的判定。"""
        self.assertTrue(is_supported_suffix(".txt"))
        self.assertTrue(is_supported_suffix(".md"))
        self.assertTrue(is_supported_suffix(".pdf"))
        self.assertTrue(is_supported_suffix(".docx"))
        self.assertTrue(is_supported_suffix("PDF"))
        self.assertTrue(is_supported_suffix("DOCX"))

        self.assertFalse(is_supported_suffix(".rtf"))
        self.assertFalse(is_supported_suffix(".epub"))
        self.assertFalse(is_supported_suffix(""))
        self.assertFalse(is_supported_suffix(None))

    def test_config_injection_override(self) -> None:
        """测试外部参数/配置注入能够覆盖默认限额。"""
        custom_cfg = ImportPolicyConfig(
            max_bytes_by_format={
                ".txt": 1 * 1024 * 1024,
                ".pdf": 16 * 1024 * 1024,
            },
            fallback_max_bytes=512 * 1024,
            allowed_suffixes=(".txt", ".pdf"),
        )

        self.assertEqual(get_max_bytes_for_suffix(".txt", custom_cfg), 1 * 1024 * 1024)
        self.assertEqual(get_max_bytes_for_suffix(".pdf", custom_cfg), 16 * 1024 * 1024)
        self.assertEqual(get_max_bytes_for_suffix(".docx", custom_cfg), 512 * 1024)
        self.assertTrue(is_supported_suffix(".txt", custom_cfg))
        self.assertFalse(is_supported_suffix(".docx", custom_cfg))

        # 支持 dict 传参
        dict_cfg = {
            "max_bytes_by_format": {".docx": 10 * 1024 * 1024},
            "fallback_max_bytes": 1024,
        }
        self.assertEqual(get_max_bytes_for_suffix(".docx", dict_cfg), 10 * 1024 * 1024)
        self.assertEqual(get_max_bytes_for_suffix(".unknown", dict_cfg), 1024)

    def test_get_capabilities_max_bytes(self) -> None:
        """验证 capabilities 接口所需的数据结构。"""
        caps = get_capabilities_max_bytes()
        self.assertIn(".txt", caps)
        self.assertIn(".md", caps)
        self.assertIn(".pdf", caps)
        self.assertIn(".docx", caps)
        self.assertEqual(caps[".txt"], 2097152)
        self.assertEqual(caps[".md"], 2097152)
        self.assertEqual(caps[".pdf"], 8388608)
        self.assertEqual(caps[".docx"], 8388608)


if __name__ == "__main__":
    unittest.main()
